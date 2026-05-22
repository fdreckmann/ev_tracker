"""
Session management routes.
"""
import logging
import sqlite3
from datetime import datetime

from flask import Blueprint, jsonify, request

from core.db import _get_db, close_db_if_owned, DB_PATH
from core.security import require_login, has_permission, _current_user, _audit

log = logging.getLogger(__name__)

sessions_bp = Blueprint("sessions", __name__)


@sessions_bp.route("/api/sessions")
@require_login
def api_sessions():
    from server import get_sessions
    if not has_permission(_current_user(), "sessions:view"):
        return jsonify({"error": "Keine Berechtigung: sessions:view"}), 403
    return jsonify(get_sessions(
        request.args.get("year",type=int),
        request.args.get("month",type=int),
        request.args.get("location",default="all"),
        request.args.get("vehicle_id",default=None),
    ))

@sessions_bp.route("/api/sessions/<int:sid>", methods=["DELETE"])
@require_login
def api_delete_session(sid):
    if not has_permission(_current_user(), "sessions:delete"):
        return jsonify({"error": "Keine Berechtigung: sessions:delete"}), 403
    con=sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM sessions WHERE id=?",(sid,))
    con.execute("DELETE FROM session_points WHERE session_id=?",(sid,))
    con.commit(); close_db_if_owned(con)
    return jsonify({"ok":True})

@sessions_bp.route("/api/sessions/<int:sid>/points")
@require_login
def api_session_points(sid):
    if not has_permission(_current_user(), "sessions:view"):
        return jsonify({"error": "Keine Berechtigung: sessions:view"}), 403
    con=sqlite3.connect(DB_PATH); con.row_factory=sqlite3.Row
    rows=con.execute("SELECT ts,soc,power_kw FROM session_points WHERE session_id=? ORDER BY ts",(sid,)).fetchall()
    close_db_if_owned(con); return jsonify([dict(r) for r in rows])

@sessions_bp.route("/api/sessions/<int:sid>/location", methods=["POST"])
@require_login
def api_update_location(sid):
    if not has_permission(_current_user(), "sessions:edit"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: sessions:edit"}), 403
    loc = (request.json or {}).get("location","unknown")
    if loc not in ("home","extern","unknown"):
        return jsonify({"ok":False,"error":"Ungültiger Standort"}), 400
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("UPDATE sessions SET location=? WHERE id=?", (loc, sid))
        con.commit()
    finally:
        close_db_if_owned(con)
    log.info("Session #%d Standort → %s", sid, loc)
    return jsonify({"ok":True})

@sessions_bp.route("/api/sessions/manual", methods=["POST"])
@require_login
def api_manual_session_create():
    if not has_permission(_current_user(), "sessions:manual_add"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: sessions:manual_add"}), 403
    data = request.get_json(force=True) or {}
    start_ts = data.get("start_ts")
    if not start_ts:
        return jsonify({"ok": False, "error": "start_ts erforderlich"}), 400
    kwh      = data.get("kwh_charged")
    cost_eur = data.get("cost_eur")
    price_kwh= data.get("price_per_kwh")
    end_ts   = data.get("end_ts") or None
    location = data.get("location", "home")
    vehicle_id = data.get("vehicle_id", "v0")
    charger_power_kw = data.get("charger_power_kw")
    con = _get_db()
    cur = con.execute("""INSERT INTO sessions
        (start_ts, end_ts, kwh_charged, cost_eur, price_per_kwh, location,
         vehicle_id, provider, charger_power_kw)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (start_ts, end_ts, kwh, cost_eur, price_kwh, location,
         vehicle_id, "manual", charger_power_kw))
    sid = cur.lastrowid
    con.commit(); close_db_if_owned(con)
    _audit("session_manual_created", f"session_id={sid} vehicle_id={vehicle_id}",
           ip=request.remote_addr)
    return jsonify({"ok": True, "id": sid}), 201

@sessions_bp.route("/api/sessions/<int:sid>/cost", methods=["POST"])
@require_login
def api_update_cost(sid):
    if not has_permission(_current_user(), "sessions:edit"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: sessions:edit"}), 403
    data = request.get_json(force=True) or {}
    if "cost_eur" not in data:
        return jsonify({"ok": False, "error": "cost_eur fehlt"}), 400
    cost = float(data["cost_eur"]); price_kwh = data.get("price_per_kwh")
    con=sqlite3.connect(DB_PATH); cur=con.cursor()
    if price_kwh is not None:
        row=cur.execute("SELECT kwh_charged FROM sessions WHERE id=?",(sid,)).fetchone()
        if row and row[0]: cost=round(float(row[0])*float(price_kwh),2)
        cur.execute("UPDATE sessions SET cost_eur=?,price_per_kwh=?,cost_manual=1 WHERE id=?",(cost,float(price_kwh),sid))
    else:
        cur.execute("UPDATE sessions SET cost_eur=?,cost_manual=1 WHERE id=?",(cost,sid))
    con.commit(); close_db_if_owned(con)
    return jsonify({"ok":True,"cost_eur":cost})

@sessions_bp.route("/api/sessions/<int:sid>", methods=["PATCH"])
@require_login
def api_patch_session(sid):
    """Edit session fields: kwh_charged, price_per_kwh, cost_eur, charger_power_kw."""
    if not has_permission(_current_user(), "sessions:edit"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: sessions:edit"}), 403
    data = request.get_json(force=True) or {}
    allowed = {"kwh_charged", "price_per_kwh", "cost_eur", "charger_power_kw"}
    fields = {k: v for k, v in data.items() if k in allowed and v is not None}
    if not fields:
        return jsonify({"ok": False, "error": "Keine gültigen Felder"}), 400
    # Recalculate cost if kWh + price provided together and cost not explicit
    if "kwh_charged" in fields and "price_per_kwh" in fields and "cost_eur" not in fields:
        fields["cost_eur"] = round(float(fields["kwh_charged"]) * float(fields["price_per_kwh"]), 2)
    # Mark cost as manual if cost or price was explicitly set
    if "cost_eur" in fields or "price_per_kwh" in fields:
        fields["cost_manual"] = 1
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [sid]
    con = _get_db()
    con.execute(f"UPDATE sessions SET {set_clause} WHERE id=?", values)
    con.commit(); close_db_if_owned(con)
    return jsonify({"ok": True, "id": sid})

@sessions_bp.route("/api/stats/monthly")
@require_login
def api_monthly_stats():
    from server import get_monthly_stats
    return jsonify(get_monthly_stats())
