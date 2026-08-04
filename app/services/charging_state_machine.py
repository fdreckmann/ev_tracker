"""
Unified Charging Intelligence (PR 10).

A single decision layer that ingests *all* available charging signals
(vehicle API, meter/wallbox power+energy, RFID/identity) for one poll cycle and
delegates to the existing detection primitives in ``wallbox_session_service``.

Design invariants (see PR 10 spec):
- No signal is a gatekeeper. A dead vehicle API must not stop meter-based
  home-charge detection — that is exactly the bug this layer fixes.
- ``confirmed`` is only reachable via *hard* evidence (identity, or API
  charging + corroboration). Never via a default vehicle alone.
- The existing ``process_power_snapshot`` / ``process_energy_snapshot`` /
  ``_resolve_vehicle`` functions are NOT moved or rewritten — this class is
  merely their new, smarter caller and post-annotator.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Meter classification
# ---------------------------------------------------------------------------

# Dedicated chargers / wallboxes measure (almost) only the car.
_WALLBOX_SOURCES = {"go_e", "goe", "openwb", "warp", "evcc", "webasto", "alfen", "juice"}
# Line meters measure a circuit and may include foreign load.
_LINE_METER_SOURCES = {"shelly", "tasmota", "generic"}


def classify_meter_kind(meter_source: Optional[str], meter_type: Optional[str] = None) -> str:
    """Map a meter source/type to "wallbox" or "line_meter".

    Unknown or ``ha`` sources default to "wallbox" (conservative — see spec:
    "im Zweifel wallbox").
    """
    for token in ((meter_source or "").strip().lower(), (meter_type or "").strip().lower()):
        if token in _WALLBOX_SOURCES:
            return "wallbox"
        if token in _LINE_METER_SOURCES:
            return "line_meter"
    return "wallbox"


# ---------------------------------------------------------------------------
# Signal bundle
# ---------------------------------------------------------------------------

@dataclass
class SignalBundle:
    ts: str
    vehicle_id: str
    # Fahrzeug-API (optional)
    api_available: bool = False
    api_charging: Optional[bool] = None
    api_soc: Optional[float] = None
    api_soc_rising: Optional[bool] = None
    api_location: Optional[str] = None
    api_power_kw: Optional[float] = None
    # Meter/Wallbox (optional)
    meter_power_kw: Optional[float] = None
    meter_energy_total_kwh: Optional[float] = None
    meter_source: Optional[str] = None
    meter_kind: str = "wallbox"            # "wallbox" | "line_meter"
    # Identität (optional, provider-neutral)
    identity_vehicle_id: Optional[str] = None
    identity_source: Optional[str] = None  # "goe_rfid" | "ocpp_idtag" | ...
    identity_confidence: str = "none"      # "confirmed" | "conflict" | "none"


# ---------------------------------------------------------------------------
# Signed confirmation tokens (ntfy direct-action)
# ---------------------------------------------------------------------------

def _token_secret() -> bytes:
    secret = os.environ.get("FLASK_SECRET_KEY", "")
    if not secret:
        try:
            from core.config import load_config
            secret = load_config().get("flask_secret_key", "") or ""
        except Exception:
            secret = ""
    return (secret or "ev-tracker-insecure-fallback").encode("utf-8")


def make_confirm_token(wbs_id: int, vehicle_id: Optional[str], ttl_seconds: int = 86400) -> str:
    """HMAC-signed token binding session id + vehicle id with a short TTL."""
    exp = int(time.time()) + int(ttl_seconds)
    payload = f"{wbs_id}:{vehicle_id or ''}:{exp}"
    sig = hmac.new(_token_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    raw = f"{payload}:{sig}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def verify_confirm_token(token: str) -> Optional[dict]:
    """Validate a confirm token. Returns {wbs_id, vehicle_id} or None."""
    try:
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + pad).decode("utf-8")
        wbs_id_s, vehicle_id, exp_s, sig = raw.rsplit(":", 3)
        payload = f"{wbs_id_s}:{vehicle_id}:{exp_s}"
        expected = hmac.new(_token_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return None
        if int(exp_s) < int(time.time()):
            return None
        return {"wbs_id": int(wbs_id_s), "vehicle_id": vehicle_id or None}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_confirm_mode(cfg: dict) -> bool:
    """confirm-mode (default): unassigned without hard evidence → ask the user.

    auto-mode (opt-in via allow_probable) suppresses the confirm prompt.
    """
    if cfg.get("home_charge_allow_probable_assignment", False):
        return False
    return True


def resolve_notification_recipient(vehicle_id: Optional[str], cfg: dict) -> Optional[str]:
    """Resolve who should be asked to confirm a home charge for ``vehicle_id``.

    Today a No-Op that returns the globally configured ntfy topic (or None).
    Fleet-ready seam: later this maps to the user assigned to the vehicle.
    Never hard-wires "broadcast to everyone".
    """
    return cfg.get("notification_ntfy_topic") or None


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class ChargingStateMachine:
    """Orchestration only — decides and delegates, never builds a second
    session mechanic."""

    def __init__(self, cfg: dict, st: dict, con):
        self.cfg, self.st, self.con = cfg, st, con

    # -- public entry ------------------------------------------------------

    def ingest(self, bundle: SignalBundle) -> Optional[int]:
        """Single entry point per poll. Returns a just-closed wallbox_session
        id, else None."""
        if not self.cfg.get("home_charge_detection_enabled", True):
            return None

        from services.wallbox_session_service import (
            process_power_snapshot, process_energy_snapshot,
        )
        src = bundle.meter_source or self.cfg.get("meter_source", "meter") or "meter"
        eff_cfg = self._effective_cfg(bundle)
        closed_id: Optional[int] = None

        if bundle.meter_power_kw is not None:
            closed_id = process_power_snapshot(
                vehicle_id=None, source_type=bundle.meter_kind, source_name=src,
                power_kw=bundle.meter_power_kw,
                energy_total_kwh=bundle.meter_energy_total_kwh,
                ts=bundle.ts, cfg=eff_cfg, st=self.st, con=self.con,
            )
        elif bundle.meter_energy_total_kwh is not None:
            closed_id = process_energy_snapshot(
                vehicle_id=None, source_type=bundle.meter_kind, source_name=src,
                energy_total_kwh=bundle.meter_energy_total_kwh,
                ts=bundle.ts, cfg=eff_cfg, st=self.st, con=self.con,
            )

        self._annotate_and_resolve(bundle, src, closed_id)
        return closed_id

    # -- resolution --------------------------------------------------------

    def resolve_vehicle(self, bundle: SignalBundle) -> tuple[Optional[str], str]:
        """Mix hard evidence over the existing mode logic. Does NOT move
        ``_resolve_vehicle``."""
        from services.wallbox_session_service import _resolve_vehicle

        # Hard evidence first (§3):
        if bundle.identity_confidence == "confirmed" and bundle.identity_vehicle_id:
            return bundle.identity_vehicle_id, "confirmed"
        if bundle.identity_confidence == "conflict":
            return None, "unassigned"
        if (bundle.api_available and bundle.api_charging and bundle.api_soc_rising
                and bundle.vehicle_id):
            return bundle.vehicle_id, "confirmed"
        if (bundle.api_available and bundle.api_charging
                and bundle.api_location == "home" and bundle.vehicle_id):
            return bundle.vehicle_id, "confirmed"
        # Otherwise fall back to the existing per-mode logic.
        return _resolve_vehicle(bundle.vehicle_id, self.cfg, st=self.st, con=self.con)

    # -- internals ---------------------------------------------------------

    def _effective_cfg(self, bundle: SignalBundle) -> dict:
        """For line meters, apply the conservative thresholds."""
        if bundle.meter_kind != "line_meter":
            return self.cfg
        c = dict(self.cfg)
        c["home_charge_power_start_threshold_kw"] = self.cfg.get(
            "home_charge_line_meter_power_start_threshold_kw", 1.4)
        c["home_charge_power_stop_threshold_kw"] = self.cfg.get(
            "home_charge_line_meter_power_stop_threshold_kw", 0.3)
        return c

    def _signal_sources(self, bundle: SignalBundle) -> list[str]:
        sources: list[str] = []
        if bundle.meter_power_kw is not None or bundle.meter_energy_total_kwh is not None:
            sources.append("meter")
        if bundle.identity_confidence == "confirmed":
            sources.append("identity")
        if bundle.api_available and bundle.api_charging:
            sources.append("api")
        return sources

    def _kwh_source_detail(self, bundle: SignalBundle) -> str:
        if bundle.meter_energy_total_kwh is not None:
            return "meter_delta_line" if bundle.meter_kind == "line_meter" else "meter_delta_wallbox"
        if bundle.api_available and bundle.api_charging:
            return "api"
        return "estimated"

    def _annotate_and_resolve(self, bundle: SignalBundle, src: str, closed_id: Optional[int]) -> None:
        """Annotate signal provenance and apply retroactive confirmed/probable
        upgrades to the relevant wallbox_session row."""
        from services.wallbox_session_service import get_active_wallbox_session

        wbs_id = closed_id
        if wbs_id is None:
            active = get_active_wallbox_session(src, self.st, self.con)
            if active:
                wbs_id = active.get("id")
        if wbs_id is None:
            return

        row = self.con.execute(
            "SELECT vehicle_id, vehicle_assignment_status, status, assignment_signals"
            " FROM wallbox_sessions WHERE id=?", (wbs_id,),
        ).fetchone()
        if not row:
            return
        cur_vid, cur_status, cur_session_status, cur_signals_raw = row[0], row[1], row[2], row[3]

        try:
            signals_log = json.loads(cur_signals_raw) if cur_signals_raw else []
            if not isinstance(signals_log, list):
                signals_log = []
        except Exception:
            signals_log = []

        # Contradiction: API says idle, meter clearly active → meter wins (warn).
        start_thr = float(self._effective_cfg(bundle).get("home_charge_power_start_threshold_kw", 1.0))
        if (bundle.api_available and bundle.api_charging is False
                and (bundle.meter_power_kw or 0.0) >= start_thr):
            warn = "api_idle_but_meter_active:meter_wins"
            if warn not in signals_log:
                signals_log.append(warn)

        vid, status = self.resolve_vehicle(bundle)
        new_vid, new_status, new_session_status = cur_vid, cur_status, cur_session_status

        if cur_status == "unassigned" and status == "confirmed" and vid:
            new_vid, new_status = vid, "confirmed"
            if cur_session_status in ("unassigned", "completed"):
                new_session_status = "completed"
            signals_log.append(
                f"upgraded_to_confirmed:{bundle.identity_source or ('api' if bundle.api_available else 'unknown')}")
        elif cur_status == "unassigned" and status == "probable" and vid:
            new_vid, new_status = vid, "probable"
            if cur_session_status == "unassigned":
                new_session_status = "completed"
            signals_log.append("assigned_probable")

        self.con.execute(
            "UPDATE wallbox_sessions SET vehicle_id=?, vehicle_assignment_status=?,"
            " status=?, signal_sources=?, kwh_source_detail=?, assignment_signals=?"
            " WHERE id=?",
            (new_vid, new_status, new_session_status,
             json.dumps(self._signal_sources(bundle)),
             self._kwh_source_detail(bundle),
             json.dumps(signals_log) if signals_log else None,
             wbs_id),
        )
        self.con.commit()

        # Confirm-flow notification: only for a freshly-closed unassigned session
        # in confirm-mode.
        if closed_id is not None and new_status == "unassigned" and _is_confirm_mode(self.cfg):
            self._notify_confirm(closed_id, bundle)

    def _notify_confirm(self, wbs_id: int, bundle: SignalBundle) -> None:
        try:
            from services.notification_service import notify
            row = self.con.execute(
                "SELECT energy_kwh FROM wallbox_sessions WHERE id=?", (wbs_id,)
            ).fetchone()
            energy = (row[0] if row else None) or 0.0
            veh_label = bundle.vehicle_id or "Fahrzeug"
            token = make_confirm_token(wbs_id, bundle.vehicle_id)
            actions = [
                {"label": f"Meine {veh_label}", "action": "assign", "token": token},
                {"label": "Fremd",             "action": "mark-foreign", "token": token},
                {"label": "Ignorieren",        "action": "ignore", "token": token},
            ]
            resolve_notification_recipient(bundle.vehicle_id, self.cfg)  # no-op seam
            notify(
                type="home_charge_confirm",
                severity="info",
                title="Heimladung bestätigen",
                message=f"{energy:.2f} kWh an der Wallbox geladen. Wer war das?",
                vehicle_id=bundle.vehicle_id,
                data={"wbs_id": wbs_id, "energy_kwh": energy},
                dedupe_key=f"home_charge_confirm:{wbs_id}",
                action_url="/?open_home_charges=1",
                action_payload={"wbs_id": wbs_id, "actions": actions},
                _background=False,
            )
        except Exception as exc:
            log.debug("home-charge confirm notify failed [wbs=%s]: %s", wbs_id, exc)
