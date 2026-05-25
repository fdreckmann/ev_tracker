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


def _get_sessions(year=None, month=None, location=None, vehicle_id=None, limit=50):
    where = ["end_ts IS NOT NULL"]; params = []
    if year and month:
        where.append("start_ts LIKE ?"); params.append(f"{year:04d}-{month:02d}%")
    if location and location != "all":
        where.append("location = ?"); params.append(location)
    if vehicle_id and vehicle_id != "all":
        where.append("vehicle_id = ?"); params.append(vehicle_id)
    sql = f"SELECT * FROM sessions WHERE {' AND '.join(where)} ORDER BY start_ts DESC"
    if not (year and month): sql += f" LIMIT {limit}"
    con = _get_db()
    rows = con.execute(sql, params).fetchall(); close_db_if_owned(con)
    return [dict(r) for r in rows]


def _get_monthly_stats():
    con = _get_db()
    rows = con.execute("""
        SELECT strftime('%Y-%m', start_ts) AS month,
               COUNT(*) AS sessions,
               SUM(kwh_charged) AS total_kwh,
               SUM(cost_eur) AS total_cost,
               MAX(odo_end) - MIN(odo_start) AS km_driven,
               SUM(CASE WHEN location='home'   THEN cost_eur ELSE 0 END) AS home_cost,
               SUM(CASE WHEN location='extern' THEN cost_eur ELSE 0 END) AS ext_cost,
               SUM(CASE WHEN charger_type='dc' THEN kwh_charged ELSE 0 END) AS dc_kwh,
               SUM(CASE WHEN charger_type='ac' THEN kwh_charged ELSE 0 END) AS ac_kwh
        FROM sessions WHERE end_ts IS NOT NULL
        GROUP BY month ORDER BY month DESC LIMIT 12
    """).fetchall()
    close_db_if_owned(con); return [dict(r) for r in rows]


@sessions_bp.route("/api/sessions")
@require_login
def api_sessions():
    if not has_permission(_current_user(), "sessions:view"):
        return jsonify({"error": "Keine Berechtigung: sessions:view"}), 403
    return jsonify(_get_sessions(
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
    con = _get_db()
    row = con.execute("SELECT vehicle_id, start_ts, provider, created_mode FROM sessions WHERE id=?", (sid,)).fetchone()
    con.execute("DELETE FROM sessions WHERE id=?", (sid,))
    con.execute("DELETE FROM session_points WHERE session_id=?", (sid,))
    con.commit(); close_db_if_owned(con)
    if row:
        src = "manual" if (row["provider"] == "manual" or row["created_mode"] == "manual") else (row["provider"] or "auto")
        _audit("session_deleted",
               f"session_id={sid} vehicle_id={row['vehicle_id']} start_ts={row['start_ts']} source={src}",
               ip=request.remote_addr)
    return jsonify({"ok": True})


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
    from core.location import normalize_location
    loc = normalize_location((request.json or {}).get("location","unknown"))
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


def _float_or_none(val):
    if val is None or val == "": return None
    try: return float(val)
    except (ValueError, TypeError): return None


@sessions_bp.route("/api/sessions/manual", methods=["POST"])
@require_login
def api_manual_session_create():
    user = _current_user()
    if not has_permission(user, "sessions:manual_add"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: sessions:manual_add"}), 403
    data = request.get_json(force=True) or {}

    # ── Timestamps ───────────────────────────────────────────────────────────
    start_ts = (data.get("start_ts") or "").strip()
    if not start_ts:
        return jsonify({"ok": False, "error": "Startzeit (start_ts) ist erforderlich."}), 400
    try:
        start_dt = datetime.fromisoformat(start_ts)
    except ValueError:
        return jsonify({"ok": False, "error": "Ungültiges Startzeit-Format. Bitte ISO-Format verwenden (YYYY-MM-DDTHH:MM)."}), 400

    end_ts = (data.get("end_ts") or "").strip() or None
    end_dt = None
    if end_ts:
        try:
            end_dt = datetime.fromisoformat(end_ts)
        except ValueError:
            return jsonify({"ok": False, "error": "Ungültiges Endzeit-Format."}), 400
        if end_dt <= start_dt:
            return jsonify({"ok": False, "error": "Endzeit muss nach der Startzeit liegen."}), 400

    # ── kWh / Meter ──────────────────────────────────────────────────────────
    meter_old = _float_or_none(data.get("meter_old"))
    meter_new = _float_or_none(data.get("meter_new"))
    kwh = _float_or_none(data.get("kwh_charged"))
    meter_used = 0
    meter_delta = None

    if meter_old is not None and meter_new is not None:
        if meter_new < meter_old:
            return jsonify({"ok": False, "error": "Zählerstand Neu darf nicht kleiner sein als Zählerstand Alt."}), 400
        meter_delta = round(meter_new - meter_old, 3)
        if kwh is None:
            kwh = meter_delta
            meter_used = 1

    if kwh is None:
        return jsonify({"ok": False, "error": "Geladene kWh oder Zählerstände (meter_old + meter_new) sind erforderlich."}), 400
    if kwh < 0:
        return jsonify({"ok": False, "error": "kWh-Wert muss >= 0 sein."}), 400

    # ── Vehicle / Location / Charger ─────────────────────────────────────────
    vehicle_id = (data.get("vehicle_id") or "v0").strip()

    location = (data.get("location") or "home").strip()
    if location not in ("home", "extern", "unknown"):
        return jsonify({"ok": False, "error": "Ungültiger Standort. Erlaubt: home, extern, unknown."}), 400

    charger_type = (data.get("charger_type") or "unknown").strip()
    if charger_type not in ("ac", "dc", "unknown"):
        return jsonify({"ok": False, "error": "Ungültige Ladeart. Erlaubt: ac, dc, unknown."}), 400

    charger_power_kw = _float_or_none(data.get("charger_power_kw"))
    max_power_kw     = _float_or_none(data.get("max_power_kw"))

    # ── SOC ──────────────────────────────────────────────────────────────────
    soc_start = _float_or_none(data.get("soc_start"))
    soc_end   = _float_or_none(data.get("soc_end"))
    if soc_start is not None and not (0 <= soc_start <= 100):
        return jsonify({"ok": False, "error": "SOC-Start muss zwischen 0 und 100 liegen."}), 400
    if soc_end is not None and not (0 <= soc_end <= 100):
        return jsonify({"ok": False, "error": "SOC-Ende muss zwischen 0 und 100 liegen."}), 400

    # ── Odometer ─────────────────────────────────────────────────────────────
    odo_start = _float_or_none(data.get("odo_start"))
    odo_end   = _float_or_none(data.get("odo_end"))
    if odo_start is not None and odo_end is not None and odo_end < odo_start:
        return jsonify({"ok": False, "error": "KM-Ende darf nicht kleiner sein als KM-Start."}), 400

    # ── Cost ─────────────────────────────────────────────────────────────────
    price_kwh = _float_or_none(data.get("price_per_kwh"))
    cost_eur  = _float_or_none(data.get("cost_eur"))
    cost_manual = 0

    if price_kwh is not None and price_kwh < 0:
        return jsonify({"ok": False, "error": "Preis pro kWh muss >= 0 sein."}), 400
    if cost_eur is not None:
        if cost_eur < 0:
            return jsonify({"ok": False, "error": "Kosten müssen >= 0 sein."}), 400
        cost_manual = 1
    elif price_kwh is not None:
        cost_eur = round(kwh * price_kwh, 2)
        cost_manual = 1

    # ── Metadata ─────────────────────────────────────────────────────────────
    manual_note   = (data.get("manual_note") or data.get("note") or "").strip() or None
    manual_reason = (data.get("manual_reason") or "").strip() or None
    location_confidence = 100 if location in ("home", "extern") else 0

    # ── Overlap check ────────────────────────────────────────────────────────
    force = bool(data.get("force", False))
    if end_ts:
        con = _get_db()
        overlapping = con.execute(
            """SELECT id, start_ts, end_ts FROM sessions
               WHERE vehicle_id=? AND end_ts IS NOT NULL
               AND start_ts < ? AND end_ts > ?""",
            (vehicle_id, end_ts, start_ts)
        ).fetchall()
        close_db_if_owned(con)
        if overlapping and not force:
            return jsonify({
                "ok": False,
                "warning": "overlap",
                "message": "Es gibt bereits einen Ladevorgang in diesem Zeitraum.",
                "overlapping_sessions": [dict(r) for r in overlapping],
            }), 409

    # ── Insert ───────────────────────────────────────────────────────────────
    con = _get_db()
    cur = con.execute("""INSERT INTO sessions
        (start_ts, end_ts, kwh_charged, cost_eur, cost_manual, price_per_kwh,
         location, location_source, location_confidence,
         charger_type, charger_power_kw, max_power_kw,
         soc_start, soc_end, odo_start, odo_end,
         meter_old, meter_new, meter_delta_kwh, meter_used,
         vehicle_id, provider, kwh_source, created_mode,
         manual_note, manual_reason)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (start_ts, end_ts, round(kwh, 3), cost_eur, cost_manual, price_kwh,
         location, "manual", location_confidence,
         charger_type, charger_power_kw, max_power_kw,
         soc_start, soc_end, odo_start, odo_end,
         meter_old, meter_new, meter_delta, meter_used,
         vehicle_id, "manual", "manual", "manual",
         manual_note, manual_reason))
    sid = cur.lastrowid
    con.commit()
    row = con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    session_data = dict(row) if row else {}
    close_db_if_owned(con)

    # If this session was created from a missing-charge candidate, finalise the link
    candidate_id = data.get("missing_charge_candidate_id") or data.get("candidate_id")
    if candidate_id:
        try:
            candidate_id = int(candidate_id)
            link_con = _get_db()
            link_con.execute(
                "UPDATE missing_charge_candidates SET status='accepted', accepted_session_id=?, updated_at=? WHERE id=?",
                (sid, datetime.utcnow().isoformat(timespec="seconds"), candidate_id),
            )
            link_con.commit()
            close_db_if_owned(link_con)
            _audit("missing_charge_candidate_accepted",
                   f"candidate_id={candidate_id} session_id={sid}", ip=request.remote_addr)
        except Exception:
            pass

    _audit("session_manual_created",
           f"session_id={sid} vehicle_id={vehicle_id} kwh={kwh:.2f} location={location}",
           ip=request.remote_addr)
    return jsonify({
        "ok": True, "id": sid, "session": session_data,
        "message": "Manueller Ladevorgang wurde gespeichert.",
    }), 201


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
    """Edit session fields — extended to support all relevant fields."""
    if not has_permission(_current_user(), "sessions:edit"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: sessions:edit"}), 403
    data = request.get_json(force=True) or {}
    allowed = {
        "kwh_charged", "price_per_kwh", "cost_eur", "charger_power_kw",
        "start_ts", "end_ts", "soc_start", "soc_end",
        "odo_start", "odo_end", "location", "charger_type",
        "max_power_kw", "meter_old", "meter_new",
        "manual_note", "manual_reason",
    }
    fields = {k: v for k, v in data.items() if k in allowed and v is not None}
    if not fields:
        return jsonify({"ok": False, "error": "Keine gültigen Felder"}), 400

    if "location" in fields and fields["location"] not in ("home", "extern", "unknown"):
        return jsonify({"ok": False, "error": "Ungültiger Standort"}), 400
    if "charger_type" in fields and fields["charger_type"] not in ("ac", "dc", "unknown"):
        return jsonify({"ok": False, "error": "Ungültige Ladeart"}), 400

    # Auto-compute cost when kWh + price both updated but cost not explicit
    if "kwh_charged" in fields and "price_per_kwh" in fields and "cost_eur" not in fields:
        try:
            fields["cost_eur"] = round(float(fields["kwh_charged"]) * float(fields["price_per_kwh"]), 2)
        except (ValueError, TypeError):
            pass

    if "cost_eur" in fields or "price_per_kwh" in fields:
        fields["cost_manual"] = 1

    # Keep meter_delta_kwh in sync
    if "meter_old" in fields and "meter_new" in fields:
        try:
            delta = float(fields["meter_new"]) - float(fields["meter_old"])
            if delta >= 0:
                fields["meter_delta_kwh"] = round(delta, 3)
                fields["meter_used"] = 1
        except (ValueError, TypeError):
            pass

    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [sid]
    con = _get_db()
    con.execute(f"UPDATE sessions SET {set_clause} WHERE id=?", values)
    con.commit()
    _audit("session_edited", f"session_id={sid} fields={list(fields.keys())}", ip=request.remote_addr)
    close_db_if_owned(con)
    return jsonify({"ok": True, "id": sid})


@sessions_bp.route("/api/stats/monthly")
@require_login
def api_monthly_stats():
    return jsonify(_get_monthly_stats())
