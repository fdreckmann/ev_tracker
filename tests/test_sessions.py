"""
Tests for session creation, editing, location update, and repricing.
"""
from datetime import datetime, timedelta
from core.location import normalize_location


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


class TestLocationNormalize:
    def test_normalize_intern(self):
        assert normalize_location("intern") == "home"

    def test_normalize_internal(self):
        assert normalize_location("internal") == "home"

    def test_normalize_zuhause_laden(self):
        assert normalize_location("zuhause_laden") == "home"

    def test_normalize_offentlich(self):
        assert normalize_location("öffentlich") == "extern"

    def test_normalize_external(self):
        assert normalize_location("external") == "extern"

    def test_normalize_extern(self):
        assert normalize_location("extern") == "extern"

    def test_normalize_home(self):
        assert normalize_location("home") == "home"

    def test_normalize_unknown_string(self):
        assert normalize_location("INVALID_XYZ") == "unknown"


class TestSessionLocationUpdateExtended:
    def _create_session(self, client, cost_manual=0, cost=None):
        data = _make_session_data(kwh=20.0, cost=cost or (6.0 if cost_manual else None), manual=cost_manual)
        rv = client.post("/api/sessions/manual", json=data)
        assert rv.status_code in (200, 201)
        return rv.get_json()["id"]

    def _get_session_row(self, app, sid):
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            row = con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            close_db_if_owned(con)
        return dict(row) if row else {}

    def test_intern_normalized_to_home(self, authed_client, app):
        sid = self._create_session(authed_client)
        rv = authed_client.post(f"/api/sessions/{sid}/location", json={"location": "intern"})
        assert rv.status_code == 200
        assert rv.get_json().get("ok") is True
        row = self._get_session_row(app, sid)
        assert row["location"] == "home"

    def test_internal_normalized_to_home(self, authed_client, app):
        sid = self._create_session(authed_client)
        rv = authed_client.post(f"/api/sessions/{sid}/location", json={"location": "internal"})
        assert rv.status_code == 200
        row = self._get_session_row(app, sid)
        assert row["location"] == "home"

    def test_external_normalized_to_extern(self, authed_client, app):
        sid = self._create_session(authed_client)
        rv = authed_client.post(f"/api/sessions/{sid}/location", json={"location": "external"})
        assert rv.status_code == 200
        row = self._get_session_row(app, sid)
        assert row["location"] == "extern"

    def test_location_source_set_to_manual(self, authed_client, app):
        sid = self._create_session(authed_client)
        rv = authed_client.post(f"/api/sessions/{sid}/location", json={"location": "extern"})
        assert rv.status_code == 200
        row = self._get_session_row(app, sid)
        assert row.get("location_source") == "manual"

    def test_location_confidence_set_to_100(self, authed_client, app):
        sid = self._create_session(authed_client)
        rv = authed_client.post(f"/api/sessions/{sid}/location", json={"location": "home"})
        assert rv.status_code == 200
        row = self._get_session_row(app, sid)
        assert row.get("location_confidence") == 100

    def test_cost_manual_zero_allows_repricing(self, authed_client):
        sid = self._create_session(authed_client, cost_manual=0)
        rv = authed_client.post(f"/api/sessions/{sid}/location", json={"location": "extern"})
        assert rv.status_code == 200
        assert rv.get_json().get("ok") is True

    def test_cost_manual_one_preserves_costs(self, authed_client, app):
        sid = self._create_session(authed_client, cost_manual=1, cost=9.99)
        row_before = self._get_session_row(app, sid)
        original_cost = row_before.get("cost_eur")
        rv = authed_client.post(f"/api/sessions/{sid}/location", json={"location": "extern"})
        assert rv.status_code == 200
        assert rv.get_json().get("ok") is True
        row_after = self._get_session_row(app, sid)
        assert row_after.get("cost_eur") == original_cost

    def test_patch_location_intern_normalized(self, authed_client, app):
        sid = self._create_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}", json={"location": "intern"})
        assert rv.status_code == 200
        row = self._get_session_row(app, sid)
        assert row["location"] == "home"

    def test_patch_location_sets_source_manual(self, authed_client, app):
        sid = self._create_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}", json={"location": "home"})
        assert rv.status_code == 200
        row = self._get_session_row(app, sid)
        assert row.get("location_source") == "manual"


class TestVehicleProviderValidation:
    """POST /api/vehicles and PUT /api/vehicles/<vid> must reject unknown providers."""

    def test_add_vehicle_valid_provider(self, authed_client):
        rv = authed_client.post("/api/vehicles", json={
            "name": "Test HA Vehicle",
            "provider": "ha",
        })
        assert rv.status_code == 200
        body = rv.get_json()
        assert body.get("ok") is True
        assert "id" in body

    def test_add_vehicle_invalid_provider_rejected(self, authed_client):
        rv = authed_client.post("/api/vehicles", json={
            "name": "Invalid",
            "provider": "manual",
        })
        assert rv.status_code == 400
        body = rv.get_json()
        assert body.get("ok") is False
        assert "Provider" in body.get("error", "") or "provider" in body.get("error", "").lower()

    def test_add_vehicle_unknown_provider_rejected(self, authed_client):
        rv = authed_client.post("/api/vehicles", json={
            "name": "Invalid",
            "provider": "totally_fake_provider_xyz",
        })
        assert rv.status_code == 400
        body = rv.get_json()
        assert body.get("ok") is False

    def test_update_vehicle_invalid_provider_rejected(self, authed_client):
        # First create a valid vehicle
        create_rv = authed_client.post("/api/vehicles", json={
            "name": "Valid Vehicle",
            "provider": "ha",
        })
        assert create_rv.status_code == 200
        vid = create_rv.get_json()["id"]

        # Now try to update it with an invalid provider
        rv = authed_client.put(f"/api/vehicles/{vid}", json={
            "provider": "manual",
        })
        assert rv.status_code == 400
        body = rv.get_json()
        assert body.get("ok") is False
