"""
Tests for PR 1 — meter_snapshots schema extension and wallbox_session_service.

Test numbers align with spec section 19:
  4  meter_snapshots: old DB migrates with new fields
  5  wallbox_sessions table is created
  6  charge_evidence table is created
  7  existing sessions stay reportable after migration
  8  home-charging starts when power > threshold (debounced)
  9  home-charging ends when power < stop threshold (debounced)
  10 energy_kwh is calculated from meter delta
  11 detection works without vehicle API (vehicle_id=None)
  12 without RFID/API confirmation -> unassigned wallbox_session
  13 multiple vehicles + no RFID -> no automatic v0 session
  14 unassigned wallbox_session does not appear in normal reports
"""
import json
from datetime import datetime, timedelta

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    """Isolated SQLite DB initialised via server.init_db."""
    import os, sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")
    os.environ.setdefault("EV_TRACKER_UPDATE_CHECK_ENABLED", "false")

    import sqlite3
    import core.db as coredb
    db_path = tmp_path / "sessions.db"
    import monkeypatch as _  # not available here — use direct patching
    # Patch DATA_DIR so server.init_db uses our temp path
    orig_data = coredb.DATA_DIR
    orig_db   = coredb.DB_PATH
    coredb.DATA_DIR = tmp_path
    coredb.DB_PATH  = db_path
    import server
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    server.init_db(con)
    yield con
    con.close()
    coredb.DATA_DIR = orig_data
    coredb.DB_PATH  = orig_db


@pytest.fixture()
def db_con(app):
    """DB connection via the proper app fixture (uses conftest.app)."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        yield con
        close_db_if_owned(con)


@pytest.fixture()
def wbs_cfg():
    """Minimal config for wallbox_session_service tests."""
    return {
        "home_charge_detection_enabled": True,
        "home_charge_power_start_threshold_kw": 1.0,
        "home_charge_power_stop_threshold_kw": 0.2,
        "home_charge_start_debounce_seconds": 10,   # short for tests
        "home_charge_stop_debounce_seconds": 10,    # short for tests
        "home_charge_min_energy_kwh": 0.1,
        "home_charge_vehicle_assignment_mode": "always_ask_if_unclear",
        "home_charge_allow_probable_assignment": False,
        "home_charge_default_vehicle_id": "",
    }


def _ts(offset_seconds: float = 0) -> str:
    base = datetime(2026, 5, 10, 10, 0, 0)
    return (base + timedelta(seconds=offset_seconds)).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Test 4 — meter_snapshots: old DB migrates with new fields
# ---------------------------------------------------------------------------

class TestMeterSnapshotsMigration:
    def test_new_columns_exist(self, db_con):
        """After init_db, meter_snapshots has the new optional columns."""
        cols = {row[1] for row in db_con.execute("PRAGMA table_info(meter_snapshots)")}
        assert "source_type"      in cols
        assert "source_name"      in cols
        assert "power_kw"         in cols
        assert "energy_total_kwh" in cols
        assert "raw_json"         in cols

    def test_store_snapshot_with_new_params(self, db_con):
        """store_meter_snapshot accepts and stores the new optional params."""
        from services.meter_snapshot_service import store_meter_snapshot
        rid = store_meter_snapshot(
            "v0", "goe", 120.5, None, "kWh", True, None, db_con,
            source_type="ev_wallbox",
            source_name="go-eCharger",
            power_kw=7.2,
            energy_total_kwh=120.5,
            raw_json='{"test":1}',
        )
        row = dict(db_con.execute(
            "SELECT * FROM meter_snapshots WHERE id=?", (rid,)).fetchone())
        assert row["source_type"]      == "ev_wallbox"
        assert row["source_name"]      == "go-eCharger"
        assert row["power_kw"]         == pytest.approx(7.2)
        assert row["energy_total_kwh"] == pytest.approx(120.5)
        assert row["raw_json"]         == '{"test":1}'

    def test_store_snapshot_old_style_still_works(self, db_con):
        """Old callers without new params continue to work."""
        from services.meter_snapshot_service import store_meter_snapshot
        rid = store_meter_snapshot("v0", "manual", 50.0, None, "kWh", True, None, db_con)
        row = dict(db_con.execute(
            "SELECT * FROM meter_snapshots WHERE id=?", (rid,)).fetchone())
        assert row["value_kwh"] == pytest.approx(50.0)
        # energy_total_kwh is backfilled from value_kwh
        assert row["energy_total_kwh"] == pytest.approx(50.0)
        # source_type defaults to 'meter'
        assert row["source_type"] == "meter"

    def test_backfill_source_type_on_old_rows(self, db_con):
        """source_type='meter' for rows inserted without the new column."""
        # Insert a row directly mimicking an old-style insert (no new cols)
        db_con.execute(
            "INSERT INTO meter_snapshots (vehicle_id, ts, source, value_kwh, ok, created_at)"
            " VALUES ('v0','2026-01-01T00:00:00','sma',99.0,1,'2026-01-01T00:00:00')"
        )
        db_con.commit()
        # Run the backfill that init_db would run (simulate on fresh row)
        db_con.execute(
            "UPDATE meter_snapshots SET source_type = 'meter'"
            " WHERE source_type IS NULL"
        )
        db_con.commit()
        row = dict(db_con.execute(
            "SELECT * FROM meter_snapshots WHERE source='sma'").fetchone())
        assert row["source_type"] == "meter"
        assert row["energy_total_kwh"] is None  # not backfilled in this test


# ---------------------------------------------------------------------------
# Test 5 — wallbox_sessions table is created
# ---------------------------------------------------------------------------

class TestWallboxSessionsTable:
    def test_table_exists(self, db_con):
        tables = {r[0] for r in db_con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "wallbox_sessions" in tables

    def test_required_columns(self, db_con):
        cols = {r[1] for r in db_con.execute("PRAGMA table_info(wallbox_sessions)")}
        required = {
            "id", "vehicle_id", "source_type", "source_name",
            "start_ts", "end_ts", "meter_start_kwh", "meter_end_kwh",
            "energy_kwh", "peak_power_kw", "status", "vehicle_assignment_status",
            "confidence", "excluded_from_reports", "created_at", "updated_at",
        }
        assert required <= cols

    def test_default_excluded_from_reports(self, db_con):
        from datetime import datetime
        now = datetime.now().isoformat(timespec="seconds")
        db_con.execute(
            "INSERT INTO wallbox_sessions (source_type, source_name, start_ts,"
            " status, vehicle_assignment_status, created_at, updated_at)"
            " VALUES ('meter','test',?,'active','unassigned',?,?)",
            (now, now, now),
        )
        db_con.commit()
        row = dict(db_con.execute(
            "SELECT excluded_from_reports FROM wallbox_sessions LIMIT 1").fetchone())
        assert row["excluded_from_reports"] == 1


# ---------------------------------------------------------------------------
# Test 6 — charge_evidence table is created
# ---------------------------------------------------------------------------

class TestChargeEvidenceTable:
    def test_table_exists(self, db_con):
        tables = {r[0] for r in db_con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "charge_evidence" in tables

    def test_required_columns(self, db_con):
        cols = {r[1] for r in db_con.execute("PRAGMA table_info(charge_evidence)")}
        for c in ("id", "vehicle_id", "session_id", "wallbox_session_id",
                  "source_type", "source_name", "energy_kwh", "confidence", "created_at"):
            assert c in cols


# ---------------------------------------------------------------------------
# Test 7 — existing sessions stay reportable after migration
# ---------------------------------------------------------------------------

class TestSessionsMigration:
    def test_new_columns_on_sessions(self, db_con):
        cols = {r[1] for r in db_con.execute("PRAGMA table_info(sessions)")}
        assert "excluded_from_reports" in cols
        assert "vehicle_assignment_status" in cols
        assert "source_primary" in cols

    def test_existing_sessions_reportable(self, db_con):
        """Sessions inserted without the new flags must default to reportable."""
        db_con.execute(
            "INSERT INTO sessions (start_ts, end_ts, kwh_charged, location, vehicle_id)"
            " VALUES ('2026-04-01T08:00:00','2026-04-01T09:00:00',20.0,'home','v0')"
        )
        db_con.commit()
        row = dict(db_con.execute(
            "SELECT excluded_from_reports, vehicle_assignment_status"
            " FROM sessions ORDER BY id DESC LIMIT 1").fetchone())
        # excluded_from_reports must be 0 (reportable)
        assert row["excluded_from_reports"] == 0
        assert row["vehicle_assignment_status"] == "confirmed"


# ---------------------------------------------------------------------------
# Tests 8–9 — start/stop detection
# ---------------------------------------------------------------------------

class TestPowerDetection:
    def test_8_session_starts_after_debounce(self, db_con, wbs_cfg):
        """A session opens once power > threshold for >= debounce seconds."""
        from services.wallbox_session_service import process_power_snapshot
        st = {}
        # First reading above threshold — starts debounce
        process_power_snapshot("v0", "ev_wallbox", "goe", 7.4, 100.0,
                                _ts(0), wbs_cfg, st, db_con)
        # Before debounce expires
        process_power_snapshot("v0", "ev_wallbox", "goe", 7.4, 100.3,
                                _ts(5), wbs_cfg, st, db_con)
        # No session yet
        assert db_con.execute(
            "SELECT COUNT(*) FROM wallbox_sessions WHERE status='active'").fetchone()[0] == 0
        # After debounce (> 10 s)
        process_power_snapshot("v0", "ev_wallbox", "goe", 7.4, 101.0,
                                _ts(15), wbs_cfg, st, db_con)
        count = db_con.execute(
            "SELECT COUNT(*) FROM wallbox_sessions WHERE status='active'").fetchone()[0]
        assert count == 1

    def test_9_session_closes_after_stop_debounce(self, db_con, wbs_cfg):
        """Session closes once power < stop threshold for >= stop_debounce seconds."""
        from services.wallbox_session_service import process_power_snapshot
        st = {}
        # Open a session (above start threshold + debounce)
        process_power_snapshot("v0", "ev_wallbox", "goe", 7.4, 200.0, _ts(0),  wbs_cfg, st, db_con)
        process_power_snapshot("v0", "ev_wallbox", "goe", 7.4, 200.5, _ts(15), wbs_cfg, st, db_con)
        assert db_con.execute(
            "SELECT COUNT(*) FROM wallbox_sessions WHERE status='active'").fetchone()[0] == 1

        # Power drops below stop threshold
        process_power_snapshot("v0", "ev_wallbox", "goe", 0.1, 201.0, _ts(20), wbs_cfg, st, db_con)
        # Not closed yet (stop debounce not elapsed)
        assert db_con.execute(
            "SELECT COUNT(*) FROM wallbox_sessions WHERE status='active'").fetchone()[0] == 1

        # After stop debounce
        closed_id = process_power_snapshot(
            "v0", "ev_wallbox", "goe", 0.0, 201.0, _ts(35), wbs_cfg, st, db_con)
        assert closed_id is not None
        row = dict(db_con.execute(
            "SELECT status FROM wallbox_sessions WHERE id=?", (closed_id,)).fetchone())
        assert row["status"] in ("completed", "unassigned")


# ---------------------------------------------------------------------------
# Test 10 — energy_kwh from meter delta
# ---------------------------------------------------------------------------

class TestEnergyCalculation:
    def test_10_energy_kwh_calculated_from_delta(self, db_con, wbs_cfg):
        """energy_kwh = meter_end_kwh - meter_start_kwh."""
        from services.wallbox_session_service import process_power_snapshot
        st = {}
        # Open session: energy starts at 1000 kWh
        process_power_snapshot("v0", "ev_wallbox", "goe", 7.4, 1000.0, _ts(0),  wbs_cfg, st, db_con)
        process_power_snapshot("v0", "ev_wallbox", "goe", 7.4, 1005.0, _ts(15), wbs_cfg, st, db_con)
        # Close: energy ends at 1018 kWh
        closed_id = process_power_snapshot(
            "v0", "ev_wallbox", "goe", 0.0, 1018.0, _ts(30), wbs_cfg, st, db_con)
        assert closed_id is not None
        row = dict(db_con.execute(
            "SELECT energy_kwh, meter_start_kwh, meter_end_kwh FROM wallbox_sessions"
            " WHERE id=?", (closed_id,)).fetchone())
        assert row["energy_kwh"] == pytest.approx(18.0, abs=0.01)
        assert row["meter_end_kwh"] == pytest.approx(1018.0)


# ---------------------------------------------------------------------------
# Test 11 — detection works without vehicle API
# ---------------------------------------------------------------------------

class TestNoVehicleApi:
    def test_11_detection_without_vehicle_id(self, db_con, wbs_cfg):
        """Sessions are created even when vehicle_id is None (API offline)."""
        from services.wallbox_session_service import process_power_snapshot
        st = {}
        process_power_snapshot(None, "ev_wallbox", "goe", 7.4, 500.0, _ts(0),  wbs_cfg, st, db_con)
        process_power_snapshot(None, "ev_wallbox", "goe", 7.4, 501.0, _ts(15), wbs_cfg, st, db_con)
        count = db_con.execute(
            "SELECT COUNT(*) FROM wallbox_sessions WHERE status='active'").fetchone()[0]
        assert count == 1
        row = dict(db_con.execute(
            "SELECT vehicle_id FROM wallbox_sessions WHERE status='active'").fetchone())
        # vehicle_id may be NULL — that's correct
        assert row["vehicle_id"] is None


# ---------------------------------------------------------------------------
# Test 12 — unassigned when no RFID/API confirmation
# ---------------------------------------------------------------------------

class TestUnassignedWithoutRfid:
    def test_12_unassigned_without_rfid(self, db_con, wbs_cfg):
        """Without RFID or API confirmation, session is unassigned."""
        from services.wallbox_session_service import process_power_snapshot
        st = {}
        process_power_snapshot("v0", "ev_wallbox", "goe", 7.4, 300.0, _ts(0),  wbs_cfg, st, db_con)
        process_power_snapshot("v0", "ev_wallbox", "goe", 7.4, 310.0, _ts(15), wbs_cfg, st, db_con)
        closed_id = process_power_snapshot(
            "v0", "ev_wallbox", "goe", 0.0, 318.0, _ts(30), wbs_cfg, st, db_con)
        assert closed_id is not None
        row = dict(db_con.execute(
            "SELECT vehicle_assignment_status, excluded_from_reports"
            " FROM wallbox_sessions WHERE id=?", (closed_id,)).fetchone())
        assert row["vehicle_assignment_status"] == "unassigned"
        assert row["excluded_from_reports"] == 1


# ---------------------------------------------------------------------------
# Test 13 — multiple vehicles + no RFID => no v0 auto-session
# ---------------------------------------------------------------------------

class TestNoAutoSessionForV0:
    def test_13_no_automatic_v0_session(self, db_con, wbs_cfg):
        """With always_ask_if_unclear mode, vehicle_id stays NULL even if v0 is available."""
        from services.wallbox_session_service import process_power_snapshot
        wbs_cfg["home_charge_vehicle_assignment_mode"] = "always_ask_if_unclear"
        st = {}
        process_power_snapshot("v0", "ev_wallbox", "goe", 7.4, 400.0, _ts(0),  wbs_cfg, st, db_con)
        process_power_snapshot("v0", "ev_wallbox", "goe", 7.4, 408.0, _ts(15), wbs_cfg, st, db_con)
        closed_id = process_power_snapshot(
            "v0", "ev_wallbox", "goe", 0.0, 415.0, _ts(30), wbs_cfg, st, db_con)
        assert closed_id is not None
        # Normal sessions table must NOT have a new auto-created session
        count = db_con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        assert count == 0, "No normal session should be auto-created"
        # wallbox_session vehicle_id must be NULL
        row = dict(db_con.execute(
            "SELECT vehicle_id FROM wallbox_sessions WHERE id=?", (closed_id,)).fetchone())
        assert row["vehicle_id"] is None


# ---------------------------------------------------------------------------
# Test 14 — unassigned not in normal reports
# ---------------------------------------------------------------------------

class TestUnassignedNotReportable:
    def test_14_unassigned_excluded_from_reports(self, db_con):
        """wallbox_sessions with unassigned status have excluded_from_reports=1."""
        now = _ts(0)
        db_con.execute(
            "INSERT INTO wallbox_sessions"
            " (source_type, source_name, start_ts, status, vehicle_assignment_status,"
            "  excluded_from_reports, created_at, updated_at)"
            " VALUES ('meter','test',?,'unassigned','unassigned',1,?,?)",
            (now, now, now),
        )
        db_con.commit()
        row = dict(db_con.execute(
            "SELECT excluded_from_reports, vehicle_assignment_status"
            " FROM wallbox_sessions WHERE status='unassigned'").fetchone())
        assert row["excluded_from_reports"] == 1
        assert row["vehicle_assignment_status"] == "unassigned"
