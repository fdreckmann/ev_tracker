"""
Tests for PR 9 — Onboarding & first-time vehicle setup.

Covers:
  - GET /setup/vehicle renders for a logged-in user
  - _compute_tracker_status returns not_configured for empty v0 (regression)
  - not_configured stays calm even when the tracker is "running"
  - after configuring a provider, status is no longer not_configured
  - the setup_security skip link now points to /setup/vehicle
"""


# ---------------------------------------------------------------------------
# Wizard step 3 route
# ---------------------------------------------------------------------------

class TestSetupVehicleRoute:
    def test_setup_vehicle_requires_login(self, client):
        """Anonymous users are redirected away from /setup/vehicle."""
        rv = client.get("/setup/vehicle", follow_redirects=False)
        assert rv.status_code in (302, 401, 403)

    def test_setup_vehicle_reachable_when_logged_in(self, authed_client):
        rv = authed_client.get("/setup/vehicle")
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        assert "Fahrzeug verbinden" in body
        assert "/setup/vehicle" not in body or "Provider" in body  # template rendered


# ---------------------------------------------------------------------------
# Security wizard now chains into the vehicle step
# ---------------------------------------------------------------------------

class TestSecurityWizardChaining:
    def test_security_skip_points_to_vehicle(self, authed_client):
        rv = authed_client.get("/setup/security")
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        assert "/setup/vehicle" in body
        # The old direct-to-dashboard skip link must be gone
        assert 'href="/" class="btn btn-skip"' not in body


# ---------------------------------------------------------------------------
# _compute_tracker_status behaviour
# ---------------------------------------------------------------------------

class TestTrackerStatusNotConfigured:
    def test_empty_v0_is_not_configured(self, app):
        """Regression: a brand-new HA config without url/token → not_configured."""
        with app.app_context():
            from routes.main_routes import _compute_tracker_status
            cfg = {"provider": "ha", "ha_url": "", "ha_token": ""}
            assert _compute_tracker_status({}, cfg) == "not_configured"

    def test_provider_none_is_not_configured(self, app):
        with app.app_context():
            from routes.main_routes import _compute_tracker_status
            assert _compute_tracker_status({}, {"provider": "none"}) == "not_configured"

    def test_running_but_unconfigured_stays_not_configured(self, app):
        """Even with a running tracker + last_error, an unconfigured provider
        must stay not_configured (never a red provider_error)."""
        with app.app_context():
            from routes.main_routes import _compute_tracker_status
            cfg = {"provider": "ha", "ha_url": "", "ha_token": ""}
            st = {
                "running": True,
                "tracker_alive": True,
                "last_error": "HA URL oder Token nicht konfiguriert",
                "provider_connected": False,
            }
            assert _compute_tracker_status(st, cfg) == "not_configured"

    def test_configured_provider_not_not_configured(self, app):
        """Once a provider is configured, status leaves the not_configured state."""
        with app.app_context():
            from routes.main_routes import _compute_tracker_status
            cfg = {"provider": "ha", "ha_url": "http://ha.local", "ha_token": "tok"}
            # not running yet → stopped, not not_configured
            assert _compute_tracker_status({}, cfg) == "stopped"

    def test_configured_running_ready(self, app):
        with app.app_context():
            from routes.main_routes import _compute_tracker_status
            cfg = {"provider": "ha", "ha_url": "http://ha.local", "ha_token": "tok"}
            st = {
                "running": True,
                "tracker_alive": True,
                "provider_connected": True,
                "last_successful_poll": "2026-06-01T10:00:00",
            }
            assert _compute_tracker_status(st, cfg) == "ready"


# ---------------------------------------------------------------------------
# /api/status integration
# ---------------------------------------------------------------------------

class TestApiStatusOnboarding:
    def test_api_status_not_configured_for_fresh_v0(self, authed_client):
        """A fresh install with no provider config → /api/status not_configured."""
        rv = authed_client.get("/api/status?vehicle_id=v0")
        assert rv.status_code == 200
        assert rv.get_json().get("tracker_status") == "not_configured"

    def test_api_status_leaves_not_configured_after_save(self, app, authed_client):
        """After saving HA config for v0, /api/status is no longer not_configured."""
        with app.app_context():
            from core.config import load_config, save_config
            cfg = load_config()
            cfg["provider"] = "ha"
            cfg["ha_url"] = "http://ha.local"
            cfg["ha_token"] = "secret-token"
            save_config(cfg)

        rv = authed_client.get("/api/status?vehicle_id=v0")
        assert rv.status_code == 200
        assert rv.get_json().get("tracker_status") != "not_configured"
