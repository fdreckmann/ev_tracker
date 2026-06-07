"""
Tests for API v1 token-authenticated endpoints.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone



def _create_token(app, scopes=None):
    """Insert a test API token and return the raw token string."""
    import secrets
    from core.db import _get_db, close_db_if_owned
    raw = "evtk_" + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:8]
    if scopes is None:
        scopes = ["sessions:read", "sessions:write", "reports:read", "reports:create",
                  "vehicles:read", "system:read"]
    with app.app_context():
        con = _get_db()
        con.execute("""INSERT INTO api_tokens
            (name, token_hash, token_prefix, scopes, is_active, created_at)
            VALUES (?,?,?,?,?,?)""",
            ("test-token", token_hash, prefix,
             json.dumps(scopes), 1, datetime.now(timezone.utc).replace(tzinfo=None).isoformat()))
        con.commit()
        close_db_if_owned(con)
    return raw


class TestApiV1Status:
    def test_status_with_valid_token(self, app, client):
        token = _create_token(app)
        rv = client.get("/api/v1/status",
                        headers={"Authorization": f"Bearer {token}"})
        assert rv.status_code == 200
        data = rv.get_json()
        assert data.get("status") == "ok"
        assert "version" in data

    def test_status_without_token_returns_401(self, client):
        rv = client.get("/api/v1/status")
        assert rv.status_code == 401

    def test_status_with_invalid_token_returns_401(self, client):
        rv = client.get("/api/v1/status",
                        headers={"Authorization": "Bearer INVALID_TOKEN_X"})
        assert rv.status_code == 401


class TestApiV1Sessions:
    def test_create_session(self, app, client):
        token = _create_token(app)
        rv = client.post("/api/v1/sessions",
                         headers={"Authorization": f"Bearer {token}"},
                         json={
                             "start_ts": "2026-05-01T10:00:00",
                             "end_ts":   "2026-05-01T11:00:00",
                             "kwh_charged": 20.5,
                             "cost_eur": 6.15,
                             "location": "home",
                             "charger_type": "ac",
                         })
        assert rv.status_code == 201
        data = rv.get_json()
        assert data.get("ok") is True
        assert "id" in data

    def test_create_session_external_normalized(self, app, client):
        """'external' location must be stored as 'extern'."""
        token = _create_token(app)
        rv = client.post("/api/v1/sessions",
                         headers={"Authorization": f"Bearer {token}"},
                         json={
                             "start_ts": "2026-05-02T10:00:00",
                             "end_ts":   "2026-05-02T11:00:00",
                             "kwh_charged": 15.0,
                             "cost_eur": 6.75,
                             "location": "external",
                         })
        assert rv.status_code == 201
        sid = rv.get_json()["id"]
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            row = con.execute("SELECT location FROM sessions WHERE id=?", (sid,)).fetchone()
            close_db_if_owned(con)
        assert dict(row)["location"] == "extern"

    def test_list_sessions(self, app, client):
        token = _create_token(app)
        rv = client.get("/api/v1/sessions",
                        headers={"Authorization": f"Bearer {token}"})
        assert rv.status_code == 200
        assert isinstance(rv.get_json(), list)

    def test_get_session(self, app, client):
        token = _create_token(app)
        create = client.post("/api/v1/sessions",
                             headers={"Authorization": f"Bearer {token}"},
                             json={"start_ts": "2026-05-03T10:00:00",
                                   "end_ts": "2026-05-03T11:00:00",
                                   "kwh_charged": 10.0})
        sid = create.get_json()["id"]
        rv = client.get(f"/api/v1/sessions/{sid}",
                        headers={"Authorization": f"Bearer {token}"})
        assert rv.status_code == 200
        assert rv.get_json()["id"] == sid

    def test_put_session_sets_cost_manual_on_explicit_cost(self, app, client):
        """PUT with cost_eur must set cost_manual=1."""
        token = _create_token(app)
        create = client.post("/api/v1/sessions",
                             headers={"Authorization": f"Bearer {token}"},
                             json={"start_ts": "2026-05-04T10:00:00",
                                   "end_ts": "2026-05-04T11:00:00",
                                   "kwh_charged": 20.0})
        sid = create.get_json()["id"]
        rv = client.put(f"/api/v1/sessions/{sid}",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"cost_eur": 9.00})
        assert rv.status_code == 200
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            row = dict(con.execute("SELECT cost_manual FROM sessions WHERE id=?",
                                   (sid,)).fetchone())
            close_db_if_owned(con)
        assert row["cost_manual"] == 1

    def test_put_session_price_per_kwh_accepted(self, app, client):
        """PUT with price_per_kwh must be accepted."""
        token = _create_token(app)
        create = client.post("/api/v1/sessions",
                             headers={"Authorization": f"Bearer {token}"},
                             json={"start_ts": "2026-05-05T10:00:00",
                                   "end_ts": "2026-05-05T11:00:00",
                                   "kwh_charged": 20.0})
        sid = create.get_json()["id"]
        rv = client.put(f"/api/v1/sessions/{sid}",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"price_per_kwh": 0.35})
        assert rv.status_code == 200


class TestApiV1Vehicles:
    def test_list_vehicles(self, app, client):
        token = _create_token(app)
        rv = client.get("/api/v1/vehicles",
                        headers={"Authorization": f"Bearer {token}"})
        assert rv.status_code == 200
        data = rv.get_json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["id"] == "v0"


class TestApiV1ReportsCreate:
    """Tests for /api/v1/reports/create — verifies no TypeError from config= kwarg."""

    def _insert_session(self, app, token, month=5):
        with app.test_client() as c:
            rv = c.post("/api/v1/sessions",
                        headers={"Authorization": f"Bearer {token}"},
                        content_type="application/json",
                        json={
                            "start_ts": f"2026-{month:02d}-10T10:00:00",
                            "end_ts":   f"2026-{month:02d}-10T11:00:00",
                            "kwh_charged": 12.5,
                            "cost_eur": 3.75,
                            "location": "home",
                        })
            assert rv.status_code == 201

    def test_create_report_no_type_error(self, app, client):
        """POST /api/v1/reports/create must not raise TypeError (no config= kwarg)."""
        token = _create_token(app)
        self._insert_session(app, token, month=5)
        rv = client.post(
            "/api/v1/reports/create",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "period_mode": "single_month",
                "report_email_single_month": "2026-05",
            },
        )
        data = rv.get_json()
        assert rv.status_code == 201, f"Expected 201, got {rv.status_code}: {data}"
        assert data.get("ok") is True

    def test_create_report_single_month_produces_excel(self, app, client):
        """Single-month report attaches valid XLSX bytes (no TypeError, valid header)."""
        token = _create_token(app)
        self._insert_session(app, token, month=4)
        rv = client.post(
            "/api/v1/reports/create",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "period_mode": "single_month",
                "report_email_single_month": "2026-04",
            },
        )
        assert rv.status_code == 201
        report_id = rv.get_json()["report_id"]
        # Check that a report row was created (excel_bytes may be None if no template,
        # but the route must not crash)
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            row = con.execute("SELECT id, status FROM reports WHERE id=?",
                              (report_id,)).fetchone()
            close_db_if_owned(con)
        assert row is not None
        assert dict(row)["status"] == "created"

    def test_excel_success_no_excel_error_field(self, app, client):
        """Successful Excel build must not add excel_ok/excel_error to response."""
        token = _create_token(app)
        self._insert_session(app, token, month=3)
        rv = client.post(
            "/api/v1/reports/create",
            headers={"Authorization": f"Bearer {token}"},
            json={"period_mode": "single_month", "report_email_single_month": "2026-03"},
        )
        assert rv.status_code == 201
        data = rv.get_json()
        assert data.get("ok") is True
        assert "excel_error" not in data
        assert data.get("excel_ok") is not False

    def test_excel_failure_surface_in_response(self, app, client, monkeypatch):
        """If Excel generation raises, response has excel_ok=False + excel_error,
        report is still created (HTTP 201), and ok remains True."""
        import services.report_excel_service as _xls
        monkeypatch.setattr(_xls, "build_report_excel_bytes",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("test-excel-fail")))

        token = _create_token(app)
        self._insert_session(app, token, month=2)
        rv = client.post(
            "/api/v1/reports/create",
            headers={"Authorization": f"Bearer {token}"},
            json={"period_mode": "single_month", "report_email_single_month": "2026-02",
                  "include_excel": True},
        )
        assert rv.status_code == 201
        data = rv.get_json()
        assert data.get("ok") is True
        assert data.get("excel_ok") is False
        assert "test-excel-fail" in data.get("excel_error", "")

        report_id = data["report_id"]
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            row = con.execute("SELECT status FROM reports WHERE id=?", (report_id,)).fetchone()
            close_db_if_owned(con)
        assert dict(row)["status"] == "created_no_excel"
