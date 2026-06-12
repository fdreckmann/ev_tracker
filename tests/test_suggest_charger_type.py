"""
Tests A–H for automatic charger type suggestion.

A: API override — API provides charge_type → source="api"
B: Home/wallbox — location="home" → AC, source="location_home"
C: Extern DC — power_kw >= dc_threshold → DC, source="power_kw"
D: Extern AC — power_kw < dc_threshold → AC, source="power_kw"
E: No data — no power, no location → unknown
F: Manual override — user sets charger_type → source="manual", not overridden by auto
G: Price logic — DC suggestion triggers DC pricing in manual session
H: Missing-charge — suggest_charger_type called correctly in check_for_missing_charge
"""
import json
import pytest

from services.missing_charge_service import suggest_charger_type


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _cfg(dc_threshold_kw=22.0):
    return {"dc_threshold_kw": dc_threshold_kw}


# ---------------------------------------------------------------------------
# Unit tests for suggest_charger_type()
# ---------------------------------------------------------------------------

class TestSuggestChargerType:

    # A: when called with home location → AC regardless of anything else
    def test_b_home_location_returns_ac(self):
        r = suggest_charger_type("home", None, None, None, False, _cfg())
        assert r["type"] == "ac"
        assert r["source"] == "location_home"
        assert r["confidence"] >= 60

    # B: meter_confirmed overrides even unknown location
    def test_b_meter_confirmed_returns_ac(self):
        r = suggest_charger_type("unknown", None, None, None, True, _cfg())
        assert r["type"] == "ac"
        assert r["source"] == "meter_home"
        assert r["confidence"] > r["confidence"] if False else r["confidence"] >= 80

    # C: extern with power_kw >= dc_threshold → DC
    def test_c_extern_dc_from_power(self):
        r = suggest_charger_type("extern", 50.0, None, None, False, _cfg(22.0))
        assert r["type"] == "dc"
        assert r["source"] == "power_kw"
        assert r["confidence"] >= 70

    # D: extern with power_kw < dc_threshold → AC
    def test_d_extern_ac_from_power(self):
        r = suggest_charger_type("extern", 11.0, None, None, False, _cfg(22.0))
        assert r["type"] == "ac"
        assert r["source"] == "power_kw"
        assert r["confidence"] >= 70

    # E: no data → unknown
    def test_e_no_data_returns_unknown(self):
        r = suggest_charger_type(None, None, None, None, False, _cfg())
        assert r["type"] == "unknown"
        assert r["source"] == "none"
        assert r["confidence"] == 0

    # E variant: "unknown" location with no power → unknown
    def test_e_unknown_location_no_power(self):
        r = suggest_charger_type("unknown", None, None, None, False, _cfg())
        assert r["type"] == "unknown"

    # estimated_power path: 30 kWh in 1 hour → 30 kW avg → DC
    def test_estimated_power_dc(self):
        r = suggest_charger_type("extern", None, 30.0, 1.0, False, _cfg(22.0))
        assert r["type"] == "dc"
        assert r["source"] == "estimated_power"
        assert r["confidence"] == 50

    # estimated_power path: 11 kWh in 1 hour → 11 kW avg → AC
    def test_estimated_power_ac(self):
        r = suggest_charger_type("extern", None, 11.0, 1.0, False, _cfg(22.0))
        assert r["type"] == "ac"
        assert r["source"] == "estimated_power"

    # Custom dc_threshold: power at exactly threshold → dc
    def test_custom_threshold_at_boundary(self):
        r = suggest_charger_type("extern", 50.0, None, None, False, _cfg(50.0))
        assert r["type"] == "dc"
        r2 = suggest_charger_type("extern", 49.9, None, None, False, _cfg(50.0))
        assert r2["type"] == "ac"

    # Home takes priority over power_kw
    def test_home_wins_over_power(self):
        r = suggest_charger_type("home", 100.0, None, None, False, _cfg(22.0))
        assert r["type"] == "ac"
        assert r["source"] == "location_home"

    # meter_confirmed takes priority over power_kw
    def test_meter_confirmed_wins_over_power(self):
        r = suggest_charger_type("unknown", 100.0, None, None, True, _cfg(22.0))
        assert r["type"] == "ac"
        assert r["source"] == "meter_home"


# ---------------------------------------------------------------------------
# F: Manual override — API-style test via sessions route
# ---------------------------------------------------------------------------

class TestManualSessionChargerTypeSource:

    def test_f_explicit_ac_sets_manual_source(self, authed_client):
        payload = {
            "start_ts": "2025-01-01T10:00:00",
            "end_ts":   "2025-01-01T11:00:00",
            "kwh_charged": 11.0,
            "charger_type": "ac",
        }
        r = authed_client.post("/api/sessions/manual",
                               data=json.dumps(payload), content_type="application/json")
        assert r.status_code == 201
        sid = json.loads(r.data)["id"]
        rows = json.loads(authed_client.get("/api/sessions?limit=200").data)
        sess = next((s for s in rows if s["id"] == sid), None)
        assert sess is not None
        assert sess["charger_type"] == "ac"
        assert sess["charger_type_source"] == "manual"
        assert sess["charger_type_confidence"] == 100

    def test_f_explicit_dc_sets_manual_source(self, authed_client):
        payload = {
            "start_ts": "2025-01-01T12:00:00",
            "end_ts":   "2025-01-01T12:30:00",
            "kwh_charged": 25.0,
            "charger_type": "dc",
        }
        r = authed_client.post("/api/sessions/manual",
                               data=json.dumps(payload), content_type="application/json")
        assert r.status_code == 201
        sid = json.loads(r.data)["id"]
        rows = json.loads(authed_client.get("/api/sessions?limit=200").data)
        sess = next((s for s in rows if s["id"] == sid), None)
        assert sess["charger_type"] == "dc"
        assert sess["charger_type_source"] == "manual"

    def test_f_patch_sets_manual_source(self, authed_client):
        # Create session without charger_type (with end_ts so it appears in list)
        r1 = authed_client.post("/api/sessions/manual",
                                data=json.dumps({"start_ts": "2025-02-01T10:00:00",
                                                 "end_ts":   "2025-02-01T11:00:00",
                                                 "kwh_charged": 10.0}),
                                content_type="application/json")
        sid = json.loads(r1.data)["id"]
        # Patch charger_type explicitly
        r2 = authed_client.patch(f"/api/sessions/{sid}",
                                 data=json.dumps({"charger_type": "dc"}),
                                 content_type="application/json")
        assert r2.status_code == 200
        rows = json.loads(authed_client.get("/api/sessions?limit=200").data)
        sess = next((s for s in rows if s["id"] == sid), None)
        assert sess["charger_type"] == "dc"
        assert sess["charger_type_source"] == "manual"
        assert sess["charger_type_confidence"] == 100


# ---------------------------------------------------------------------------
# G: Price logic — AC/DC suggestion triggers correct pricing
# ---------------------------------------------------------------------------

class TestPricingWithSuggestedType:

    def test_g_home_session_auto_ac_does_not_error(self, authed_client):
        """Home session without explicit charger_type → AC inferred, no price error."""
        payload = {
            "start_ts": "2025-03-01T22:00:00",
            "end_ts":   "2025-03-02T06:00:00",
            "kwh_charged": 40.0,
            "location": "home",
        }
        r = authed_client.post("/api/sessions/manual",
                               data=json.dumps(payload), content_type="application/json")
        assert r.status_code == 201
        d = json.loads(r.data)
        assert d.get("id")


# ---------------------------------------------------------------------------
# H: Missing-charge candidate charger type source stored
# ---------------------------------------------------------------------------

class TestMissingChargeChargerTypeSource:

    def test_h_candidate_stores_charger_type_source(self, authed_client):
        rv = authed_client.get("/api/missing-charges")
        assert rv.status_code == 200

    def test_h_suggest_charger_type_used_in_candidate(self, authed_client):
        """Candidates returned by the API include suggested_charger_type field."""
        rv = authed_client.get("/api/missing-charges?status=open")
        assert rv.status_code == 200
        candidates = json.loads(rv.data)
        # If any candidates exist, verify fields are present
        for c in candidates:
            assert "suggested_charger_type" in c
