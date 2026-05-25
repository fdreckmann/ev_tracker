"""Central secrets masking for API responses."""
import re

SECRET_MASK = "********"

# Vehicle config keys that contain credentials and must never be sent to the client
_SECRET_VEHICLE_KEYS: frozenset[str] = frozenset({
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
})

# Global config keys with credentials (not vehicle-specific)
_SECRET_GLOBAL_KEYS: frozenset[str] = frozenset({
    # SMTP / mail
    "smtp_password",
    "smtp_google_client_secret", "smtp_google_refresh_token", "smtp_google_access_token",
    "smtp_ms_client_secret", "smtp_ms_refresh_token", "smtp_ms_access_token",
    # Notification channels
    "notification_ntfy_token",
    "notification_gotify_token",
    "notification_telegram_bot_token",
    # Tariff / energy APIs
    "entsoe_api_key",
    "octopus_api_key",
    "tibber_token",
    "tariff_ha_token",
    # OAuth (login via Google/Microsoft)
    "oauth_google_client_secret",
    "oauth_microsoft_client_secret",
    # Meter integration
    "meter_password",
    "meter_alfen_pass",
    "meter_token",
    # MQTT
    "mqtt_password",
    # Legacy single-user auth
    "auth_password_hash",
    "auth_totp_secret",
    # EnBW live price API
    "enbw_api_subscription_key",
})

SECRET_KEYS: frozenset[str] = _SECRET_VEHICLE_KEYS | _SECRET_GLOBAL_KEYS

# Regex for dynamic detection of secret-like keys not in the explicit sets above
_SECRET_KEY_PATTERN = re.compile(
    r"(password|_token|_secret|api_key|apikey|_refresh|client_secret|_pin)$",
    re.IGNORECASE,
)


def _is_secret_key(key: str) -> bool:
    return key in SECRET_KEYS or bool(_SECRET_KEY_PATTERN.search(key))


def mask_vehicle(obj: dict) -> dict:
    """Return a copy of a vehicle config dict with secret values replaced by SECRET_MASK."""
    result = dict(obj)
    for key in list(result):
        if _is_secret_key(key) and result[key]:
            result[key] = SECRET_MASK
    return result


def mask_config(obj: dict) -> dict:
    """Return a copy of the global config dict with secret values replaced by SECRET_MASK."""
    result = dict(obj)
    for key in list(result):
        if _is_secret_key(key) and result[key]:
            result[key] = SECRET_MASK
    return result


def is_masked(value: object) -> bool:
    """Return True if a value is the placeholder mask (should not overwrite stored secret)."""
    return value == SECRET_MASK
