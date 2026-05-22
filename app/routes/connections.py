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
    data=request.json or {}; cfg=load_config()
    # merge submitted fields into config for test
    test_cfg={**cfg,**data}
    # use saved token if empty
    if not test_cfg.get("ha_token"): test_cfg["ha_token"]=cfg.get("ha_token","")
    try:
        from server import get_provider
        provider=get_provider(test_cfg.get("provider","ha"), test_cfg)
        result=provider.test_connection()
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok":False,"message":str(e)})


@connections_bp.route("/api/meter/test", methods=["POST"])
@require_login
def api_meter_test():
    if not has_permission(_current_user(), "meter:test"):
        return jsonify({"ok": False, "message": "Keine Berechtigung: meter:test"}), 403
    # Load saved config as base
    cfg = load_config()
    # Body-first: merge body values with priority over stored config (without saving)
    body = request.get_json(silent=True) or {}
    for k, v in body.items():
        if k.startswith("meter_") or k in ("ha_url", "ha_token"):
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
    from server import _entsoe_cache, fetch_entsoe_spot
    _entsoe_cache["price"]=None
    price=fetch_entsoe_spot(key)
    if price: return jsonify({"ok":True,"price_kwh":price,"price_mwh":round(price*1000,2)})
    return jsonify({"ok":False,"error":"Kein Preis erhalten"})
