"""
Wallbox session management routes.

Allows users to view open (unassigned) wallbox sessions and assign them to a
vehicle, mark them as foreign-vehicle charges, or ignore them.
"""
import logging

from flask import Blueprint, jsonify, request

from core.db import _get_db, close_db_if_owned
from core.security import require_login, has_permission, _current_user, _audit

log = logging.getLogger(__name__)

wallbox_bp = Blueprint("wallbox", __name__)


@wallbox_bp.route("/api/wallbox/sessions/open")
@require_login
def api_wallbox_open_sessions():
    """Return wallbox_sessions that need user action (unassigned)."""
    if not has_permission(_current_user(), "sessions:view"):
        return jsonify({"error": "Keine Berechtigung: sessions:view"}), 403
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except (ValueError, TypeError):
        limit = 50
    from services.wallbox_session_service import get_open_wallbox_sessions
    con = _get_db()
    rows = get_open_wallbox_sessions(con, limit=limit)
    close_db_if_owned(con)
    return jsonify(rows)


@wallbox_bp.route("/api/wallbox/sessions")
@require_login
def api_wallbox_sessions():
    """Return recent wallbox_sessions (all statuses)."""
    if not has_permission(_current_user(), "sessions:view"):
        return jsonify({"error": "Keine Berechtigung: sessions:view"}), 403
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except (ValueError, TypeError):
        limit = 50
    con = _get_db()
    rows = con.execute(
        "SELECT * FROM wallbox_sessions ORDER BY start_ts DESC LIMIT ?", (limit,)
    ).fetchall()
    close_db_if_owned(con)
    return jsonify([dict(r) for r in rows])


@wallbox_bp.route("/api/wallbox/sessions/<int:wbs_id>/assign", methods=["POST"])
@require_login
def api_wallbox_assign(wbs_id):
    """Assign a wallbox_session to a vehicle.

    Body: {"vehicle_id": "v0", "create_session": true}
    """
    if not has_permission(_current_user(), "sessions:manual_add"):
        return jsonify({"error": "Keine Berechtigung: sessions:manual_add"}), 403
    data = request.get_json(force=True) or {}
    vehicle_id = data.get("vehicle_id")
    if not vehicle_id:
        return jsonify({"ok": False, "error": "vehicle_id fehlt"}), 400

    con = _get_db()
    row = con.execute(
        "SELECT * FROM wallbox_sessions WHERE id=?", (wbs_id,)
    ).fetchone()
    if not row:
        close_db_if_owned(con)
        return jsonify({"ok": False, "error": "Wallbox-Session nicht gefunden"}), 404
    wbs = dict(row)

    from services.wallbox_session_service import assign_wallbox_session
    ok = assign_wallbox_session(con, wbs_id, vehicle_id)

    session_id = None
    if ok and data.get("create_session", False):
        from services.reconciliation_service import reconcile_charging_evidence
        result = reconcile_charging_evidence(con, wbs, vehicle_id)
        session_id = result.get("session_id")

    close_db_if_owned(con)
    _audit("wallbox_assigned",
           f"wbs_id={wbs_id} vehicle_id={vehicle_id} session_id={session_id}",
           ip=request.remote_addr)
    return jsonify({"ok": ok, "session_id": session_id})


@wallbox_bp.route("/api/wallbox/sessions/<int:wbs_id>/mark-foreign", methods=["POST"])
@require_login
def api_wallbox_mark_foreign(wbs_id):
    """Mark a wallbox_session as a foreign-vehicle charge."""
    if not has_permission(_current_user(), "sessions:manual_add"):
        return jsonify({"error": "Keine Berechtigung: sessions:manual_add"}), 403
    con = _get_db()
    if not con.execute("SELECT 1 FROM wallbox_sessions WHERE id=?", (wbs_id,)).fetchone():
        close_db_if_owned(con)
        return jsonify({"ok": False, "error": "Wallbox-Session nicht gefunden"}), 404
    from services.wallbox_session_service import mark_foreign_vehicle
    ok = mark_foreign_vehicle(con, wbs_id)
    close_db_if_owned(con)
    _audit("wallbox_marked_foreign", f"wbs_id={wbs_id}", ip=request.remote_addr)
    return jsonify({"ok": ok})


@wallbox_bp.route("/api/wallbox/sessions/<int:wbs_id>/ignore", methods=["POST"])
@require_login
def api_wallbox_ignore(wbs_id):
    """Ignore a wallbox_session (exclude from everything)."""
    if not has_permission(_current_user(), "sessions:manual_add"):
        return jsonify({"error": "Keine Berechtigung: sessions:manual_add"}), 403
    con = _get_db()
    if not con.execute("SELECT 1 FROM wallbox_sessions WHERE id=?", (wbs_id,)).fetchone():
        close_db_if_owned(con)
        return jsonify({"ok": False, "error": "Wallbox-Session nicht gefunden"}), 404
    from services.wallbox_session_service import ignore_wallbox_session
    ok = ignore_wallbox_session(con, wbs_id)
    close_db_if_owned(con)
    _audit("wallbox_ignored", f"wbs_id={wbs_id}", ip=request.remote_addr)
    return jsonify({"ok": ok})


@wallbox_bp.route("/api/wallbox/confirm", methods=["POST", "GET"])
def api_wallbox_confirm():
    """Direct-action confirmation from a notification (ntfy button / link).

    Auth is the signed token itself (binds session_id + vehicle_id, short TTL),
    not a login session. ``action`` is one of assign / mark-foreign / ignore.
    """
    from services.charging_state_machine import verify_confirm_token
    token  = request.args.get("token", "")
    action = request.args.get("action", "assign")
    claim = verify_confirm_token(token)
    if not claim:
        return jsonify({"ok": False, "error": "Ungültiger oder abgelaufener Token"}), 403

    wbs_id     = claim["wbs_id"]
    vehicle_id = claim.get("vehicle_id")

    con = _get_db()
    if not con.execute("SELECT 1 FROM wallbox_sessions WHERE id=?", (wbs_id,)).fetchone():
        close_db_if_owned(con)
        return jsonify({"ok": False, "error": "Wallbox-Session nicht gefunden"}), 404

    # Permission seam (fleet-ready): "darf dieser Nutzer dieses Fahrzeug?" —
    # heute immer erlaubt, da der Token das Fahrzeug bereits bindet.
    from services.wallbox_session_service import (
        assign_wallbox_session, mark_foreign_vehicle, ignore_wallbox_session,
    )
    if action in ("assign", "mine"):
        if not vehicle_id:
            close_db_if_owned(con)
            return jsonify({"ok": False, "error": "Kein Fahrzeug im Token"}), 400
        ok = assign_wallbox_session(con, wbs_id, vehicle_id)
        audit_detail = f"wbs_id={wbs_id} vehicle_id={vehicle_id}"
        audit_action = "wallbox_confirm_assigned"
    elif action in ("mark-foreign", "foreign"):
        ok = mark_foreign_vehicle(con, wbs_id)
        audit_detail = f"wbs_id={wbs_id}"
        audit_action = "wallbox_confirm_foreign"
    elif action == "ignore":
        ok = ignore_wallbox_session(con, wbs_id)
        audit_detail = f"wbs_id={wbs_id}"
        audit_action = "wallbox_confirm_ignored"
    else:
        close_db_if_owned(con)
        return jsonify({"ok": False, "error": f"Unbekannte Aktion: {action}"}), 400

    close_db_if_owned(con)
    _audit(audit_action, audit_detail, ip=request.remote_addr)
    return jsonify({"ok": ok, "action": action})


# ---------------------------------------------------------------------------
# Internal helper: create a normal session from a confirmed wallbox_session
# ---------------------------------------------------------------------------

def _create_session_from_wallbox(con, wbs: dict, vehicle_id: str) -> int | None:
    """Insert a normal session row from a confirmed wallbox_session.

    Returns the new session id or None on error.
    """
    try:
        from core.config import load_config
        from services.pricing_service import resolve_session_price, calculate_session_cost
        cfg = load_config()
        kwh = wbs.get("energy_kwh")
        start_ts = wbs.get("start_ts")
        end_ts   = wbs.get("end_ts") or start_ts
        meter_old = wbs.get("meter_start_kwh")
        meter_new = wbs.get("meter_end_kwh")

        price_kwh = None
        cost_eur  = None
        price_source = None
        price_conf = 0
        contract_id = None
        contract_name = None
        try:
            pr = resolve_session_price("home", "ac", cfg, con)
            if pr.get("price_per_kwh") is not None and kwh is not None:
                price_kwh     = pr["price_per_kwh"]
                cost_eur      = calculate_session_cost(float(kwh), price_kwh)
                price_source  = pr.get("price_source")
                price_conf    = pr.get("price_confidence", 0)
                contract_id   = pr.get("contract_id")
                contract_name = pr.get("contract_name")
        except Exception:
            pass

        cur = con.execute(
            """INSERT INTO sessions
               (start_ts, end_ts, kwh_charged, cost_eur, cost_manual,
                price_per_kwh, location, charger_type, vehicle_id, provider,
                kwh_source, created_mode, meter_old, meter_new,
                source_primary, vehicle_assignment_status, excluded_from_reports,
                price_source, price_confidence, charging_contract_id, charging_contract_name)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (start_ts, end_ts, kwh, cost_eur, 0,
             price_kwh, "home", "ac", vehicle_id, "wallbox",
             "meter", "wallbox", meter_old, meter_new,
             "wallbox", "confirmed", 0,
             price_source, price_conf, contract_id, contract_name),
        )
        con.commit()
        # Link wallbox_session -> normal session
        sid = cur.lastrowid
        con.execute(
            "UPDATE wallbox_sessions SET excluded_from_reports=0, updated_at=datetime('now')"
            " WHERE id=?", (wbs["id"],)
        )
        con.commit()
        return sid
    except Exception as e:
        log.warning("_create_session_from_wallbox failed: %s", e)
        return None
