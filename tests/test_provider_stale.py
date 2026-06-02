"""
Tests for PR 4 — provider data-stale status.

Test numbers align with spec section 19:
  23  HA entity last_changed/last_updated old → provider_data_stale=True
  24  HA reachable but stale → not provider_error (tracker_status = data_stale)
  25  wallbox tracking continues despite provider_data_stale
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ha_entity_response(state: str, last_updated_offset_minutes: float = -5,
                         last_changed_offset_minutes: float = -5) -> dict:
    """Build a fake HA entity response with configurable timestamps."""
    now = datetime.now(timezone.utc)
    last_updated = (now + timedelta(minutes=last_updated_offset_minutes)).isoformat()
    last_changed = (now + timedelta(minutes=last_changed_offset_minutes)).isoformat()
    return {
        "entity_id": "sensor.test",
        "state": state,
        "attributes": {"unit_of_measurement": "%"},
        "last_changed": last_changed,
        "last_updated": last_updated,
    }


# ---------------------------------------------------------------------------
# Test 23 — old entity timestamps → data_stale=True
# ---------------------------------------------------------------------------

class TestHaDataStaleness:
    def test_23_old_entity_timestamp_sets_stale(self):
        """HA provider sets data_stale=True when entity timestamps are older than threshold."""
        from providers.ha_provider import HomeAssistantProvider

        cfg = {
            "ha_url": "http://ha.local:8123",
            "ha_token": "test-token",
            "charging_sensor": "sensor.charging",
            "soc_sensor": "sensor.soc",
            "provider_stale_after_minutes": 30,
        }
        provider = HomeAssistantProvider(cfg)

        # Simulate entity data that is 90 minutes old
        old_entity = _ha_entity_response("50", last_updated_offset_minutes=-90,
                                          last_changed_offset_minutes=-90)

        def mock_get_entity(entity_id):
            return old_entity

        provider._get_entity = mock_get_entity
        # Manually trigger _store_entity_debug path by calling _get_entity wrapper
        # Reset the tracker
        if hasattr(provider, "_newest_entity_ts"):
            delattr(provider, "_newest_entity_ts")
        # Manually process the timestamp as _get_entity would
        provider._store_entity_debug(
            "sensor.soc",
            http_status=200, reachable=True, state="50",
            last_changed=old_entity["last_changed"],
            last_updated=old_entity["last_updated"],
        )

        state = provider.get_state()
        assert state.data_stale is True, f"Expected data_stale=True, got {state.data_stale}"
        assert state.stale_reason is not None
        assert "schläft" in state.stale_reason or "min" in state.stale_reason.lower()

    def test_23b_fresh_entity_not_stale(self):
        """HA provider sets data_stale=False when entity timestamps are recent."""
        from providers.ha_provider import HomeAssistantProvider

        cfg = {
            "ha_url": "http://ha.local:8123",
            "ha_token": "test-token",
            "charging_sensor": "sensor.charging",
            "soc_sensor": "sensor.soc",
            "provider_stale_after_minutes": 60,
        }
        provider = HomeAssistantProvider(cfg)

        # Entity updated 5 minutes ago (fresh)
        fresh_entity = _ha_entity_response("75", last_updated_offset_minutes=-5,
                                            last_changed_offset_minutes=-5)
        if hasattr(provider, "_newest_entity_ts"):
            delattr(provider, "_newest_entity_ts")

        provider._store_entity_debug(
            "sensor.soc",
            http_status=200, reachable=True, state="75",
            last_changed=fresh_entity["last_changed"],
            last_updated=fresh_entity["last_updated"],
        )

        state = provider.get_state()
        assert state.data_stale is False, f"Expected data_stale=False, got {state.data_stale}"


# ---------------------------------------------------------------------------
# Test 24 — stale but reachable → tracker_status=data_stale, not provider_error
# ---------------------------------------------------------------------------

class TestStaleNotError:
    def test_24_stale_gives_data_stale_status_not_error(self, app):
        """provider_data_stale=True + provider_connected=True → tracker_status='data_stale'."""
        from routes.main_routes import _compute_tracker_status

        # Simulate a state where HA is reachable but data is stale
        st = {
            "running": True,
            "tracker_alive": True,
            "provider_connected": True,
            "last_successful_poll": datetime.now().isoformat(),
            "last_error": None,
            "last_fatal_error": None,
            "charging": False,
            "provider_data_stale": True,
            "provider_stale_reason": "Fahrzeug schläft seit 90 Minuten",
        }
        status = _compute_tracker_status(st)
        assert status == "data_stale", f"Expected 'data_stale', got '{status}'"

    def test_24b_stale_false_gives_ready(self, app):
        """provider_data_stale=False → tracker_status='ready' (not stale)."""
        from routes.main_routes import _compute_tracker_status

        st = {
            "running": True,
            "tracker_alive": True,
            "provider_connected": True,
            "last_successful_poll": datetime.now().isoformat(),
            "last_error": None,
            "last_fatal_error": None,
            "charging": False,
            "provider_data_stale": False,
        }
        status = _compute_tracker_status(st)
        assert status == "ready"

    def test_24c_stale_in_api_status_response(self, app, authed_client):
        """GET /api/status includes provider_data_stale fields."""
        # Inject stale state into vehicle_states
        from core import state as _state
        old = dict(_state.vehicle_states.get("v0", {}))
        _state.vehicle_states["v0"] = {
            **old,
            "running": True,
            "tracker_alive": True,
            "provider_connected": True,
            "last_successful_poll": datetime.now().isoformat(),
            "provider_data_stale": True,
            "provider_data_age_seconds": 5400.0,
            "provider_stale_reason": "Fahrzeug schläft",
        }
        try:
            rv = authed_client.get("/api/status")
            data = rv.get_json()
        finally:
            _state.vehicle_states["v0"] = old

        assert "provider_data_stale" in data
        assert data["provider_data_stale"] is True
        assert data.get("provider_data_age_seconds") == pytest.approx(5400.0)


# ---------------------------------------------------------------------------
# Test 25 — wallbox tracking continues despite provider_data_stale
# ---------------------------------------------------------------------------

class TestWallboxTrackingDespiteStale:
    def test_25_wallbox_detection_works_when_provider_stale(self, app):
        """process_power_snapshot() works even when vehicle_id=None (provider offline/stale)."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            from services.wallbox_session_service import process_power_snapshot
            con = _get_db()

            cfg = {
                "home_charge_detection_enabled": True,
                "home_charge_power_start_threshold_kw": 1.0,
                "home_charge_power_stop_threshold_kw": 0.2,
                "home_charge_start_debounce_seconds": 5,
                "home_charge_stop_debounce_seconds": 5,
                "home_charge_min_energy_kwh": 0.1,
                "home_charge_vehicle_assignment_mode": "always_ask_if_unclear",
            }
            st = {}
            from datetime import datetime, timedelta
            base = datetime(2026, 6, 1, 8, 0, 0)

            # vehicle_id=None simulates stale/offline provider
            process_power_snapshot(None, "ev_wallbox", "goe_stale_test",
                                    7.2, 300.0,
                                    base.isoformat(), cfg, st, con)
            process_power_snapshot(None, "ev_wallbox", "goe_stale_test",
                                    7.2, 305.0,
                                    (base + timedelta(seconds=10)).isoformat(),
                                    cfg, st, con)

            count = con.execute(
                "SELECT COUNT(*) FROM wallbox_sessions WHERE source_name='goe_stale_test'"
                " AND status='active'").fetchone()[0]
            close_db_if_owned(con)

        assert count == 1, (
            "wallbox_session must be opened even when provider is stale (vehicle_id=None)")
