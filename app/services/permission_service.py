"""
Permission service — thin wrapper used by routes during incremental extraction.
The authoritative implementation lives in server.py until extraction is complete.
"""
from __future__ import annotations


def get_user_permissions(con, user_id: int) -> set[str]:
    """Return set of permission keys for user via their assigned roles."""
    rows = con.execute("""
        SELECT DISTINCT rp.permission_key
        FROM user_roles ur
        JOIN role_permissions rp ON ur.role_id = rp.role_id
        WHERE ur.user_id = ?
    """, (user_id,)).fetchall()
    return {r["permission_key"] for r in rows}


def has_permission(con, user: dict | None, permission_key: str) -> bool:
    """Return True if user has the given permission (or admin:all)."""
    if not user:
        return False
    user_id = user["id"] if isinstance(user, dict) else int(user)
    perms = get_user_permissions(con, user_id)
    return "admin:all" in perms or permission_key in perms
