"""
Notification rules routes.
"""
from datetime import datetime

from flask import Blueprint, jsonify, request

from core.db import _get_db, close_db_if_owned
from core.config import load_config
from core.security import require_login, has_permission, _current_user, _audit

notifications_bp = Blueprint("notifications", __name__)


@notifications_bp.route("/api/notifications/rules", methods=["GET"])
@require_login
def api_notif_rules_list():
    if not has_permission(_current_user(), "notifications:view"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    con  = _get_db()
    rows = con.execute("SELECT * FROM notification_rules ORDER BY id DESC").fetchall()
    close_db_if_owned(con)
    return jsonify([dict(r) for r in rows])


@notifications_bp.route("/api/notifications/rules", methods=["POST"])
@require_login
def api_notif_rules_create():
    if not has_permission(_current_user(), "notifications:configure"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    data = request.get_json(force=True) or {}
    now_iso = datetime.utcnow().isoformat()
    con = _get_db()
    cur = con.execute("""INSERT INTO notification_rules
        (name,enabled,event_type,channel,vehicle_filter,user_filter,recipient,
         threshold,quiet_hours_enabled,quiet_hours_start,quiet_hours_end,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (data.get("name", "Neue Regel"),
         int(bool(data.get("enabled", True))),
         data.get("event_type", "charging_stopped"),
         data.get("channel", "email"),
         data.get("vehicle_filter", "all"),
         data.get("user_filter", "all"),
         data.get("recipient", ""),
         data.get("threshold"),
         int(bool(data.get("quiet_hours_enabled", False))),
         data.get("quiet_hours_start", "22:00"),
         data.get("quiet_hours_end", "07:00"),
         now_iso, now_iso))
    rule_id = cur.lastrowid
    con.commit()
    close_db_if_owned(con)
    _audit("notification_rule_created", f"id={rule_id}", ip=request.remote_addr)
    return jsonify({"ok": True, "id": rule_id})


@notifications_bp.route("/api/notifications/rules/<int:rule_id>", methods=["PUT"])
@require_login
def api_notif_rules_update(rule_id):
    if not has_permission(_current_user(), "notifications:configure"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    data = request.get_json(force=True) or {}
    now_iso = datetime.utcnow().isoformat()
    allowed = {"name", "enabled", "event_type", "channel", "vehicle_filter", "user_filter",
               "recipient", "threshold", "quiet_hours_enabled", "quiet_hours_start", "quiet_hours_end"}
    updates = {k: v for k, v in data.items() if k in allowed}
    updates["updated_at"] = now_iso
    con = _get_db()
    if not con.execute("SELECT 1 FROM notification_rules WHERE id=?", (rule_id,)).fetchone():
        close_db_if_owned(con)
        return jsonify({"error": "Regel nicht gefunden"}), 404
    sets = ", ".join(f"{k}=?" for k in updates)
    con.execute(f"UPDATE notification_rules SET {sets} WHERE id=?",
                list(updates.values()) + [rule_id])
    con.commit()
    close_db_if_owned(con)
    return jsonify({"ok": True})


@notifications_bp.route("/api/notifications/rules/<int:rule_id>", methods=["DELETE"])
@require_login
def api_notif_rules_delete(rule_id):
    if not has_permission(_current_user(), "notifications:configure"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    con = _get_db()
    con.execute("DELETE FROM notification_rules WHERE id=?", (rule_id,))
    con.commit()
    close_db_if_owned(con)
    return jsonify({"ok": True})


@notifications_bp.route("/api/notifications/test/<int:rule_id>", methods=["POST"])
@require_login
def api_notif_test(rule_id):
    if not has_permission(_current_user(), "notifications:test"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    con = _get_db()
    row = con.execute("SELECT * FROM notification_rules WHERE id=?", (rule_id,)).fetchone()
    close_db_if_owned(con)
    if not row:
        return jsonify({"error": "Regel nicht gefunden"}), 404
    cfg = load_config()
    try:
        from notification_manager import test_rule
        result = test_rule(dict(row), cfg)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})


@notifications_bp.route("/api/notifications/events")
@require_login
def api_notif_events():
    if not has_permission(_current_user(), "notifications:view"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    try:
        from notification_manager import EVENT_TYPES, EVENT_LABELS_DE, CHANNEL_TYPES, CHANNEL_LABELS
        return jsonify({
            "events": [{"key": e, "label": EVENT_LABELS_DE.get(e, e)} for e in EVENT_TYPES],
            "channels": [{"key": c, "label": CHANNEL_LABELS.get(c, c)} for c in CHANNEL_TYPES],
        })
    except ImportError:
        return jsonify({"events": [], "channels": []})
