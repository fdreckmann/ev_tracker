"""
Provider connection test routes.
"""
import re as _re_sanitize_url
import time
from flask import Blueprint, jsonify, request

from core.config import load_config
from core.security import require_login, has_permission, _current_user

connections_bp = Blueprint("connections", __name__)

# TTL cache for /api/meter/status — avoids hammering devices on every dashboard poll
_meter_status_cache: dict = {}   # key: vehicle_id → {"ts": float, "data": dict}
_METER_STATUS_TTL = 30           # seconds

def _sanitize_url(url):
    if not url:
        return url
    # Remove user:pass@ from URL
    return _re_sanitize_url.sub(r'://([^@]+)@', '://', url)


@connections_bp.route("/api/test-connection", methods=["POST"])
@require_login
def api_test():
    if not has_permission(_current_user(), "providers:test"):
        return jsonify({"ok": False, "message": "Keine Berechtigung: providers:test"}), 403
    data = request.json or {}
    cfg  = load_config()
    test_cfg = {**cfg, **data}
    _MASK = "********"
    # Generic: for any password field sent as empty/"********", use saved value
    provider_id = test_cfg.get("provider", "ha")
    try:
        from providers import get_config_fields
        fields = get_config_fields(provider_id)
        for f in fields:
            if f.get("type") == "password":
                key = f["id"]
                if test_cfg.get(key) in ("", _MASK):
                    test_cfg[key] = cfg.get(key, "")
    except Exception:
        pass
    # Fallback for legacy keys
    for secret_key in ("ha_token","tronity_client_secret","enode_client_secret",
                       "smartcar_access_token","tesla_access_token","vw_password"):
        if test_cfg.get(secret_key) in ("", _MASK):
            test_cfg[secret_key] = cfg.get(secret_key, "")
    try:
        from providers import get_provider
        provider = get_provider(test_cfg.get("provider","ha"), test_cfg)
        result   = provider.test_connection()
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})


@connections_bp.route("/api/meter/test", methods=["POST"])
@require_login
def api_meter_test():
    if not has_permission(_current_user(), "meter:test"):
        return jsonify({"ok": False, "message": "Keine Berechtigung: meter:test"}), 403
    # Load saved config as base
    cfg = load_config()
    # Body-first: merge body values with priority over stored config (without saving)
    body = request.get_json(silent=True) or {}
    _MASK = "********"
    for k, v in body.items():
        if k.startswith("meter_") or k in ("ha_url", "ha_token"):
            if v == _MASK:
                continue  # keep stored real value
            cfg[k] = v

    from meter_providers import read_meter as _read_meter_impl
    result = _read_meter_impl(cfg)

    msg = (f"Zählerstand: {result.value:.3f} kWh" if result.ok
           else result.error or "Kein Wert erhalten")

    return jsonify({
        "ok":             result.ok,
        "value_kwh":      result.value,
        "value":          result.value,   # backward compat
        "message":        msg,
        "provider":       cfg.get("meter_source", ""),
        "endpoint":       _sanitize_url(result.endpoint),
        "raw_value":      result.raw_value,
        "unit":           result.unit,
        "normalized_from": result.normalized_from,
        "debug":          result.debug,
        "suggestions":    result.suggestions or [],
    })


@connections_bp.route("/api/meter/status", methods=["GET"])
@require_login
def api_meter_status():
    """Return live meter reading from the configured source with 30-second TTL cache."""
    if not has_permission(_current_user(), "meter:test"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: meter:test"}), 403

    vid = request.args.get("vehicle_id", "v0")
    force = request.args.get("force", "").lower() in ("1", "true")

    cached = _meter_status_cache.get(vid)
    if cached and not force and (time.time() - cached["ts"]) < _METER_STATUS_TTL:
        return jsonify(cached["data"])

    cfg = load_config()
    # For extra vehicles merge vehicle-specific config with global config
    if vid != "v0":
        extras = cfg.get("extra_vehicles", [])
        vehicle = next((v for v in extras if v.get("id") == vid), None)
        if vehicle:
            try:
                from server import build_vehicle_config
                cfg = build_vehicle_config(vehicle, cfg)
            except Exception:
                cfg = {**cfg, **vehicle}

    source = cfg.get("meter_source", "none")
    if not source or source == "none":
        data = {"ok": False, "source": "none", "value_kwh": None,
                "endpoint": None, "last_read": None, "error": "Keine Zählerquelle konfiguriert"}
        _meter_status_cache[vid] = {"ts": time.time(), "data": data}
        return jsonify(data)

    from meter_providers import read_meter as _read_meter_impl
    try:
        result = _read_meter_impl(cfg)
        data = {
            "ok":         result.ok,
            "value_kwh":  result.value,
            "source":     source,
            "endpoint":   _sanitize_url(result.endpoint),
            "last_read":  time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "error":      result.error if not result.ok else None,
        }
    except Exception as e:
        data = {"ok": False, "source": source, "value_kwh": None,
                "endpoint": None, "last_read": None, "error": str(e)}

    _meter_status_cache[vid] = {"ts": time.time(), "data": data}
    return jsonify(data)


@connections_bp.route("/api/entsoe/test", methods=["POST"])
@require_login
def api_entsoe_test():
    if not has_permission(_current_user(), "tariffs:test"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: tariffs:test"}), 403
    key=(request.json or {}).get("entsoe_api_key","").strip()
    if not key: return jsonify({"ok":False,"error":"Kein API Key"})
    import core.state as _cs
    _cs.entsoe_cache["price"] = None
    from server import fetch_entsoe_spot as _fetch
    price = _fetch(key)
    if price: return jsonify({"ok":True,"price_kwh":price,"price_mwh":round(price*1000,2)})
    return jsonify({"ok":False,"error":"Kein Preis erhalten"})
