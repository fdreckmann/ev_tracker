"""
Authentication, authorization, and audit-logging helpers.

Usage:
    from core.security import (
        require_login, require_admin, require_permission,
        has_permission, _current_user, _audit,
        ALL_PERMISSIONS, DEFAULT_ROLE_PERMISSIONS,
        _hash_password, _password_ok, _get_secret_key,
        _has_users, _get_user_by_email, _get_user_by_id,
        _get_user_permissions, _safe_next,
    )
"""
import functools
import hashlib
import secrets
from datetime import datetime

import flask

SECRET_MASK = "********"
from flask import g, jsonify, redirect, request, session, url_for

from core.db import _get_db, close_db_if_owned

# ── Permission data ───────────────────────────────────────────────────────────

ALL_PERMISSIONS = {
    # Dashboard
    "dashboard:view":           {"label": "Dashboard ansehen",              "group": "Dashboard"},
    # Fahrzeuge
    "vehicles:view":            {"label": "Fahrzeuge ansehen",              "group": "Fahrzeuge"},
    "vehicles:create":          {"label": "Fahrzeug erstellen",             "group": "Fahrzeuge"},
    "vehicles:edit":            {"label": "Fahrzeug bearbeiten",            "group": "Fahrzeuge"},
    "vehicles:delete":          {"label": "Fahrzeug löschen",               "group": "Fahrzeuge"},
    "vehicles:switch":          {"label": "Fahrzeug wechseln",              "group": "Fahrzeuge"},
    "vehicles:image_manage":    {"label": "Fahrzeugbild verwalten",         "group": "Fahrzeuge"},
    "vehicles:location_view":         {"label": "Fahrzeug-Standort anzeigen",         "group": "Fahrzeuge"},
    "vehicles:location_exact_view":   {"label": "Genaue Fahrzeug-Koordinaten sehen",  "group": "Fahrzeuge"},
    "vehicles:location_configure":    {"label": "Fahrzeug-Standort konfigurieren",     "group": "Fahrzeuge"},
    "vehicles:location_history_view": {"label": "Fahrzeug-Standort-Historie anzeigen","group": "Fahrzeuge"},
    "vehicles:provider_configure": {"label": "Provider konfigurieren",     "group": "Fahrzeuge"},
    # Ladevorgänge
    "sessions:view":            {"label": "Ladevorgänge ansehen",           "group": "Ladevorgänge"},
    "sessions:create":          {"label": "Ladevorgang erstellen",          "group": "Ladevorgänge"},
    "sessions:edit":            {"label": "Ladevorgang bearbeiten",         "group": "Ladevorgänge"},
    "sessions:delete":          {"label": "Ladevorgang löschen",            "group": "Ladevorgänge"},
    "sessions:manual_add":      {"label": "Manuell hinzufügen",             "group": "Ladevorgänge"},
    "sessions:validate":        {"label": "Ladevorgang validieren",         "group": "Ladevorgänge"},
    "sessions:ignore_warnings": {"label": "Warnungen ignorieren",           "group": "Ladevorgänge"},
    # Analyse
    "analytics:view":           {"label": "Analyse ansehen",                "group": "Analyse"},
    # Export
    "export:view":              {"label": "Export-Bereich ansehen",         "group": "Export"},
    "export:create":            {"label": "Export erstellen",               "group": "Export"},
    "export:preview":           {"label": "Export-Vorschau",                "group": "Export"},
    "export:download":          {"label": "Export herunterladen",           "group": "Export"},
    "export:templates_view":    {"label": "Templates ansehen",              "group": "Export"},
    "export:templates_manage":  {"label": "Templates verwalten",            "group": "Export"},
    "export:signature_use":     {"label": "Signatur im Export nutzen",      "group": "Export"},
    # Templates
    "templates:view":           {"label": "Templates ansehen",              "group": "Templates"},
    "templates:upload":         {"label": "Template hochladen",             "group": "Templates"},
    "templates:edit_mapping":   {"label": "Template-Mapping bearbeiten",    "group": "Templates"},
    "templates:delete":         {"label": "Template löschen",               "group": "Templates"},
    "templates:gallery_use":    {"label": "Template-Galerie nutzen",        "group": "Templates"},
    # Signatur
    "signature:view":           {"label": "Signatur ansehen",               "group": "Signatur"},
    "signature:upload":         {"label": "Signatur hochladen",             "group": "Signatur"},
    "signature:draw":           {"label": "Signatur zeichnen",              "group": "Signatur"},
    "signature:delete":         {"label": "Signatur löschen",               "group": "Signatur"},
    "signature:use_in_export":  {"label": "Signatur im Export verwenden",   "group": "Signatur"},
    # Zählerstand
    "meter:view":               {"label": "Zählerstand ansehen",            "group": "Zählerstand"},
    "meter:test":               {"label": "Zählerstand testen",             "group": "Zählerstand"},
    "meter:configure":          {"label": "Zählerstand konfigurieren",      "group": "Zählerstand"},
    # Provider
    "providers:view":           {"label": "Provider ansehen",               "group": "Provider"},
    "providers:configure":      {"label": "Provider konfigurieren",         "group": "Provider"},
    "providers:test":           {"label": "Provider testen",                "group": "Provider"},
    # Einstellungen
    "settings:view":            {"label": "Einstellungen ansehen",          "group": "Einstellungen"},
    "settings:edit":            {"label": "Einstellungen bearbeiten",       "group": "Einstellungen"},
    # Benutzer
    "users:view":               {"label": "Benutzer ansehen",               "group": "Benutzer"},
    "users:create":             {"label": "Benutzer erstellen",             "group": "Benutzer"},
    "users:edit":               {"label": "Benutzer bearbeiten",            "group": "Benutzer"},
    "users:delete":             {"label": "Benutzer löschen",               "group": "Benutzer"},
    "users:reset_password":     {"label": "Passwort zurücksetzen",          "group": "Benutzer"},
    "users:manage_2fa":         {"label": "2FA verwalten",                  "group": "Benutzer"},
    "users:manage_permissions": {"label": "Rollen & Rechte verwalten",      "group": "Benutzer"},
    # Backup
    "backup:view":              {"label": "Backup ansehen",                 "group": "Backup"},
    "backup:create":            {"label": "Backup erstellen",               "group": "Backup"},
    "backup:download":          {"label": "Backup herunterladen",           "group": "Backup"},
    "backup:restore":           {"label": "Backup wiederherstellen",        "group": "Backup"},
    "backup:delete":            {"label": "Backup löschen",                 "group": "Backup"},
    # Updates
    "updates:view":             {"label": "Updates ansehen",                "group": "Updates"},
    "updates:check":            {"label": "Updates prüfen",                 "group": "Updates"},
    "updates:start":            {"label": "Update starten",                 "group": "Updates"},
    "updates:history":          {"label": "Update-Historie ansehen",        "group": "Updates"},
    # Audit/Sicherheit
    "audit:view":               {"label": "Audit-Log ansehen",              "group": "Sicherheit"},
    "security:view":            {"label": "Sicherheit ansehen",             "group": "Sicherheit"},
    "api_tokens:manage":        {"label": "API-Tokens verwalten",           "group": "Sicherheit"},
    # System
    "system:status":            {"label": "Systemstatus ansehen",           "group": "System"},
    "system:logs":              {"label": "Logs ansehen",                   "group": "System"},
    "system:health":            {"label": "Health-Check",                   "group": "System"},
    # Reports
    "reports:view":             {"label": "Reports ansehen",                "group": "Reports"},
    "reports:configure":        {"label": "Reports konfigurieren",          "group": "Reports"},
    "reports:send":             {"label": "Report senden",                  "group": "Reports"},
    "reports:history":          {"label": "Report-Historie ansehen",        "group": "Reports"},
    "reports:archive":          {"label": "Report-Archiv ansehen",          "group": "Reports"},
    "reports:approve":          {"label": "Report freigeben",               "group": "Reports"},
    "reports:resend":           {"label": "Report erneut senden",           "group": "Reports"},
    # Billing / Abrechnung
    "billing:view":             {"label": "Abrechnung ansehen",             "group": "Abrechnung"},
    "billing:configure":        {"label": "Abrechnung konfigurieren",       "group": "Abrechnung"},
    # Tarife
    "tariffs:view":             {"label": "Tarife ansehen",                 "group": "Tarife"},
    "tariffs:configure":        {"label": "Tarife konfigurieren",           "group": "Tarife"},
    "tariffs:test":             {"label": "Tarif testen",                   "group": "Tarife"},
    # Export PDF
    "export:pdf":               {"label": "PDF exportieren",                "group": "Export"},
    # API Tokens
    "api_tokens:view":          {"label": "API-Tokens ansehen",             "group": "API"},
    "api_tokens:create":        {"label": "API-Token erstellen",            "group": "API"},
    "api_tokens:delete":        {"label": "API-Token widerrufen",           "group": "API"},
    # MQTT
    "mqtt:view":                {"label": "MQTT ansehen",                   "group": "MQTT"},
    "mqtt:configure":           {"label": "MQTT konfigurieren",             "group": "MQTT"},
    "mqtt:test":                {"label": "MQTT testen",                    "group": "MQTT"},
    # Benachrichtigungen
    "notifications:view":       {"label": "Benachrichtigungen ansehen",     "group": "Benachrichtigungen"},
    "notifications:configure":  {"label": "Benachrichtigungen konfigurieren","group": "Benachrichtigungen"},
    "notifications:test":       {"label": "Testbenachrichtigung senden",    "group": "Benachrichtigungen"},
    # Admin-Sonderrecht
    "admin:all":                {"label": "Vollzugriff (Admin)",            "group": "Admin"},
}

DEFAULT_ROLE_PERMISSIONS = {
    "admin": ["admin:all"],
    "user": [
        "dashboard:view", "vehicles:view", "vehicles:switch", "vehicles:location_view",
        "sessions:view", "sessions:create", "sessions:edit", "sessions:manual_add",
        "analytics:view",
        "export:view", "export:create", "export:preview", "export:download", "export:pdf",
        "export:templates_view", "export:signature_use",
        "templates:view", "templates:gallery_use",
        "signature:view", "signature:upload", "signature:draw",
        "signature:delete", "signature:use_in_export",
        "meter:view", "meter:test",
        "providers:view",
        "settings:view",
        "reports:view", "reports:history", "reports:archive", "reports:send",
        "billing:view",
        "tariffs:view",
        "notifications:view",
        "mqtt:view",
        "api_tokens:view",
    ],
    "readonly": [
        "dashboard:view", "vehicles:view", "sessions:view",
        "analytics:view", "export:view", "export:preview", "export:download",
        "reports:view", "billing:view",
    ],
}

# ── Password helpers ──────────────────────────────────────────────────────────

def _hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def _password_ok(pw: str) -> "str | None":
    """Returns error string or None if password is acceptable."""
    if len(pw) < 8:
        return "Mindestens 8 Zeichen"
    if not any(c.isdigit() for c in pw):
        return "Mindestens eine Zahl erforderlich"
    return None


def _get_secret_key() -> str:
    from core.db import DATA_DIR
    key_file = DATA_DIR / ".secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key_file.write_text(key)
    return key

# ── User helpers ──────────────────────────────────────────────────────────────

def _has_users() -> bool:
    try:
        con = _get_db()
        count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        close_db_if_owned(con)
        return count > 0
    except Exception:
        return False


def _get_user_by_email(email: str):
    try:
        con = _get_db()
        row = con.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        close_db_if_owned(con)
        return dict(row) if row else None
    except Exception:
        return None


def _get_user_by_id(uid):
    try:
        con = _get_db()
        row = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        close_db_if_owned(con)
        return dict(row) if row else None
    except Exception:
        return None


def _current_user():
    uid = session.get("user_id")
    return _get_user_by_id(uid) if uid else None

# ── Route decorators ──────────────────────────────────────────────────────────

def require_login(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Nicht eingeloggt", "login_required": True}), 401
            return redirect(url_for("auth.login_page", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def require_admin(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Nicht eingeloggt", "login_required": True}), 401
            return redirect(url_for("auth.login_page", next=request.path))
        user = _get_user_by_id(session.get("user_id"))
        is_admin = (session.get("user_role") == "admin") or \
                   (user and ("admin:all" in _get_user_permissions(user["id"])))
        if not is_admin:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Admin-Berechtigung erforderlich"}), 403
            return redirect(url_for("main_routes.index"))
        return f(*args, **kwargs)
    return wrapper


def require_auth(f):
    """Legacy alias — kept for backward compat."""
    return require_login(f)

# ── Permission checking ───────────────────────────────────────────────────────

def _get_user_permissions(user_id: int) -> set:
    con = _get_db()
    rows = con.execute("""
        SELECT DISTINCT rp.permission_key
        FROM user_roles ur
        JOIN role_permissions rp ON ur.role_id = rp.role_id
        WHERE ur.user_id = ?
    """, (user_id,)).fetchall()
    close_db_if_owned(con)
    return {r["permission_key"] for r in rows}


def has_permission(user, permission_key: str) -> bool:
    if not user:
        return False
    user_id = user["id"] if isinstance(user, dict) else int(user)
    cache_key = f"_perms_{user_id}"
    if not hasattr(g, cache_key):
        setattr(g, cache_key, _get_user_permissions(user_id))
    perms = getattr(g, cache_key)
    return "admin:all" in perms or permission_key in perms


def require_permission(permission_key: str):
    """Decorator: require a specific permission, return 403 if missing."""
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            user = _current_user()
            if not user:
                return jsonify({"ok": False, "error": "Nicht angemeldet"}), 401
            if not has_permission(user, permission_key):
                _audit("permission_denied",
                       f"perm={permission_key} endpoint={request.path}",
                       ip=request.remote_addr)
                return jsonify({"ok": False, "error": f"Keine Berechtigung: {permission_key}"}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator

# ── Open-redirect guard ───────────────────────────────────────────────────────

def _safe_next(next_url: "str | None") -> str:
    """Return next_url only if it is a relative path on this app (no open redirect)."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return url_for("main_routes.index")

# ── Audit logging ─────────────────────────────────────────────────────────────

def _audit(action: str, details: str = "", ip: str = "") -> None:
    uid = session.get("user_id") if session else None
    try:
        con = _get_db()
        con.execute(
            "INSERT INTO audit_log (ts, action, details, ip, user_id) VALUES (?,?,?,?,?)",
            (datetime.utcnow().isoformat(), action, details.strip(), ip, uid))
        con.commit()
        close_db_if_owned(con)
    except Exception:
        pass
