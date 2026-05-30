"""
Admin routes: permissions, roles, user-role assignments, current user permissions.
"""
import re
from datetime import datetime, timezone


from flask import Blueprint, jsonify, request

from core.db import _get_db, close_db_if_owned
from core.security import (
    require_login, has_permission, _current_user, _audit,
    ALL_PERMISSIONS, _get_user_permissions,
)

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/api/me/permissions")
@require_login
def api_me_permissions():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "Nicht angemeldet"}), 401
    con = _get_db()
    roles = con.execute("""
        SELECT r.name FROM user_roles ur JOIN roles r ON ur.role_id = r.id
        WHERE ur.user_id = ?
    """, (user["id"],)).fetchall()
    close_db_if_owned(con)
    perms = _get_user_permissions(user["id"])
    if "admin:all" in perms:
        perms = set(ALL_PERMISSIONS.keys())
    return jsonify({
        "ok": True,
        "user": {"id": user["id"], "email": user.get("email", ""), "name": user.get("name", ""),
                 "roles": [r["name"] for r in roles]},
        "permissions": sorted(perms),
    })


@admin_bp.route("/api/admin/permissions")
@require_login
def api_admin_permissions_list():
    user = _current_user()
    if not user or not has_permission(user, "users:manage_permissions"):
        return jsonify({"ok": False, "error": "Keine Berechtigung"}), 403
    groups = {}
    for key, meta in ALL_PERMISSIONS.items():
        g = meta["group"]
        if g not in groups:
            groups[g] = []
        groups[g].append({"key": key, "label": meta["label"]})
    return jsonify({"ok": True, "groups": [{"name": g, "permissions": p} for g, p in groups.items()]})


@admin_bp.route("/api/admin/roles")
@require_login
def api_admin_roles_list():
    user = _current_user()
    if not user or not has_permission(user, "users:manage_permissions"):
        return jsonify({"ok": False, "error": "Keine Berechtigung"}), 403
    con = _get_db()
    roles = [dict(r) for r in con.execute("SELECT * FROM roles ORDER BY is_system DESC, name").fetchall()]
    for role in roles:
        perms = [r["permission_key"] for r in
            con.execute("SELECT permission_key FROM role_permissions WHERE role_id=?",
                       (role["id"],)).fetchall()]
        role["permissions"] = perms
        role["user_count"] = con.execute(
            "SELECT COUNT(*) as c FROM user_roles WHERE role_id=?", (role["id"],)
        ).fetchone()["c"]
    close_db_if_owned(con)
    return jsonify({"ok": True, "roles": roles})


@admin_bp.route("/api/admin/roles", methods=["POST"])
@require_login
def api_admin_roles_create():
    user = _current_user()
    if not user or not has_permission(user, "users:manage_permissions"):
        return jsonify({"ok": False, "error": "Keine Berechtigung"}), 403
    body = request.get_json(force=True) or {}
    name = (body.get("name") or "").strip()
    if not name or len(name) < 2:
        return jsonify({"ok": False, "error": "Rollenname zu kurz"}), 400
    if not re.match(r'^[a-zA-Z0-9_\-äöüÄÖÜß ]+$', name):
        return jsonify({"ok": False, "error": "Ungültiger Rollenname"}), 400
    desc  = (body.get("description") or "").strip()[:200]
    perms = [p for p in (body.get("permissions") or []) if p in ALL_PERMISSIONS]
    now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    con = _get_db()
    try:
        cur = con.execute(
            "INSERT INTO roles (name, description, is_system, created_at, updated_at) VALUES (?,?,0,?,?)",
            (name, desc, now_iso, now_iso))
        role_id = cur.lastrowid
        for pkey in perms:
            con.execute("INSERT INTO role_permissions (role_id, permission_key) VALUES (?,?)",
                        (role_id, pkey))
        con.commit()
        _audit("role_created", f"name={name} perms={len(perms)}", ip=request.remote_addr)
        return jsonify({"ok": True, "role_id": role_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        close_db_if_owned(con)


@admin_bp.route("/api/admin/roles/<int:role_id>", methods=["PUT"])
@require_login
def api_admin_roles_update(role_id):
    user = _current_user()
    if not user or not has_permission(user, "users:manage_permissions"):
        return jsonify({"ok": False, "error": "Keine Berechtigung"}), 403
    body = request.get_json(force=True) or {}
    con  = _get_db()
    role = con.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if not role:
        close_db_if_owned(con)
        return jsonify({"ok": False, "error": "Rolle nicht gefunden"}), 404
    role = dict(role)
    name  = (body.get("name") or role["name"]).strip()
    desc  = (body.get("description") or role["description"] or "").strip()[:200]
    perms = [p for p in (body.get("permissions") or []) if p in ALL_PERMISSIONS]
    now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    try:
        con.execute("UPDATE roles SET name=?, description=?, updated_at=? WHERE id=?",
                    (name, desc, now_iso, role_id))
        con.execute("DELETE FROM role_permissions WHERE role_id=?", (role_id,))
        for pkey in perms:
            con.execute("INSERT INTO role_permissions (role_id, permission_key) VALUES (?,?)",
                        (role_id, pkey))
        con.commit()
        _audit("role_updated", f"id={role_id} name={name} perms={len(perms)}", ip=request.remote_addr)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        close_db_if_owned(con)


@admin_bp.route("/api/admin/roles/<int:role_id>", methods=["DELETE"])
@require_login
def api_admin_roles_delete(role_id):
    user = _current_user()
    if not user or not has_permission(user, "users:manage_permissions"):
        return jsonify({"ok": False, "error": "Keine Berechtigung"}), 403
    con  = _get_db()
    role = con.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if not role:
        close_db_if_owned(con)
        return jsonify({"ok": False, "error": "Rolle nicht gefunden"}), 404
    role = dict(role)
    if role["is_system"]:
        close_db_if_owned(con)
        return jsonify({"ok": False, "error": "Systemrolle kann nicht gelöscht werden"}), 400
    admin_role = con.execute("SELECT id FROM roles WHERE name='admin'").fetchone()
    if admin_role:
        remaining_admins = con.execute("""
            SELECT COUNT(*) as c FROM user_roles ur
            JOIN role_permissions rp ON ur.role_id = rp.role_id
            WHERE rp.permission_key = 'admin:all' AND ur.role_id != ?
        """, (role_id,)).fetchone()["c"]
        if remaining_admins == 0 and role.get("name") == "admin":
            close_db_if_owned(con)
            return jsonify({"ok": False, "error": "Mindestens eine Admin-Rolle muss erhalten bleiben"}), 400
    con.execute("DELETE FROM role_permissions WHERE role_id=?", (role_id,))
    con.execute("DELETE FROM user_roles WHERE role_id=?", (role_id,))
    con.execute("DELETE FROM roles WHERE id=?", (role_id,))
    con.commit()
    close_db_if_owned(con)
    _audit("role_deleted", f"id={role_id} name={role['name']}", ip=request.remote_addr)
    return jsonify({"ok": True})


@admin_bp.route("/api/admin/users/<int:target_user_id>/roles")
@require_login
def api_admin_user_roles_get(target_user_id):
    user = _current_user()
    if not user or not has_permission(user, "users:manage_permissions"):
        return jsonify({"ok": False, "error": "Keine Berechtigung"}), 403
    con   = _get_db()
    roles = [r["role_id"] for r in
        con.execute("SELECT role_id FROM user_roles WHERE user_id=?", (target_user_id,)).fetchall()]
    close_db_if_owned(con)
    return jsonify({"ok": True, "role_ids": roles})


@admin_bp.route("/api/admin/users/<int:target_user_id>/roles", methods=["PUT"])
@require_login
def api_admin_user_roles_set(target_user_id):
    user = _current_user()
    if not user or not has_permission(user, "users:manage_permissions"):
        return jsonify({"ok": False, "error": "Keine Berechtigung"}), 403
    body     = request.get_json(force=True) or {}
    role_ids = [int(r) for r in (body.get("role_ids") or [])]
    con = _get_db()
    current_user_has_admin = "admin:all" in _get_user_permissions(target_user_id)
    if current_user_has_admin and target_user_id == user["id"]:
        new_perms_check = set()
        for rid in role_ids:
            p = {r["permission_key"] for r in
                 con.execute("SELECT permission_key FROM role_permissions WHERE role_id=?", (rid,)).fetchall()}
            new_perms_check |= p
        if "admin:all" not in new_perms_check:
            other_admins = con.execute("""
                SELECT COUNT(DISTINCT ur.user_id) as c FROM user_roles ur
                JOIN role_permissions rp ON ur.role_id = rp.role_id
                WHERE rp.permission_key = 'admin:all' AND ur.user_id != ?
            """, (target_user_id,)).fetchone()["c"]
            if other_admins == 0:
                close_db_if_owned(con)
                return jsonify({"ok": False, "error": "Mindestens ein Admin muss erhalten bleiben"}), 400
    con.execute("DELETE FROM user_roles WHERE user_id=?", (target_user_id,))
    for rid in role_ids:
        con.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?,?)", (target_user_id, rid))
    con.commit()
    close_db_if_owned(con)
    _audit("user_roles_updated", f"user_id={target_user_id} roles={role_ids}", ip=request.remote_addr)
    return jsonify({"ok": True})
