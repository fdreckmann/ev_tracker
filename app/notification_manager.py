"""
Notification Manager — rule-based notification dispatcher.
Channels: email, mqtt, ntfy, gotify, webhook.
"""
import json
import logging
import threading
from datetime import datetime, time as dtime

log = logging.getLogger(__name__)

# All event types
EVENT_TYPES = [
    "charging_started", "charging_stopped", "charging_error",
    "meter_read_failed", "provider_error",
    "report_created", "report_waiting_for_approval", "report_sent", "report_failed",
    "vehicle_home", "vehicle_external",
    "soc_below_threshold", "soc_above_threshold",
    "backup_success", "backup_failed",
    "update_available", "update_success", "update_failed",
    "session_cost_high",
]

EVENT_LABELS_DE = {
    "charging_started":             "Ladevorgang gestartet",
    "charging_stopped":             "Ladevorgang beendet",
    "charging_error":               "Ladefehler",
    "meter_read_failed":            "Zählerstand-Fehler",
    "provider_error":               "Provider-Fehler",
    "report_created":               "Report erstellt",
    "report_waiting_for_approval":  "Report wartet auf Freigabe",
    "report_sent":                  "Report gesendet",
    "report_failed":                "Report fehlgeschlagen",
    "vehicle_home":                 "Fahrzeug zuhause",
    "vehicle_external":             "Fahrzeug extern",
    "soc_below_threshold":          "Akkustand unter Schwellwert",
    "soc_above_threshold":          "Akkustand über Schwellwert",
    "backup_success":               "Backup erfolgreich",
    "backup_failed":                "Backup fehlgeschlagen",
    "update_available":             "Update verfügbar",
    "update_success":               "Update erfolgreich",
    "update_failed":                "Update fehlgeschlagen",
    "session_cost_high":            "Hohe Ladekosten",
}

CHANNEL_TYPES = ["email", "mqtt", "ntfy", "gotify", "webhook"]
CHANNEL_LABELS = {
    "email":   "E-Mail",
    "mqtt":    "MQTT Event",
    "ntfy":    "ntfy.sh",
    "gotify":  "Gotify",
    "webhook": "Webhook (HTTP)",
}

_quiet_lock = threading.Lock()


def _is_quiet_time(rule: dict) -> bool:
    """Return True if we're currently in the rule's quiet hours."""
    if not rule.get("quiet_hours_enabled"):
        return False
    now_time = datetime.now().time()
    try:
        qh_start = dtime(*[int(x) for x in rule.get("quiet_hours_start", "22:00").split(":")])
        qh_end   = dtime(*[int(x) for x in rule.get("quiet_hours_end",   "07:00").split(":")])
    except Exception:
        return False
    if qh_start <= qh_end:
        return qh_start <= now_time <= qh_end
    return now_time >= qh_start or now_time <= qh_end


def _rule_matches(rule: dict, event_type: str, context: dict) -> bool:
    if not rule.get("enabled", True):
        return False
    if rule.get("event_type") != event_type:
        return False
    vf = rule.get("vehicle_filter", "all")
    if vf != "all" and context.get("vehicle_id") and context["vehicle_id"] != vf:
        return False
    threshold = rule.get("threshold")
    if threshold is not None:
        val = context.get("threshold_value")
        if val is not None and float(val) >= float(threshold):
            return False
    return True


def _format_message(event_type: str, context: dict) -> dict:
    """Build title+body for a notification."""
    label = EVENT_LABELS_DE.get(event_type, event_type)
    body  = context.get("message", "")
    if not body:
        if event_type in ("charging_started", "charging_stopped"):
            kwh  = context.get("kwh_charged", "?")
            cost = context.get("cost_eur", "?")
            body = f"kWh: {kwh}, Kosten: {cost} €"
        elif event_type in ("report_sent", "report_created"):
            body = f"Zeitraum: {context.get('period_label', '?')}"
        elif event_type == "soc_below_threshold":
            body = f"SOC: {context.get('soc', '?')} %"
        elif event_type in ("backup_success", "backup_failed"):
            body = context.get("details", "")
        else:
            body = str(context)[:200]
    vehicle = context.get("vehicle_name") or context.get("vehicle_id") or ""
    title = f"EV Tracker: {label}" + (f" — {vehicle}" if vehicle else "")
    return {"title": title, "body": body, "event": event_type}


def _send_email(rule: dict, msg: dict, config: dict) -> bool:
    try:
        from services.email_service import _send_email_with_attachments
        subject = msg["title"]
        html = (f'<p style="font-family:sans-serif"><b>{subject}</b></p>'
                f'<p>{msg["body"]}</p>'
                f'<p style="color:#888;font-size:.8rem">EV Tracker</p>')
        recipient = rule.get("recipient", "")
        if not recipient:
            return False
        ok, _ = _send_email_with_attachments(recipient, subject, html)
        return ok
    except Exception as e:
        log.warning("Notification email error: %s", e)
        return False


def _send_ntfy(rule: dict, msg: dict, config: dict) -> bool:
    import requests
    topic = rule.get("recipient", "")
    if not topic:
        return False
    server = config.get("ntfy_server", "https://ntfy.sh")
    url = f"{server.rstrip('/')}/{topic}"
    try:
        headers = {"Title": msg["title"], "Content-Type": "text/plain"}
        token = config.get("ntfy_token", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = requests.post(url, data=msg["body"].encode("utf-8"), headers=headers, timeout=10)
        return r.ok
    except Exception as e:
        log.warning("ntfy error: %s", e)
        return False


def _send_gotify(rule: dict, msg: dict, config: dict) -> bool:
    import requests
    server = config.get("gotify_server", "")
    token  = config.get("gotify_token",  "")
    app_token = rule.get("recipient", "") or token
    if not server or not app_token:
        return False
    url = f"{server.rstrip('/')}/message"
    try:
        r = requests.post(url, json={
            "title": msg["title"], "message": msg["body"], "priority": 5
        }, headers={"X-Gotify-Key": app_token}, timeout=10)
        return r.ok
    except Exception as e:
        log.warning("Gotify error: %s", e)
        return False


def _send_webhook(rule: dict, msg: dict, config: dict) -> bool:
    import requests
    url = rule.get("recipient", "")
    if not url:
        return False
    try:
        r = requests.post(url, json={
            "event": msg["event"], "title": msg["title"],
            "body": msg["body"], "ts": datetime.utcnow().isoformat()
        }, timeout=10)
        return r.ok
    except Exception as e:
        log.warning("Webhook error: %s", e)
        return False


def _send_mqtt_event(rule: dict, msg: dict, config: dict) -> bool:
    try:
        from mqtt_publisher import publish_once
        topic = rule.get("recipient", "") or f"events/{msg['event']}"
        return publish_once(config, topic, {"title": msg["title"], "body": msg["body"], "ts": datetime.utcnow().isoformat()})
    except Exception as e:
        log.warning("MQTT notification error: %s", e)
        return False


_CHANNEL_DISPATCH = {
    "email":   _send_email,
    "ntfy":    _send_ntfy,
    "gotify":  _send_gotify,
    "webhook": _send_webhook,
    "mqtt":    _send_mqtt_event,
}


def send_notification(rule: dict, event_type: str, context: dict, config: dict) -> bool:
    """Dispatch a single notification rule."""
    if _is_quiet_time(rule):
        log.debug("Notification suppressed (quiet hours): %s", event_type)
        return False
    msg = _format_message(event_type, context)
    channel = rule.get("channel", "email")
    fn = _CHANNEL_DISPATCH.get(channel)
    if not fn:
        log.warning("Unbekannter Notification-Kanal: %s", channel)
        return False
    try:
        return fn(rule, msg, config)
    except Exception as e:
        log.warning("Notification dispatch error (%s/%s): %s", channel, event_type, e)
        return False


def fire_event(event_type: str, context: dict, config: dict,
               db_path=None) -> None:
    """
    Fire an event: load all matching rules from DB and dispatch notifications.
    Runs in a background thread to avoid blocking.
    db_path: explicit Path to the SQLite DB (defaults to sessions.db next to data/)
    """
    def _dispatch():
        try:
            import sqlite3
            from pathlib import Path
            _db = db_path
            if _db is None:
                # Fallback: look for sessions.db relative to this file
                _db = Path(__file__).parent.parent / "data" / "sessions.db"
                if not _db.exists():
                    _db = Path(__file__).parent.parent / "data" / "ev_tracker.db"
            if not Path(_db).exists():
                return
            con = sqlite3.connect(str(_db))
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM notification_rules WHERE enabled=1 AND event_type=?",
                (event_type,)
            ).fetchall()
            con.close()
            for row in rows:
                rule = dict(row)
                if _rule_matches(rule, event_type, context):
                    send_notification(rule, event_type, context, config)
        except Exception as e:
            log.warning("fire_event error (%s): %s", event_type, e)

    t = threading.Thread(target=_dispatch, daemon=True)
    t.start()


def test_rule(rule: dict, config: dict) -> dict:
    """Send a test notification for a rule."""
    context = {
        "message": "Dies ist eine Testbenachrichtigung von EV Tracker.",
        "vehicle_name": config.get("car_name", "EV"),
    }
    ok = send_notification(rule, rule.get("event_type", "test"), context, config)
    return {"ok": ok, "message": "Testbenachrichtigung gesendet" if ok else "Fehler beim Senden"}
