"""
Tests for PR 3 — reconciliation_service.

Test 22 from spec section 19:
  22  existing API session + wallbox_session → no duplicate session created

Additional coverage:
  - wallbox session without existing match → new session created
  - meter data enriched on existing session
  - kWh from wallbox preferred when no prior meter
  - time window overlap detection works correctly
"""
import json
import pytest
from datetime import datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now():
    return datetime.now().isoformat(timespec="seconds")


def _insert_api_session(con, vehicle_id="v0",
                         start_ts="2026-05-10T08:00:00",
                         end_ts="2026-05-10T09:30:00",
                         kwh_charged=17.5,
                         location="home"):
    """Insert a session as if created by the vehicle API."""
    cur = con.execute(
        """INSERT INTO sessions
           (start_ts, end_ts, kwh_charged, cost_eur, location, vehicle_id,
            provider, kwh_source, created_mode,
            vehicle_assignment_status, excluded_from_reports)
           VALUES (?,?,?,?,?,?,'ha','soc','auto','confirmed',0)""",
        (start_ts, end_ts, kwh_charged, 5.25, location, vehicle_id),
    )
    con.commit()
    return cur.lastrowid


def _insert_wallbox_session(con, vehicle_id=None,
                              start_ts="2026-05-10T08:05:00",
                              end_ts="2026-05-10T09:25:00",
                              energy_kwh=18.0,
                              meter_start=500.0,
                              meter_end=518.0,
                              status="unassigned",
                              assignment_status="unassigned"):
    now = _now()
    cur = con.execute(
        """INSERT INTO wallbox_sessions
           (vehicle_id, source_type, source_name, start_ts, end_ts,
            meter_start_kwh, meter_end_kwh, energy_kwh,
            status, vehicle_assignment_status, excluded_from_reports,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)""",
        (vehicle_id, "ev_wallbox", "goe", start_ts, end_ts,
         meter_start, meter_end, energy_kwh,
         status, assignment_status, now, now),
    )
    con.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Test 22 — existing API-session + wallbox_session → no duplicate
# ---------------------------------------------------------------------------

class TestNoDuplicate:
    def test_22_overlapping_api_session_not_duplicated(self, app):
        """reconcile_charging_evidence must enrich, not duplicate, an existing session."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            from services.reconciliation_service import reconcile_charging_evidence
            con = _get_db()

            # Existing API-based session covering same window
            sid = _insert_api_session(
                con, vehicle_id="v0",
                start_ts="2026-05-10T08:00:00",
                end_ts="2026-05-10T09:30:00",
                kwh_charged=17.5,
            )
            wbs_id = _insert_wallbox_session(
                con, vehicle_id=None,
                start_ts="2026-05-10T08:05:00",
                end_ts="2026-05-10T09:25:00",
                energy_kwh=18.0,
                meter_start=500.0,
                meter_end=518.0,
            )
            wbs = dict(con.execute(
                "SELECT * FROM wallbox_sessions WHERE id=?", (wbs_id,)).fetchone())

            before_count = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            result = reconcile_charging_evidence(con, wbs, "v0")
            after_count = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            close_db_if_owned(con)

        assert result["action"] == "enriched"
        assert result["session_id"] == sid
        assert after_count == before_count, "No new session must be created when one already exists"

    def test_22b_non_overlapping_creates_new_session(self, app):
        """When no overlap exists, a new session is created."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            from services.reconciliation_service import reconcile_charging_evidence
            con = _get_db()

            # Existing session in a completely different time window
            _insert_api_session(
                con, vehicle_id="v0",
                start_ts="2026-05-08T10:00:00",
                end_ts="2026-05-08T11:00:00",
                kwh_charged=10.0,
            )
            wbs_id = _insert_wallbox_session(
                con, vehicle_id=None,
                start_ts="2026-05-10T08:05:00",
                end_ts="2026-05-10T09:25:00",
                energy_kwh=18.0,
            )
            wbs = dict(con.execute(
                "SELECT * FROM wallbox_sessions WHERE id=?", (wbs_id,)).fetchone())

            before_count = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            result = reconcile_charging_evidence(con, wbs, "v0")
            after_count = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            close_db_if_owned(con)

        assert result["action"] == "created"
        assert result["session_id"] is not None
        assert after_count == before_count + 1


# ---------------------------------------------------------------------------
# Enrichment tests
# ---------------------------------------------------------------------------

class TestEnrichment:
    def test_meter_data_added_to_existing_session(self, app):
        """Meter values from wallbox_session are written onto the enriched session."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            from services.reconciliation_service import reconcile_charging_evidence
            con = _get_db()

            sid = _insert_api_session(con, kwh_charged=17.0)
            wbs_id = _insert_wallbox_session(
                con, energy_kwh=18.2, meter_start=1000.0, meter_end=1018.2)
            wbs = dict(con.execute(
                "SELECT * FROM wallbox_sessions WHERE id=?", (wbs_id,)).fetchone())

            reconcile_charging_evidence(con, wbs, "v0")
            sess = dict(con.execute(
                "SELECT * FROM sessions WHERE id=?", (sid,)).fetchone())
            close_db_if_owned(con)

        assert sess["meter_old"] == pytest.approx(1000.0)
        assert sess["meter_new"] == pytest.approx(1018.2)
        assert sess["kwh_charged"] == pytest.approx(18.2)  # wallbox kWh preferred
        assert sess["kwh_source"] == "meter"

    def test_evidence_json_records_wallbox_session_id(self, app):
        """evidence_json on the enriched session includes the wallbox_session_id."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            from services.reconciliation_service import reconcile_charging_evidence
            con = _get_db()

            sid = _insert_api_session(con)
            wbs_id = _insert_wallbox_session(con)
            wbs = dict(con.execute(
                "SELECT * FROM wallbox_sessions WHERE id=?", (wbs_id,)).fetchone())

            reconcile_charging_evidence(con, wbs, "v0")
            sess = dict(con.execute(
                "SELECT evidence_json FROM sessions WHERE id=?", (sid,)).fetchone())
            close_db_if_owned(con)

        ev = json.loads(sess["evidence_json"] or "{}")
        assert ev.get("wallbox_session_id") == wbs_id

    def test_existing_meter_data_not_overwritten(self, app):
        """If a session already has meter data, it must not be overwritten."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            from services.reconciliation_service import reconcile_charging_evidence
            con = _get_db()

            sid = _insert_api_session(con)
            # Pre-set meter values on the session
            con.execute(
                "UPDATE sessions SET meter_old=200.0, meter_new=218.0 WHERE id=?", (sid,))
            con.commit()

            wbs_id = _insert_wallbox_session(
                con, energy_kwh=18.0, meter_start=500.0, meter_end=518.0)
            wbs = dict(con.execute(
                "SELECT * FROM wallbox_sessions WHERE id=?", (wbs_id,)).fetchone())

            reconcile_charging_evidence(con, wbs, "v0")
            sess = dict(con.execute(
                "SELECT meter_old, meter_new FROM sessions WHERE id=?", (sid,)).fetchone())
            close_db_if_owned(con)

        # Original values must be preserved
        assert sess["meter_old"] == pytest.approx(200.0)
        assert sess["meter_new"] == pytest.approx(218.0)

    def test_kwh_mismatch_too_large_no_enrich(self, app):
        """Sessions with kWh difference > tolerance are not matched as duplicates."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            from services.reconciliation_service import reconcile_charging_evidence, KWH_TOLERANCE
            con = _get_db()

            # API session with very different kWh (> tolerance)
            sid = _insert_api_session(con, kwh_charged=5.0)
            wbs_id = _insert_wallbox_session(
                con, energy_kwh=5.0 + KWH_TOLERANCE + 1.0)
            wbs = dict(con.execute(
                "SELECT * FROM wallbox_sessions WHERE id=?", (wbs_id,)).fetchone())

            before = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            result = reconcile_charging_evidence(con, wbs, "v0")
            after = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            close_db_if_owned(con)

        # Should create a new session, not enrich the mismatched one
        assert result["action"] == "created"
        assert after == before + 1


# ---------------------------------------------------------------------------
# Via API route (integration)
# ---------------------------------------------------------------------------

class TestReconcileViaRoute:
    def test_assign_route_uses_reconciliation(self, app, authed_client):
        """POST /assign with create_session=true uses reconciliation (no duplicate)."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            sid_existing = _insert_api_session(
                con, vehicle_id="v0",
                start_ts="2026-05-10T08:00:00",
                end_ts="2026-05-10T09:30:00",
                kwh_charged=17.5,
            )
            wbs_id = _insert_wallbox_session(
                con, vehicle_id=None,
                start_ts="2026-05-10T08:05:00",
                end_ts="2026-05-10T09:25:00",
                energy_kwh=18.0,
            )
            before = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            close_db_if_owned(con)

        rv = authed_client.post(
            f"/api/wallbox/sessions/{wbs_id}/assign",
            json={"vehicle_id": "v0", "create_session": True},
        )
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        # session_id must be the *existing* session, not a new one
        assert data["session_id"] == sid_existing

        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            after = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            close_db_if_owned(con)

        assert after == before, "No new session created when existing one was enriched"
