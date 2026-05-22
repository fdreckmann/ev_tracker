"""
MQTT configuration and management routes.
"""
import logging

from flask import Blueprint, jsonify, request

from core.config import load_config, save_config, DEFAULT_CONFIG
from core.security import require_login, has_permission, _current_user, _audit
from core.state import vehicle_states

log = logging.getLogger(__name__)

mqtt_bp = Blueprint("mqtt", __name__)

_MQTT_CONFIG_KEYS = [k for k in DEFAULT_CONFIG if k.startswith("mqtt_")]
_MQTT_SENSITIVE   = {"mqtt_password"}


@mqtt_bp.route("/api/mqtt/config", methods=["GET"])
@require_login
def api_mqtt_config_get():
    if not has_permission(_current_user(), "mqtt:view"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    cfg = load_config()
    result = {k: cfg.get(k, DEFAULT_CONFIG.get(k)) for k in _MQTT_CONFIG_KEYS}
    for k in _MQTT_SENSITIVE:
        if result.get(k):
            result[k] = "********"
    return jsonify(result)


@mqtt_bp.route("/api/mqtt/config", methods=["POST"])
@require_login
def api_mqtt_config_save():
    if not has_permission(_current_user(), "mqtt:configure"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    data = request.get_json(force=True) or {}
    cfg  = load_config()
    for k in _MQTT_CONFIG_KEYS:
        if k in data and data[k] != "********":
            cfg[k] = data[k]
    save_config(cfg)
    try:
        from mqtt_publisher import start_periodic_publisher, stop_periodic_publisher
        stop_periodic_publisher()
        if cfg.get("mqtt_enabled"):
            def _get_mqtt_vehicle_states():
                return {vid: dict(st) for vid, st in vehicle_states.items()}
            start_periodic_publisher(cfg, _get_mqtt_vehicle_states)
    except Exception as _mqtt_err:
        log.warning("MQTT-Publisher-Start fehlgeschlagen: %s", _mqtt_err)
    _audit("mqtt_config_saved", ip=request.remote_addr)
    return jsonify({"ok": True})


@mqtt_bp.route("/api/mqtt/test", methods=["POST"])
@require_login
def api_mqtt_test():
    if not has_permission(_current_user(), "mqtt:test"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    data = request.get_json(force=True) or {}
    cfg  = load_config()
    cfg.update({k: v for k, v in data.items() if v != "********"})
    try:
        from mqtt_publisher import test_connection
        return jsonify(test_connection(cfg))
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})


@mqtt_bp.route("/api/mqtt/publish", methods=["POST"])
@require_login
def api_mqtt_publish():
    if not has_permission(_current_user(), "mqtt:test"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    data = request.get_json(force=True) or {}
    cfg  = load_config()
    try:
        from mqtt_publisher import publish_once
        ok = publish_once(cfg, data.get("topic", "test"), data.get("payload", "ping"))
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@mqtt_bp.route("/api/mqtt/ha-discovery", methods=["POST"])
@require_login
def api_mqtt_ha_discovery():
    if not has_permission(_current_user(), "mqtt:configure"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    cfg = load_config()
    try:
        from mqtt_publisher import publish_ha_discovery
        ok = publish_ha_discovery(cfg)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
