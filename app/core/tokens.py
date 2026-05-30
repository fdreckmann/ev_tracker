"""
API token management helpers — shared by routes/tokens.py and api/v1/* routes.

Usage:
    from core.tokens import _API_SCOPES, _hash_token, _check_api_token, _require_api_token
"""
import hashlib
import json
from datetime import datetime, timezone


from flask import jsonify, request

from core.db import _get_db, close_db_if_owned

_API_SCOPES = [
    "api:read", "api:write", "vehicles:read", "sessions:read", "sessions:write",
    "reports:read", "reports:create", "meter:read", "system:read",
]


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _check_api_token(raw: str):
    """Validate a raw Bearer token. Returns token row dict or None."""
    con = _get_db()
    row = con.execute(
        "SELECT * FROM api_tokens WHERE token_hash=? AND is_active=1",
        (_hash_token(raw),)).fetchone()
    if row:
        expires = row["expires_at"]
        if expires and datetime.now(timezone.utc).replace(tzinfo=None).isoformat() > expires:
            close_db_if_owned(con)
            return None
        con.execute("UPDATE api_tokens SET last_used_at=? WHERE id=?",
                    (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), row["id"]))
        con.commit()
    close_db_if_owned(con)
    return dict(row) if row else None


def _require_api_token(required_scope: str):
    """Validate Authorization: Bearer header. Returns (token_row, None, None) on success
    or (None, error_response, status_code) on failure."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, jsonify({"error": "Kein Token angegeben"}), 401
    raw = auth[7:]
    token_row = _check_api_token(raw)
    if not token_row:
        return None, jsonify({"error": "Ungültiger oder abgelaufener Token"}), 401
    scopes = json.loads(token_row.get("scopes") or "[]")
    if required_scope and required_scope not in scopes:
        return None, jsonify({"error": f"Scope '{required_scope}' fehlt"}), 403
    return token_row, None, None
