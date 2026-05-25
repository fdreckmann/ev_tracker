"""
Authentication routes: login, logout, setup, password reset, invitations,
TOTP, WebAuthn/Passkeys, Google OAuth2, Microsoft OAuth2.
"""
import hashlib
import json
import secrets
import sqlite3
import time
from datetime import datetime, timedelta

import requests

from flask import (
    Blueprint, jsonify, redirect, render_template, request,
    session, url_for
)

from core.db import _get_db, close_db_if_owned
from core.config import load_config, save_config
from core.security import (
    require_login, require_admin,
    _current_user, _audit,
    _has_users, _get_user_by_email, _get_user_by_id,
    _hash_password, _verify_password, _is_legacy_sha256, _password_ok, _safe_next,
)

import logging
log = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

# In-memory rate-limit store for forgot-password requests
_forgot_pw_attempts: dict = {}  # email -> [timestamp, ...]


# ── Login / Logout / Setup ────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login_page():
    if session.get("user_id"):
        return redirect(url_for("main_routes.index"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw    = request.form.get("password", "")
        user  = _get_user_by_email(email)
        now_dt = datetime.utcnow()
        # Generic error to prevent user enumeration
        if not user or user.get("status") == "disabled":
            error = "Anmeldung fehlgeschlagen"
            _audit("login_failed", f"email={email}", ip=request.remote_addr)
        else:
            # Check account lockout
            locked_until = user.get("locked_until")
            if locked_until:
                try:
                    lu_dt = datetime.fromisoformat(locked_until)
                    if now_dt < lu_dt:
                        error = f"Konto gesperrt bis {lu_dt.strftime('%H:%M')} Uhr. Bitte später erneut versuchen."
                except Exception:
                    pass
            if not error and not _verify_password(pw, user.get("password_hash", "")):
                # Wrong password — increment failed attempts
                con = _get_db()
                new_attempts = (user.get("failed_attempts") or 0) + 1
                new_locked = None
                if new_attempts >= 5:
                    new_locked = (now_dt + timedelta(minutes=15)).isoformat()
                con.execute("UPDATE users SET failed_attempts=?,locked_until=? WHERE id=?",
                            (new_attempts, new_locked, user["id"]))
                con.commit(); close_db_if_owned(con)
                error = "Anmeldung fehlgeschlagen"
                _audit("login_failed", f"email={email} attempts={new_attempts}", ip=request.remote_addr)
                if new_locked:
                    # notify admins about account lockout
                    try:
                        from services.email_service import _send_email, _email_html
                        con2 = _get_db()
                        admins = con2.execute("SELECT email,name FROM users WHERE role='admin' AND status='active'").fetchall()
                        close_db_if_owned(con2)
                        for adm in admins:
                            body = _email_html(
                                "⚠️ Konto gesperrt",
                                f"Das Konto <b>{email}</b> wurde wegen zu vieler Fehlversuche für 15 Minuten gesperrt.",
                                f"IP-Adresse: {request.remote_addr}"
                            )
                            _send_email(adm["email"], f"EV Tracker — Konto gesperrt: {email}", body)
                    except Exception:
                        pass
            elif not error:
                # Check TOTP if enabled
                if user.get("totp_enabled") and user.get("totp_secret"):
                    code = request.form.get("totp", "").strip().replace(" ", "")
                    totp_ok = False
                    try:
                        import pyotp
                        totp_ok = pyotp.TOTP(user["totp_secret"]).verify(code, valid_window=1)
                    except Exception:
                        pass
                    if not totp_ok:
                        # Try backup code
                        code_hash = hashlib.sha256(code.encode()).hexdigest()
                        con = _get_db()
                        backup = con.execute(
                            "SELECT id FROM totp_backup_codes WHERE user_id=? AND code_hash=? AND used_at IS NULL",
                            (user["id"], code_hash)).fetchone()
                        if backup:
                            con.execute("UPDATE totp_backup_codes SET used_at=? WHERE id=?",
                                        (now_dt.isoformat(), backup["id"]))
                            con.commit(); close_db_if_owned(con)
                            totp_ok = True
                        else:
                            close_db_if_owned(con)
                    if not totp_ok:
                        error = "Ungültiger 2FA-Code"
                if not error:
                    # Reset failed attempts on success; upgrade legacy SHA-256 hash to werkzeug PBKDF2
                    con = _get_db()
                    if _is_legacy_sha256(user.get("password_hash", "")):
                        con.execute("UPDATE users SET password_hash=? WHERE id=?",
                                    (_hash_password(pw), user["id"]))
                    con.execute("UPDATE users SET failed_attempts=0,locked_until=NULL,last_login_at=? WHERE id=?",
                                (now_dt.isoformat(), user["id"]))
                    con.commit(); close_db_if_owned(con)
                    session["user_id"]    = user["id"]
                    session["user_email"] = user["email"]
                    session["user_role"]  = user["role"]
                    session["user_name"]  = user["name"]
                    session["csrf_token"] = secrets.token_hex(32)
                    session.permanent     = True
                    _audit("login", f"email={email} role={user['role']}", ip=request.remote_addr)
                    return redirect(_safe_next(request.args.get("next")))
    cfg = load_config()
    return render_template("login.html", error=error,
                           totp_enabled=False,  # TOTP check done server-side now
                           google_enabled=bool(cfg.get("oauth_google_client_id", "")),
                           microsoft_enabled=bool(cfg.get("oauth_microsoft_client_id", "")))


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login_page"))


@auth_bp.route("/setup", methods=["GET", "POST"])
def setup_page():
    if _has_users():
        return redirect(url_for("main_routes.index"))
    error = None
    cfg = load_config()
    has_old_auth = bool(cfg.get("auth_password_hash", ""))
    if request.method == "POST":
        name  = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        pw    = request.form.get("password", "")
        pw2   = request.form.get("password2", "")
        if not name or not email or not pw:
            error = "Alle Felder sind erforderlich"
        elif pw != pw2:
            error = "Passwörter stimmen nicht überein"
        elif _password_ok(pw):
            error = _password_ok(pw)
        else:
            now = datetime.utcnow().isoformat()
            con = _get_db()
            try:
                cur = con.execute(
                    "INSERT INTO users (name,email,password_hash,role,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (name, email, _hash_password(pw), "admin", "active", now, now))
                user_id = cur.lastrowid
                # Assign to admin role in user_roles (required for permission checks)
                role_row = con.execute("SELECT id FROM roles WHERE name='admin'").fetchone()
                if role_row:
                    con.execute("INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?,?)",
                                (user_id, role_row["id"]))
                con.commit()
            except sqlite3.IntegrityError:
                error = "E-Mail-Adresse bereits vorhanden"
            finally:
                close_db_if_owned(con)
            if not error:
                # Clear old single-user auth from config
                cfg["auth_password_hash"] = ""
                cfg["auth_totp_secret"]   = ""
                save_config(cfg)
                _audit("setup_complete", f"admin={email}", ip=request.remote_addr)
                return redirect(url_for("auth.login_page"))
    return render_template("setup.html", error=error, has_old_auth=has_old_auth)


# ── Password Reset ────────────────────────────────────────────────────────────

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password_page():
    if request.method == "GET":
        sent = request.args.get("sent", "")
        return render_template("forgot_password.html", sent=bool(sent))
    email = request.form.get("email", "").strip().lower()
    # Rate limit: max 3 requests per email per hour
    now_ts = time.time()
    attempts = _forgot_pw_attempts.get(email, [])
    attempts = [t for t in attempts if now_ts - t < 3600]  # last hour
    if len(attempts) >= 3:
        # Still redirect to avoid timing oracle, just don't send
        return redirect(url_for("auth.forgot_password_page", sent=1))
    attempts.append(now_ts)
    _forgot_pw_attempts[email] = attempts
    user  = _get_user_by_email(email)
    if user and user.get("status") != "disabled":
        from services.email_service import _send_email, _email_html, _email_btn
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now_dt = datetime.utcnow()
        expires = (now_dt + timedelta(hours=1)).isoformat()
        con = _get_db()
        # Invalidate old tokens for this user
        con.execute("UPDATE password_reset_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL",
                    (now_dt.isoformat(), user["id"]))
        con.execute("INSERT INTO password_reset_tokens (user_id,token_hash,expires_at,created_at) VALUES (?,?,?,?)",
                    (user["id"], token_hash, expires, now_dt.isoformat()))
        con.commit(); close_db_if_owned(con)
        reset_url = request.host_url.rstrip("/") + url_for("auth.reset_password_page", token=token)
        body_html = _email_html(
            "Passwort zurücksetzen",
            f"Hallo {user['name']},",
            "du hast einen Passwort-Reset für deinen EV Tracker Account angefordert.",
            _email_btn(reset_url, "🔑 Passwort zurücksetzen"),
            "Dieser Link ist <b>1 Stunde</b> gültig. Falls du diese Anfrage nicht gestellt hast, kannst du diese E-Mail ignorieren."
        )
        _send_email(user["email"], "EV Tracker — Passwort zurücksetzen", body_html)
        _audit("password_reset_requested", f"email={email}", ip=request.remote_addr)
    return redirect(url_for("auth.forgot_password_page", sent=1))


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password_page(token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now_iso = datetime.utcnow().isoformat()
    con = _get_db()
    row = con.execute(
        "SELECT * FROM password_reset_tokens WHERE token_hash=? AND used_at IS NULL AND expires_at > ?",
        (token_hash, now_iso)).fetchone()
    if not row:
        close_db_if_owned(con)
        return render_template("reset_password.html", token=token, invalid=True)
    row = dict(row)
    if request.method == "GET":
        close_db_if_owned(con)
        return render_template("reset_password.html", token=token, invalid=False)
    pw  = request.form.get("password", "")
    pw2 = request.form.get("password2", "")
    pw_err = _password_ok(pw)
    if pw_err:
        close_db_if_owned(con)
        return render_template("reset_password.html", token=token, invalid=False,
                               error=pw_err)
    if pw != pw2:
        close_db_if_owned(con)
        return render_template("reset_password.html", token=token, invalid=False,
                               error="Passwörter stimmen nicht überein")
    now_iso2 = datetime.utcnow().isoformat()
    con.execute("UPDATE users SET password_hash=?,updated_at=?,failed_attempts=0,locked_until=NULL WHERE id=?",
                (_hash_password(pw), now_iso2, row["user_id"]))
    con.execute("UPDATE password_reset_tokens SET used_at=? WHERE id=?", (now_iso2, row["id"]))
    con.commit(); close_db_if_owned(con)
    _audit("password_reset_done", f"uid={row['user_id']}", ip=request.remote_addr)
    return redirect(url_for("auth.login_page"))


# ── User Invitations ──────────────────────────────────────────────────────────

@auth_bp.route("/invite/<token>", methods=["GET", "POST"])
def accept_invite_page(token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now_iso = datetime.utcnow().isoformat()
    con = _get_db()
    row = con.execute(
        "SELECT * FROM invite_tokens WHERE token_hash=? AND used_at IS NULL AND expires_at > ?",
        (token_hash, now_iso)).fetchone()
    if not row:
        close_db_if_owned(con)
        return render_template("accept_invite.html", token=token, invalid=True)
    row = dict(row)
    user = _get_user_by_id(row["user_id"])
    if not user:
        close_db_if_owned(con)
        return render_template("accept_invite.html", token=token, invalid=True)
    if request.method == "GET":
        close_db_if_owned(con)
        return render_template("accept_invite.html", token=token, invalid=False,
                               user_name=user.get("name", ""))
    pw  = request.form.get("password", "")
    pw2 = request.form.get("password2", "")
    pw_err = _password_ok(pw)
    if pw_err:
        close_db_if_owned(con)
        return render_template("accept_invite.html", token=token, invalid=False,
                               user_name=user.get("name", ""),
                               error=pw_err)
    if pw != pw2:
        close_db_if_owned(con)
        return render_template("accept_invite.html", token=token, invalid=False,
                               user_name=user.get("name", ""),
                               error="Passwörter stimmen nicht überein")
    now_iso2 = datetime.utcnow().isoformat()
    new_status = "active" if user.get("status") == "invited" else user.get("status", "active")
    con.execute("UPDATE users SET password_hash=?,status=?,updated_at=? WHERE id=?",
                (_hash_password(pw), new_status, now_iso2, user["id"]))
    con.execute("UPDATE invite_tokens SET used_at=? WHERE id=?", (now_iso2, row["id"]))
    con.commit(); close_db_if_owned(con)
    _audit("invite_accepted", f"uid={user['id']}", ip=request.remote_addr)
    return redirect(url_for("auth.login_page"))


# ── API auth routes ───────────────────────────────────────────────────────────

@auth_bp.route("/api/auth/setup", methods=["POST"])
def api_auth_setup():
    # If any users exist this is a privileged operation — require admin
    if _has_users():
        user = _current_user()
        if not user or user.get("role") != "admin":
            return jsonify({"error": "Keine Berechtigung: admin erforderlich"}), 403
    data = request.json or {}
    cfg  = load_config()
    if "password" in data and data["password"]:
        cfg["auth_password_hash"] = _hash_password(data["password"])
        _audit("password_set", ip=request.remote_addr)
    if data.get("disable_password"):
        cfg["auth_password_hash"] = ""
        cfg["auth_totp_secret"]   = ""
        _audit("password_disabled", ip=request.remote_addr)
    save_config(cfg)
    return jsonify({"ok": True})


@auth_bp.route("/api/auth/totp/setup", methods=["POST"])
@require_login
def api_totp_setup():
    import pyotp
    user = _current_user()
    if not user or user.get("role") != "admin":
        return jsonify({"error": "Keine Berechtigung: admin erforderlich"}), 403
    secret = pyotp.random_base32()
    cfg = load_config()
    cfg["auth_totp_secret"] = secret
    save_config(cfg)
    car_name = cfg.get("car_name", "EV Tracker")
    email    = cfg.get("ha_token", "")[:6] or "user"
    uri = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=f"EV Tracker ({car_name})")
    return jsonify({"ok": True, "secret": secret, "uri": uri})


@auth_bp.route("/api/auth/totp/disable", methods=["POST"])
@require_login
def api_totp_disable():
    user = _current_user()
    if not user or user.get("role") != "admin":
        return jsonify({"error": "Keine Berechtigung: admin erforderlich"}), 403
    cfg = load_config()
    cfg["auth_totp_secret"] = ""
    save_config(cfg)
    _audit("totp_disabled", ip=request.remote_addr)
    return jsonify({"ok": True})


@auth_bp.route("/api/auth/status")
def api_auth_status():
    cfg = load_config()
    user = _current_user()
    return jsonify({
        "auth_enabled":       _has_users(),
        "user_id":            session.get("user_id"),
        "user_email":         session.get("user_email", ""),
        "user_name":          session.get("user_name", ""),
        "user_role":          session.get("user_role", ""),
        "totp_enabled":       bool(user.get("totp_enabled")) if user else False,
        "google_enabled":     bool(cfg.get("oauth_google_client_id", "")),
        "microsoft_enabled":  bool(cfg.get("oauth_microsoft_client_id", "")),
        "has_users":          _has_users(),
    })


# ── WebAuthn / Passkey helpers ────────────────────────────────────────────────

def _webauthn_rp_id() -> str:
    """Get WebAuthn relying party ID (hostname without port)."""
    base_url = load_config().get("oauth_base_url", "").rstrip("/")
    if base_url:
        from urllib.parse import urlparse
        return urlparse(base_url).hostname or "localhost"
    host = request.host or "localhost"
    return host.split(":")[0]  # strip port


def _webauthn_rp_name() -> str:
    return "EV Tracker"


def _webauthn_origin() -> str:
    """Get expected origin for WebAuthn."""
    base_url = load_config().get("oauth_base_url", "").rstrip("/")
    if base_url:
        return base_url
    # Respect X-Forwarded-Proto from reverse proxies (nginx, Traefik, etc.)
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    return f"{scheme}://{request.host}"


def _webauthn_credential_id_from_body(body: dict) -> str:
    """Normalize credential ID from a WebAuthn response body to canonical base64url.

    rawId (bytes as base64url) is preferred over id; both are round-tripped
    through bytes so padding/variant differences (Bitwarden, Chrome, Safari)
    don't cause lookup mismatches against the stored canonical form.
    """
    from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
    raw = body.get("rawId") or body.get("id") or ""
    if not raw:
        return ""
    try:
        return bytes_to_base64url(base64url_to_bytes(raw))
    except Exception:
        return raw  # best-effort: return as-is


# ── WebAuthn / Passkey routes ─────────────────────────────────────────────────

@auth_bp.route("/api/auth/passkey/register/begin", methods=["POST"])
@require_login
def api_passkey_register_begin():
    import webauthn
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria, ResidentKeyRequirement,
        UserVerificationRequirement, AttestationConveyancePreference
    )
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "Nicht angemeldet"}), 401

    con = _get_db()
    existing = con.execute(
        "SELECT credential_id FROM webauthn_credentials WHERE user_id=?",
        (user["id"],)
    ).fetchall()
    close_db_if_owned(con)

    from webauthn.helpers.structs import PublicKeyCredentialDescriptor
    from webauthn.helpers import base64url_to_bytes
    exclude_creds = []
    for row in existing:
        try:
            exclude_creds.append(PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(row["credential_id"])
            ))
        except Exception:
            pass

    try:
        options = webauthn.generate_registration_options(
            rp_id=_webauthn_rp_id(),
            rp_name=_webauthn_rp_name(),
            user_id=str(user["id"]).encode(),
            user_name=user["email"],
            user_display_name=user["name"],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            attestation=AttestationConveyancePreference.NONE,
            exclude_credentials=exclude_creds,
        )
        session["webauthn_reg_challenge"] = webauthn.helpers.bytes_to_base64url(options.challenge)
        import json as _json
        from webauthn.helpers import options_to_json
        return jsonify({"ok": True, "options": _json.loads(options_to_json(options))})
    except Exception as e:
        log.exception("Passkey register begin failed")
        return jsonify({"ok": False, "error": str(e)}), 500


@auth_bp.route("/api/auth/passkey/register/complete", methods=["POST"])
@require_login
def api_passkey_register_complete():
    import webauthn
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "Nicht angemeldet"}), 401

    challenge_b64 = session.pop("webauthn_reg_challenge", None)
    if not challenge_b64:
        return jsonify({"ok": False, "error": "Keine Registrierungs-Challenge gefunden"}), 400

    body = request.get_json(force=True) or {}
    cred_name = body.pop("name", "Passkey")

    try:
        from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
        expected_challenge = base64url_to_bytes(challenge_b64)

        verification = webauthn.verify_registration_response(
            credential=body,
            expected_challenge=expected_challenge,
            expected_rp_id=_webauthn_rp_id(),
            expected_origin=_webauthn_origin(),
            require_user_verification=False,
        )

        # Always use server-verified credential_id as canonical storage key
        cred_id = bytes_to_base64url(verification.credential_id)
        pub_key = bytes_to_base64url(verification.credential_public_key)
        rp_id   = _webauthn_rp_id()
        origin  = _webauthn_origin()
        log.info("Passkey registered: user=%s rp_id=%s origin=%s cred_len=%s cred_prefix=%s",
                 user["email"], rp_id, origin, len(cred_id), cred_id[:12])

        con = _get_db()
        now = datetime.utcnow().isoformat()
        con.execute(
            "INSERT INTO webauthn_credentials (user_id, credential_id, public_key, sign_count, name, created_at) VALUES (?,?,?,?,?,?)",
            (user["id"], cred_id, pub_key, verification.sign_count, cred_name, now)
        )
        con.commit(); close_db_if_owned(con)
        _audit("passkey_registered", f"user={user['email']} name={cred_name}", ip=request.remote_addr)
        return jsonify({"ok": True, "message": f"Passkey '{cred_name}' erfolgreich registriert"})
    except Exception as e:
        log.exception("Passkey register complete failed")
        return jsonify({"ok": False, "error": str(e)}), 400


@auth_bp.route("/api/auth/passkey/credentials")
@require_login
def api_passkey_credentials():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "Nicht angemeldet"}), 401
    con = _get_db()
    rows = con.execute(
        "SELECT id, name, created_at, last_used_at FROM webauthn_credentials WHERE user_id=? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()
    close_db_if_owned(con)
    return jsonify({"ok": True, "credentials": [dict(r) for r in rows]})


@auth_bp.route("/api/auth/passkey/credentials/<int:cred_db_id>", methods=["DELETE"])
@require_login
def api_passkey_credential_delete(cred_db_id):
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "Nicht angemeldet"}), 401
    con = _get_db()
    con.execute("DELETE FROM webauthn_credentials WHERE id=? AND user_id=?", (cred_db_id, user["id"]))
    con.commit(); close_db_if_owned(con)
    _audit("passkey_deleted", f"cred_id={cred_db_id}", ip=request.remote_addr)
    return jsonify({"ok": True})


@auth_bp.route("/api/auth/passkey/webauthn-config")
@require_login
def api_passkey_webauthn_config():
    """Admin-only debug endpoint: shows WebAuthn RP-ID, Origin, and proxy headers.
    Helps diagnose domain/origin mismatches when Passkeys don't work behind a reverse proxy.
    """
    user = _current_user()
    if not has_permission(user, "admin:all"):
        return jsonify({"ok": False, "error": "Admin-Berechtigung erforderlich"}), 403
    cfg = load_config()
    base_url = cfg.get("oauth_base_url", "").rstrip("/")
    rp_id    = _webauthn_rp_id()
    origin   = _webauthn_origin()
    x_proto  = request.headers.get("X-Forwarded-Proto", "")
    x_host   = request.headers.get("X-Forwarded-Host", "")
    x_for    = request.headers.get("X-Forwarded-For", "")
    return jsonify({
        "ok": True,
        "webauthn_rp_id":         rp_id,
        "webauthn_origin":        origin,
        "oauth_base_url":         base_url or "(nicht gesetzt)",
        "request_host":           request.host,
        "request_scheme":         request.scheme,
        "x_forwarded_proto":      x_proto or "(nicht vorhanden)",
        "x_forwarded_host":       x_host  or "(nicht vorhanden)",
        "x_forwarded_for":        x_for   or "(nicht vorhanden)",
        "reverse_proxy_mode":     bool(base_url or x_proto),
        "hint": (
            "rp_id und origin müssen exakt der externen HTTPS-Domain entsprechen, "
            "unter der Bitwarden/Browser den Passkey gespeichert hat. "
            "Falls oauth_base_url nicht gesetzt ist, wird request.host + X-Forwarded-Proto verwendet."
        ),
    })


@auth_bp.route("/api/auth/passkey/login/begin", methods=["POST"])
def api_passkey_login_begin():
    import webauthn
    from webauthn.helpers.structs import UserVerificationRequirement

    body = request.get_json(silent=True) or {}
    email = body.get("email", "").strip().lower()

    # allow_credentials=None → discoverable/resident credentials (Bitwarden, iCloud Keychain).
    # Only restrict to known credential IDs when an email is provided and credentials exist.
    allow_creds = None
    if email:
        user = _get_user_by_email(email)
        if user:
            con = _get_db()
            rows = con.execute(
                "SELECT credential_id FROM webauthn_credentials WHERE user_id=?",
                (user["id"],)
            ).fetchall()
            close_db_if_owned(con)
            if rows:
                from webauthn.helpers.structs import PublicKeyCredentialDescriptor
                from webauthn.helpers import base64url_to_bytes
                creds = []
                for row in rows:
                    try:
                        creds.append(PublicKeyCredentialDescriptor(
                            id=base64url_to_bytes(row["credential_id"])
                        ))
                    except Exception:
                        pass
                if creds:
                    allow_creds = creds

    try:
        options = webauthn.generate_authentication_options(
            rp_id=_webauthn_rp_id(),
            allow_credentials=allow_creds,
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        from webauthn.helpers import bytes_to_base64url
        session["webauthn_auth_challenge"] = bytes_to_base64url(options.challenge)
        import json as _json
        from webauthn.helpers import options_to_json
        return jsonify({"ok": True, "options": _json.loads(options_to_json(options))})
    except Exception as e:
        log.exception("Passkey login begin failed")
        return jsonify({"ok": False, "error": str(e)}), 500


@auth_bp.route("/api/auth/passkey/login/complete", methods=["POST"])
def api_passkey_login_complete():
    import webauthn

    challenge_b64 = session.pop("webauthn_auth_challenge", None)
    if not challenge_b64:
        return jsonify({"ok": False, "error": "Keine Authentifizierungs-Challenge"}), 400

    body = request.get_json(force=True) or {}

    try:
        from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
        expected_challenge = base64url_to_bytes(challenge_b64)

        # Normalize to canonical base64url via the helper (rawId preferred over id).
        cred_id_b64 = _webauthn_credential_id_from_body(body)
        if not cred_id_b64:
            return jsonify({"ok": False, "error": "Keine Credential-ID in der Antwort"}), 400

        rp_id  = _webauthn_rp_id()
        origin = _webauthn_origin()
        log.info("Passkey login attempt: cred_len=%s cred_prefix=%s rp_id=%s origin=%s",
                 len(cred_id_b64), cred_id_b64[:12], rp_id, origin)

        con = _get_db()
        row = con.execute(
            """SELECT wc.id as cred_id, wc.credential_id, wc.public_key, wc.sign_count, wc.name as cred_name,
                      u.id as user_id, u.email, u.name, u.role, u.status
               FROM webauthn_credentials wc
               JOIN users u ON wc.user_id = u.id
               WHERE wc.credential_id=?""",
            (cred_id_b64,)
        ).fetchone()

        if not row:
            close_db_if_owned(con)
            log.warning("Passkey login: no DB match — cred_len=%s cred_prefix=%s rp_id=%s origin=%s",
                        len(cred_id_b64), cred_id_b64[:12] if cred_id_b64 else "", rp_id, origin)
            return jsonify({"ok": False, "error": (
                "Dieser Passkey wurde vom Browser gesendet, ist dem EV Tracker jedoch nicht bekannt. "
                "Mögliche Ursachen: Passkey wurde unter einer anderen Domain/RP-ID registriert, "
                "oder Bitwarden hat ihn für eine andere URL gespeichert. "
                "Bitte alte EV-Tracker-Passkeys in Bitwarden löschen, "
                "dich einmal mit Passwort anmelden und den Passkey neu hinzufügen."
            )}), 400

        row = dict(row)
        if row.get("status") == "disabled":
            close_db_if_owned(con)
            return jsonify({"ok": False, "error": "Konto deaktiviert"}), 403

        verification = webauthn.verify_authentication_response(
            credential=body,
            expected_challenge=expected_challenge,
            expected_rp_id=_webauthn_rp_id(),
            expected_origin=_webauthn_origin(),
            credential_public_key=base64url_to_bytes(row["public_key"]),
            credential_current_sign_count=row["sign_count"],
            require_user_verification=False,
        )

        now = datetime.utcnow().isoformat()
        con.execute(
            "UPDATE webauthn_credentials SET sign_count=?, last_used_at=? WHERE credential_id=?",
            (verification.new_sign_count, now, cred_id_b64)
        )
        con.execute(
            "UPDATE users SET last_login_at=?, failed_attempts=0, locked_until=NULL WHERE id=?",
            (now, row["user_id"])
        )
        con.commit(); close_db_if_owned(con)

        log.info("Passkey login success: user=%s cred_prefix=%s", row["email"], cred_id_b64[:12])
        session["user_id"]    = row["user_id"]
        session["user_email"] = row["email"]
        session["user_role"]  = row["role"]
        session["user_name"]  = row["name"]
        session["csrf_token"] = secrets.token_hex(32)
        session.permanent     = True
        _audit("passkey_login", f"user={row['email']}", ip=request.remote_addr)

        return jsonify({"ok": True, "redirect": "/"})
    except Exception as e:
        log.exception("Passkey login complete failed")
        return jsonify({"ok": False, "error": str(e)}), 400


# ── OAuth2 helpers ────────────────────────────────────────────────────────────

def _oauth_finish(email: str):
    """Called after successful OAuth2 — find or reject user, set session."""
    user = _get_user_by_email(email)
    if not user:
        # Auto-create user if no users exist yet (shouldn't happen post-setup, but just in case)
        if not _has_users():
            return redirect("/setup")
        # Deny if user not in DB
        return render_template("login.html", error=f"Kein Konto für {email} vorhanden. Bitte Admin kontaktieren.",
                               totp_enabled=False, google_enabled=False, microsoft_enabled=False)
    if user.get("status") == "disabled":
        return render_template("login.html", error="Konto deaktiviert.",
                               totp_enabled=False, google_enabled=False, microsoft_enabled=False)
    session["user_id"]    = user["id"]
    session["user_email"] = user["email"]
    session["user_role"]  = user["role"]
    session["user_name"]  = user["name"]
    session.permanent     = True
    now = datetime.utcnow().isoformat()
    con = _get_db()
    con.execute("UPDATE users SET last_login_at=? WHERE id=?", (now, user["id"]))
    con.commit(); close_db_if_owned(con)
    _audit("login_oauth", f"email={email} role={user['role']}", ip=request.remote_addr)
    return redirect(_safe_next(request.args.get("next")))


def _oauth_redirect_base() -> str:
    cfg = load_config()
    base = cfg.get("oauth_base_url", "").rstrip("/")
    if base:
        return base
    # Auto-detect: honour X-Forwarded-Proto for reverse proxy
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    host   = request.headers.get("X-Forwarded-Host", request.host)
    return f"{scheme}://{host}"


# ── Google OAuth2 ─────────────────────────────────────────────────────────────

@auth_bp.route("/auth/google")
def auth_google():
    cfg = load_config()
    if not cfg.get("oauth_google_client_id"):
        return "Google OAuth nicht konfiguriert", 400
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    session["oauth_next"]  = request.args.get("next", "/")
    redirect_uri = _oauth_redirect_base() + "/auth/google/callback"
    params = {
        "client_id":     cfg["oauth_google_client_id"],
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         state,
        "access_type":   "online",
    }
    from urllib.parse import urlencode
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@auth_bp.route("/auth/google/callback")
def auth_google_callback():
    cfg   = load_config()
    state = request.args.get("state", "")
    code  = request.args.get("code", "")
    if not code or state != session.pop("oauth_state", ""):
        return render_template("login.html", error="OAuth-Fehler: ungültiger State", totp_enabled=False,
                               google_enabled=bool(cfg.get("oauth_google_client_id")),
                               microsoft_enabled=bool(cfg.get("oauth_microsoft_client_id")))
    redirect_uri = _oauth_redirect_base() + "/auth/google/callback"
    try:
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "code":          code,
            "client_id":     cfg["oauth_google_client_id"],
            "client_secret": cfg["oauth_google_client_secret"],
            "redirect_uri":  redirect_uri,
            "grant_type":    "authorization_code",
        }, timeout=10)
        r.raise_for_status()
        token = r.json().get("access_token", "")
        info  = requests.get("https://www.googleapis.com/oauth2/v3/userinfo",
                             headers={"Authorization": f"Bearer {token}"}, timeout=10).json()
        email = info.get("email", "")
        if not email:
            raise RuntimeError("Keine E-Mail vom Google-Konto erhalten")
        return _oauth_finish(email)
    except Exception as e:
        return render_template("login.html", error=f"Google Login fehlgeschlagen: {e}",
                               totp_enabled=False,
                               google_enabled=True, microsoft_enabled=bool(cfg.get("oauth_microsoft_client_id")))


# ── Microsoft OAuth2 ──────────────────────────────────────────────────────────

@auth_bp.route("/auth/microsoft")
def auth_microsoft():
    cfg = load_config()
    if not cfg.get("oauth_microsoft_client_id"):
        return "Microsoft OAuth nicht konfiguriert", 400
    tenant = cfg.get("oauth_microsoft_tenant", "common") or "common"
    state  = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    session["oauth_next"]  = request.args.get("next", "/")
    redirect_uri = _oauth_redirect_base() + "/auth/microsoft/callback"
    from urllib.parse import urlencode
    params = {
        "client_id":     cfg["oauth_microsoft_client_id"],
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         "openid email profile User.Read",
        "state":         state,
        "response_mode": "query",
    }
    return redirect(f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?" + urlencode(params))


@auth_bp.route("/auth/microsoft/callback")
def auth_microsoft_callback():
    cfg    = load_config()
    tenant = cfg.get("oauth_microsoft_tenant", "common") or "common"
    state  = request.args.get("state", "")
    code   = request.args.get("code", "")
    if not code or state != session.pop("oauth_state", ""):
        return render_template("login.html", error="OAuth-Fehler: ungültiger State", totp_enabled=False,
                               google_enabled=bool(cfg.get("oauth_google_client_id")),
                               microsoft_enabled=bool(cfg.get("oauth_microsoft_client_id")))
    redirect_uri = _oauth_redirect_base() + "/auth/microsoft/callback"
    try:
        r = requests.post(
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            data={
                "code":          code,
                "client_id":     cfg["oauth_microsoft_client_id"],
                "client_secret": cfg["oauth_microsoft_client_secret"],
                "redirect_uri":  redirect_uri,
                "grant_type":    "authorization_code",
            }, timeout=10)
        r.raise_for_status()
        token = r.json().get("access_token", "")
        info  = requests.get("https://graph.microsoft.com/v1.0/me",
                             headers={"Authorization": f"Bearer {token}"}, timeout=10).json()
        email = info.get("mail") or info.get("userPrincipalName", "")
        if not email:
            raise RuntimeError("Keine E-Mail vom Microsoft-Konto erhalten")
        return _oauth_finish(email)
    except Exception as e:
        return render_template("login.html", error=f"Microsoft Login fehlgeschlagen: {e}",
                               totp_enabled=False,
                               google_enabled=bool(cfg.get("oauth_google_client_id")),
                               microsoft_enabled=True)
