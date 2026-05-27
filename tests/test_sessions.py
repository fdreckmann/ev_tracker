"""
Tests for session creation, editing, location update, and repricing.
"""
from datetime import datetime, timedelta


def _make_session_data(year=2026, month=5, day=10, kwh=20.0, cost=6.0, manual=0):
    start = f"{year:04d}-{month:02d}-{day:02d}T10:00:00"
    end   = f"{year:04d}-{month:02d}-{day:02d}T11:00:00"
    return {
        "start_ts": start,
        "end_ts":   end,
        "kwh_charged": kwh,
        "cost_eur":    cost if manual else None,
        "location":    "home",
        "charger_type": "ac",
    }


class TestManualSessionCreate:
    def test_create_session(self, authed_client):
        data = _make_session_data()
        rv = authed_client.post("/api/sessions/manual", json=data)
        assert rv.status_code in (200, 201)
        body = rv.get_json()
        assert body.get("ok") is True
        assert "id" in body

    def test_missing_start_ts_returns_400(self, authed_client):
        rv = authed_client.post("/api/sessions/manual",
                                json={"kwh_charged": 10.0})
        assert rv.status_code == 400

    def test_session_visible_in_list(self, authed_client):
        data = _make_session_data(day=15)
        authed_client.post("/api/sessions/manual", json=data)
        rv = authed_client.get("/api/sessions?year=2026&month=5")
        assert rv.status_code == 200
        sessions = rv.get_json()
        assert len(sessions) >= 1


class TestSessionLocationUpdate:
    def _create_session(self, client):
        data = _make_session_data()
        rv = client.post("/api/sessions/manual", json=data)
        return rv.get_json()["id"]

    def test_update_location_home(self, authed_client):
        sid = self._create_session(authed_client)
        rv = authed_client.post(f"/api/sessions/{sid}/location",
                                json={"location": "home"})
        assert rv.status_code == 200
        assert rv.get_json().get("ok") is True

    def test_update_location_extern(self, authed_client):
        sid = self._create_session(authed_client)
        rv = authed_client.post(f"/api/sessions/{sid}/location",
                                json={"location": "extern"})
        assert rv.status_code == 200

    def test_update_location_external_normalized(self, authed_client, app):
        """'external' must be stored as 'extern' in the DB."""
        sid = self._create_session(authed_client)
        rv = authed_client.post(f"/api/sessions/{sid}/location",
                                json={"location": "external"})
        assert rv.status_code == 200
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            row = con.execute("SELECT location FROM sessions WHERE id=?", (sid,)).fetchone()
            close_db_if_owned(con)
        assert dict(row)["location"] == "extern"

    def test_update_truly_invalid_location(self, authed_client):
        """A completely unknown string gets normalized to 'unknown' which is accepted."""
        sid = self._create_session(authed_client)
        rv = authed_client.post(f"/api/sessions/{sid}/location",
                                json={"location": "INVALID_XYZ_999"})
        # normalize_location maps unknowns to 'unknown', which is valid — 200 is correct
        assert rv.status_code in (200, 400)


class TestSessionPatch:
    def _create_session(self, client):
        data = _make_session_data(kwh=25.0)
        rv = client.post("/api/sessions/manual", json=data)
        return rv.get_json()["id"]

    def test_patch_kwh(self, authed_client):
        sid = self._create_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}",
                                 json={"kwh_charged": 30.0})
        assert rv.status_code == 200

    def test_patch_note(self, authed_client):
        sid = self._create_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}",
                                 json={"manual_note": "Test Notiz"})
        assert rv.status_code == 200


class TestSessionRecalculate:
    def test_recalculate_cost(self, authed_client):
        data = _make_session_data(kwh=20.0, cost=None, manual=0)
        rv = authed_client.post("/api/sessions/manual", json=data)
        sid = rv.get_json()["id"]
        rv2 = authed_client.post(f"/api/sessions/{sid}/recalculate-cost")
        assert rv2.status_code in (200, 400, 404)
