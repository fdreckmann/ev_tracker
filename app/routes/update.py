"""
Read-only update information route.

This endpoint only reads version metadata — it cannot install updates,
restart containers, or access the Docker socket.
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
