"""Central secrets masking for API responses."""

SECRET_MASK = "********"

# Vehicle config keys that contain credentials and must never be sent to the client
_SECRET_VEHICLE_KEYS: frozenset[str] = frozenset({
    "ha_token",
    "vw_password",
    "volvo_access_token",
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
})

# Global config keys with credentials
_SECRET_GLOBAL_KEYS: frozenset[str] = frozenset({
    "smtp_password",
    "smtp_google_client_secret",
    "smtp_google_refresh_token",
    "smtp_google_access_token",
    "smtp_ms_client_secret",
    "smtp_ms_refresh_token",
    "smtp_ms_access_token",
    "notification_ntfy_token",
    "notification_gotify_token",
    "notification_telegram_bot_token",
})

SECRET_KEYS: frozenset[str] = _SECRET_VEHICLE_KEYS | _SECRET_GLOBAL_KEYS


def mask_vehicle(obj: dict) -> dict:
    """Return a copy of a vehicle config dict with secret values replaced by SECRET_MASK."""
    result = dict(obj)
    for key in _SECRET_VEHICLE_KEYS:
        if key in result and result[key]:
            result[key] = SECRET_MASK
    return result


def mask_config(obj: dict) -> dict:
    """Return a copy of the global config dict with secret values replaced by SECRET_MASK."""
    result = dict(obj)
    for key in _SECRET_GLOBAL_KEYS:
        if key in result and result[key]:
            result[key] = SECRET_MASK
    return result
