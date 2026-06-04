"""
Tests for PR 10 — Unified Charging Intelligence.

The ChargingStateMachine is a thin orchestration layer over the existing
process_power_snapshot / process_energy_snapshot / _resolve_vehicle primitives.
It must:
  - detect meter-based home charging even when the vehicle API is down (the bug),
  - only reach `confirmed` via hard evidence (identity or API+corroboration),
  - retroactively upgrade an open/closed unassigned session when evidence lands,
  - fire a confirm-notification in confirm-mode,
  - apply conservative thresholds for line meters,
  - mint/verify signed confirmation tokens for direct ntfy actions.
"""
import json
from datetime import datetime, timedelta

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(offset_seconds: float = 0) -> str:
    base = datetime(2026, 6, 1, 9, 0, 0)
    return (base + timedelta(seconds=offset_seconds)).isoformat(timespec="seconds")


def _cfg(**overrides):
    base = {
        "home_charge_detection_enabled": True,
        "home_charge_power_start_threshold_kw": 1.0,
        "home_charge_power_stop_threshold_kw": 0.2,
        "home_charge_start_debounce_seconds": 10,
        "home_charge_stop_debounce_seconds": 10,
        "home_charge_min_energy_kwh": 0.1,
        "home_charge_vehicle_assignment_mode": "always_ask_if_unclear",
        "home_charge_allow_probable_assignment": False,
        "home_charge_default_vehicle_id": "",
        "home_charge_line_meter_power_start_threshold_kw": 1.4,
        "home_charge_line_meter_power_stop_threshold_kw": 0.3,
        "goe_rfid_enabled": False,
        # notification channels all off by default
    }
    base.update(overrides)
    return base


def _ingest(con, cfg, st, ts, **bundle_kwargs):
    from services.charging_state_machine import ChargingStateMachine, SignalBundle
    bundle_kwargs.setdefault("vehicle_id", "v0")
    bundle = SignalBundle(ts=ts, **bundle_kwargs)
    return ChargingStateMachine(cfg, st, con).ingest(bundle)


def _drive_power_session(con, cfg, st, *, power=3.0, kind="wallbox",
                         meter_source=None, start=100.0, **extra):
    """Open and close one power-path session across five poll ticks.
    Returns the closed session id (may come from any tick)."""
    common = dict(meter_kind=kind, meter_source=meter_source, **extra)
    results = [
        _ingest(con, cfg, st, _ts(0),  meter_power_kw=power, meter_energy_total_kwh=start, **common),
        _ingest(con, cfg, st, _ts(15), meter_power_kw=power, meter_energy_total_kwh=start + 0.5, **common),
        _ingest(con, cfg, st, _ts(30), meter_power_kw=power, meter_energy_total_kwh=start + 1.0, **common),
        _ingest(con, cfg, st, _ts(45), meter_power_kw=0.05,  meter_energy_total_kwh=start + 1.0, **common),
        _ingest(con, cfg, st, _ts(60), meter_power_kw=0.05,  meter_energy_total_kwh=start + 1.0, **common),
    ]
    return next((r for r in results if r is not None), None)


def _last_row(con):
    return dict(con.execute(
        "SELECT * FROM wallbox_sessions ORDER BY id DESC LIMIT 1").fetchone())


# ---------------------------------------------------------------------------
# 1 + 11 — Meter detection survives a dead vehicle API
# ---------------------------------------------------------------------------

class TestApiDownStillDetects:
    def test_meter_only_api_down_creates_unassigned(self, app):
        """Nur Meter, API down → Session entsteht, unassigned."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            st = {}
            closed = _drive_power_session(con, _cfg(), st, api_available=False)
            row = _last_row(con)
            close_db_if_owned(con)
        assert closed is not None
        assert row["vehicle_id"] is None
        assert row["vehicle_assignment_status"] == "unassigned"
        assert row["excluded_from_reports"] == 1

    def test_energy_path_api_down_creates_session(self, app):
        """(Kern) state.error + Meter-Delta (energy path) → Session entsteht."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            cfg = _cfg()
            st = {}
            _ingest(con, cfg, st, _ts(0),   api_available=False, meter_energy_total_kwh=50.0)
            _ingest(con, cfg, st, _ts(60),  api_available=False, meter_energy_total_kwh=51.0)
            _ingest(con, cfg, st, _ts(120), api_available=False, meter_energy_total_kwh=52.0)
            _ingest(con, cfg, st, _ts(180), api_available=False, meter_energy_total_kwh=52.0)
            _ingest(con, cfg, st, _ts(250), api_available=False, meter_energy_total_kwh=52.0)
            rows = con.execute(
                "SELECT * FROM wallbox_sessions WHERE status!='active'").fetchall()
            close_db_if_owned(con)
        assert len(rows) >= 1
        assert dict(rows[0])["vehicle_assignment_status"] == "unassigned"


# ---------------------------------------------------------------------------
# 2 — API-only (no meter) does not fabricate a wallbox session
# ---------------------------------------------------------------------------

class TestApiOnlyNoMeter:
    def test_api_charging_no_meter_no_wallbox_session(self, app):
        """Kein Meter → der Wallbox-Layer legt nichts an (normale API-Session
        läuft über den regulären Tracker, nicht über diese Schicht)."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            st = {}
            ret = _ingest(con, _cfg(), st, _ts(0),
                          api_available=True, api_charging=True, api_soc=50.0)
            n = con.execute("SELECT COUNT(*) FROM wallbox_sessions").fetchone()[0]
            close_db_if_owned(con)
        assert ret is None
        assert n == 0


# ---------------------------------------------------------------------------
# 3 — Identity + meter, API down → confirmed
# ---------------------------------------------------------------------------

class TestIdentityConfirmed:
    def test_identity_plus_meter_api_down_confirmed(self, app):
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            st = {}
            _drive_power_session(
                con, _cfg(), st,
                api_available=False,
                identity_vehicle_id="extra_v1",
                identity_source="goe_rfid",
                identity_confidence="confirmed",
            )
            row = _last_row(con)
            close_db_if_owned(con)
        assert row["vehicle_id"] == "extra_v1"
        assert row["vehicle_assignment_status"] == "confirmed"
        assert row["kwh_source_detail"] == "meter_delta_wallbox"
        sources = json.loads(row["signal_sources"])
        assert "meter" in sources and "identity" in sources


# ---------------------------------------------------------------------------
# 4 — Identity conflict → unassigned
# ---------------------------------------------------------------------------

class TestIdentityConflict:
    def test_identity_conflict_stays_unassigned(self, app):
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            st = {}
            _drive_power_session(
                con, _cfg(), st,
                api_available=False,
                identity_confidence="conflict",
            )
            row = _last_row(con)
            close_db_if_owned(con)
        assert row["vehicle_id"] is None
        assert row["vehicle_assignment_status"] == "unassigned"


# ---------------------------------------------------------------------------
# 5 — API + meter both active → meter kWh wins, both signals logged
# ---------------------------------------------------------------------------

class TestApiAndMeter:
    def test_api_home_charging_plus_meter_confirmed_meter_kwh(self, app):
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            st = {}
            _drive_power_session(
                con, _cfg(), st,
                api_available=True, api_charging=True,
                api_location="home", api_soc=40.0,
            )
            row = _last_row(con)
            close_db_if_owned(con)
        assert row["vehicle_assignment_status"] == "confirmed"
        assert row["vehicle_id"] == "v0"
        assert row["kwh_source_detail"] == "meter_delta_wallbox"
        sources = json.loads(row["signal_sources"])
        assert "meter" in sources and "api" in sources


# ---------------------------------------------------------------------------
# 6 — API says idle but meter is clearly active → meter wins, warn flag
# ---------------------------------------------------------------------------

class TestContradiction:
    def test_api_idle_meter_active_warns(self, app):
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            cfg = _cfg()
            st = {}
            # Open the session, then a high-power poll while API says not-charging.
            _ingest(con, cfg, st, _ts(0),  meter_power_kw=3.0, meter_energy_total_kwh=10.0,
                    api_available=True, api_charging=False)
            _ingest(con, cfg, st, _ts(15), meter_power_kw=3.0, meter_energy_total_kwh=10.5,
                    api_available=True, api_charging=False)
            _ingest(con, cfg, st, _ts(30), meter_power_kw=3.0, meter_energy_total_kwh=11.0,
                    api_available=True, api_charging=False)
            row = _last_row(con)
            close_db_if_owned(con)
        signals = json.loads(row["assignment_signals"] or "[]")
        assert any("meter_wins" in s for s in signals)


# ---------------------------------------------------------------------------
# 7 — SOC rising only, no meter/identity, home → probable only in auto-mode
# ---------------------------------------------------------------------------

class TestSocRisingResolution:
    def _resolve(self, app, cfg):
        from services.charging_state_machine import ChargingStateMachine, SignalBundle
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            sm = ChargingStateMachine(cfg, {}, con)
            b = SignalBundle(
                ts=_ts(0), vehicle_id="v0",
                api_available=True, api_charging=False, api_soc_rising=True,
                api_location="home",
            )
            res = sm.resolve_vehicle(b)
            close_db_if_owned(con)
        return res

    def test_confirm_mode_unassigned(self, app):
        vid, status = self._resolve(app, _cfg())  # always_ask_if_unclear
        assert (vid, status) == (None, "unassigned")

    def test_auto_mode_probable(self, app):
        cfg = _cfg(
            home_charge_vehicle_assignment_mode="default_vehicle_always",
            home_charge_default_vehicle_id="v0",
            home_charge_allow_probable_assignment=True,
        )
        vid, status = self._resolve(app, cfg)
        assert vid == "v0"
        assert status == "probable"


# ---------------------------------------------------------------------------
# 8 + 13 — Retroactive upgrade of an open session when evidence lands
# ---------------------------------------------------------------------------

class TestRetroactiveUpgrade:
    def test_api_recovers_enriches_open_session(self, app):
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            cfg = _cfg()
            st = {}
            # Open session while API is down
            _ingest(con, cfg, st, _ts(0),  api_available=False,
                    meter_power_kw=3.0, meter_energy_total_kwh=10.0)
            _ingest(con, cfg, st, _ts(15), api_available=False,
                    meter_power_kw=3.0, meter_energy_total_kwh=10.5)
            before = _last_row(con)
            # API recovers mid-charge: charging + soc rising → confirmed
            _ingest(con, cfg, st, _ts(30), api_available=True, api_charging=True,
                    api_soc_rising=True, meter_power_kw=3.0, meter_energy_total_kwh=11.0)
            after = _last_row(con)
            close_db_if_owned(con)
        assert before["vehicle_assignment_status"] == "unassigned"
        assert before["status"] == "active"
        assert after["vehicle_assignment_status"] == "confirmed"
        assert after["status"] == "active"  # still charging, just enriched
        signals = json.loads(after["assignment_signals"] or "[]")
        assert any("upgraded_to_confirmed" in s for s in signals)

    def test_identity_upgrade_documented(self, app):
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            cfg = _cfg()
            st = {}
            _ingest(con, cfg, st, _ts(0),  api_available=False,
                    meter_power_kw=3.0, meter_energy_total_kwh=10.0)
            _ingest(con, cfg, st, _ts(15), api_available=False,
                    meter_power_kw=3.0, meter_energy_total_kwh=10.5)
            assert _last_row(con)["vehicle_assignment_status"] == "unassigned"
            _ingest(con, cfg, st, _ts(30), api_available=False,
                    meter_power_kw=3.0, meter_energy_total_kwh=11.0,
                    identity_vehicle_id="v0", identity_source="goe_rfid",
                    identity_confidence="confirmed")
            row = _last_row(con)
            close_db_if_owned(con)
        assert row["vehicle_assignment_status"] == "confirmed"
        assert row["vehicle_id"] == "v0"
        signals = json.loads(row["assignment_signals"] or "[]")
        assert any("upgraded_to_confirmed:goe_rfid" in s for s in signals)


# ---------------------------------------------------------------------------
# 9 — master switch off
# ---------------------------------------------------------------------------

class TestMasterSwitch:
    def test_detection_disabled_creates_nothing(self, app):
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            st = {}
            _drive_power_session(con, _cfg(home_charge_detection_enabled=False), st,
                                 api_available=False)
            n = con.execute("SELECT COUNT(*) FROM wallbox_sessions").fetchone()[0]
            close_db_if_owned(con)
        assert n == 0


# ---------------------------------------------------------------------------
# 10 — everything off, open session stays open, closes on next signal
# ---------------------------------------------------------------------------

class TestOpenSessionPersists:
    def test_open_session_survives_signal_gap(self, app):
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            cfg = _cfg()
            st = {}
            _ingest(con, cfg, st, _ts(0),  api_available=False,
                    meter_power_kw=3.0, meter_energy_total_kwh=10.0)
            _ingest(con, cfg, st, _ts(15), api_available=False,
                    meter_power_kw=3.0, meter_energy_total_kwh=10.5)
            assert _last_row(con)["status"] == "active"
            # A poll with no usable signal at all — session must persist.
            _ingest(con, cfg, st, _ts(30), api_available=False)
            assert _last_row(con)["status"] == "active"
            # Stop signal arrives → closes.
            _ingest(con, cfg, st, _ts(45), api_available=False,
                    meter_power_kw=0.05, meter_energy_total_kwh=11.0)
            _ingest(con, cfg, st, _ts(60), api_available=False,
                    meter_power_kw=0.05, meter_energy_total_kwh=11.0)
            row = _last_row(con)
            close_db_if_owned(con)
        assert row["status"] != "active"


# ---------------------------------------------------------------------------
# 14 — confirm-mode fires a notification, open_home_charges counts it
# ---------------------------------------------------------------------------

class TestConfirmNotification:
    def test_unassigned_close_notifies(self, app):
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            st = {}
            _drive_power_session(con, _cfg(), st, api_available=False)
            notif = con.execute(
                "SELECT COUNT(*) FROM notifications WHERE type='home_charge_confirm'"
            ).fetchone()[0]
            open_count = con.execute(
                "SELECT COUNT(*) FROM wallbox_sessions"
                " WHERE vehicle_assignment_status='unassigned'"
            ).fetchone()[0]
            close_db_if_owned(con)
        assert notif >= 1
        assert open_count >= 1


# ---------------------------------------------------------------------------
# 15 — signed confirmation tokens
# ---------------------------------------------------------------------------

class TestConfirmToken:
    def test_make_and_verify_roundtrip(self, app):
        from services.charging_state_machine import make_confirm_token, verify_confirm_token
        tok = make_confirm_token(42, "v0")
        claim = verify_confirm_token(tok)
        assert claim == {"wbs_id": 42, "vehicle_id": "v0"}

    def test_tampered_token_rejected(self, app):
        from services.charging_state_machine import make_confirm_token, verify_confirm_token
        tok = make_confirm_token(42, "v0")
        assert verify_confirm_token(tok + "x") is None

    def test_expired_token_rejected(self, app):
        from services.charging_state_machine import make_confirm_token, verify_confirm_token
        tok = make_confirm_token(42, "v0", ttl_seconds=-1)
        assert verify_confirm_token(tok) is None

    def test_confirm_route_assigns_with_valid_token(self, app, client):
        from services.charging_state_machine import make_confirm_token
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            now = _ts(0)
            cur = con.execute(
                """INSERT INTO wallbox_sessions
                   (source_type, source_name, start_ts, status,
                    vehicle_assignment_status, excluded_from_reports, created_at, updated_at)
                   VALUES ('wallbox','meter',?,'unassigned','unassigned',1,?,?)""",
                (now, now, now))
            con.commit()
            wbs_id = cur.lastrowid
            close_db_if_owned(con)

        tok = make_confirm_token(wbs_id, "v0")
        rv = client.post(f"/api/wallbox/confirm?token={tok}&action=assign")
        assert rv.status_code == 200
        assert rv.get_json().get("ok") is True

        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            row = dict(con.execute(
                "SELECT * FROM wallbox_sessions WHERE id=?", (wbs_id,)).fetchone())
            close_db_if_owned(con)
        assert row["vehicle_assignment_status"] == "confirmed"
        assert row["vehicle_id"] == "v0"

    def test_confirm_route_rejects_bad_token(self, app, client):
        rv = client.post("/api/wallbox/confirm?token=garbage&action=assign")
        assert rv.status_code == 403


# ---------------------------------------------------------------------------
# 16 — line meter uses conservative thresholds + marks kwh source
# ---------------------------------------------------------------------------

class TestLineMeter:
    def test_classify_meter_kind(self):
        from services.charging_state_machine import classify_meter_kind
        assert classify_meter_kind("go_e") == "wallbox"
        assert classify_meter_kind("evcc") == "wallbox"
        assert classify_meter_kind("shelly") == "line_meter"
        assert classify_meter_kind("tasmota") == "line_meter"
        assert classify_meter_kind("ha") == "wallbox"        # default
        assert classify_meter_kind("unknown_thing") == "wallbox"

    def test_line_meter_below_conservative_threshold_no_session(self, app):
        """1.2 kW is above the wallbox start (1.0) but below the line start (1.4)
        → no session opens for a line meter."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            cfg = _cfg()
            st = {}
            for t in (0, 15, 30, 45):
                _ingest(con, cfg, st, _ts(t), api_available=False,
                        meter_power_kw=1.2, meter_energy_total_kwh=10.0 + t * 0.001,
                        meter_kind="line_meter", meter_source="shelly")
            n = con.execute("SELECT COUNT(*) FROM wallbox_sessions").fetchone()[0]
            close_db_if_owned(con)
        assert n == 0

    def test_line_meter_above_threshold_opens_and_marks_source(self, app):
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            cfg = _cfg()
            st = {}
            # energy-path line meter → kwh_source_detail marks the line origin
            _ingest(con, cfg, st, _ts(0),   api_available=False, meter_energy_total_kwh=50.0,
                    meter_kind="line_meter", meter_source="shelly")
            _ingest(con, cfg, st, _ts(60),  api_available=False, meter_energy_total_kwh=51.0,
                    meter_kind="line_meter", meter_source="shelly")
            _ingest(con, cfg, st, _ts(120), api_available=False, meter_energy_total_kwh=52.0,
                    meter_kind="line_meter", meter_source="shelly")
            _ingest(con, cfg, st, _ts(180), api_available=False, meter_energy_total_kwh=52.0,
                    meter_kind="line_meter", meter_source="shelly")
            _ingest(con, cfg, st, _ts(250), api_available=False, meter_energy_total_kwh=52.0,
                    meter_kind="line_meter", meter_source="shelly")
            rows = con.execute(
                "SELECT * FROM wallbox_sessions WHERE status!='active'").fetchall()
            close_db_if_owned(con)
        assert len(rows) >= 1
        assert dict(rows[0])["kwh_source_detail"] == "meter_delta_line"
