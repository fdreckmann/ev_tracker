"""
API token management routes (user-facing UI).
"""
import json
import secrets as _sec
from datetime import datetime

from flask import Blueprint, jsonify, request

from core.db import _get_db, close_db_if_owned
from core.security import require_login, has_permission, _current_user, _audit
from core.tokens import _API_SCOPES, _hash_token

tokens_bp = Blueprint("tokens", __name__)


@tokens_bp.route("/api/tokens", methods=["GET"])
@require_login
def api_tokens_list():
    if not has_permission(_current_user(), "api_tokens:view"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    con = _get_db()
    rows = con.execute(
        "SELECT id,name,token_prefix,scopes,expires_at,last_used_at,created_at,is_active,created_by"
        " FROM api_tokens ORDER BY id DESC").fetchall()
    close_db_if_owned(con)
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["scopes"] = json.loads(d.get("scopes") or "[]")
        except Exception:
            pass
        result.append(d)
    return jsonify(result)


@tokens_bp.route("/api/tokens", methods=["POST"])
@require_login
def api_tokens_create():
    if not has_permission(_current_user(), "api_tokens:create"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name erforderlich"}), 400
    scopes     = [s for s in (data.get("scopes") or []) if s in _API_SCOPES]
    expires_at = data.get("expires_at")
    raw    = "evt_" + _sec.token_urlsafe(40)
    prefix = raw[:12] + "..."
    user   = _current_user()
    con    = _get_db()
    cur = con.execute("""INSERT INTO api_tokens
        (name, token_hash, token_prefix, scopes, expires_at, created_by, created_at, is_active)
        VALUES (?,?,?,?,?,?,?,1)""",
        (name, _hash_token(raw), prefix, json.dumps(scopes),
         expires_at, user["id"] if user else None, datetime.utcnow().isoformat()))
    token_id = cur.lastrowid
    con.commit()
    close_db_if_owned(con)
    _audit("api_token_created", f"id={token_id} name={name}", ip=request.remote_addr)
    return jsonify({"ok": True, "id": token_id, "token": raw,
                    "note": "Token wird nur einmalig angezeigt. Bitte sicher aufbewahren."})


@tokens_bp.route("/api/tokens/<int:token_id>", methods=["DELETE"])
@require_login
def api_tokens_delete(token_id):
    if not has_permission(_current_user(), "api_tokens:delete"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    con = _get_db()
    con.execute("UPDATE api_tokens SET is_active=0 WHERE id=?", (token_id,))
    con.commit()
    close_db_if_owned(con)
    _audit("api_token_revoked", f"id={token_id}", ip=request.remote_addr)
    return jsonify({"ok": True})
