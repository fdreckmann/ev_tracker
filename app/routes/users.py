"""
User management and profile routes.
"""
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, session, url_for

from core.db import _get_db, close_db_if_owned
from core.security import (
    require_login, require_admin, has_permission, _current_user, _audit,
    _get_user_by_id, _hash_password, _password_ok,
)

users_bp = Blueprint("users", __name__)


# ── User Management ───────────────────────────────────────────────────────────
@users_bp.route("/api/users", methods=["GET"])
@require_login
def api_get_users():
    if not has_permission(_current_user(), "users:view"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: users:view"}), 403
    con = _get_db()
    rows = con.execute(
        "SELECT id,name,email,role,status,totp_enabled,created_at,updated_at,last_login_at FROM users ORDER BY id"
    ).fetchall()
    close_db_if_owned(con)
    return jsonify([dict(r) for r in rows])

@users_bp.route("/api/users", methods=["POST"])
@require_login
def api_create_user():
    if not has_permission(_current_user(), "users:create"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: users:create"}), 403
    data  = request.json or {}
    name  = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    pw    = data.get("password") or ""
    role  = data.get("role","user")
    invite_mode = not pw  # no password → create as invited
    if not name or not email:
        return jsonify({"ok": False, "error": "Name und E-Mail erforderlich"})
    now = datetime.utcnow().isoformat()
    try:
        con = _get_db()
        if invite_mode:
            con.execute(
                "INSERT INTO users (name,email,password_hash,role,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (name, email, "", role, "invited", now, now))
        else:
            pw_err = _password_ok(pw)
            if pw_err:
                return jsonify({"ok": False, "error": pw_err})
            con.execute(
                "INSERT INTO users (name,email,password_hash,role,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (name, email, _hash_password(pw), role, "active", now, now))
        con.commit(); close_db_if_owned(con)
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "error": "E-Mail bereits vorhanden"})
    _audit("user_created", f"email={email} role={role} invited={invite_mode}", ip=request.remote_addr)
    return jsonify({"ok": True, "invited": invite_mode})

@users_bp.route("/api/users/<int:uid>", methods=["PUT"])
@require_login
def api_update_user(uid):
    if not has_permission(_current_user(), "users:edit"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: users:edit"}), 403
    data = request.json or {}
    user = _get_user_by_id(uid)
    if not user:
        return jsonify({"ok": False, "error": "Nicht gefunden"}), 404
    now = datetime.utcnow().isoformat()
    con = _get_db()
    for field in ("name","role","status"):
        if field in data:
            con.execute(f"UPDATE users SET {field}=?,updated_at=? WHERE id=?", (data[field], now, uid))
    if data.get("password") and len(data["password"]) >= 8:
        con.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?",
                    (_hash_password(data["password"]), now, uid))
    con.commit(); close_db_if_owned(con)
    _audit("user_updated", f"uid={uid}", ip=request.remote_addr)
    return jsonify({"ok": True})

@users_bp.route("/api/users/<int:uid>", methods=["DELETE"])
@require_login
def api_delete_user(uid):
    if not has_permission(_current_user(), "users:delete"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: users:delete"}), 403
    if uid == session.get("user_id"):
        return jsonify({"ok": False, "error": "Eigenen Account nicht löschbar"})
    con = _get_db()
    con.execute("DELETE FROM users WHERE id=?", (uid,))
    con.commit(); close_db_if_owned(con)
    _audit("user_deleted", f"uid={uid}", ip=request.remote_addr)
    return jsonify({"ok": True})

@users_bp.route("/api/users/<int:uid>/reset-2fa", methods=["POST"])
@require_admin
def api_admin_reset_2fa(uid):
    now = datetime.utcnow().isoformat()
    con = _get_db()
    con.execute("UPDATE users SET totp_secret='',totp_enabled=0,updated_at=? WHERE id=?", (now, uid))
    con.commit(); close_db_if_owned(con)
    _audit("totp_reset", f"uid={uid}", ip=request.remote_addr)
    return jsonify({"ok": True})

@users_bp.route("/api/users/<int:uid>/invite", methods=["POST"])
@require_admin
def api_invite_user(uid):
    from services.email_service import _send_email, _email_html, _email_btn
    user = _get_user_by_id(uid)
    if not user:
        return jsonify({"ok": False, "error": "Benutzer nicht gefunden"}), 404
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now_dt = datetime.utcnow()
    expires = (now_dt + timedelta(hours=48)).isoformat()
    con = _get_db()
    # Invalidate old invite tokens
    con.execute("UPDATE invite_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL",
                (now_dt.isoformat(), uid))
    con.execute("INSERT INTO invite_tokens (user_id,token_hash,expires_at,created_at) VALUES (?,?,?,?)",
                (uid, token_hash, expires, now_dt.isoformat()))
    con.commit(); close_db_if_owned(con)
    invite_url = request.host_url.rstrip("/") + url_for("auth.accept_invite_page", token=token)
    body_html = _email_html(
        "Einladung zu EV Tracker",
        f"Hallo {user['name']},",
        "du wurdest zu EV Tracker eingeladen. Klicke auf den Button, um dein Passwort festzulegen und dein Konto zu aktivieren.",
        _email_btn(invite_url, "✉ Einladung annehmen"),
        "Dieser Link ist <b>48 Stunden</b> gültig."
    )
    ok, err = _send_email(user["email"], "EV Tracker — Einladung", body_html)
    _audit("user_invited", f"uid={uid} email={user['email']} smtp_ok={ok}", ip=request.remote_addr)
    if ok:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": err or "E-Mail konnte nicht gesendet werden"})

# ── Profile (own account) ─────────────────────────────────────────────────────
@users_bp.route("/api/users/me")
def api_get_me():
    user = _current_user()
    if not user:
        return jsonify({"error": "Nicht eingeloggt"}), 401
    return jsonify({
        "id":           user["id"],
        "name":         user["name"],
        "email":        user["email"],
        "role":         user["role"],
        "totp_enabled": bool(user.get("totp_enabled")),
    })

@users_bp.route("/api/users/me/password", methods=["POST"])
def api_change_password():
    user = _current_user()
    if not user:
        return jsonify({"error": "Nicht eingeloggt"}), 401
    data    = request.json or {}
    current = data.get("current","")
    new_pw  = data.get("new","")
    if _hash_password(current) != user["password_hash"]:
        return jsonify({"ok": False, "error": "Aktuelles Passwort falsch"})
    pw_err = _password_ok(new_pw)
    if pw_err:
        return jsonify({"ok": False, "error": pw_err})
    now = datetime.utcnow().isoformat()
    con = _get_db()
    con.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?",
                (_hash_password(new_pw), now, user["id"]))
    con.commit(); close_db_if_owned(con)
    _audit("password_changed", f"uid={user['id']}", ip=request.remote_addr)
    return jsonify({"ok": True})

@users_bp.route("/api/users/me/totp/setup", methods=["POST"])
def api_my_totp_setup():
    user = _current_user()
    if not user:
        return jsonify({"error": "Nicht eingeloggt"}), 401
    import pyotp
    secret = pyotp.random_base32()
    session["pending_totp"] = secret
    uri = pyotp.TOTP(secret).provisioning_uri(name=user["email"], issuer_name="EV Tracker")
    return jsonify({"ok": True, "secret": secret, "uri": uri})

@users_bp.route("/api/users/me/totp/confirm", methods=["POST"])
def api_my_totp_confirm():
    user   = _current_user()
    secret = session.get("pending_totp","")
    if not user or not secret:
        return jsonify({"ok": False, "error": "Kein ausstehender TOTP"})
    code = (request.json or {}).get("code","").strip().replace(" ","")
    import pyotp
    if not pyotp.TOTP(secret).verify(code, valid_window=1):
        return jsonify({"ok": False, "error": "Ungültiger Code — bitte erneut versuchen"})
    now = datetime.utcnow().isoformat()
    con = _get_db()
    con.execute("UPDATE users SET totp_secret=?,totp_enabled=1,updated_at=? WHERE id=?",
                (secret, now, user["id"]))
    con.commit(); close_db_if_owned(con)
    session.pop("pending_totp", None)
    _audit("totp_enabled", f"uid={user['id']}", ip=request.remote_addr)
    return jsonify({"ok": True})

@users_bp.route("/api/users/me/totp/disable", methods=["POST"])
def api_my_totp_disable():
    user = _current_user()
    if not user:
        return jsonify({"error": "Nicht eingeloggt"}), 401
    now = datetime.utcnow().isoformat()
    con = _get_db()
    con.execute("UPDATE users SET totp_secret='',totp_enabled=0,updated_at=? WHERE id=?",
                (now, user["id"]))
    con.commit(); close_db_if_owned(con)
    _audit("totp_disabled", f"uid={user['id']}", ip=request.remote_addr)
    return jsonify({"ok": True})

@users_bp.route("/api/users/me/totp/backup-codes", methods=["POST"])
@require_login
def api_generate_backup_codes():
    user = _current_user()
    if not user:
        return jsonify({"error": "Nicht eingeloggt"}), 401
    raw_codes = [secrets.token_hex(4).upper() for _ in range(8)]
    formatted = [f"{c[:4]}-{c[4:]}" for c in raw_codes]
    now_iso = datetime.utcnow().isoformat()
    con = _get_db()
    con.execute("DELETE FROM totp_backup_codes WHERE user_id=?", (user["id"],))
    for code in raw_codes:
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        con.execute("INSERT INTO totp_backup_codes (user_id, code_hash) VALUES (?,?)",
                    (user["id"], code_hash))
    con.commit(); close_db_if_owned(con)
    _audit("backup_codes_generated", f"uid={user['id']}", ip=request.remote_addr)
    return jsonify({"ok": True, "codes": formatted})

@users_bp.route("/api/users/me/totp/backup-codes/count", methods=["GET"])
@require_login
def api_backup_codes_count():
    user = _current_user()
    if not user:
        return jsonify({"error": "Nicht eingeloggt"}), 401
    con = _get_db()
    count = con.execute(
        "SELECT COUNT(*) FROM totp_backup_codes WHERE user_id=? AND used_at IS NULL",
        (user["id"],)).fetchone()[0]
    close_db_if_owned(con)
    return jsonify({"count": count})

@users_bp.route("/api/csrf-token", methods=["GET"])
@require_login
def api_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return jsonify({"token": session["csrf_token"]})
