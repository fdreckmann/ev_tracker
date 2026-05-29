"""
MQTT Publisher — publishes vehicle state, session state, and report status to MQTT.
Optional dependency: paho-mqtt. Gracefully disabled if not installed.
"""
import json
import logging
import threading
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_PAHO_AVAILABLE = False
try:
    import paho.mqtt.client as mqtt_client
    _PAHO_AVAILABLE = True
except ImportError:
    pass

_publisher_lock  = threading.Lock()
_publisher_timer: threading.Timer | None = None


def is_available() -> bool:
    return _PAHO_AVAILABLE


def _build_client(config: dict):
    """Create and connect a paho MQTT client. Returns client or raises."""
    if not _PAHO_AVAILABLE:
        raise RuntimeError("paho-mqtt nicht installiert. Bitte zu requirements.txt hinzufügen.")
    host     = config.get("mqtt_host", "")
    port     = int(config.get("mqtt_port", 1883))
    username = config.get("mqtt_username", "")
    password = config.get("mqtt_password", "")
    tls      = bool(config.get("mqtt_tls", False))
    if not host:
        raise ValueError("Kein MQTT-Host konfiguriert")
    client = mqtt_client.Client(
        client_id=f"evtracker_{datetime.now().strftime('%H%M%S')}",
        protocol=mqtt_client.MQTTv311,
    )
    if username:
        client.username_pw_set(username, password or None)
    if tls:
        client.tls_set()
    client.connect(host, port, keepalive=30)
    return client


def _base_topic(config: dict) -> str:
    return config.get("mqtt_base_topic", "evtracker").rstrip("/")


def test_connection(config: dict) -> dict:
    """Test MQTT connection. Returns {"ok": bool, "message": str}."""
    if not _PAHO_AVAILABLE:
        return {"ok": False, "message": "paho-mqtt nicht installiert (siehe requirements.txt)"}
    if not config.get("mqtt_host", ""):
        return {"ok": False, "message": "Kein MQTT-Host konfiguriert"}
    try:
        client = _build_client(config)
        base  = _base_topic(config)
        client.publish(f"{base}/status", json.dumps({"status": "test", "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}))
        client.disconnect()
        return {"ok": True, "message": f"Verbunden mit {config['mqtt_host']}:{config.get('mqtt_port',1883)}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def publish_once(config: dict, topic_suffix: str, payload) -> bool:
    """Publish a single message. Returns True on success."""
    if not config.get("mqtt_enabled"):
        return False
    if not _PAHO_AVAILABLE:
        return False
    try:
        client = _build_client(config)
        base   = _base_topic(config)
        msg    = json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)
        client.publish(f"{base}/{topic_suffix}", msg, retain=True)
        client.disconnect()
        return True
    except Exception as e:
        log.warning("MQTT publish error (%s): %s", topic_suffix, e)
        return False


def publish_vehicle_state(config: dict, vehicle_id: str, state: dict) -> bool:
    """Publish vehicle state. state keys: soc, location, charging, odometer, last_update."""
    base_ok = publish_once(config, f"vehicles/{vehicle_id}/state", state)
    for key in ("soc", "location", "charging", "odometer"):
        if key in state:
            publish_once(config, f"vehicles/{vehicle_id}/{key}", state[key])
    publish_once(config, f"vehicles/{vehicle_id}/last_update",
                 datetime.now(timezone.utc).isoformat(timespec="seconds"))
    publish_once(config, "status", {"status": "online", "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return base_ok


def publish_session_update(config: dict, session: dict) -> bool:
    """Publish current session state."""
    if not session:
        return False
    payload = {
        "state":    "active" if not session.get("end_ts") else "completed",
        "kwh":      session.get("kwh_charged") or 0,
        "cost":     session.get("cost_eur") or 0,
        "soc_end":  session.get("soc_end"),
        "location": session.get("location"),
        "ts":       datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    ok = publish_once(config, "sessions/current/state",    payload["state"])
    publish_once(config, "sessions/current/kwh",       payload["kwh"])
    publish_once(config, "sessions/current/cost",      payload["cost"])
    publish_once(config, "sessions/current/soc",       payload.get("soc_end"))
    publish_once(config, "sessions/current/location",  payload["location"])
    return ok


def publish_report_status(config: dict, report_info: dict) -> bool:
    """Publish report send result."""
    payload = {
        "status":  report_info.get("status", "unknown"),
        "period":  report_info.get("period_label", ""),
        "ts":      datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    publish_once(config, "reports/last/status", payload["status"])
    publish_once(config, "reports/last/period", payload["period"])
    return publish_once(config, "reports/last/state", payload)


def publish_ha_discovery(config: dict) -> bool:
    """Publish Home Assistant MQTT discovery messages for all sensors."""
    if not config.get("mqtt_discovery_enabled"):
        return False
    base    = _base_topic(config)
    disc    = config.get("mqtt_discovery_prefix", "homeassistant")
    sensors = [
        ("soc",       "State of Charge",    "%",    "battery",    f"{base}/vehicles/v0/soc"),
        ("charging",  "Charging",           None,   "plug",       f"{base}/vehicles/v0/charging"),
        ("odometer",  "Odometer",           "km",   "counter",    f"{base}/vehicles/v0/odometer"),
        ("kwh",       "Session kWh",        "kWh",  "lightning-bolt", f"{base}/sessions/current/kwh"),
        ("cost",      "Session Cost",       "€",    "currency-eur", f"{base}/sessions/current/cost"),
    ]
    ok = True
    for uid, name, unit, icon, state_topic in sensors:
        disc_topic = f"{disc}/sensor/evtracker_{uid}/config"
        payload: dict = {
            "name":           f"EV Tracker {name}",
            "unique_id":      f"evtracker_{uid}",
            "state_topic":    state_topic,
            "icon":           f"mdi:{icon}",
        }
        if unit:
            payload["unit_of_measurement"] = unit
        ok = ok and publish_once(config, f"__ha_disc/{uid}", None)  # placeholder
        try:
            client = _build_client(config)
            client.publish(disc_topic, json.dumps(payload), retain=True)
            client.disconnect()
        except Exception as e:
            log.warning("HA Discovery publish error: %s", e)
            ok = False
    return ok


def _periodic_publish(config: dict, get_state_fn):
    """Internal: publish periodically while enabled."""
    global _publisher_timer
    if not config.get("mqtt_enabled"):
        return
    try:
        state = get_state_fn()
        if state:
            publish_vehicle_state(config, state.get("vehicle_id", "v0"), state)
    except Exception as e:
        log.warning("Periodic MQTT publish error: %s", e)
    interval = int(config.get("mqtt_publish_interval_seconds", 60))
    with _publisher_lock:
        _publisher_timer = threading.Timer(interval, _periodic_publish, args=(config, get_state_fn))
        _publisher_timer.daemon = True
        _publisher_timer.start()


def start_periodic_publisher(config: dict, get_state_fn) -> None:
    """Start background periodic MQTT publishing."""
    global _publisher_timer
    if not config.get("mqtt_enabled") or not _PAHO_AVAILABLE:
        return
    with _publisher_lock:
        if _publisher_timer and _publisher_timer.is_alive():
            _publisher_timer.cancel()
    interval = int(config.get("mqtt_publish_interval_seconds", 60))
    with _publisher_lock:
        _publisher_timer = threading.Timer(interval, _periodic_publish, args=(config, get_state_fn))
        _publisher_timer.daemon = True
        _publisher_timer.start()
    log.info("MQTT Periodic Publisher gestartet (Intervall: %ds)", interval)


def stop_periodic_publisher() -> None:
    """Stop background periodic MQTT publishing."""
    global _publisher_timer
    with _publisher_lock:
        if _publisher_timer:
            _publisher_timer.cancel()
            _publisher_timer = None
