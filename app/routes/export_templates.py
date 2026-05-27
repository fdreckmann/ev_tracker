"""
Export template management routes.
"""
import secrets

from flask import Blueprint, jsonify, request

from core.config import load_config, save_config
from core.security import require_login, has_permission, _current_user, _audit

export_templates_bp = Blueprint("export_templates", __name__)


@export_templates_bp.route("/api/export/templates", methods=["GET"])
@require_login
def get_export_templates():
    if not has_permission(_current_user(), "export:templates_view"):
        return jsonify({"error": "Keine Berechtigung: export:templates_view"}), 403
    cfg = load_config()
    return jsonify(cfg.get("export_templates", []))

@export_templates_bp.route("/api/export/templates", methods=["POST"])
@require_login
def create_export_template():
    if not has_permission(_current_user(), "export:templates_manage"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: export:templates_manage"}), 403
    data = request.get_json(silent=True) or {}
    cfg  = load_config()
    templates = cfg.get("export_templates", [])
    tid = secrets.token_hex(6)
    # Support both old "mapping" and new "column_mapping" field names
    _col_map = data.get("column_mapping") or data.get("mapping") or {}
    tpl = {
        "id":               tid,
        "name":             str(data.get("name", "Neue Vorlage"))[:128],
        "mapping":          _col_map,          # legacy compat
        "column_mapping":   _col_map,
        "cell_mapping":     data.get("cell_mapping") or {},
        "signature_mapping":data.get("signature_mapping") or {},
        "start_row":        data.get("start_row"),
        "header_row":       data.get("header_row"),
        "sheet":            data.get("sheet"),
        "include_signature":bool(data.get("include_signature", False)),
        "mapping_version":  2,
        "is_default":       bool(data.get("is_default", False)),
    }
    if tpl["is_default"]:
        for t in templates: t["is_default"] = False
    templates.append(tpl)
    cfg["export_templates"] = templates
    save_config(cfg)
    _audit("export_template_create", f"name={tpl['name']}", ip=request.remote_addr)
    return jsonify({"ok": True, "template": tpl})

@export_templates_bp.route("/api/export/templates/<tid>", methods=["PUT"])
@require_login
def update_export_template(tid):
    if not has_permission(_current_user(), "export:templates_manage"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: export:templates_manage"}), 403
    data = request.get_json(silent=True) or {}
    cfg  = load_config()
    templates = cfg.get("export_templates", [])
    tpl = next((t for t in templates if t["id"]==tid), None)
    if not tpl:
        return jsonify({"ok": False, "error": "Nicht gefunden"}), 404
    if data.get("is_default"):
        for t in templates: t["is_default"] = False
    # Keep legacy "mapping" in sync with "column_mapping"
    if "column_mapping" in data and "mapping" not in data:
        data = {**data, "mapping": data["column_mapping"]}
    elif "mapping" in data and "column_mapping" not in data:
        data = {**data, "column_mapping": data["mapping"]}
    tpl.update({k: v for k, v in data.items() if k != "id"})
    cfg["export_templates"] = templates
    save_config(cfg)
    _audit("export_template_update", f"id={tid} name={tpl['name']}", ip=request.remote_addr)
    return jsonify({"ok": True, "template": tpl})

@export_templates_bp.route("/api/export/templates/<tid>", methods=["DELETE"])
@require_login
def delete_export_template(tid):
    if not has_permission(_current_user(), "export:templates_manage"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: export:templates_manage"}), 403
    cfg  = load_config()
    templates = cfg.get("export_templates", [])
    cfg["export_templates"] = [t for t in templates if t["id"] != tid]
    save_config(cfg)
    _audit("export_template_delete", f"id={tid}", ip=request.remote_addr)
    return jsonify({"ok": True})
