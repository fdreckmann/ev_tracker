"""
Admin dashboard and audit log routes.
"""
import sqlite3
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from core.db import _get_db, close_db_if_owned, DB_PATH, DATA_DIR
from core.config import load_config
from core.security import require_login, has_permission, _current_user, _audit, require_admin

audit_routes_bp = Blueprint("audit_routes", __name__)


@audit_routes_bp.route("/api/admin/dashboard")
@require_admin
def api_admin_dashboard():
    con = _get_db()
    total   = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active  = con.execute("SELECT COUNT(*) FROM users WHERE status='active'").fetchone()[0]
    invited = con.execute("SELECT COUNT(*) FROM users WHERE status='invited'").fetchone()[0]
    locked  = con.execute("SELECT COUNT(*) FROM users WHERE locked_until IS NOT NULL AND locked_until > ?",
                          (datetime.utcnow().isoformat(),)).fetchone()[0]
    since24 = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    failures = con.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='login_failed' AND ts > ?", (since24,)).fetchone()[0]
    lockouts = con.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='account_locked' AND ts > ?", (since24,)).fetchone()[0]
    close_db_if_owned(con)
    return jsonify({
        "total_users":    total,
        "active_users":   active,
        "invited_users":  invited,
        "locked_users":   locked,
        "recent_failures": failures,
        "recent_lockouts": lockouts,
    })

@audit_routes_bp.route("/api/audit-log")
@require_login
def get_audit_log():
    if not has_permission(_current_user(), "audit:view"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: audit:view"}), 403
    try: limit = int(request.args.get("limit", 200))
    except (ValueError, TypeError): limit = 200
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT a.*, u.name as user_name, u.email as user_email
        FROM audit_log a
        LEFT JOIN users u ON a.user_id = u.id
        ORDER BY a.id DESC LIMIT ?
    """, (limit,)).fetchall()
    close_db_if_owned(con)
    return jsonify([dict(r) for r in rows])
