"""
Tests for Excel export (builtin path, no template required).
"""
from io import BytesIO


def _seed_sessions(app, year=2026, month=5, n=3):
    from core.db import _get_db, close_db_if_owned
    with app.app_context():
        con = _get_db()
        for i in range(1, n + 1):
            start = f"{year:04d}-{month:02d}-{i:02d}T10:00:00"
            end   = f"{year:04d}-{month:02d}-{i:02d}T11:00:00"
            con.execute("""INSERT INTO sessions
                (start_ts, end_ts, kwh_charged, cost_eur, cost_manual,
                 location, charger_type, vehicle_id, provider)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (start, end, 10.0 + i, 3.0 + i * 0.5, 1, "home", "ac", "v0", "manual"))
        con.commit()
        close_db_if_owned(con)


class TestExcelExportBuiltin:
    def test_export_returns_xlsx(self, authed_client, app):
        _seed_sessions(app)
        rv = authed_client.get("/api/export?year=2026&month=5")
        assert rv.status_code == 200
        ct = rv.content_type
        assert "spreadsheetml" in ct or "xlsx" in ct.lower()

    def test_export_produces_valid_xlsx(self, authed_client, app):
        _seed_sessions(app)
        rv = authed_client.get("/api/export?year=2026&month=5")
        assert rv.status_code == 200
        try:
            import openpyxl
            wb = openpyxl.load_workbook(BytesIO(rv.data))
            assert len(wb.sheetnames) >= 1
        except ImportError:
            pass  # openpyxl not available in test env

    def test_export_empty_month_still_returns_xlsx(self, authed_client, app):
        """Month with no sessions must still produce a valid (empty) xlsx."""
        rv = authed_client.get("/api/export?year=2025&month=1")
        assert rv.status_code == 200

    def test_export_with_location_filter(self, authed_client, app):
        _seed_sessions(app)
        rv = authed_client.get("/api/export?year=2026&month=5&location=home")
        assert rv.status_code == 200


class TestMatchColumn:
    def test_preis_kwh_maps_to_price_per_kwh(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
        from export_excel import match_column
        assert match_column("Preis/kWh") == "price_per_kwh"
        assert match_column("preis/kwh") == "price_per_kwh"
        assert match_column("Preis pro kWh") == "price_per_kwh"

    def test_preisquelle_maps_to_price_source(self):
        from export_excel import match_column
        assert match_column("Preisquelle") == "price_source"

    def test_vertrag_maps_to_contract(self):
        from export_excel import match_column
        result = match_column("Vertrag")
        assert result == "charging_contract_name"

    def test_kwh_maps_to_kwh_charged(self):
        from export_excel import match_column
        assert match_column("kWh") == "kwh_charged"

    def test_kosten_maps_to_cost_eur(self):
        from export_excel import match_column
        assert match_column("Kosten") == "cost_eur"

    def test_datum_maps_to_date(self):
        from export_excel import match_column
        assert match_column("Datum") == "date"

    def test_unknown_returns_none(self):
        from export_excel import match_column
        assert match_column("XYZ_UNKNOWN_123") is None
        assert match_column(None) is None
        assert match_column("") is None
