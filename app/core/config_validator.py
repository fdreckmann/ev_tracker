"""
Central config validation.

validate_config_patch(data) validates an incoming partial config update dict.
Returns the coerced (type-correct) copy of data on success.
Raises ValueError with a user-facing message on the first invalid value found.

Does NOT touch secrets (masked/empty password fields are handled by the caller).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Field type definitions
# ---------------------------------------------------------------------------

# Fields that must be stored as float (when non-empty/non-None)
_FLOATS: frozenset[str] = frozenset({
    "battery_capacity_kwh",
    "price_per_kwh_home",
    "price_per_kwh_ac",
    "price_per_kwh_dc",
    "dc_threshold_kw",
    "entsoe_ac_markup",
    "entsoe_dc_markup",
    "home_radius_m",
    "meter_value_factor",
    "meter_home_detection_min_delta_kwh",
    "meter_home_detection_max_delta_kwh_per_hour",
    "home_charger_power_kw",
    "missing_charge_min_soc_gain_percent",
    "missing_charge_min_kwh",
    "missing_charge_default_consumption_kwh_per_100km",
    "tariff_fallback_price",
    "octopus_gbp_eur_factor",
    "generic_tariff_factor",
    "public_charging_fallback_ac",
    "public_charging_fallback_dc",
    "template_meter_start",
    "signature_padding_px",
})

# Fields that must be stored as int (when non-empty/non-None)
_INTS: frozenset[str] = frozenset({
    "poll_interval",
    "smtp_port",
    "mqtt_port",
    "mqtt_publish_interval_seconds",
    "meter_evcc_port",
    "meter_evcc_lp",
    "meter_channel",
    "meter_openwb_lp",
    "meter_warp_meter_index",
    "meter_timeout_seconds",
    "location_history_retention_days",
    "notification_dedupe_window_hours",
    "notification_rate_limit_per_hour",
    "report_email_weekday",
    "report_email_day_of_month",
    "report_email_month",
    "report_email_custom_days",
    "enbw_price_cache_minutes",
    "missing_charge_min_gap_minutes",
})

# Range constraints: field → (min_value, allow_equal_to_min)
# allow_equal_to_min=True  → value >= min_value
# allow_equal_to_min=False → value > min_value  (strictly greater)
_RANGES: dict[str, tuple[float, bool]] = {
    "battery_capacity_kwh":                         (0.0, False),  # > 0
    "home_radius_m":                                (0.0, False),  # > 0
    "poll_interval":                                (0.0, False),  # > 0
    "dc_threshold_kw":                              (0.0, True),   # >= 0
    "price_per_kwh_home":                           (0.0, True),   # >= 0
    "price_per_kwh_ac":                             (0.0, True),   # >= 0
    "price_per_kwh_dc":                             (0.0, True),   # >= 0
    "entsoe_ac_markup":                             (0.0, True),   # >= 0
    "entsoe_dc_markup":                             (0.0, True),   # >= 0
    "meter_value_factor":                           (0.0, False),  # > 0
    "meter_home_detection_min_delta_kwh":           (0.0, True),   # >= 0
    "meter_home_detection_max_delta_kwh_per_hour":  (0.0, False),  # > 0
    "home_charger_power_kw":                        (0.0, True),   # >= 0
    "tariff_fallback_price":                        (0.0, True),   # >= 0
    "octopus_gbp_eur_factor":                       (0.0, False),  # > 0
    "generic_tariff_factor":                        (0.0, False),  # > 0
    "public_charging_fallback_ac":                  (0.0, True),   # >= 0
    "public_charging_fallback_dc":                  (0.0, True),   # >= 0
    "smtp_port":                                    (0.0, False),  # > 0
    "mqtt_port":                                    (0.0, False),  # > 0
    "mqtt_publish_interval_seconds":                (0.0, False),  # > 0
    "meter_timeout_seconds":                        (0.0, False),  # > 0
    "location_history_retention_days":              (0.0, False),  # > 0
    "missing_charge_min_gap_minutes":               (0.0, False),  # > 0
    "missing_charge_min_soc_gain_percent":          (0.0, False),  # > 0
    "missing_charge_min_kwh":                       (0.0, True),   # >= 0
    "enbw_price_cache_minutes":                     (0.0, False),  # > 0
}

# Lat/lon stored as string — validate as valid float or empty string
_LAT_LON: frozenset[str] = frozenset({"home_lat", "home_lon"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_config_patch(data: dict) -> dict:
    """Validate and coerce an incoming partial config update.

    Returns a new dict with all values coerced to their correct types.
    Raises ValueError(user-facing message) on the first invalid field.
    """
    if not isinstance(data, dict):
        raise ValueError("Ungültige Config-Daten (kein Objekt)")

    result: dict = {}

    # Provider must be from the known set
    if "provider" in data:
        _validate_provider(data["provider"])

    for key, v in data.items():
        result[key] = _coerce_field(key, v)

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_provider(value: object) -> None:
    from providers import PROVIDERS  # lazy import — avoids circular deps at module load
    if value not in PROVIDERS:
        raise ValueError(f"Unbekannter Provider: {value!r}")


def _coerce_field(key: str, v: object) -> object:
    # Pass through None and masked placeholders unchanged — caller handles them
    if v is None:
        return v
    if isinstance(v, str) and v.strip() == "********":
        return v  # masked secret — caller must not overwrite stored value

    if key in _FLOATS:
        return _to_float(key, v)

    if key in _INTS:
        return _to_int(key, v)

    if key in _LAT_LON:
        return _to_lat_lon(key, v)

    return v  # strings, bools, lists, dicts — passed through as-is


def _to_float(key: str, v: object) -> float | str:
    if v == "" or v is None:
        return v  # empty string means "clear" — caller decides whether to allow
    try:
        fv = float(v)
    except (ValueError, TypeError):
        raise ValueError(f"Ungültiger Zahlenwert für '{key}': {v!r}")
    import math
    if not math.isfinite(fv):
        raise ValueError(f"'{key}' muss eine endliche Zahl sein")
    _check_range(key, fv)
    return fv


def _to_int(key: str, v: object) -> int | str:
    if v == "" or v is None:
        return v
    try:
        # Accept float strings like "60.0" → 60
        iv = int(float(v))
    except (ValueError, TypeError):
        raise ValueError(f"Ungültiger Ganzzahlwert für '{key}': {v!r}")
    _check_range(key, float(iv))
    return iv


def _to_lat_lon(key: str, v: object) -> str:
    if v == "" or v is None:
        return ""
    try:
        parsed = float(v)
    except (ValueError, TypeError):
        raise ValueError(f"Ungültiger Koordinatenwert für '{key}': {v!r}")
    if key == "home_lat" and not (-90.0 <= parsed <= 90.0):
        raise ValueError(f"'{key}' muss zwischen -90 und 90 liegen")
    if key == "home_lon" and not (-180.0 <= parsed <= 180.0):
        raise ValueError(f"'{key}' muss zwischen -180 und 180 liegen")
    return str(parsed)


def _check_range(key: str, value: float) -> None:
    if key not in _RANGES:
        return
    mn, allow_equal = _RANGES[key]
    if allow_equal:
        if value < mn:
            raise ValueError(f"'{key}' muss >= {mn} sein, war: {value}")
    else:
        if value <= mn:
            raise ValueError(f"'{key}' muss > {mn} sein, war: {value}")
