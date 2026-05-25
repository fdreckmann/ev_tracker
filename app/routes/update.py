"""
Read-only update information route.

This endpoint only reads version metadata — it cannot install updates,
restart containers, or access the Docker socket.
"""
from flask import Blueprint, jsonify

from core.security import require_login
from services.update_service import get_update_info

update_bp = Blueprint("update", __name__)


@update_bp.route("/api/update-info", methods=["GET"])
@require_login
def api_update_info():
    """Return current version and available remote update metadata (read-only)."""
    return jsonify(get_update_info())
