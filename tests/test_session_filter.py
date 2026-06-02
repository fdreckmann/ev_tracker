"""
Tests for PR 2 — central reportable_session_where_clause and wallbox assignment routes.

Test numbers align with spec section 19:
  15  foreign_vehicle wallbox_session not in reports
  16  ignored wallbox_session not in reports
  17  require_rfid_or_api prevents automatic assignment
  18  default_vehicle_always creates probable, not confirmed
  19  user assigns wallbox_session → normal session created
  20  user marks foreign vehicle → excluded_from_reports=1
  21  user ignores → excluded_from_reports=1
  29  unassigned/foreign/ignored sessions not exported (session_filter)
  30  assigned wallbox_session appears in export
  31  dashboard counts only confirmed sessions
  32  historical session without new flags stays reportable
"""
import json
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_session(con, location="home", vehicle_id="v0",
                    excluded_from_reports=0,
                    vehicle_assignment_status="confirmed"):
    cur = con.execute(
        """INSERT INTO sessions
           (start_ts, end_ts, kwh_charged, cost_eur, location, vehicle_id,
            excluded_from_reports, vehicle_assignment_status)
           VALUES ('2026-04-10T10:00:00','2026-04-10T11:00:00',20.0,5.0,?,?,?,?)""",
        (location, vehicle_id, excluded_from_reports, vehicle_assignment_status),
    )
    con.commit()
    return cur.lastrowid


def _insert_wallbox_session(con, status="unassigned",
                             vehicle_assignment_status="unassigned",
                             excluded_from_reports=1,
                             energy_kwh=18.0):
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    cur = con.execute(
        """INSERT INTO wallbox_sessions
           (source_type, source_name, start_ts, end_ts, energy_kwh,
            meter_start_kwh, meter_end_kwh, status, vehicle_assignment_status,
            excluded_from_reports, created_at, updated_at)
           VALUES ('ev_wallbox','goe','2026-05-10T08:00:00','2026-05-10T09:30:00',?,
                   500.0, 518.0, ?,?,?,?,?)""",
        (energy_kwh, status, vehicle_assignment_status, excluded_from_reports, now, now),
    )
    con.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Test 15 — foreign_vehicle not in reports
# ---------------------------------------------------------------------------

class TestForeignVehicleNotReportable:
    def test_15_foreign_vehicle_session_excluded(self, app, client, authed_client):
        """A session with vehicle_assignment_status='foreign_vehicle' must not appear in _get_sessions."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            sid = _insert_session(con, vehicle_assignment_status="foreign_vehicle",
                                   excluded_from_reports=1)
            close_db_if_owned(con)

        rv = authed_client.get("/api/sessions?year=2026&month=4")
        data = rv.get_json()
        ids = [s["id"] for s in data]
        assert sid not in ids, "foreign_vehicle session must not appear in /api/sessions"


# ---------------------------------------------------------------------------
# Test 16 — ignored not in reports
# ---------------------------------------------------------------------------

class TestIgnoredNotReportable:
    def test_16_ignored_session_excluded(self, app, client, authed_client):
        """A session with vehicle_assignment_status='ignored' must not appear in /api/sessions."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            sid = _insert_session(con, vehicle_assignment_status="ignored",
                                   excluded_from_reports=1)
            close_db_if_owned(con)

        rv = authed_client.get("/api/sessions?year=2026&month=4")
        data = rv.get_json()
        ids = [s["id"] for s in data]
        assert sid not in ids, "ignored session must not appear in /api/sessions"


# ---------------------------------------------------------------------------
# Test 17 — require_rfid_or_api prevents automatic assignment
# ---------------------------------------------------------------------------

class TestRequireRfidOrApi:
    def test_17_require_rfid_or_api_unassigned(self, app):
        """require_rfid_or_api mode => vehicle stays NULL (unassigned)."""
        from services.wallbox_session_service import _resolve_vehicle
        cfg = {"home_charge_vehicle_assignment_mode": "require_rfid_or_api"}
        vid, status = _resolve_vehicle("v0", cfg)
        assert vid is None
        assert status == "unassigned"


# ---------------------------------------------------------------------------
# Test 18 — default_vehicle_always produces probable (not confirmed)
# ---------------------------------------------------------------------------

class TestDefaultVehicleAlwaysProbable:
    def test_18_default_vehicle_always_requires_allow_probable(self, app):
        """default_vehicle_always + allow_probable=False → unassigned (no auto-assignment)."""
        from services.wallbox_session_service import _resolve_vehicle
        cfg = {
            "home_charge_vehicle_assignment_mode": "default_vehicle_always",
            "home_charge_default_vehicle_id": "v0",
            "home_charge_allow_probable_assignment": False,
        }
        vid, status = _resolve_vehicle("v0", cfg)
        assert vid is None
        assert status == "unassigned"

    def test_18b_default_vehicle_always_probable_when_enabled(self, app):
        """default_vehicle_always + allow_probable=True → probable (never confirmed)."""
        from services.wallbox_session_service import _resolve_vehicle
        cfg = {
            "home_charge_vehicle_assignment_mode": "default_vehicle_always",
            "home_charge_default_vehicle_id": "v0",
            "home_charge_allow_probable_assignment": True,
        }
        vid, status = _resolve_vehicle("v0", cfg)
        assert vid == "v0"
        assert status == "probable"
        assert status != "confirmed"


# ---------------------------------------------------------------------------
# Test 19 — user assigns wallbox_session → normal session created
# ---------------------------------------------------------------------------

class TestAssignCreatesSession:
    def test_19_assign_creates_normal_session(self, app, authed_client):
        """POST assign with create_session=true creates a normal sessions row."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            wbs_id = _insert_wallbox_session(con)
            close_db_if_owned(con)

        rv = authed_client.post(
            f"/api/wallbox/sessions/{wbs_id}/assign",
            json={"vehicle_id": "v0", "create_session": True},
        )
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert data["session_id"] is not None

        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            sess = dict(con.execute(
                "SELECT * FROM sessions WHERE id=?", (data["session_id"],)).fetchone())
            wbs = dict(con.execute(
                "SELECT * FROM wallbox_sessions WHERE id=?", (wbs_id,)).fetchone())
            close_db_if_owned(con)

        assert sess["location"] == "home"
        assert sess["vehicle_id"] == "v0"
        assert sess["vehicle_assignment_status"] == "confirmed"
        assert sess["excluded_from_reports"] == 0
        assert wbs["vehicle_assignment_status"] == "confirmed"


# ---------------------------------------------------------------------------
# Test 20 — mark-foreign → excluded_from_reports=1
# ---------------------------------------------------------------------------

class TestMarkForeign:
    def test_20_mark_foreign_excluded(self, app, authed_client):
        """POST mark-foreign sets excluded_from_reports=1 and foreign_vehicle status."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            wbs_id = _insert_wallbox_session(con)
            close_db_if_owned(con)

        rv = authed_client.post(f"/api/wallbox/sessions/{wbs_id}/mark-foreign")
        assert rv.status_code == 200
        assert rv.get_json()["ok"] is True

        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            row = dict(con.execute(
                "SELECT vehicle_assignment_status, excluded_from_reports"
                " FROM wallbox_sessions WHERE id=?", (wbs_id,)).fetchone())
            close_db_if_owned(con)

        assert row["vehicle_assignment_status"] == "foreign_vehicle"
        assert row["excluded_from_reports"] == 1


# ---------------------------------------------------------------------------
# Test 21 — ignore → excluded_from_reports=1
# ---------------------------------------------------------------------------

class TestIgnoreSession:
    def test_21_ignore_excluded(self, app, authed_client):
        """POST ignore sets excluded_from_reports=1 and ignored status."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            wbs_id = _insert_wallbox_session(con)
            close_db_if_owned(con)

        rv = authed_client.post(f"/api/wallbox/sessions/{wbs_id}/ignore")
        assert rv.status_code == 200
        assert rv.get_json()["ok"] is True

        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            row = dict(con.execute(
                "SELECT vehicle_assignment_status, excluded_from_reports"
                " FROM wallbox_sessions WHERE id=?", (wbs_id,)).fetchone())
            close_db_if_owned(con)

        assert row["vehicle_assignment_status"] == "ignored"
        assert row["excluded_from_reports"] == 1


# ---------------------------------------------------------------------------
# Test 29 — unassigned/foreign/ignored not in report sessions (session_filter)
# ---------------------------------------------------------------------------

class TestSessionFilterExcludes:
    def test_29_unassigned_not_in_report_sessions(self, app):
        """_get_report_sessions must exclude unassigned/foreign/ignored sessions."""
        from datetime import date
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            # Insert a confirmed reportable session
            sid_ok = _insert_session(con, vehicle_assignment_status="confirmed",
                                      excluded_from_reports=0)
            # Insert non-reportable sessions
            sid_unassigned = _insert_session(
                con, vehicle_assignment_status="unassigned", excluded_from_reports=1)
            sid_foreign = _insert_session(
                con, vehicle_assignment_status="foreign_vehicle", excluded_from_reports=1)
            sid_ignored = _insert_session(
                con, vehicle_assignment_status="ignored", excluded_from_reports=1)
            close_db_if_owned(con)

        with app.app_context():
            from server import _get_report_sessions
            start = date(2026, 4, 1)
            end   = date(2026, 4, 30)
            sessions = _get_report_sessions(start, end)
            ids = [s["id"] for s in sessions]

        assert sid_ok in ids
        assert sid_unassigned not in ids
        assert sid_foreign not in ids
        assert sid_ignored not in ids

    def test_29b_filter_in_excel_fetch(self, app):
        """fetch_sessions (export_excel) must exclude non-reportable sessions."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            sid_ok = _insert_session(con, vehicle_assignment_status="confirmed",
                                      excluded_from_reports=0)
            sid_bad = _insert_session(
                con, vehicle_assignment_status="foreign_vehicle", excluded_from_reports=1)
            close_db_if_owned(con)

        with app.app_context():
            from export_excel import fetch_sessions
            rows = fetch_sessions(2026, 4, "home")
            ids = [r["id"] for r in rows]

        assert sid_ok in ids
        assert sid_bad not in ids


# ---------------------------------------------------------------------------
# Test 30 — assigned wallbox_session appears in export
# ---------------------------------------------------------------------------

class TestAssignedWallboxInExport:
    def test_30_assigned_session_in_export(self, app, authed_client):
        """A confirmed session (excluded_from_reports=0) appears in the session list."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            sid = _insert_session(con, excluded_from_reports=0,
                                  vehicle_assignment_status="confirmed")
            close_db_if_owned(con)

        rv = authed_client.get("/api/sessions?year=2026&month=4")
        ids = [s["id"] for s in rv.get_json()]
        assert sid in ids

    def test_30b_assigned_wallbox_in_excel_export(self, app):
        """fetch_sessions includes session where assignment is confirmed."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            sid = _insert_session(con, excluded_from_reports=0,
                                  vehicle_assignment_status="confirmed")
            close_db_if_owned(con)

        with app.app_context():
            from export_excel import fetch_sessions
            rows = fetch_sessions(2026, 4, "home")
            ids = [r["id"] for r in rows]
        assert sid in ids


# ---------------------------------------------------------------------------
# Test 31 — dashboard counts only confirmed sessions
# ---------------------------------------------------------------------------

class TestDashboardCountsConfirmed:
    def test_31_monthly_stats_exclude_unassigned(self, app, authed_client):
        """Monthly stats (kwh, cost, count) must not include unassigned sessions."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            # confirmed → should count
            _insert_session(con, excluded_from_reports=0,
                            vehicle_assignment_status="confirmed")
            # unassigned → must not count
            _insert_session(con, excluded_from_reports=1,
                            vehicle_assignment_status="unassigned")
            close_db_if_owned(con)

        rv = authed_client.get("/api/stats/monthly")
        assert rv.status_code == 200
        months = {m["month"]: m for m in rv.get_json()}
        april = months.get("2026-04", {})
        # Exactly one session (20 kWh) should be counted, not the unassigned one
        assert april.get("sessions", 0) == 1
        assert abs(april.get("total_kwh", 0) - 20.0) < 0.1


# ---------------------------------------------------------------------------
# Test 32 — historical session without new flags stays reportable
# ---------------------------------------------------------------------------

class TestHistoricalSessionsReportable:
    def test_32_old_session_no_new_columns_reportable(self, app, authed_client):
        """Sessions with excluded_from_reports=NULL and no assignment status are reportable."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            # Insert as if it came from an old install (NULL in new cols via explicit NULL)
            cur = con.execute(
                """INSERT INTO sessions
                   (start_ts, end_ts, kwh_charged, cost_eur, location, vehicle_id,
                    excluded_from_reports, vehicle_assignment_status)
                   VALUES ('2026-04-05T08:00:00','2026-04-05T09:00:00',15.0,4.5,'home','v0',
                           NULL, NULL)"""
            )
            sid = cur.lastrowid
            con.commit()
            close_db_if_owned(con)

        rv = authed_client.get("/api/sessions?year=2026&month=4")
        data = rv.get_json()
        ids = [s["id"] for s in data]
        assert sid in ids, "Historical session with NULL flags must stay reportable"

    def test_32b_backfilled_session_reportable(self, app, authed_client):
        """Sessions inserted via init_db backfill (excluded_from_reports=0) are reportable."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            sid = _insert_session(con, excluded_from_reports=0,
                                   vehicle_assignment_status="confirmed")
            close_db_if_owned(con)

        rv = authed_client.get("/api/sessions?year=2026&month=4")
        data = rv.get_json()
        ids = [s["id"] for s in data]
        assert sid in ids
