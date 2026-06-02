"""
Tests for PR 6 — POST /api/sessions/quick-add-external

Spec §16: External session quick-add in <30s.
Created sessions must be reportable (excluded_from_reports=0,
vehicle_assignment_status='confirmed').

Also covers import_service.make_dedup_key.
"""
from services.import_service import make_dedup_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal(start_ts="2026-05-15T14:00:00", kwh=22.5, **extra):
    d = {"start_ts": start_ts, "kwh_charged": kwh}
    d.update(extra)
    return d


# ---------------------------------------------------------------------------
# make_dedup_key
# ---------------------------------------------------------------------------

class TestMakeDedupKey:
    def test_same_inputs_same_key(self):
        k1 = make_dedup_key("enbw", "2026-05-01T10:00:00", 15.0, cost=4.50)
        k2 = make_dedup_key("enbw", "2026-05-01T10:00:00", 15.0, cost=4.50)
        assert k1 == k2

    def test_different_provider_different_key(self):
        k1 = make_dedup_key("enbw",  "2026-05-01T10:00:00", 15.0)
        k2 = make_dedup_key("logpay","2026-05-01T10:00:00", 15.0)
        assert k1 != k2

    def test_different_energy_different_key(self):
        k1 = make_dedup_key("enbw", "2026-05-01T10:00:00", 15.0)
        k2 = make_dedup_key("enbw", "2026-05-01T10:00:00", 15.1)
        assert k1 != k2

    def test_card_reference_affects_key(self):
        k1 = make_dedup_key("csv", "2026-05-01T10:00:00", 10.0, card_reference="CARD1")
        k2 = make_dedup_key("csv", "2026-05-01T10:00:00", 10.0, card_reference="CARD2")
        assert k1 != k2

    def test_returns_64_char_hex(self):
        k = make_dedup_key("x", "2026-01-01T00:00:00", 0.0)
        assert len(k) == 64
        assert all(c in "0123456789abcdef" for c in k)


# ---------------------------------------------------------------------------
# POST /api/sessions/quick-add-external
# ---------------------------------------------------------------------------

class TestQuickAddExternal:
    def test_minimal_payload_returns_201(self, authed_client):
        rv = authed_client.post("/api/sessions/quick-add-external",
                                json=_minimal())
        assert rv.status_code == 201
        body = rv.get_json()
        assert body["ok"] is True
        assert "id" in body

    def test_location_always_extern(self, authed_client):
        rv = authed_client.post("/api/sessions/quick-add-external",
                                json=_minimal(location="home"))
        assert rv.status_code == 201
        session = rv.get_json()["session"]
        assert session["location"] == "extern"

    def test_reportable_flags(self, authed_client):
        """Created session must be reportable."""
        rv = authed_client.post("/api/sessions/quick-add-external",
                                json=_minimal())
        assert rv.status_code == 201
        session = rv.get_json()["session"]
        assert session["excluded_from_reports"] == 0
        assert session["vehicle_assignment_status"] == "confirmed"

    def test_missing_start_ts_returns_400(self, authed_client):
        rv = authed_client.post("/api/sessions/quick-add-external",
                                json={"kwh_charged": 10.0})
        assert rv.status_code == 400

    def test_missing_kwh_returns_400(self, authed_client):
        rv = authed_client.post("/api/sessions/quick-add-external",
                                json={"start_ts": "2026-05-15T14:00:00"})
        assert rv.status_code == 400

    def test_negative_kwh_returns_400(self, authed_client):
        rv = authed_client.post("/api/sessions/quick-add-external",
                                json=_minimal(kwh=-1.0))
        assert rv.status_code == 400

    def test_end_ts_estimated_from_power(self, authed_client):
        """When end_ts is absent, it is estimated from kwh / charger_power_kw."""
        rv = authed_client.post("/api/sessions/quick-add-external",
                                json=_minimal(kwh=22.0, charger_power_kw=22.0))
        assert rv.status_code == 201
        session = rv.get_json()["session"]
        assert session["end_ts"] is not None

    def test_explicit_cost_stored(self, authed_client):
        rv = authed_client.post("/api/sessions/quick-add-external",
                                json=_minimal(cost_eur=7.50))
        assert rv.status_code == 201
        session = rv.get_json()["session"]
        assert float(session["cost_eur"]) == 7.50
        assert session["cost_manual"] == 1

    def test_price_per_kwh_derives_cost(self, authed_client):
        rv = authed_client.post("/api/sessions/quick-add-external",
                                json=_minimal(kwh=10.0, price_per_kwh=0.40))
        assert rv.status_code == 201
        session = rv.get_json()["session"]
        assert abs(float(session["cost_eur"]) - 4.00) < 0.01

    def test_default_vehicle_falls_back_to_v0(self, authed_client):
        """When no vehicle_id and no prior sessions, defaults to v0."""
        rv = authed_client.post("/api/sessions/quick-add-external",
                                json=_minimal())
        assert rv.status_code == 201
        session = rv.get_json()["session"]
        assert session["vehicle_id"] == "v0"

    def test_explicit_vehicle_id_used(self, authed_client):
        rv = authed_client.post("/api/sessions/quick-add-external",
                                json=_minimal(vehicle_id="v0"))
        assert rv.status_code == 201
        session = rv.get_json()["session"]
        assert session["vehicle_id"] == "v0"

    def test_session_appears_in_monthly_list(self, authed_client):
        """Quick-add session is included in regular session list (reportable)."""
        rv = authed_client.post("/api/sessions/quick-add-external",
                                json=_minimal(start_ts="2026-05-20T09:00:00"))
        assert rv.status_code == 201

        rv2 = authed_client.get("/api/sessions?year=2026&month=5")
        assert rv2.status_code == 200
        ids = [s["id"] for s in rv2.get_json()]
        assert rv.get_json()["id"] in ids

    def test_contract_stored_when_provided(self, authed_client):
        rv = authed_client.post("/api/sessions/quick-add-external",
                                json=_minimal(charging_contract_id="enbw-1",
                                              charging_contract_name="EnBW Ladetarif"))
        assert rv.status_code == 201
        session = rv.get_json()["session"]
        assert session["charging_contract_id"] == "enbw-1"
        assert session["charging_contract_name"] == "EnBW Ladetarif"
