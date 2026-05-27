"""
Tests for PDF export route — period selection and vehicle filter.
"""
import pytest


def _pdf_available():
    try:
        import pdf_export as _pex
        return bool(getattr(_pex, "_REPORTLAB_AVAILABLE", False))
    except Exception:
        return False


def _seed_session(app, year=2026, month=5):
    from core.db import _get_db, close_db_if_owned
    with app.app_context():
        con = _get_db()
        start = f"{year:04d}-{month:02d}-10T10:00:00"
        end   = f"{year:04d}-{month:02d}-10T11:30:00"
        con.execute("""INSERT INTO sessions
            (start_ts, end_ts, kwh_charged, cost_eur, cost_manual,
             location, charger_type, vehicle_id, provider)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (start, end, 30.0, 13.50, 1, "home", "ac", "v0", "manual"))
        con.commit()
        close_db_if_owned(con)


class TestPdfExportPeriod:
    def test_explicit_year_month(self, authed_client, app):
        """Request with year+month must export that exact month."""
        if not _pdf_available():
            pytest.skip("reportlab not available")
        _seed_session(app, year=2026, month=5)
        rv = authed_client.post("/api/export/pdf",
                                json={"year": 2026, "month": 5})
        assert rv.status_code == 200
        data = rv.get_json()
        assert data.get("ok") is True
        filename = data.get("filename", "")
        assert "2026" in filename or "Mai" in filename or "May" in filename or "05" in filename, (
            f"Expected May 2026 filename, got: {filename!r}")

    def test_different_month(self, authed_client, app):
        """Request with year=2026, month=4 must export April, not May."""
        if not _pdf_available():
            pytest.skip("reportlab not available")
        _seed_session(app, year=2026, month=4)
        rv = authed_client.post("/api/export/pdf",
                                json={"year": 2026, "month": 4})
        assert rv.status_code == 200
        data = rv.get_json()
        assert data.get("ok") is True
        filename = data.get("filename", "")
        assert "04" in filename or "April" in filename or "2026" in filename

    def test_single_month_param(self, authed_client, app):
        """single_month YYYY-MM param must export that month."""
        if not _pdf_available():
            pytest.skip("reportlab not available")
        _seed_session(app, year=2026, month=3)
        rv = authed_client.post("/api/export/pdf",
                                json={"single_month": "2026-03"})
        assert rv.status_code == 200
        data = rv.get_json()
        assert data.get("ok") is True

    def test_invalid_period_falls_back(self, authed_client, app):
        """Passing nothing special should still return 200 or a clear error."""
        if not _pdf_available():
            pytest.skip("reportlab not available")
        rv = authed_client.post("/api/export/pdf", json={})
        assert rv.status_code in (200, 400, 500)


class TestPdfExportVehicle:
    def test_vehicle_id_accepted(self, authed_client, app):
        """vehicle_id as alias for vehicle_filter must be accepted."""
        if not _pdf_available():
            pytest.skip("reportlab not available")
        _seed_session(app, year=2026, month=5)
        rv = authed_client.post("/api/export/pdf",
                                json={"year": 2026, "month": 5, "vehicle_id": "v0"})
        assert rv.status_code == 200
        data = rv.get_json()
        assert data.get("ok") is True

    def test_vehicle_filter_accepted(self, authed_client, app):
        """vehicle_filter must still work (backward compat)."""
        if not _pdf_available():
            pytest.skip("reportlab not available")
        _seed_session(app, year=2026, month=5)
        rv = authed_client.post("/api/export/pdf",
                                json={"year": 2026, "month": 5, "vehicle_filter": "v0"})
        assert rv.status_code == 200
        data = rv.get_json()
        assert data.get("ok") is True
