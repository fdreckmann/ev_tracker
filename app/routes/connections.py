"""
Provider connection test routes.
"""
import re as _re_sanitize_url
from flask import Blueprint, jsonify, request

from core.config import load_config
from core.security import require_login, has_permission, _current_user

connections_bp = Blueprint("connections", __name__)

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


@connections_bp.route("/api/entsoe/test", methods=["POST"])
@require_login
def api_entsoe_test():
    if not has_permission(_current_user(), "tariffs:test"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: tariffs:test"}), 403
    key=(request.json or {}).get("entsoe_api_key","").strip()
    if not key: return jsonify({"ok":False,"error":"Kein API Key"})
    import server as _srv
    _srv._entsoe_cache["price"] = None
    price = _srv.fetch_entsoe_spot(key)
    if price: return jsonify({"ok":True,"price_kwh":price,"price_mwh":round(price*1000,2)})
    return jsonify({"ok":False,"error":"Kein Preis erhalten"})
