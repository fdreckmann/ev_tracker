"""
Wallbox/meter-based home-charge detection service.

Detects charging sessions from power/energy snapshots without requiring a vehicle
API. Produces wallbox_sessions records that must be explicitly assigned to a
vehicle before they become reportable normal sessions.

Key design invariants:
- A rising meter value means "energy was drawn at the wallbox", NOT "the tracked
  vehicle was charged". No automatic session creation without vehicle confirmation.
- Vehicle API offline/stale must not prevent detection.
- All wallbox_sessions start with excluded_from_reports = 1.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Tracker state helpers
# ---------------------------------------------------------------------------

def _wbs_state_key(source_name: str) -> str:
    return f"wbs_{source_name}"


def get_active_wallbox_session(source_name: str, st: dict, con=None) -> Optional[dict]:
    """Return the in-progress wallbox_session state dict, or None.

    ``st`` is process-local (RAM only) and is empty right after an app/tracker
    restart even though the DB may still have an active wallbox_sessions row
    (charging that was in progress when the process died/restarted). When
    ``con`` is given and ``st`` has no cached state, this rehydrates the
    newest active row for ``source_name`` from the DB so detection continues
    on the SAME row instead of opening a duplicate. The stop-debounce timer
    is restarted cleanly on rehydration (``last_above_ts`` = now) since we
    don't know how long ago the meter was last actively rising/drawing power.
    """
    key = _wbs_state_key(source_name)
    cached = st.get(key)
    if cached is not None or con is None:
        return cached

    rows = con.execute(
        "SELECT id, start_ts, meter_start_kwh, peak_power_kw FROM wallbox_sessions"
        " WHERE source_name=? AND status='active' ORDER BY id DESC",
        (source_name,),
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        log.warning(
            "wallbox_sessions: %d aktive Sessions für Quelle %r gefunden — setze nur "
            "die neueste (id=%s) fort, ältere bleiben unangetastet",
            len(rows), source_name, rows[0][0],
        )
    row = rows[0]
    rehydrated = {
        "id": row[0],
        "start_ts": row[1],
        "meter_start_kwh": row[2],
        "peak_power_kw": row[3],
        "last_above_ts": _now(),  # restart stop-debounce cleanly after a restart
    }
    st[key] = rehydrated
    log.info("wallbox_session %s aus DB rehydriert (source=%s, start=%s, meter_start_kwh=%s)",
             row[0], source_name, row[1], row[2])
    return rehydrated


def set_active_wallbox_session(source_name: str, st: dict, wbs: Optional[dict]) -> None:
    key = _wbs_state_key(source_name)
    if wbs is None:
        st.pop(key, None)
    else:
        st[key] = wbs


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _open_session(con, source_type: str, source_name: str, start_ts: str,
                  meter_start_kwh: Optional[float]) -> int:
    """Insert a new active wallbox_session. Returns the row id."""
    now = _now_iso()
    cur = con.execute(
        """INSERT INTO wallbox_sessions
           (source_type, source_name, start_ts, meter_start_kwh,
            status, vehicle_assignment_status, excluded_from_reports,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (source_type, source_name, start_ts, meter_start_kwh,
         "active", "unassigned", 1, now, now),
    )
    con.commit()
    return cur.lastrowid


def _close_session(con, wbs_id: int, end_ts: str, meter_end_kwh: Optional[float],
                   energy_kwh: Optional[float], peak_power_kw: Optional[float],
                   vehicle_id: Optional[str], assignment_status: str,
                   status: str) -> None:
    now = _now_iso()
    con.execute(
        """UPDATE wallbox_sessions
           SET end_ts=?, meter_end_kwh=?, energy_kwh=?, peak_power_kw=?,
               vehicle_id=?, vehicle_assignment_status=?, status=?,
               updated_at=?
           WHERE id=?""",
        (end_ts, meter_end_kwh, energy_kwh, peak_power_kw,
         vehicle_id, assignment_status, status, now, wbs_id),
    )
    con.commit()


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

def process_power_snapshot(
    vehicle_id: Optional[str],
    source_type: str,
    source_name: str,
    power_kw: Optional[float],
    energy_total_kwh: Optional[float],
    ts: str,
    cfg: dict,
    st: dict,
    con,
) -> Optional[int]:
    """Process one power/energy reading and open/close wallbox_sessions as needed.

    Returns the wallbox_session id if a session was just closed, else None.

    ``st`` is the mutable tracker state dict (in-process only; survives between
    polls in the same process lifetime). ``con`` is an open SQLite connection.
    """
    if not cfg.get("home_charge_detection_enabled", True):
        return None

    start_threshold = float(cfg.get("home_charge_power_start_threshold_kw", 1.0))
    stop_threshold  = float(cfg.get("home_charge_power_stop_threshold_kw", 0.2))
    start_debounce  = float(cfg.get("home_charge_start_debounce_seconds", 120))
    stop_debounce   = float(cfg.get("home_charge_stop_debounce_seconds", 300))
    min_energy_kwh  = float(cfg.get("home_charge_min_energy_kwh", 1.0))

    state = get_active_wallbox_session(source_name, st, con)

    # Determine if power indicates charging is ongoing
    above_start = power_kw is not None and power_kw >= start_threshold
    below_stop  = power_kw is None or power_kw < stop_threshold

    # Parse current timestamp
    try:
        ts_dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        ts_dt = _now()

    if state is None:
        # No active session — check whether to start one
        if above_start:
            debounce_since = st.get(f"wbs_{source_name}_above_since")
            if debounce_since is None:
                st[f"wbs_{source_name}_above_since"] = ts_dt
                st[f"wbs_{source_name}_peak_kw"] = power_kw
                # Remember energy at start of debounce so meter_start_kwh is accurate
                st[f"wbs_{source_name}_start_energy"] = energy_total_kwh
            else:
                elapsed = (ts_dt - debounce_since).total_seconds()
                peak = max(st.get(f"wbs_{source_name}_peak_kw") or 0.0, power_kw or 0.0)
                st[f"wbs_{source_name}_peak_kw"] = peak
                if elapsed >= start_debounce:
                    # Start a new wallbox_session using energy at debounce start
                    start_ts = debounce_since.isoformat(timespec="seconds")
                    start_energy = st.get(f"wbs_{source_name}_start_energy")
                    wbs_id = _open_session(con, source_type, source_name, start_ts, start_energy)
                    set_active_wallbox_session(source_name, st, {
                        "id": wbs_id,
                        "start_ts": start_ts,
                        "meter_start_kwh": start_energy,
                        "peak_power_kw": peak,
                        "last_above_ts": ts_dt,
                    })
                    st.pop(f"wbs_{source_name}_above_since", None)
                    st.pop(f"wbs_{source_name}_peak_kw", None)
                    st.pop(f"wbs_{source_name}_start_energy", None)
                    # go-e RFID: capture card snapshot at session start
                    if cfg.get("goe_rfid_enabled", False):
                        try:
                            from services.goe_rfid_service import read_card_energy_snapshot
                            st["goe_card_snapshot_start"] = read_card_energy_snapshot(cfg)
                            st.pop("goe_card_snapshot_end", None)
                        except Exception:
                            pass
                    log.info("wallbox_session %d opened (source=%s, start=%s)", wbs_id, source_name, start_ts)
        else:
            # Not above threshold — clear debounce state
            st.pop(f"wbs_{source_name}_above_since", None)
            st.pop(f"wbs_{source_name}_peak_kw", None)
            st.pop(f"wbs_{source_name}_start_energy", None)
        return None

    # Active session — update peak, track last-above timestamp
    if not below_stop:
        state["last_above_ts"] = ts_dt
        if power_kw is not None:
            state["peak_power_kw"] = max(state.get("peak_power_kw") or 0.0, power_kw)

    # Check for stop condition
    last_above = state.get("last_above_ts")
    if last_above is not None:
        idle_seconds = (ts_dt - last_above).total_seconds()
    else:
        idle_seconds = 0

    if below_stop and idle_seconds >= stop_debounce:
        # Close the session
        wbs_id = state["id"]
        meter_start = state.get("meter_start_kwh")
        meter_end   = energy_total_kwh
        if meter_start is not None and meter_end is not None:
            energy_kwh = round(meter_end - meter_start, 3)
        else:
            energy_kwh = None

        if energy_kwh is not None and energy_kwh < min_energy_kwh:
            # Too little energy — discard the session
            log.info("wallbox_session %d discarded (energy %.3f kWh < min %.1f)",
                     wbs_id, energy_kwh, min_energy_kwh)
            con.execute("DELETE FROM wallbox_sessions WHERE id=?", (wbs_id,))
            con.commit()
            set_active_wallbox_session(source_name, st, None)
            return None

        # go-e RFID: capture card snapshot at session end (before resolve)
        if cfg.get("goe_rfid_enabled", False):
            try:
                from services.goe_rfid_service import read_card_energy_snapshot
                st["goe_card_snapshot_end"] = read_card_energy_snapshot(cfg)
            except Exception:
                pass

        # Determine vehicle assignment
        assigned_vid, assign_status = _resolve_vehicle(vehicle_id, cfg, st=st, con=con)

        _close_session(
            con, wbs_id,
            end_ts=ts,
            meter_end_kwh=meter_end,
            energy_kwh=energy_kwh,
            peak_power_kw=state.get("peak_power_kw"),
            vehicle_id=assigned_vid,
            assignment_status=assign_status,
            status="completed" if assign_status in ("confirmed", "probable") else "unassigned",
        )
        log.info("wallbox_session %d closed (energy=%.3f kWh, status=%s)",
                 wbs_id, energy_kwh or 0.0, assign_status)
        set_active_wallbox_session(source_name, st, None)
        return wbs_id

    return None


def _resolve_vehicle(
    vehicle_id: Optional[str],
    cfg: dict,
    st: Optional[dict] = None,
    con=None,
) -> tuple[Optional[str], str]:
    """Determine which vehicle to assign and at what confidence.

    Returns (vehicle_id_or_None, assignment_status).
    """
    # 1. go-e RFID/Card: stärkstes Signal — geht vor allen anderen Regeln
    if cfg.get("goe_rfid_enabled", False) and st is not None:
        snap_before = st.get("goe_card_snapshot_start")
        snap_after  = st.get("goe_card_snapshot_end")
        if snap_before and snap_after:
            try:
                from services.goe_rfid_service import resolve_vehicle_from_cards
                vid, status, _card = resolve_vehicle_from_cards(cfg, snap_before, snap_after)
                if status == "confirmed" and vid:
                    return vid, "confirmed"
                # conflict / unassigned → fällt durch, kein Default-Fallback
                return None, "unassigned"
            except Exception:
                pass

    mode = cfg.get("home_charge_vehicle_assignment_mode", "always_ask_if_unclear")
    allow_probable = bool(cfg.get("home_charge_allow_probable_assignment", False))
    default_vid = cfg.get("home_charge_default_vehicle_id") or None

    if mode == "default_vehicle_always" and default_vid:
        if allow_probable:
            return default_vid, "probable"
        return None, "unassigned"

    if mode == "require_rfid_or_api":
        return None, "unassigned"

    if mode == "always_ask_if_unclear":
        return None, "unassigned"

    if mode == "default_vehicle_if_api_confirms" and vehicle_id:
        return vehicle_id, "probable"

    return None, "unassigned"


# ---------------------------------------------------------------------------
# Detection from cumulative energy (no power signal)
# ---------------------------------------------------------------------------

def process_energy_snapshot(
    vehicle_id: Optional[str],
    source_type: str,
    source_name: str,
    energy_total_kwh: float,
    ts: str,
    cfg: dict,
    st: dict,
    con,
) -> Optional[int]:
    """Open/close wallbox_sessions based on energy counter changes only.

    Used when only a cumulative kWh meter is available (no live power reading).
    Returns closed wallbox_session id or None.

    Design (mirrors process_power_snapshot's debounce shape, adapted for a
    counter instead of a power reading):
      * The very first reading for a source is ONLY a baseline — we don't yet
        know if a rise is happening, so no session may open on it.
      * A session opens only once a real, positive, plausible rise is
        observed relative to the last reading. Its meter_start_kwh is the
        value BEFORE the rise began (the baseline), not the first-ever value.
      * A falling/reset counter never produces negative energy and never
        opens a phantom session — the baseline is simply re-armed.
    """
    if not cfg.get("home_charge_detection_enabled", True):
        return None

    min_energy_kwh = float(cfg.get("home_charge_min_energy_kwh", 1.0))
    stop_debounce  = float(cfg.get("home_charge_stop_debounce_seconds", 300))
    rise_noise_floor = 0.01  # kWh — below this, treat as flat (meter jitter)

    key_last_val    = f"wbs_energy_{source_name}_last_val"
    key_baseline    = f"wbs_energy_{source_name}_baseline_val"
    key_baseline_ts = f"wbs_energy_{source_name}_baseline_ts"

    try:
        ts_dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        ts_dt = _now()

    last_val = st.get(key_last_val)
    active = get_active_wallbox_session(source_name, st, con)

    def _rearm_baseline():
        st[key_baseline] = energy_total_kwh
        st[key_baseline_ts] = ts_dt

    st[key_last_val] = energy_total_kwh

    if last_val is None:
        # First-ever reading for this source: baseline only, no session yet —
        # we cannot tell from a single sample whether a charge is mid-flight.
        _rearm_baseline()
        log.debug("wallbox energy-only[%s]: Baseline=%.3f kWh gesetzt (erster Messwert, keine Session)",
                 source_name, energy_total_kwh)
        return None

    delta = energy_total_kwh - last_val

    if active is None:
        if delta > rise_noise_floor:
            baseline_val = st.get(key_baseline, last_val)
            baseline_ts  = st.get(key_baseline_ts, ts_dt)
            start_ts = (baseline_ts.isoformat(timespec="seconds")
                        if hasattr(baseline_ts, "isoformat") else str(baseline_ts))
            wbs_id = _open_session(con, source_type, source_name, start_ts, baseline_val)
            set_active_wallbox_session(source_name, st, {
                "id": wbs_id, "start_ts": start_ts,
                "meter_start_kwh": baseline_val,
                "peak_power_kw": None,
                "last_above_ts": ts_dt,
            })
            log.info("wallbox_session %d opened via energy-only rise (source=%s, "
                     "start=%.3f kWh, now=%.3f kWh)", wbs_id, source_name, baseline_val or 0.0,
                     energy_total_kwh)
        elif delta < -rise_noise_floor:
            log.info("wallbox energy-only[%s]: Zählerreset/-rückgang (%.3f → %.3f kWh) — "
                     "Baseline neu gesetzt, keine Phantom-Session", source_name, last_val, energy_total_kwh)
            _rearm_baseline()
        else:
            # Flat within noise — this becomes the new stable baseline.
            _rearm_baseline()
        return None

    # An energy-only session is active — decide stop vs. continue.
    if delta > rise_noise_floor:
        active["last_above_ts"] = ts_dt
        set_active_wallbox_session(source_name, st, active)
        return None

    last_active_ts = active.get("last_above_ts")
    idle = (ts_dt - last_active_ts).total_seconds() if last_active_ts else 0
    if idle < stop_debounce:
        return None

    wbs_id = active["id"]
    meter_start = active.get("meter_start_kwh")
    meter_end   = energy_total_kwh
    energy_kwh = None
    if meter_start is not None and meter_end is not None and meter_end >= meter_start:
        energy_kwh = round(meter_end - meter_start, 3)

    if energy_kwh is None or energy_kwh < min_energy_kwh:
        reason = ("Zählerreset/implausibel (Ende < Start)"
                  if (meter_start is not None and meter_end is not None and meter_end < meter_start)
                  else f"Energie {energy_kwh} kWh < Minimum {min_energy_kwh}")
        log.info("wallbox_session %d verworfen (%s)", wbs_id, reason)
        con.execute("DELETE FROM wallbox_sessions WHERE id=?", (wbs_id,))
        con.commit()
        set_active_wallbox_session(source_name, st, None)
        _rearm_baseline()
        return None

    assigned_vid, assign_status = _resolve_vehicle(vehicle_id, cfg, st=st, con=con)
    _close_session(
        con, wbs_id,
        end_ts=ts,
        meter_end_kwh=meter_end,
        energy_kwh=energy_kwh,
        peak_power_kw=None,
        vehicle_id=assigned_vid,
        assignment_status=assign_status,
        status="completed" if assign_status in ("confirmed", "probable") else "unassigned",
    )
    log.info("wallbox_session %d closed via energy-only (energy=%.3f kWh, status=%s)",
             wbs_id, energy_kwh, assign_status)
    set_active_wallbox_session(source_name, st, None)
    _rearm_baseline()
    return wbs_id


# ---------------------------------------------------------------------------
# Public query helpers
# ---------------------------------------------------------------------------

def get_open_wallbox_sessions(con, limit: int = 50) -> list[dict]:
    """Return wallbox_sessions that still need user action."""
    rows = con.execute(
        """SELECT * FROM wallbox_sessions
           WHERE vehicle_assignment_status IN ('unassigned')
             AND status IN ('active','completed','unassigned')
           ORDER BY start_ts DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def assign_wallbox_session(con, wbs_id: int, vehicle_id: str) -> bool:
    """Mark a wallbox_session as confirmed for a vehicle. Returns True on success."""
    now = _now_iso()
    cur = con.execute(
        """UPDATE wallbox_sessions
           SET vehicle_id=?, vehicle_assignment_status='confirmed',
               excluded_from_reports=1, updated_at=?
           WHERE id=?""",
        (vehicle_id, now, wbs_id),
    )
    con.commit()
    return cur.rowcount > 0


def mark_foreign_vehicle(con, wbs_id: int) -> bool:
    now = _now_iso()
    cur = con.execute(
        """UPDATE wallbox_sessions
           SET vehicle_assignment_status='foreign_vehicle', status='foreign_vehicle',
               excluded_from_reports=1, updated_at=?
           WHERE id=?""",
        (now, wbs_id),
    )
    con.commit()
    return cur.rowcount > 0


def ignore_wallbox_session(con, wbs_id: int) -> bool:
    now = _now_iso()
    cur = con.execute(
        """UPDATE wallbox_sessions
           SET vehicle_assignment_status='ignored', status='ignored',
               excluded_from_reports=1, updated_at=?
           WHERE id=?""",
        (now, wbs_id),
    )
    con.commit()
    return cur.rowcount > 0
