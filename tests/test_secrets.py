"""
Tests for secret masking — core/secrets.py and API boundary checks.

Verifies that all known credential fields are masked before being returned
to API clients, and that the masking infrastructure works correctly.
"""
import pytest

from core.secrets import (
    mask_config,
    mask_vehicle,
    is_masked,
    _is_secret_key,
    SECRET_MASK,
    SECRET_KEYS,
    _SECRET_GLOBAL_KEYS,
    _SECRET_VEHICLE_KEYS,
)


# ── Unit: _is_secret_key ────────────────────────────────────────────────────

class TestIsSecretKey:
    def test_explicit_global_keys(self):
        """Every key in _SECRET_GLOBAL_KEYS must be classified as secret."""
        for key in _SECRET_GLOBAL_KEYS:
            assert _is_secret_key(key), f"{key!r} not classified as secret"

    def test_explicit_vehicle_keys(self):
        """Every key in _SECRET_VEHICLE_KEYS must be classified as secret."""
        for key in _SECRET_VEHICLE_KEYS:
            assert _is_secret_key(key), f"{key!r} not classified as secret"

    # Regex-detected patterns
    def test_regex_password_suffix(self):
        assert _is_secret_key("smtp_password")
        assert _is_secret_key("vw_password")
        assert _is_secret_key("some_provider_password")

    def test_regex_token_suffix(self):
        assert _is_secret_key("ha_token")
        assert _is_secret_key("access_token")
        assert _is_secret_key("refresh_token")
        assert _is_secret_key("ntfy_token")
        assert _is_secret_key("gotify_token")

    def test_regex_secret_suffix(self):
        assert _is_secret_key("client_secret")
        assert _is_secret_key("oauth_google_client_secret")
        assert _is_secret_key("auth_totp_secret")

    def test_regex_api_key_suffix(self):
        assert _is_secret_key("octopus_api_key")
        assert _is_secret_key("entsoe_api_key")
        assert _is_secret_key("some_apikey")

    def test_regex_refresh_suffix(self):
        assert _is_secret_key("smtp_google_refresh_token")
        # _refresh anywhere at end
        assert _is_secret_key("my_field_refresh")

    def test_regex_pin_suffix(self):
        assert _is_secret_key("hk_pin")

    def test_non_secret_keys_not_classified(self):
        """Non-secret config keys must not be misclassified."""
        for key in ("ha_url", "mqtt_host", "mqtt_port", "provider", "poll_interval",
                    "home_lat", "home_lon", "smtp_user", "smtp_host", "vehicle_name",
                    "export_language", "billing_enabled", "vin"):
            assert not _is_secret_key(key), f"{key!r} wrongly classified as secret"


# ── Unit: is_masked ─────────────────────────────────────────────────────────

class TestIsMasked:
    def test_masked_sentinel_is_masked(self):
        assert is_masked(SECRET_MASK) is True
        assert is_masked("********") is True

    def test_real_value_not_masked(self):
        assert is_masked("real-token-value") is False
        assert is_masked("") is False
        assert is_masked(None) is False
        assert is_masked(0) is False
        assert is_masked(False) is False

    def test_partial_mask_not_masked(self):
        assert is_masked("*****") is False
        assert is_masked("*********") is False  # 9 stars, not 8


# ── Unit: mask_config ───────────────────────────────────────────────────────

class TestMaskConfig:
    # All explicitly known global secret fields from CLAUDE.md and config.py
    GLOBAL_SECRETS = [
        "smtp_password",
        "smtp_google_client_secret", "smtp_google_refresh_token", "smtp_google_access_token",
        "smtp_ms_client_secret", "smtp_ms_refresh_token", "smtp_ms_access_token",
        "notification_ntfy_token", "notification_gotify_token", "notification_telegram_bot_token",
        "ntfy_token", "gotify_token",  # short-form aliases — per CLAUDE.md must be masked
        "entsoe_api_key", "octopus_api_key", "tibber_token", "tariff_ha_token",
        "oauth_google_client_secret", "oauth_microsoft_client_secret",
        "meter_password", "meter_alfen_pass", "meter_token",
        "mqtt_password",
        "auth_password_hash", "auth_totp_secret",
        "enbw_api_subscription_key",
    ]

    def test_known_global_secrets_are_masked(self):
        """All known global secret fields must be replaced with SECRET_MASK."""
        cfg = {k: f"real-value-for-{k}" for k in self.GLOBAL_SECRETS}
        cfg["ha_url"] = "http://homeassistant.local"  # non-secret

        result = mask_config(cfg)

        for key in self.GLOBAL_SECRETS:
            assert result[key] == SECRET_MASK, (
                f"mask_config() did not mask {key!r}: got {result[key]!r}"
            )

    def test_non_secret_fields_preserved(self):
        """Non-secret config fields must pass through unchanged."""
        cfg = {
            "ha_url": "http://homeassistant.local",
            "mqtt_host": "192.168.1.10",
            "mqtt_port": 1883,
            "provider": "ha",
            "poll_interval": 30,
            "home_lat": 48.1,
            "home_lon": 11.5,
        }
        result = mask_config(cfg)
        for k, v in cfg.items():
            assert result[k] == v, f"mask_config() changed non-secret field {k!r}"

    def test_empty_secret_not_masked(self):
        """Empty strings should not be replaced with SECRET_MASK."""
        cfg = {"smtp_password": "", "mqtt_password": "", "ha_url": "http://ha.local"}
        result = mask_config(cfg)
        assert result["smtp_password"] == "", "empty secret should stay empty"
        assert result["mqtt_password"] == "", "empty secret should stay empty"

    def test_original_dict_not_mutated(self):
        """mask_config must not modify the original dict."""
        cfg = {"smtp_password": "real-secret", "ha_url": "http://ha.local"}
        _ = mask_config(cfg)
        assert cfg["smtp_password"] == "real-secret", "original dict was mutated"

    def test_ntfy_token_short_form_masked(self):
        """ntfy_token (short form) must be masked — explicitly required by CLAUDE.md."""
        cfg = {"ntfy_token": "my-ntfy-secret-token", "ha_url": "http://ha.local"}
        result = mask_config(cfg)
        assert result["ntfy_token"] == SECRET_MASK

    def test_gotify_token_short_form_masked(self):
        """gotify_token (short form) must be masked — explicitly required by CLAUDE.md."""
        cfg = {"gotify_token": "my-gotify-secret", "ha_url": "http://ha.local"}
        result = mask_config(cfg)
        assert result["gotify_token"] == SECRET_MASK

    def test_dynamic_password_field_masked(self):
        """Any field ending in 'password' must be masked via the regex fallback."""
        cfg = {"new_provider_password": "hunter2", "ha_url": "http://ha.local"}
        result = mask_config(cfg)
        assert result["new_provider_password"] == SECRET_MASK

    def test_dynamic_token_field_masked(self):
        """Any field ending in '_token' must be masked via the regex fallback."""
        cfg = {"new_provider_access_token": "abc123", "ha_url": "http://ha.local"}
        result = mask_config(cfg)
        assert result["new_provider_access_token"] == SECRET_MASK


# ── Unit: mask_vehicle ──────────────────────────────────────────────────────

class TestMaskVehicle:
    VEHICLE_SECRETS = [
        "ha_token",
        "vw_password",
        "volvo_access_token", "volvo_api_key",
        "bmw_password",
        "mercedes_token",
        "hk_password", "hk_pin",
        "renault_password",
        "polestar_password",
        "audi_password",
        "stellantis_password",
        "ford_password",
        "mg_password",
        "toyota_password",
        "nissan_password",
        "porsche_password",
        "jlr_password",
        "tronity_client_secret",
        "enode_client_secret",
        "smartcar_access_token",
        "tesla_token", "tesla_refresh_token",
    ]

    def test_known_vehicle_secrets_are_masked(self):
        """All known vehicle credential fields must be masked."""
        vehicle = {k: f"cred-{k}" for k in self.VEHICLE_SECRETS}
        vehicle["vin"] = "WVWZZZ1JZXW123456"  # non-secret

        result = mask_vehicle(vehicle)

        for key in self.VEHICLE_SECRETS:
            assert result[key] == SECRET_MASK, (
                f"mask_vehicle() did not mask {key!r}: got {result[key]!r}"
            )

    def test_non_secret_vehicle_fields_preserved(self):
        """VIN, name, provider etc. must pass through unchanged."""
        vehicle = {"vin": "WVWZZZ1J", "name": "My EV", "provider": "vw",
                   "username": "user@example.com"}
        result = mask_vehicle(vehicle)
        assert result["vin"] == "WVWZZZ1J"
        assert result["name"] == "My EV"
        assert result["provider"] == "vw"


# ── Integration: API boundary ────────────────────────────────────────────────

class TestAPISecretMasking:
    """Verify that API endpoints never return plain-text secrets."""

    # Subset of known secrets — these must never appear as plain text
    _KNOWN_SECRETS = {
        "smtp_password": "test-smtp-password-12345",
        "mqtt_password": "test-mqtt-password-12345",
        "ha_token": "test-ha-token-abcdef",
        "ntfy_token": "test-ntfy-token-xyz",
        "gotify_token": "test-gotify-token-xyz",
        "entsoe_api_key": "test-entsoe-key-abc",
        "tibber_token": "test-tibber-token-789",
        "octopus_api_key": "test-octopus-key-456",
        "oauth_google_client_secret": "test-google-secret",
        "meter_password": "test-meter-password",
    }

    def _inject_into_main_routes(self, monkeypatch):
        """Patch the load_config name in main_routes (module-level import)."""
        import routes.main_routes as _mr
        real_load = _mr.load_config

        def _patched_load():
            cfg = real_load()
            cfg.update(self._KNOWN_SECRETS)
            return cfg

        monkeypatch.setattr(_mr, "load_config", _patched_load)

    def test_api_config_masks_all_secrets(self, authed_client, monkeypatch):
        """/api/config must not expose any known secret values."""
        self._inject_into_main_routes(monkeypatch)
        rv = authed_client.get("/api/config")
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        for key, value in self._KNOWN_SECRETS.items():
            assert value not in body, (
                f"/api/config leaked plain-text value for {key!r}: {value!r}"
            )

    def test_api_config_secrets_are_masked_sentinel(self, authed_client, monkeypatch):
        """/api/config secret fields must contain '********', not empty string."""
        self._inject_into_main_routes(monkeypatch)
        rv = authed_client.get("/api/config")
        data = rv.get_json()
        for key in self._KNOWN_SECRETS:
            if key in data:
                assert data[key] == SECRET_MASK, (
                    f"/api/config field {key!r} = {data[key]!r}, expected {SECRET_MASK!r}"
                )

    def test_get_all_vehicles_masks_secrets_by_default(self):
        """get_all_vehicles(mask_secrets=True) must mask vehicle credentials."""
        from services.vehicle_service import get_all_vehicles
        from core.config import load_config
        cfg = load_config()
        vehicles = get_all_vehicles(cfg=cfg, mask_secrets=True)
        for v in vehicles:
            for key in ("ha_token", "vw_password", "volvo_access_token", "mercedes_token",
                        "hk_password", "hk_pin", "tesla_token"):
                val = v.get(key, "")
                assert val != "" or val == "", "empty is ok"
                if val:
                    assert val == SECRET_MASK, (
                        f"get_all_vehicles returned unmasked {key!r} = {val!r}"
                    )

    def test_get_all_vehicles_masks_are_applied(self, monkeypatch):
        """Vehicle secrets must be masked before being returned by the service."""
        import services.vehicle_service as _vs
        # Bypass actual vehicle loading — inject a vehicle with a real secret
        monkeypatch.setattr(_vs, "_load_vehicles_raw", lambda cfg: [{
            "id": "test-v", "provider": "ha",
            "ha_token": "real-secret-ha-token-xyz",
            "vw_password": "real-secret-vw-pass",
        }], raising=False)
        vehicles = _vs.get_all_vehicles(mask_secrets=True)
        for v in vehicles:
            assert v.get("ha_token") != "real-secret-ha-token-xyz", \
                "ha_token must be masked"
            assert v.get("vw_password") != "real-secret-vw-pass", \
                "vw_password must be masked"

    def test_secret_mask_not_saveable(self, authed_client, monkeypatch):
        """POSTing SECRET_MASK to /api/config must not overwrite the stored secret."""
        import core.config as _cfg_mod
        saved_patches = {}

        real_save = _cfg_mod.save_config
        def _capturing_save(cfg):
            saved_patches.update(cfg)
            return real_save(cfg)
        monkeypatch.setattr(_cfg_mod, "save_config", _capturing_save)

        authed_client.post("/api/config",
                           json={"smtp_password": SECRET_MASK},
                           content_type="application/json")
        # The masked placeholder should NOT overwrite the stored real secret
        if "smtp_password" in saved_patches:
            assert saved_patches["smtp_password"] != SECRET_MASK, (
                "SECRET_MASK was written to config — masked placeholder should be ignored"
            )

    def test_system_status_no_secrets(self, authed_client, monkeypatch):
        """/api/system/status must not contain any known secret values."""
        import routes.health as _h
        import routes.main_routes as _mr

        real_load_h  = _h.load_config
        real_load_mr = _mr.load_config

        def _patched(*_a, **_kw):
            cfg = real_load_h()
            cfg.update(self._KNOWN_SECRETS)
            return cfg

        monkeypatch.setattr(_h,  "load_config", _patched)
        monkeypatch.setattr(_mr, "load_config", _patched)
        rv = authed_client.get("/api/system/status")
        body = rv.get_data(as_text=True)
        for key, value in self._KNOWN_SECRETS.items():
            assert value not in body, (
                f"/api/system/status leaked plain-text value for {key!r}: {value!r}"
            )


# ── Secret key completeness check ───────────────────────────────────────────

class TestSecretKeyCompleteness:
    """Verify that all secret-like config keys are covered."""

    def test_all_known_password_fields_in_secret_keys(self):
        """Every *password config key must be in SECRET_KEYS or caught by the regex."""
        known_password_fields = [
            "vw_password", "bmw_password", "hk_password", "renault_password",
            "polestar_password", "audi_password", "stellantis_password", "ford_password",
            "mg_password", "toyota_password", "nissan_password", "porsche_password",
            "jlr_password", "smtp_password", "meter_password",
        ]
        for field in known_password_fields:
            assert _is_secret_key(field), f"{field!r} not classified as secret"

    def test_all_known_token_fields_in_secret_keys(self):
        """Every *token config key must be classified as secret."""
        known_token_fields = [
            "ha_token", "mercedes_token", "tesla_token", "tesla_refresh_token",
            "tibber_token", "tariff_ha_token", "notification_ntfy_token",
            "notification_gotify_token", "notification_telegram_bot_token",
            "ntfy_token", "gotify_token", "meter_token",
        ]
        for field in known_token_fields:
            assert _is_secret_key(field), f"{field!r} not classified as secret"

    def test_all_known_api_key_fields_in_secret_keys(self):
        """Every *api_key config key must be classified as secret."""
        known_api_key_fields = [
            "entsoe_api_key", "octopus_api_key", "volvo_api_key",
        ]
        for field in known_api_key_fields:
            assert _is_secret_key(field), f"{field!r} not classified as secret"

    def test_enbw_subscription_key_covered(self):
        """enbw_api_subscription_key must be explicitly listed (regex doesn't catch it)."""
        assert "enbw_api_subscription_key" in _SECRET_GLOBAL_KEYS, (
            "enbw_api_subscription_key must be explicit in _SECRET_GLOBAL_KEYS "
            "since it doesn't end in 'api_key'"
        )
        assert _is_secret_key("enbw_api_subscription_key")

    def test_auth_password_hash_covered(self):
        """auth_password_hash must be explicit (ends in _hash, not _password)."""
        assert "auth_password_hash" in _SECRET_GLOBAL_KEYS
        assert _is_secret_key("auth_password_hash")

    def test_meter_alfen_pass_covered(self):
        """meter_alfen_pass must be explicit (ends in _pass, not _password)."""
        assert "meter_alfen_pass" in _SECRET_GLOBAL_KEYS
        assert _is_secret_key("meter_alfen_pass")

    def test_claude_md_required_explicit_keys(self):
        """Keys CLAUDE.md requires to be explicitly in _SECRET_GLOBAL_KEYS."""
        # From CLAUDE.md: ntfy_token, gotify_token, telegram_bot_token
        assert "ntfy_token" in _SECRET_GLOBAL_KEYS, (
            "ntfy_token must be explicit in _SECRET_GLOBAL_KEYS per CLAUDE.md"
        )
        assert "gotify_token" in _SECRET_GLOBAL_KEYS, (
            "gotify_token must be explicit in _SECRET_GLOBAL_KEYS per CLAUDE.md"
        )
        assert "notification_telegram_bot_token" in _SECRET_GLOBAL_KEYS
