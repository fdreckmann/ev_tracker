"""Missing charge candidate API routes."""
from datetime import datetime, timezone


from flask import Blueprint, jsonify, request

from core.db import _get_db, close_db_if_owned
from core.security import require_login, has_permission, _current_user, _audit

missing_charges_bp = Blueprint("missing_charges", __name__)


def _row_to_dict(row, cursor):
    return dict(zip([d[0] for d in cursor.description], row))


@missing_charges_bp.route("/api/missing-charges", methods=["GET"])
@require_login
def api_list_missing_charges():
    user = _current_user()
    if not has_permission(user, "sessions:view"):
        return jsonify({"error": "Keine Berechtigung: sessions:view"}), 403
    vehicle_id = request.args.get("vehicle_id")
    status_filter = request.args.get("status", "open")
    con = _get_db()
    q = "SELECT * FROM missing_charge_candidates WHERE status=?"
    params: list = [status_filter]
    if vehicle_id:
        q += " AND vehicle_id=?"
        params.append(vehicle_id)
    q += " ORDER BY created_at DESC LIMIT 100"
    cur = con.execute(q, params)
    rows = [_row_to_dict(r, cur) for r in cur.fetchall()]
    close_db_if_owned(con)
    return jsonify(rows)


@missing_charges_bp.route("/api/missing-charges/count", methods=["GET"])
@require_login
def api_count_missing_charges():
    user = _current_user()
    if not has_permission(user, "sessions:view"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    con = _get_db()
    count = con.execute(
        "SELECT COUNT(*) FROM missing_charge_candidates WHERE status='open'"
    ).fetchone()[0]
    close_db_if_owned(con)
    return jsonify({"count": count})


@missing_charges_bp.route("/api/missing-charges/<int:cid>", methods=["GET"])
@require_login
def api_get_missing_charge(cid):
    user = _current_user()
    if not has_permission(user, "sessions:view"):
        return jsonify({"error": "Keine Berechtigung: sessions:view"}), 403
    con = _get_db()
    cur = con.execute("SELECT * FROM missing_charge_candidates WHERE id=?", (cid,))
    row = cur.fetchone()
    if not row:
        close_db_if_owned(con)
        return jsonify({"error": "Nicht gefunden"}), 404
    result = _row_to_dict(row, cur)
    close_db_if_owned(con)
    return jsonify(result)


@missing_charges_bp.route("/api/missing-charges/<int:cid>/dismiss", methods=["POST"])
@require_login
def api_dismiss_missing_charge(cid):
    user = _current_user()
    if not has_permission(user, "sessions:manual_add"):
        return jsonify({"error": "Keine Berechtigung: sessions:manual_add"}), 403
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    con = _get_db()
    updated = con.execute(
        "UPDATE missing_charge_candidates SET status='dismissed',updated_at=? WHERE id=? AND status='open'",
        (now, cid),
    ).rowcount
    con.commit()
    close_db_if_owned(con)
    if not updated:
        return jsonify({"ok": False, "error": "Nicht gefunden oder bereits erledigt"}), 404
    _audit("missing_charge_candidate_ignored", f"candidate_id={cid}", ip=request.remote_addr)
    return jsonify({"ok": True})


@missing_charges_bp.route("/api/missing-charges/<int:cid>/ignore", methods=["POST"])
@require_login
def api_ignore_missing_charge(cid):
    """Permanently ignore — won't be re-created for the same time window."""
    user = _current_user()
    if not has_permission(user, "sessions:manual_add"):
        return jsonify({"error": "Keine Berechtigung: sessions:manual_add"}), 403
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    con = _get_db()
    updated = con.execute(
        "UPDATE missing_charge_candidates SET status='ignored',updated_at=? WHERE id=?",
        (now, cid),
    ).rowcount
    con.commit()
    close_db_if_owned(con)
    if not updated:
        return jsonify({"ok": False, "error": "Nicht gefunden"}), 404
    _audit("missing_charge_candidate_ignored",
           f"candidate_id={cid} permanent=true", ip=request.remote_addr)
    return jsonify({"ok": True})


@missing_charges_bp.route("/api/missing-charges/<int:cid>/accept", methods=["POST"])
@require_login
def api_accept_missing_charge(cid):
    """Mark candidate as accepted and return prefill data for the manual session dialog."""
    user = _current_user()
    if not has_permission(user, "sessions:manual_add"):
        return jsonify({"error": "Keine Berechtigung: sessions:manual_add"}), 403
    con = _get_db()
    cur = con.execute("SELECT * FROM missing_charge_candidates WHERE id=?", (cid,))
    row = cur.fetchone()
    if not row:
        close_db_if_owned(con)
        return jsonify({"error": "Nicht gefunden"}), 404
    candidate = _row_to_dict(row, cur)
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    # Mark as in_review — becomes 'accepted' only when the manual session is actually saved.
    # This prevents ghost acceptances if the user closes the dialog without saving.
    con.execute(
        "UPDATE missing_charge_candidates SET status='in_review',updated_at=? WHERE id=?",
        (now, cid),
    )
    con.commit()
    close_db_if_owned(con)
    _audit("missing_charge_candidate_in_review", f"candidate_id={cid}", ip=request.remote_addr)
    return jsonify({"ok": True, "prefill": candidate, "candidate_id": cid})


@missing_charges_bp.route("/api/missing-charges/check", methods=["POST"])
@require_login
def api_trigger_check():
    """Manually trigger gap detection across all snapshots for a vehicle."""
    user = _current_user()
    if not has_permission(user, "sessions:manual_add"):
        return jsonify({"error": "Keine Berechtigung: sessions:manual_add"}), 403
    from core.config import load_config
    from services.missing_charge_service import check_for_missing_charge
    vehicle_id = (request.json or {}).get("vehicle_id", "v0")
    cfg = load_config()
    con = _get_db()
    # Re-check all snapshot pairs that don't already have a candidate
    snaps = con.execute(
        "SELECT id FROM vehicle_snapshots WHERE vehicle_id=? ORDER BY id",
        (vehicle_id,),
    ).fetchall()
    created = 0
    for snap in snaps:
        sid = snap[0]
        try:
            cid = check_for_missing_charge(vehicle_id, sid, cfg, con)
            if cid:
                created += 1
        except Exception:
            pass
    close_db_if_owned(con)
    return jsonify({"ok": True, "created": created, "message": f"{created} neue Vorschläge"})
