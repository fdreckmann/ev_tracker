"""
Read-only update information routes.

These endpoints only read version metadata — they cannot install updates,
restart containers, or access the Docker socket.

Legacy compat routes (/api/update/check, /api/update/log, /api/update/pull)
are kept to avoid 404s on older installations that may poll these URLs.
/api/update/pull returns 410 Gone — in-app updates have been removed;
use docker compose pull / container update instead.
"""
from flask import Blueprint, jsonify, request, make_response

from core.security import require_login, has_permission, _current_user
from services.update_service import get_update_info

update_bp = Blueprint("update", __name__)


@update_bp.route("/api/update-info", methods=["GET"])
@require_login
def api_update_info():
    """Return current version and available remote update metadata (read-only).

    Query params:
      force=1  — bypass server-side cache and re-fetch from remote
    """
    user = _current_user()
    if not has_permission(user, "updates:view") and user.get("role") != "admin":
        return jsonify({"error": "Keine Berechtigung: updates:view"}), 403
    force = request.args.get("force") in ("1", "true", "yes")
    data = get_update_info(force=force)
    resp = make_response(jsonify(data))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@update_bp.route("/api/update/check", methods=["GET"])
@require_login
def api_update_check():
    """Legacy alias for /api/update-info — kept for backward compatibility."""
    return api_update_info()


@update_bp.route("/api/update/log", methods=["GET"])
@require_login
def api_update_log():
    """Legacy update log endpoint — in-app updates removed.

    Returns an empty log list so older UIs don't crash.
    """
    user = _current_user()
    if not has_permission(user, "updates:view") and user.get("role") != "admin":
        return jsonify({"error": "Keine Berechtigung: updates:view"}), 403
    return jsonify({"ok": True, "log": [], "deprecated": True,
                    "message": "In-App-Update entfernt. Bitte Docker Pull / Container-Update verwenden."})


@update_bp.route("/api/update/pull", methods=["POST"])
@require_login
def api_update_pull():
    """Legacy in-app update endpoint — removed in v2.0.

    Returns 410 Gone with a clear message directing users to the correct
    update mechanism (docker compose pull).
    """
    return jsonify({
        "ok": False,
        "error": "In-App-Update entfernt. Bitte Docker Pull / Container-Update verwenden.",
        "instructions": ["docker compose pull", "docker compose up -d"],
    }), 410
