"""
Central notification service.

Usage:
    from services.notification_service import notify

    notify(
        type="missing_charge_candidate_created",
        severity="warning",
        title="Möglicher fehlender Ladevorgang",
        message="SOC stieg von 32% auf 76%. Geschätzte Ladung: 37,8 kWh.",
        vehicle_id=vid,
        data={"candidate_id": cid, ...},
        dedupe_key=f"missing_charge:{vid}:{start_ts}:{end_ts}",
        action_url="/",
    )
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, time as dtime

log = logging.getLogger(__name__)

# Severity ordering
_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


def _is_quiet_time(cfg: dict, severity: str) -> bool:
    """Return True if now is in quiet hours and we should suppress this notification."""
    if not cfg.get("notification_quiet_hours_enabled", False):
        return False
    # Critical notifications bypass quiet hours unless explicitly blocked
    if severity == "critical" and cfg.get("notification_quiet_hours_allow_critical", True):
        return False
    try:
        qs = cfg.get("notification_quiet_hours_start", "22:00")
        qe = cfg.get("notification_quiet_hours_end", "07:00")
        start = dtime(*[int(x) for x in qs.split(":")])
        end   = dtime(*[int(x) for x in qe.split(":")])
        now   = datetime.now().time()
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end
    except Exception:
        return False


def _is_dedupe_blocked(con, dedupe_key: str, window_hours: float) -> bool:
    """Return True if an identical dedupe_key was already sent within window_hours."""
    if not dedupe_key:
        return False
    cutoff_ts = time.time() - window_hours * 3600
    cutoff_iso = datetime.utcfromtimestamp(cutoff_ts).isoformat(timespec="seconds")
    row = con.execute(
        "SELECT id FROM notifications WHERE dedupe_key=? AND status IN ('sent','pending') AND created_at > ?",
        (dedupe_key, cutoff_iso)
    ).fetchone()
    return row is not None


def _is_rate_limited(con, max_per_hour: int) -> bool:
    """Return True if we've exceeded the global rate limit for sent notifications."""
    if max_per_hour <= 0:
        return False
    cutoff_iso = datetime.utcfromtimestamp(time.time() - 3600).isoformat(timespec="seconds")
    row = con.execute(
        "SELECT COUNT(*) FROM notifications WHERE status='sent' AND sent_at > ?",
        (cutoff_iso,)
    ).fetchone()
    return (row[0] if row else 0) >= max_per_hour


def _below_min_severity(cfg: dict, severity: str) -> bool:
    """Return True if this severity is below the configured minimum."""
    min_sev = cfg.get("notification_min_severity", "info")
    return _SEVERITY_ORDER.get(severity, 0) < _SEVERITY_ORDER.get(min_sev, 0)


def _event_type_enabled(cfg: dict, event_type: str) -> bool:
    """Check per-event-type enabled flag. Defaults to True if not configured."""
    key = f"notification_event_{event_type}"
    return bool(cfg.get(key, True))


def notify(
    type: str,
    severity: str,
    title: str,
    message: str,
    vehicle_id: str | None = None,
    data: dict | None = None,
    dedupe_key: str | None = None,
    action_url: str | None = None,
    action_payload: dict | None = None,
    _background: bool = True,
) -> None:
    """
    Create and dispatch a notification.

    Runs in a background thread by default to avoid blocking the caller.
    Set _background=False for synchronous use (tests).
    """
    def _run():
        try:
            _notify_sync(type, severity, title, message, vehicle_id, data,
                         dedupe_key, action_url, action_payload)
        except Exception as exc:
            log.warning("notify() error [%s]: %s", type, exc)

    if _background:
        threading.Thread(target=_run, daemon=True).start()
    else:
        _run()


def _notify_sync(
    type: str,
    severity: str,
    title: str,
    message: str,
    vehicle_id: str | None,
    data: dict | None,
    dedupe_key: str | None,
    action_url: str | None,
    action_payload: dict | None,
) -> None:
    from core.db import _get_db, close_db_if_owned
    from core.config import load_config

    cfg = load_config()
    con = _get_db()
    now_iso = datetime.utcnow().isoformat(timespec="seconds")

    try:
        # Per-event-type kill switch
        if not _event_type_enabled(cfg, type):
            log.debug("notify: event type %s disabled, skipping", type)
            return

        # Severity filter
        if _below_min_severity(cfg, severity):
            log.debug("notify: severity %s below minimum, skipping", severity)
            return

        # Dedupe check
        dedupe_window = float(cfg.get("notification_dedupe_window_hours", 6))
        if _is_dedupe_blocked(con, dedupe_key, dedupe_window):
            log.debug("notify: dedupe blocked [%s]", dedupe_key)
            return

        # Rate limit check
        rate_limit = int(cfg.get("notification_rate_limit_per_hour", 20))
        if _is_rate_limited(con, rate_limit):
            log.warning("notify: rate limit reached, dropping notification [%s]", type)
            return

        # Insert into DB
        data_str = json.dumps(data, default=str) if data else None
        ap_str   = json.dumps(action_payload, default=str) if action_payload else None
        cur = con.execute(
            """INSERT INTO notifications
               (type, severity, vehicle_id, title, message, data_json, dedupe_key,
                status, created_at, action_url, action_payload)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (type, severity, vehicle_id, title, message, data_str, dedupe_key,
             "pending", now_iso, action_url, ap_str)
        )
        notif_id = cur.lastrowid
        con.commit()

        # Quiet hours → keep as pending in inbox but don't push
        quiet = _is_quiet_time(cfg, severity)

        # Dispatch to channels
        channel_results = {}
        if not quiet:
            channel_results = _dispatch_channels(cfg, type, severity, title, message,
                                                  vehicle_id, data or {}, action_url)

        sent_any = any(v for v in channel_results.values())
        now_sent = datetime.utcnow().isoformat(timespec="seconds")
        status = "sent" if (sent_any or not channel_results) else "failed"
        if quiet:
            status = "pending"  # kept in inbox, not sent

        con.execute(
            "UPDATE notifications SET status=?, sent_at=?, channel_results_json=? WHERE id=?",
            (status, now_sent if not quiet else None,
             json.dumps(channel_results), notif_id)
        )
        con.commit()

    finally:
        close_db_if_owned(con)


def _dispatch_channels(
    cfg: dict,
    event_type: str,
    severity: str,
    title: str,
    message: str,
    vehicle_id: str | None,
    data: dict,
    action_url: str | None,
) -> dict[str, bool]:
    """Send to all enabled channels. Returns {channel: success}."""
    results: dict[str, bool] = {}

    # ── Home Assistant Notify ──────────────────────────────────────────────
    if cfg.get("notification_ha_enabled", False):
        results["ha"] = _send_ha(cfg, title, message, data, action_url)

    # ── ntfy ──────────────────────────────────────────────────────────────
    if cfg.get("notification_ntfy_enabled", False):
        results["ntfy"] = _send_ntfy(cfg, title, message, severity, action_url)

    # ── Gotify ────────────────────────────────────────────────────────────
    if cfg.get("notification_gotify_enabled", False):
        results["gotify"] = _send_gotify(cfg, title, message, severity)

    # ── Telegram ──────────────────────────────────────────────────────────
    if cfg.get("notification_telegram_enabled", False):
        results["telegram"] = _send_telegram(cfg, title, message)

    # ── E-Mail ────────────────────────────────────────────────────────────
    if cfg.get("notification_email_enabled", False):
        results["email"] = _send_email(cfg, title, message)

    return results


def _send_ha(cfg: dict, title: str, message: str, data: dict, action_url: str | None) -> bool:
    import requests
    ha_url = cfg.get("ha_url", "").rstrip("/")
    service = cfg.get("notification_ha_service", "") or cfg.get("notify_service", "")
    service = service.strip()
    if not ha_url or not service:
        return False
    token = cfg.get("ha_token", "")
    if not token:
        return False
    # service may be "notify.mobile_app_xyz" or just "mobile_app_xyz"
    if "." in service:
        svc_path = service.replace(".", "/", 1)
    else:
        svc_path = f"notify/{service}"
    url = f"{ha_url}/api/services/{svc_path}"
    payload: dict = {"title": title, "message": message}
    ha_data: dict = {}
    tag = data.get("dedupe_key") or data.get("candidate_id") or data.get("type", "ev_tracker")
    ha_data["tag"] = str(tag)[:64]
    if action_url:
        ha_data["url"] = action_url
        ha_data["clickAction"] = action_url
    target = cfg.get("notification_ha_target", "")
    if target:
        payload["target"] = target
    if ha_data:
        payload["data"] = ha_data
    try:
        r = requests.post(url, json=payload,
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json"},
                          timeout=10)
        return r.ok
    except Exception as e:
        log.warning("HA notify error: %s", e)
        return False


_NTFY_PRIORITY = {"info": "default", "warning": "high", "critical": "urgent"}

def _send_ntfy(cfg: dict, title: str, message: str, severity: str, action_url: str | None) -> bool:
    import requests
    ntfy_url = cfg.get("notification_ntfy_url", "https://ntfy.sh").rstrip("/")
    topic    = cfg.get("notification_ntfy_topic", "").strip()
    if not topic:
        return False
    url = f"{ntfy_url}/{topic}"
    headers = {
        "Title":    title[:250],
        "Priority": cfg.get("notification_ntfy_priority") or _NTFY_PRIORITY.get(severity, "default"),
        "Content-Type": "text/plain",
    }
    token = cfg.get("notification_ntfy_token", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if action_url:
        headers["Click"] = action_url
    try:
        r = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
        return r.ok
    except Exception as e:
        log.warning("ntfy error: %s", e)
        return False


def _send_gotify(cfg: dict, title: str, message: str, severity: str) -> bool:
    import requests
    server = cfg.get("notification_gotify_url", "").rstrip("/")
    token  = cfg.get("notification_gotify_token", "")
    if not server or not token:
        return False
    priority_map = {"info": 3, "warning": 7, "critical": 10}
    try:
        r = requests.post(f"{server}/message", json={
            "title": title, "message": message,
            "priority": priority_map.get(severity, 3),
        }, headers={"X-Gotify-Key": token}, timeout=10)
        return r.ok
    except Exception as e:
        log.warning("Gotify error: %s", e)
        return False


def _send_telegram(cfg: dict, title: str, message: str) -> bool:
    import requests
    bot_token = cfg.get("notification_telegram_bot_token", "")
    chat_id   = cfg.get("notification_telegram_chat_id", "")
    if not bot_token or not chat_id:
        return False
    text = f"*{title}*\n{message}"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        return r.ok
    except Exception as e:
        log.warning("Telegram error: %s", e)
        return False


def _send_email(cfg: dict, title: str, message: str) -> bool:
    email_to = cfg.get("notification_email_to", "") or cfg.get("smtp_from_email", "")
    if not email_to:
        return False
    try:
        from services.email_service import _send_email_with_attachments
        html = (f'<p style="font-family:sans-serif"><b>{title}</b></p>'
                f'<p>{message}</p>'
                f'<p style="color:#888;font-size:.8rem">EV Tracker</p>')
        ok, _ = _send_email_with_attachments(email_to, title, html)
        return ok
    except Exception as e:
        log.warning("Notification email error: %s", e)
        return False


def send_test(channel: str, cfg: dict) -> dict:
    """Send a test notification on a specific channel. Returns {ok, message}."""
    title   = "EV Tracker: Testbenachrichtigung"
    message = "Dies ist eine Testbenachrichtigung von EV Tracker."
    ok = False
    if channel == "ha":
        ok = _send_ha(cfg, title, message, {}, None)
    elif channel == "ntfy":
        ok = _send_ntfy(cfg, title, message, "info", None)
    elif channel == "gotify":
        ok = _send_gotify(cfg, title, message, "info")
    elif channel == "telegram":
        ok = _send_telegram(cfg, title, message)
    elif channel == "email":
        ok = _send_email(cfg, title, message)
    else:
        return {"ok": False, "message": f"Unbekannter Kanal: {channel}"}
    return {"ok": ok, "message": "Gesendet" if ok else "Fehler beim Senden"}
