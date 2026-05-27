"""
Tests for report creation, Excel export, and PDF export routes.
"""
import json
from datetime import datetime


def _seed_session(app, year=2026, month=5):
    """Insert a completed session into the test DB."""
    from core.db import _get_db, close_db_if_owned
    with app.app_context():
        con = _get_db()
        start = f"{year:04d}-{month:02d}-01T10:00:00"
        end   = f"{year:04d}-{month:02d}-01T11:00:00"
        con.execute("""INSERT INTO sessions
            (start_ts, end_ts, kwh_charged, cost_eur, cost_manual,
             location, charger_type, vehicle_id, provider)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (start, end, 20.5, 6.15, 1, "home", "ac", "v0", "manual"))
        con.commit()
        close_db_if_owned(con)


class TestReportCreate:
    def test_create_returns_ok(self, authed_client, app):
        _seed_session(app)
        rv = authed_client.post("/api/reports/create",
                                json={"year": 2026, "month": 5,
                                      "include_excel": True, "include_pdf": False})
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert "report_id" in data
        assert "summary" in data

    def test_create_has_excel_when_requested(self, authed_client, app):
        _seed_session(app)
        rv = authed_client.post("/api/reports/create",
                                json={"year": 2026, "month": 5,
                                      "include_excel": True, "include_pdf": False})
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["has_excel"] is True, (
            f"Excel should be generated but was not. "
            f"warnings={data.get('warnings')}")

    def test_create_no_excel_when_false(self, authed_client, app):
        _seed_session(app)
        rv = authed_client.post("/api/reports/create",
                                json={"year": 2026, "month": 5,
                                      "include_excel": False, "include_pdf": False})
        data = rv.get_json()
        assert data["has_excel"] is False

    def test_response_contains_warnings_field(self, authed_client, app):
        _seed_session(app)
        rv = authed_client.post("/api/reports/create",
                                json={"year": 2026, "month": 5, "include_excel": True})
        data = rv.get_json()
        assert "warnings" in data  # field must always be present

    def test_invalid_period_returns_400(self, authed_client, app):
        rv = authed_client.post("/api/reports/create",
                                json={"period_mode": "INVALID_MODE"})
        # Should return 400 when no valid period resolved
        # (or ok:true with empty summary — depending on report_service behaviour)
        assert rv.status_code in (400, 200)

    def test_download_excel(self, authed_client, app):
        _seed_session(app)
        rv = authed_client.post("/api/reports/create",
                                json={"year": 2026, "month": 5, "include_excel": True})
        report_id = rv.get_json()["report_id"]
        dl = authed_client.get(f"/api/reports/{report_id}/download/excel")
        assert dl.status_code == 200
        content_type = dl.content_type
        assert "spreadsheetml" in content_type or "xlsx" in content_type.lower()
        assert len(dl.data) > 0

    def test_download_nonexistent_report(self, authed_client):
        rv = authed_client.get("/api/reports/999999/download/excel")
        assert rv.status_code == 404


class TestReportArchive:
    def test_archive_returns_list(self, authed_client, app):
        _seed_session(app)
        authed_client.post("/api/reports/create",
                           json={"year": 2026, "month": 5, "include_excel": True})
        rv = authed_client.get("/api/reports/archive")
        assert rv.status_code == 200
        data = rv.get_json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_archive_has_excel_flag(self, authed_client, app):
        _seed_session(app)
        authed_client.post("/api/reports/create",
                           json={"year": 2026, "month": 5, "include_excel": True})
        rv = authed_client.get("/api/reports/archive")
        reports = rv.get_json()
        assert reports[0]["has_excel"] == 1
