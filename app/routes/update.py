"""
Docker update management routes.
"""
from flask import Blueprint, jsonify, request

from core.config import load_config
from core.security import require_login, has_permission, _current_user

update_bp = Blueprint("update", __name__)


@update_bp.route("/api/update/check")
def api_update_check():
    from server import get_update_info, fetch_remote_version, docker_pull_and_restart
    info = get_update_info()
    if info.get("ok") and not info.get("up_to_date"):
        tag = info.get("tag", "latest")
        remote_ver = fetch_remote_version(tag)
        info["remote_version"] = remote_ver.get("version", "")
        info["remote_changelog"] = remote_ver.get("changelog", [])
    return jsonify(info)

@update_bp.route("/api/update/pull", methods=["POST"])
@require_login
def api_update_pull():
    from server import get_update_info, fetch_remote_version, docker_pull_and_restart
    user = _current_user()
    if not has_permission(user, "updates:start"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: updates:start"}), 403
    cfg = load_config()
    tag = cfg.get("update_channel","latest")
    ok, msg = docker_pull_and_restart(tag)
    return jsonify({"ok": ok, "output": msg, "restarting": ok})

@update_bp.route("/api/update/log")
def api_update_log():
    import server as _srv
    return jsonify({"running": _srv._update_running, "lines": list(_srv._update_log)})
