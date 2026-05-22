"""
Export template management routes.
"""
import secrets
from datetime import datetime

from flask import Blueprint, jsonify, request

from core.db import _get_db, close_db_if_owned
from core.config import load_config, save_config
from core.security import require_login, has_permission, _current_user, _audit

export_templates_bp = Blueprint("export_templates", __name__)


@export_templates_bp.route("/api/export/templates", methods=["GET"])
def get_export_templates():
    cfg = load_config()
    return jsonify(cfg.get("export_templates", []))

@export_templates_bp.route("/api/export/templates", methods=["POST"])
def create_export_template():
    data = request.json or {}
    cfg  = load_config()
    templates = cfg.get("export_templates", [])
    tid = secrets.token_hex(6)
    tpl = {
        "id":         tid,
        "name":       data.get("name", "Neue Vorlage"),
        "mapping":    data.get("mapping", {}),
        "start_row":  data.get("start_row"),
        "is_default": data.get("is_default", False),
    }
    if tpl["is_default"]:
        for t in templates: t["is_default"] = False
    templates.append(tpl)
    cfg["export_templates"] = templates
    save_config(cfg)
    _audit("export_template_create", f"name={tpl['name']}")
    return jsonify({"ok": True, "template": tpl})

@export_templates_bp.route("/api/export/templates/<tid>", methods=["PUT"])
def update_export_template(tid):
    data = request.json or {}
    cfg  = load_config()
    templates = cfg.get("export_templates", [])
    tpl = next((t for t in templates if t["id"]==tid), None)
    if not tpl:
        return jsonify({"ok": False, "error": "Nicht gefunden"}), 404
    if data.get("is_default"):
        for t in templates: t["is_default"] = False
    tpl.update({k:v for k,v in data.items() if k != "id"})
    cfg["export_templates"] = templates
    save_config(cfg)
    _audit("export_template_update", f"id={tid} name={tpl['name']}")
    return jsonify({"ok": True, "template": tpl})

@export_templates_bp.route("/api/export/templates/<tid>", methods=["DELETE"])
def delete_export_template(tid):
    cfg  = load_config()
    templates = cfg.get("export_templates", [])
    cfg["export_templates"] = [t for t in templates if t["id"]!=tid]
    save_config(cfg)
    _audit("export_template_delete", f"id={tid}")
    return jsonify({"ok": True})
