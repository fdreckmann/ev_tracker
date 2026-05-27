"""
Tests for API v1 token-authenticated endpoints.
"""
import hashlib
import json
from datetime import datetime, timedelta


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
             json.dumps(scopes), 1, datetime.utcnow().isoformat()))
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
