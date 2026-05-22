"""
Backup management routes.
"""
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, send_file

from core.db import _get_db, close_db_if_owned
from core.config import load_config, save_config
from core.security import require_login, has_permission, _current_user, _audit
from services.backup_service import (
    create_backup, restore_backup, get_backup_dir, parse_cron_next,
    schedule_backup, get_max_upload_bytes, get_backup_timer,
)

backup_bp = Blueprint("backup", __name__)


@backup_bp.route("/api/backup/list")
@require_login
def api_backup_list():
    if not has_permission(_current_user(), "backup:view"):
        return jsonify({"error": "Keine Berechtigung: backup:view"}), 403
    BACKUP_DIR = get_backup_dir()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = [{"name": f.name, "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")}
               for f in sorted(BACKUP_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)]
    cfg = load_config(); cron = cfg.get("backup_cron", "")
    next_backup = None
    if cron:
        secs = parse_cron_next(cron)
        if secs: next_backup = (datetime.now() + timedelta(seconds=secs)).strftime("%d.%m.%Y %H:%M")
    return jsonify({"backups": backups, "next_backup": next_backup, "cron": cron})


@backup_bp.route("/api/backup/create", methods=["POST"])
@require_login
def api_backup_create():
    if not has_permission(_current_user(), "backup:create"):
        return jsonify({"error": "Keine Berechtigung: backup:create"}), 403
    try:
        out = create_backup("manual")
        return jsonify({"ok": True, "name": out.name, "size": out.stat().st_size})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@backup_bp.route("/api/backup/download/<filename>")
@require_login
def api_backup_download(filename):
    if not has_permission(_current_user(), "backup:download"):
        return jsonify({"error": "Keine Berechtigung: backup:download"}), 403
    if ".." in filename or "/" in filename:
        return jsonify({"error": "ungültig"}), 400
    BACKUP_DIR = get_backup_dir()
    path = BACKUP_DIR / filename
    if not path.exists():
        return jsonify({"error": "nicht gefunden"}), 404
    return send_file(path, as_attachment=True)


@backup_bp.route("/api/backup/restore", methods=["POST"])
@require_login
def api_backup_restore():
    user = _current_user()
    if not has_permission(user, "backup:restore"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: backup:restore"}), 403
    name = (request.json or {}).get("name", "")
    if ".." in name or "/" in name:
        return jsonify({"ok": False, "error": "ungültig"}), 400
    BACKUP_DIR = get_backup_dir()
    path = BACKUP_DIR / name
    if not path.exists():
        return jsonify({"ok": False, "error": "nicht gefunden"}), 404
    try:
        restore_backup(path)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@backup_bp.route("/api/backup/upload", methods=["POST"])
@require_login
def api_backup_upload():
    if not has_permission(_current_user(), "backup:restore"):
        return jsonify({"error": "Keine Berechtigung: backup:restore"}), 403
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Keine Datei"}), 400
    f = request.files["file"]
    if not f.filename.endswith(".zip"):
        return jsonify({"ok": False, "error": "Nur .zip"}), 400
    BACKUP_DIR = get_backup_dir()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = BACKUP_DIR / f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    max_bytes = get_max_upload_bytes()
    total = 0
    with open(tmp, "wb") as out_f:
        for chunk in f.stream:
            total += len(chunk)
            if total > max_bytes:
                out_f.close(); tmp.unlink(missing_ok=True)
                return jsonify({"ok": False, "error": f"Datei zu groß (max. {max_bytes // 1024 // 1024} MB)"}), 413
            out_f.write(chunk)
    try:
        restore_backup(tmp)
        _audit("backup_restored", f"file={tmp.name}", ip=request.remote_addr)
        return jsonify({"ok": True, "restored": tmp.name})
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": str(e)})


@backup_bp.route("/api/backup/cron", methods=["POST"])
@require_login
def api_backup_cron():
    if not has_permission(_current_user(), "backup:create"):
        return jsonify({"error": "Keine Berechtigung: backup:create"}), 403
    cron = (request.json or {}).get("cron", "").strip()
    cfg = load_config(); cfg["backup_cron"] = cron; save_config(cfg)
    timer = get_backup_timer()
    if timer:
        timer.cancel()
    if cron:
        schedule_backup()
        secs = parse_cron_next(cron)
        nxt = (datetime.now() + timedelta(seconds=secs)).strftime("%d.%m.%Y %H:%M") if secs else "?"
        return jsonify({"ok": True, "next": nxt})
    return jsonify({"ok": True, "next": None})


@backup_bp.route("/api/backup/delete/<filename>", methods=["DELETE"])
@require_login
def api_backup_delete(filename):
    if not has_permission(_current_user(), "backup:delete"):
        return jsonify({"error": "Keine Berechtigung: backup:delete"}), 403
    if ".." in filename or "/" in filename:
        return jsonify({"ok": False}), 400
    BACKUP_DIR = get_backup_dir()
    path = BACKUP_DIR / filename
    if path.exists():
        path.unlink()
        _audit("backup_deleted", f"file={filename}", ip=request.remote_addr)
    return jsonify({"ok": True})
