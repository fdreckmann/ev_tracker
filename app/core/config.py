"""
Configuration management — DEFAULT_CONFIG, load_config, save_config.

Usage:
    from core.config import load_config, save_config, DEFAULT_CONFIG, CONFIG_FILE, VEHICLE_SPECIFIC_KEYS
"""
import json
import time
from pathlib import Path

from core.db import DATA_DIR

CONFIG_FILE = DATA_DIR / "config.json"

DEFAULT_CONFIG = {
    # Provider selection
    "provider":             "ha",
    "car_name":             "Mein EV",

    # HA provider fields
    "ha_url":               "http://homeassistant.local:8123",
    "ha_token":             "",
    "charging_sensor":      "",
    "soc_sensor":           "",
    "odo_sensor":           "",
    "power_sensor":         "",
    "charge_speed_sensor":  "",
    "charge_type_sensor":   "",
    "location_sensor":      "",
    "home_states":          "home,zuhause",
    "dc_threshold_kw":                22.0,
    "ha_connected_means_charging":    False,

    # VW provider fields
    "vw_username":          "",
    "vw_password":          "",
    "vw_vin":               "",

    # Tesla provider fields
    "tesla_email":          "",
    "tesla_vin":            "",

    # Volvo provider fields
    "volvo_api_key":        "",
    "volvo_access_token":   "",
    "volvo_vin":            "",

    # BMW provider fields
    "bmw_username":         "",
    "bmw_password":         "",
    "bmw_vin":              "",
    "bmw_region":           "rest_of_world",

    # Mercedes provider fields
    "mercedes_token":       "",
    "mercedes_vin":         "",

    # Shared location fields (non-HA providers)
    "home_lat":             "",
    "home_lon":             "",
    "home_radius_m":        200,

    # Location feature
    "location_enabled":              False,
    "location_mode":                 "home_external",
    "location_source":               "combined",
    "home_detection_mode":           "any",
    "location_history_enabled":      False,
    "location_history_precision":    "status_only",
    "location_history_retention_days": 30,
    "location_ha_entities":          [],

    # Pricing
    "battery_capacity_kwh": 77.0,
    "price_per_kwh_home":   0.30,
    "price_per_kwh_ac":     0.45,
    "price_per_kwh_dc":     0.75,
    "entsoe_api_key":       "",
    "entsoe_ac_markup":     3.0,
    "entsoe_dc_markup":     6.0,

    # Notifications
    "notify_service":       "",

    # System
    "poll_interval":        60,
    "backup_cron":          "",
    "update_channel":       "latest",
    "template_mapping":     {},
    "template_start_row":   None,
    "template_header_row":  None,
    "export_language":      "de",
    "active_template":      {"source": None, "template_id": None, "name": None},
    "template_fahrer":        "",
    "template_kennzeichen":   "",
    "template_abteilung":     "",
    "template_kostenstelle":  "",
    "template_meter_start":   0.0,
    "meter_source":      "none",
    "meter_sensor":      "",
    "meter_device_ip":   "",
    "meter_evcc_port":   7070,
    "meter_evcc_lp":     0,
    "meter_alfen_pass":  "admin",
    "meter_device_scheme": "http",
    "meter_device_port": "",
    "meter_username":    "",
    "meter_password":    "",
    "meter_channel":     0,
    "meter_phase_mode":  "total",
    "meter_json_path":   "",
    "meter_value_unit":  "auto",
    "meter_value_factor": 1.0,
    "meter_generic_url": "",
    "meter_openwb_lp":   1,
    "meter_warp_meter_index": 0,
    "meter_timeout_seconds": 8,
    "meter_verify_ssl":  True,
    "meter_prefer_meter_delta": False,
    "meter_scope":       "home_only",
    "home_charger_power_kw":    11.0,

    # Auth — password + TOTP
    "auth_password_hash": "",
    "auth_totp_secret":   "",

    # OAuth2 SSO — Google
    "oauth_google_client_id":     "",
    "oauth_google_client_secret": "",

    # OAuth2 SSO — Microsoft
    "oauth_microsoft_client_id":     "",
    "oauth_microsoft_client_secret": "",
    "oauth_microsoft_tenant":        "common",

    # Base URL for OAuth redirect URIs
    "oauth_base_url": "",

    # SMTP
    "smtp_host":      "",
    "smtp_port":      587,
    "smtp_tls":       "starttls",
    "smtp_user":      "",
    "smtp_password":  "",
    "smtp_from_name": "EV Tracker",
    "smtp_from_email":"",
    "smtp_reply_to":  "",
    "smtp_auth_method": "basic",
    "smtp_google_client_id":        "",
    "smtp_google_client_secret":    "",
    "smtp_google_refresh_token":    "",
    "smtp_google_access_token":     "",
    "smtp_google_token_expires_at": 0,
    "smtp_google_sender_email":     "",
    "smtp_ms_tenant_id":         "common",
    "smtp_ms_client_id":         "",
    "smtp_ms_client_secret":     "",
    "smtp_ms_refresh_token":     "",
    "smtp_ms_access_token":      "",
    "smtp_ms_token_expires_at":  0,
    "smtp_ms_sender_email":      "",

    # Export templates
    "export_templates": [],

    # Signature
    "signature":        {"source": None, "created_at": None},
    "signature_mapping": {},
    "export_include_signature": False,
    "signature_padding_px": 24,

    # Email Reports
    "report_email_enabled":          False,
    "report_email_recipients":       [],
    "report_email_schedule_type":    "monthly",
    "report_email_time":             "08:00",
    "report_email_weekday":          1,
    "report_email_day_of_month":     1,
    "report_email_month":            1,
    "report_email_custom_days":      14,
    "report_email_cron":             "",
    "report_email_period_mode":      "previous_period",
    "report_email_custom_start_date": "",
    "report_email_custom_end_date":   "",
    "report_email_location_filter":  "all",
    "report_email_vehicle_filter":   "all",
    "report_email_include_excel":    True,
    "report_email_include_summary":  True,
    "report_email_language":         "auto",
    "report_email_include_signature": False,
    "report_email_template_id":      None,
    "report_email_last_sent_key":    "",
    "report_email_single_month":     "",
    "report_email_months":           [],

    # Multi-vehicle
    "extra_vehicles":    [],

    # Tariff provider
    "tariff_provider":         "fixed",
    "tariff_currency":         "EUR",
    "tariff_include_tax":      True,
    "tariff_include_grid_fees": False,
    "tariff_fallback_price":   0.30,
    "octopus_api_key":         "",
    "octopus_account_id":      "",
    "octopus_product_code":    "",
    "octopus_tariff_code":     "",
    "octopus_gbp_eur_factor":  1.17,
    "tibber_token":            "",
    "generic_tariff_url":      "",
    "generic_tariff_headers":  {},
    "generic_tariff_json_path": "price",
    "generic_tariff_unit":     "EUR/kWh",
    "generic_tariff_factor":   1.0,
    "tariff_ha_url":           "",
    "tariff_ha_token":         "",
    "tariff_ha_entity":        "",
    "tariff_evcc_url":         "",

    # MQTT
    "mqtt_enabled":                    False,
    "mqtt_host":                       "",
    "mqtt_port":                       1883,
    "mqtt_username":                   "",
    "mqtt_password":                   "",
    "mqtt_tls":                        False,
    "mqtt_base_topic":                 "evtracker",
    "mqtt_discovery_enabled":          False,
    "mqtt_discovery_prefix":           "homeassistant",
    "mqtt_publish_interval_seconds":   60,

    # Notification channels
    "notifications_enabled": False,
    "ntfy_server":   "https://ntfy.sh",
    "ntfy_token":    "",
    "gotify_server": "",
    "gotify_token":  "",

    # PDF reports
    "report_include_pdf":            False,
    "report_pdf_language":           "auto",
    "report_pdf_include_signature":  False,

    # Vehicle image (primary vehicle v0)
    "vehicle_image_mode":          "none",
    "vehicle_image_path":          "",
    "vehicle_default_image_key":   "",
    "vehicle_image_source":        "",
    "vehicle_image_attribution":   "",
}

VEHICLE_SPECIFIC_KEYS = {
    "provider","car_name","poll_interval","battery_capacity_kwh",
    "home_lat","home_lon","home_radius_m","dc_threshold_kw",
    "ha_url","ha_token","charging_sensor","soc_sensor","odo_sensor",
    "power_sensor","charge_speed_sensor","charge_type_sensor","location_sensor","home_states",
    "vw_username","vw_password","vw_vin","vw_update_interval",
    "tesla_email","tesla_vin",
    "volvo_api_key","volvo_access_token","volvo_vin",
    "bmw_username","bmw_password","bmw_vin","bmw_region",
    "mercedes_token","mercedes_vin",
    "hk_brand","hk_username","hk_password","hk_pin","hk_region","hk_vin",
    "renault_username","renault_password","renault_locale","renault_account","renault_vin",
    "polestar_username","polestar_password","polestar_vin",
    "audi_username","audi_password","audi_vin",
    "stellantis_brand","stellantis_username","stellantis_password","stellantis_vin",
    "ford_username","ford_password","ford_vin",
    "mg_username","mg_password","mg_vin","mg_region",
    "toyota_username","toyota_password","toyota_vin","toyota_locale","toyota_region",
    "nissan_username","nissan_password","nissan_vin","nissan_region",
    "porsche_username","porsche_password","porsche_vin",
    "jlr_username","jlr_password","jlr_vin",
    "tronity_client_id","tronity_client_secret","tronity_vehicle_id",
    "enode_client_id","enode_client_secret","enode_user_id","enode_vehicle_id",
    "smartcar_access_token","smartcar_vehicle_id",
}

_config_cache: dict = {"data": None, "ts": 0.0}
_CONFIG_CACHE_TTL = 30  # seconds


def load_config() -> dict:
    import logging as _log
    now = time.time()
    if _config_cache["data"] is not None and now - _config_cache["ts"] < _CONFIG_CACHE_TTL:
        return dict(_config_cache["data"])
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                cfg = {**DEFAULT_CONFIG, **json.load(f)}
        except (json.JSONDecodeError, OSError, ValueError) as e:
            _log.getLogger(__name__).warning("Config file unreadable (%s), using defaults", e)
            cfg = DEFAULT_CONFIG.copy()
    else:
        cfg = DEFAULT_CONFIG.copy()
    _config_cache["data"] = cfg
    _config_cache["ts"] = now
    return cfg


def save_config(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    _config_cache["data"] = None
