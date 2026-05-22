"""
SMTP and email OAuth routes.
"""
import json
import time
import secrets
import requests
from datetime import datetime
from urllib.parse import urlencode

from flask import Blueprint, jsonify, redirect, request, session

from core.config import load_config, save_config
from core.security import require_admin, require_login, has_permission, _current_user, _audit

smtp_bp = Blueprint("smtp", __name__)

_SMTP_OAUTH = {
    "google": {
        "auth_url":  "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scope":     "https://mail.google.com/",
        "cid":       "smtp_google_client_id",
        "csec":      "smtp_google_client_secret",
        "refresh":   "smtp_google_refresh_token",
        "access":    "smtp_google_access_token",
        "expires":   "smtp_google_token_expires_at",
        "sender":    "smtp_google_sender_email",
    },
    "microsoft": {
        "token_url": None,  # tenant-specific, built dynamically
        "scope":     "https://outlook.office365.com/SMTP.Send offline_access",
        "cid":       "smtp_ms_client_id",
        "csec":      "smtp_ms_client_secret",
        "refresh":   "smtp_ms_refresh_token",
        "access":    "smtp_ms_access_token",
        "expires":   "smtp_ms_token_expires_at",
        "sender":    "smtp_ms_sender_email",
    },
}


@smtp_bp.route("/api/smtp/oauth/status")
@require_admin
def api_smtp_oauth_status():
    """Connection status only — never exposes tokens/secrets."""
    cfg = load_config()
    return jsonify({
        "auth_method": cfg.get("smtp_auth_method", "basic"),
        "google": {
            "connected": bool(cfg.get("smtp_google_refresh_token")),
            "sender":    cfg.get("smtp_google_sender_email", ""),
            "client_configured": bool(cfg.get("smtp_google_client_id")),
        },
        "microsoft": {
            "connected": bool(cfg.get("smtp_ms_refresh_token")),
            "sender":    cfg.get("smtp_ms_sender_email", ""),
            "client_configured": bool(cfg.get("smtp_ms_client_id")),
        },
    })


@smtp_bp.route("/api/smtp/oauth/<provider>/connect")
@require_admin
def api_smtp_oauth_connect(provider):
    from server import _oauth_redirect_base
    if provider not in _SMTP_OAUTH:
        return jsonify({"error": "Unbekannter Provider"}), 400
    cfg = load_config()
    spec = _SMTP_OAUTH[provider]
    client_id = cfg.get(spec["cid"], "")
    if not client_id:
        return jsonify({"error": f"{provider.title()} Client-ID fehlt"}), 400
    state = secrets.token_urlsafe(24)
    session["smtp_oauth_state"]    = state
    session["smtp_oauth_provider"] = provider
    redirect_uri = _oauth_redirect_base() + f"/smtp/oauth/{provider}/callback"
    if provider == "google":
        params = {
            "client_id": client_id, "redirect_uri": redirect_uri,
            "response_type": "code", "scope": spec["scope"],
            "access_type": "offline", "prompt": "consent", "state": state,
        }
        url = spec["auth_url"] + "?" + urlencode(params)
    else:
        tenant = cfg.get("smtp_ms_tenant_id", "common") or "common"
        params = {
            "client_id": client_id, "redirect_uri": redirect_uri,
            "response_type": "code", "scope": spec["scope"],
            "response_mode": "query", "state": state,
        }
        url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?" + urlencode(params)
    return jsonify({"auth_url": url})


@smtp_bp.route("/smtp/oauth/<provider>/callback")
@require_admin
def smtp_oauth_callback(provider):
    from server import _oauth_redirect_base
    if provider not in _SMTP_OAUTH:
        return "Unbekannter Provider", 400
    state = request.args.get("state", "")
    code  = request.args.get("code", "")
    if not code or state != session.pop("smtp_oauth_state", ""):
        return "<script>window.close()</script>OAuth-Fehler: ungültiger State", 400
    session.pop("smtp_oauth_provider", None)
    cfg  = load_config()
    spec = _SMTP_OAUTH[provider]
    redirect_uri = _oauth_redirect_base() + f"/smtp/oauth/{provider}/callback"
    try:
        if provider == "google":
            token_url = spec["token_url"]
            extra = {}
        else:
            tenant = cfg.get("smtp_ms_tenant_id", "common") or "common"
            token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
            extra = {"scope": spec["scope"]}
        r = requests.post(token_url, data={
            "code": code,
            "client_id":     cfg.get(spec["cid"], ""),
            "client_secret": cfg.get(spec["csec"], ""),
            "redirect_uri":  redirect_uri,
            "grant_type":    "authorization_code",
            **extra,
        }, timeout=15)
        r.raise_for_status()
        j = r.json()
        refresh = j.get("refresh_token", "")
        access  = j.get("access_token", "")
        ttl     = int(j.get("expires_in", 3600))
        if not refresh:
            return ("<script>window.close()</script>Kein Refresh-Token erhalten — "
                    "bitte App-Registrierung auf 'offline access' prüfen.", 400)
        # Determine sender email from the granted token
        sender = cfg.get(spec["sender"], "") or cfg.get("smtp_from_email", "")
        cfg[spec["refresh"]] = refresh
        cfg[spec["access"]]  = access
        cfg[spec["expires"]] = time.time() + ttl
        if sender:
            cfg[spec["sender"]] = sender
        cfg["smtp_auth_method"] = "oauth2_" + provider
        save_config(cfg)
        _audit("smtp_oauth_connected", f"provider={provider}", ip=request.remote_addr)
        return ("<html><body style='background:#0f1117;color:#6ee7b7;font-family:sans-serif;"
                "text-align:center;padding:60px'><h2>✅ "
                f"{provider.title()} verbunden</h2><p>Dieses Fenster kann geschlossen werden.</p>"
                "<script>setTimeout(()=>window.close(),1500);"
                "if(window.opener)window.opener.postMessage('smtp_oauth_done','*')</script></body></html>")
    except Exception as e:
        return f"<script>window.close()</script>OAuth fehlgeschlagen: {e}", 500


@smtp_bp.route("/api/smtp/oauth/<provider>/disconnect", methods=["POST"])
@require_admin
def api_smtp_oauth_disconnect(provider):
    if provider not in _SMTP_OAUTH:
        return jsonify({"error": "Unbekannter Provider"}), 400
    spec = _SMTP_OAUTH[provider]
    cfg  = load_config()
    for k in (spec["refresh"], spec["access"]):
        cfg[k] = ""
    cfg[spec["expires"]] = 0
    if cfg.get("smtp_auth_method") == "oauth2_" + provider:
        cfg["smtp_auth_method"] = "basic"
    save_config(cfg)
    _audit("smtp_oauth_disconnected", f"provider={provider}", ip=request.remote_addr)
    return jsonify({"ok": True})


@smtp_bp.route("/api/smtp/test", methods=["POST"])
@require_login
def smtp_test():
    from server import _smtp_open, _SECRET_MASK
    if not has_permission(_current_user(), "settings:edit"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: settings:edit"}), 403
    data = request.json or {}
    cfg  = load_config()
    method = data.get("smtp_auth_method") or cfg.get("smtp_auth_method", "basic")
    # For a live test, merge any provided non-secret overrides
    test_cfg = dict(cfg)
    for k in ("smtp_host", "smtp_port", "smtp_tls", "smtp_user", "smtp_auth_method"):
        if data.get(k) not in (None, ""):
            test_cfg[k] = data[k]
    if data.get("smtp_password") and data["smtp_password"] != _SECRET_MASK:
        test_cfg["smtp_password"] = data["smtp_password"]
    test_cfg["smtp_auth_method"] = method
    srv, frm, err = _smtp_open(test_cfg)
    if err:
        return jsonify({"ok": False, "error": err, "auth_method": method})
    try:
        srv.quit()
    except Exception:
        pass
    _audit("smtp_test", f"method={method}", ip=request.remote_addr)
    return jsonify({"ok": True, "message": "✅ SMTP-Verbindung erfolgreich",
                    "auth_method": method})


@smtp_bp.route("/api/smtp/send-test", methods=["POST"])
@require_login
def smtp_send_test():
    from server import _send_email, _email_html
    if not has_permission(_current_user(), "settings:edit"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: settings:edit"}), 403
    data = request.json or {}
    cfg = load_config()
    to  = data.get("to") or cfg.get("smtp_from_email","")
    method = cfg.get("smtp_auth_method", "basic")
    has_basic  = bool(cfg.get("smtp_host","") and cfg.get("smtp_from_email",""))
    has_oauth  = method in ("oauth2_google", "oauth2_microsoft")
    if not (has_basic or has_oauth) or not to:
        return jsonify({"ok": False, "error": "SMTP nicht konfiguriert"})
    body_html = _email_html(
        "SMTP Testmail",
        "Diese E-Mail bestätigt, dass deine SMTP-Konfiguration in EV Tracker korrekt funktioniert.",
        f"Gesendet an: <b>{to}</b>",
        "Falls du diese E-Mail erhalten hast, ist alles richtig eingestellt. ✅"
    )
    ok, err = _send_email(to, "EV Tracker — SMTP Test", body_html)
    if ok:
        _audit("smtp_test_sent", f"to={to} method={method}", ip=request.remote_addr)
        return jsonify({"ok": True, "message": f"Testmail an {to} versendet ({method})",
                        "auth_method": method})
    return jsonify({"ok": False, "error": err or "Unbekannter Fehler"})
