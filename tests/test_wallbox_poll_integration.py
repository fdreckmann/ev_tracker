"""
Integration tests for PR 8 — Wallbox/Zähler detection wired into poll loop.

These tests close the gap: until PR 8 the detection functions existed and were
unit-tested but were never called in the real poll loop. Here we exercise the
full chain:

  process_power_snapshot / process_energy_snapshot
  → wallbox_session created (unassigned, excluded_from_reports=1)
  → reportable_session_where_clause keeps it out of reports

Additional coverage:
  go-e RFID snapshots → confirmed assignment
  allow_probable flag: False → unassigned, True → probable
"""
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
        "goe_rfid_enabled": False,
    }
    base.update(overrides)
    return base


def _snapshot(energy_wh_list):
    """Build a goe_rfid_service-style card snapshot."""
    cards = [{"slot": i, "name": f"Karte {i+1}", "energy_wh": e}
             for i, e in enumerate(energy_wh_list)]
    return {"cards": cards, "raw": {}}


# ---------------------------------------------------------------------------
# Poll-loop simulation helper
# ---------------------------------------------------------------------------

def _run_poll(db_con, cfg, st, power_kw, energy_total_kwh, ts):
    """Simulate one poll tick: call process_power_snapshot or process_energy_snapshot."""
    from services.wallbox_session_service import (
        process_power_snapshot, process_energy_snapshot,
    )
    src_name = "meter"
    src_type = "meter"
    if power_kw is not None:
        return process_power_snapshot(
            vehicle_id=None, source_type=src_type, source_name=src_name,
            power_kw=power_kw, energy_total_kwh=energy_total_kwh,
            ts=ts, cfg=cfg, st=st, con=db_con,
        )
    return process_energy_snapshot(
        vehicle_id=None, source_type=src_type, source_name=src_name,
        energy_total_kwh=energy_total_kwh, ts=ts, cfg=cfg, st=st, con=db_con,
    )


# ---------------------------------------------------------------------------
# Core: detection loop creates an unassigned wallbox_session
# ---------------------------------------------------------------------------

class TestPollLoopCreatesWallboxSession:
    def test_power_path_creates_unassigned_session(self, app):
        """Full power-path: rising power → debounce → open → stop → unassigned session."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            cfg = _cfg()
            st = {}

            # t=0: power above threshold — start debounce
            _run_poll(con, cfg, st, power_kw=3.0, energy_total_kwh=100.0, ts=_ts(0))
            # t=15: debounce elapsed → session opens
            _run_poll(con, cfg, st, power_kw=3.0, energy_total_kwh=100.5, ts=_ts(15))
            # t=30: power still above
            _run_poll(con, cfg, st, power_kw=3.0, energy_total_kwh=101.0, ts=_ts(30))
            # t=45: power drops below stop — start stop-debounce
            _run_poll(con, cfg, st, power_kw=0.05, energy_total_kwh=101.0, ts=_ts(45))
            # t=60: stop debounce elapsed → session closes
            _run_poll(con, cfg, st, power_kw=0.05, energy_total_kwh=101.0, ts=_ts(60))

            rows = con.execute(
                "SELECT * FROM wallbox_sessions ORDER BY id DESC LIMIT 1"
            ).fetchall()
            close_db_if_owned(con)

        assert len(rows) == 1
        s = dict(rows[0])
        assert s["vehicle_id"] is None
        assert s["vehicle_assignment_status"] == "unassigned"
        assert s["excluded_from_reports"] == 1

    def test_energy_path_creates_unassigned_session(self, app):
        """Energy-delta path (no power): rising counter → session → unassigned."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            cfg = _cfg()
            st = {}

            # First reading — opens a tentative session
            _run_poll(con, cfg, st, power_kw=None, energy_total_kwh=50.0, ts=_ts(0))
            # Rising for a while
            _run_poll(con, cfg, st, power_kw=None, energy_total_kwh=51.0, ts=_ts(60))
            _run_poll(con, cfg, st, power_kw=None, energy_total_kwh=52.0, ts=_ts(120))
            # Stable for stop_debounce → session closes
            _run_poll(con, cfg, st, power_kw=None, energy_total_kwh=52.0, ts=_ts(180))
            _run_poll(con, cfg, st, power_kw=None, energy_total_kwh=52.0, ts=_ts(250))

            rows = con.execute(
                "SELECT vehicle_assignment_status, excluded_from_reports, vehicle_id"
                " FROM wallbox_sessions WHERE status != 'active'"
            ).fetchall()
            close_db_if_owned(con)

        assert len(rows) >= 1
        s = dict(rows[0])
        assert s["vehicle_assignment_status"] == "unassigned"
        assert s["excluded_from_reports"] == 1
        assert s["vehicle_id"] is None


# ---------------------------------------------------------------------------
# Unassigned session absent from reports
# ---------------------------------------------------------------------------

class TestUnassignedNotInReports:
    def test_unassigned_wallbox_not_in_session_list(self, app, authed_client):
        """wallbox_session with unassigned status must not appear in GET /api/sessions."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            now = _ts(0)
            con.execute(
                """INSERT INTO wallbox_sessions
                   (source_type, source_name, start_ts, status,
                    vehicle_assignment_status, excluded_from_reports,
                    created_at, updated_at)
                   VALUES ('meter','meter',?,'unassigned','unassigned',1,?,?)""",
                (now, now, now),
            )
            con.commit()
            close_db_if_owned(con)

        rv = authed_client.get("/api/sessions?year=2026&month=6")
        assert rv.status_code == 200
        sessions = rv.get_json()
        assert all(s.get("vehicle_assignment_status") != "unassigned" for s in sessions)

    def test_monthly_stats_exclude_unassigned(self, app, authed_client):
        """Monthly stats must not count unassigned wallbox_sessions."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            ts = _ts(0)
            # Insert a fake session row that is unassigned / excluded
            con.execute(
                """INSERT INTO sessions
                   (start_ts, end_ts, kwh_charged, cost_eur, location, vehicle_id,
                    excluded_from_reports, vehicle_assignment_status)
                   VALUES (?,?,10.0,3.0,'home','v0',1,'unassigned')""",
                (ts, _ts(3600)),
            )
            con.commit()
            close_db_if_owned(con)

        rv = authed_client.get("/api/stats/monthly")
        assert rv.status_code == 200
        months = {m["month"]: m for m in rv.get_json()}
        june = months.get("2026-06", {})
        assert june.get("sessions", 0) == 0


# ---------------------------------------------------------------------------
# go-e RFID → confirmed assignment
# ---------------------------------------------------------------------------

class TestGoeRfidConfirmedAssignment:
    def test_goe_single_card_yields_confirmed(self, app):
        """When exactly one mapped card increases, _resolve_vehicle returns confirmed."""
        from services.wallbox_session_service import _resolve_vehicle
        cfg = _cfg(
            goe_rfid_enabled=True,
            goe_card_vehicle_map={0: "v0", 1: "extra_v1"},
        )
        st = {
            "goe_card_snapshot_start": _snapshot([10000.0, 5000.0]),
            "goe_card_snapshot_end":   _snapshot([10000.0, 23000.0]),  # slot 1 += 18 kWh
        }
        vid, status = _resolve_vehicle(None, cfg, st=st)
        assert vid == "extra_v1"
        assert status == "confirmed"

    def test_goe_multiple_cards_unassigned(self, app):
        """Two cards increase → conflict → unassigned (no Default-Fallback)."""
        from services.wallbox_session_service import _resolve_vehicle
        cfg = _cfg(
            goe_rfid_enabled=True,
            goe_card_vehicle_map={0: "v0", 1: "extra_v1"},
        )
        st = {
            "goe_card_snapshot_start": _snapshot([1000.0, 2000.0]),
            "goe_card_snapshot_end":   _snapshot([6000.0, 10000.0]),  # both rise
        }
        vid, status = _resolve_vehicle(None, cfg, st=st)
        assert vid is None
        assert status == "unassigned"

    def test_goe_no_cards_unassigned(self, app):
        """No card increases → unassigned."""
        from services.wallbox_session_service import _resolve_vehicle
        cfg = _cfg(
            goe_rfid_enabled=True,
            goe_card_vehicle_map={0: "v0"},
        )
        st = {
            "goe_card_snapshot_start": _snapshot([5000.0]),
            "goe_card_snapshot_end":   _snapshot([5000.0]),  # unchanged
        }
        vid, status = _resolve_vehicle(None, cfg, st=st)
        assert vid is None
        assert status == "unassigned"


# ---------------------------------------------------------------------------
# allow_probable flag
# ---------------------------------------------------------------------------

class TestAllowProbableFlag:
    def test_allow_probable_false_gives_unassigned(self, app):
        """default_vehicle_always + allow_probable=False → unassigned."""
        from services.wallbox_session_service import _resolve_vehicle
        cfg = _cfg(
            home_charge_vehicle_assignment_mode="default_vehicle_always",
            home_charge_default_vehicle_id="v0",
            home_charge_allow_probable_assignment=False,
        )
        vid, status = _resolve_vehicle(None, cfg)
        assert vid is None
        assert status == "unassigned"

    def test_allow_probable_true_gives_probable(self, app):
        """default_vehicle_always + allow_probable=True → probable (never confirmed)."""
        from services.wallbox_session_service import _resolve_vehicle
        cfg = _cfg(
            home_charge_vehicle_assignment_mode="default_vehicle_always",
            home_charge_default_vehicle_id="v0",
            home_charge_allow_probable_assignment=True,
        )
        vid, status = _resolve_vehicle(None, cfg)
        assert vid == "v0"
        assert status == "probable"
        assert status != "confirmed"
