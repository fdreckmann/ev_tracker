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
    con.row_factory = sqlite3.Row
    try:
        existing = con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not existing:
            return jsonify({"ok": False, "error": "Session nicht gefunden"}), 404
        existing = dict(existing)
        if existing.get("cost_manual", 0) == 0:
            try:
                from services.pricing_service import resolve_session_price, calculate_session_cost
                from core.config import load_config
                cfg = load_config()
                charger_type = existing.get("charger_type", "unknown")
                kwh = existing.get("kwh_charged")
                _pr = resolve_session_price(loc, charger_type, cfg, con, sid)
                if _pr["price_per_kwh"] is not None and kwh is not None:
                    new_cost = calculate_session_cost(float(kwh), _pr["price_per_kwh"])
                    con.execute(
                        """UPDATE sessions SET location=?, price_per_kwh=?, cost_eur=?,
                           price_source=?, price_confidence=?, charging_contract_id=?, charging_contract_name=?
                           WHERE id=?""",
                        (loc, _pr["price_per_kwh"], new_cost,
                         _pr.get("price_source"), _pr.get("price_confidence", 0),
                         _pr.get("contract_id"), _pr.get("contract_name"), sid))
                else:
                    con.execute("UPDATE sessions SET location=? WHERE id=?", (loc, sid))
            except Exception as _e:
                log.debug("Reprice after location update failed: %s", _e)
                con.execute("UPDATE sessions SET location=? WHERE id=?", (loc, sid))
        else:
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
    _auto_price_source = None; _auto_price_conf = 0; _auto_contract_id = None; _auto_contract_name = None

    if price_kwh is not None and price_kwh < 0:
        return jsonify({"ok": False, "error": "Preis pro kWh muss >= 0 sein."}), 400
    if cost_eur is not None:
        if cost_eur < 0:
            return jsonify({"ok": False, "error": "Kosten müssen >= 0 sein."}), 400
        cost_manual = 1
    elif price_kwh is not None:
        cost_eur = round(kwh * price_kwh, 2)
        cost_manual = 1
    else:
        # Auto-price via pricing_service (cost_manual stays 0)
        try:
            from services.pricing_service import resolve_session_price, calculate_session_cost
            from core.config import load_config
            _cfg = load_config()
            con_tmp = _get_db()
            _pr = resolve_session_price(location, charger_type, _cfg, con_tmp)
            close_db_if_owned(con_tmp)
            if _pr["price_per_kwh"] is not None:
                price_kwh = _pr["price_per_kwh"]
                cost_eur  = calculate_session_cost(kwh, price_kwh)
                _auto_price_source    = _pr.get("price_source")
                _auto_price_conf      = _pr.get("price_confidence", 0)
                _auto_contract_id     = _pr.get("contract_id")
                _auto_contract_name   = _pr.get("contract_name")
        except Exception as _e:
            log.debug("Auto-pricing for manual session failed: %s", _e)

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
         manual_note, manual_reason,
         price_source, price_confidence, charging_contract_id, charging_contract_name)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (start_ts, end_ts, round(kwh, 3), cost_eur, cost_manual, price_kwh,
         location, "manual", location_confidence,
         charger_type, charger_power_kw, max_power_kw,
         soc_start, soc_end, odo_start, odo_end,
         meter_old, meter_new, meter_delta, meter_used,
         vehicle_id, "manual", "manual", "manual",
         manual_note, manual_reason,
         _auto_price_source, _auto_price_conf, _auto_contract_id, _auto_contract_name))
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
    try:
        cost = float(data["cost_eur"])
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "cost_eur muss eine Zahl sein"}), 400
    if cost < 0:
        return jsonify({"ok": False, "error": "cost_eur darf nicht negativ sein"}), 400
    price_kwh_raw = data.get("price_per_kwh")
    price_kwh = None
    if price_kwh_raw is not None:
        try:
            price_kwh = float(price_kwh_raw)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "price_per_kwh muss eine Zahl sein"}), 400
        if price_kwh < 0:
            return jsonify({"ok": False, "error": "price_per_kwh darf nicht negativ sein"}), 400
    con = _get_db()
    row = con.execute("SELECT id, kwh_charged FROM sessions WHERE id=?", (sid,)).fetchone()
    if not row:
        close_db_if_owned(con)
        return jsonify({"ok": False, "error": "Session nicht gefunden"}), 404
    if price_kwh is not None:
        kwh = row["kwh_charged"] if isinstance(row, dict) else row[1]
        if kwh:
            cost = round(float(kwh) * price_kwh, 2)
        con.execute("UPDATE sessions SET cost_eur=?,price_per_kwh=?,cost_manual=1 WHERE id=?",
                    (cost, price_kwh, sid))
    else:
        con.execute("UPDATE sessions SET cost_eur=?,cost_manual=1 WHERE id=?", (cost, sid))
    con.commit()
    close_db_if_owned(con)
    return jsonify({"ok": True, "cost_eur": cost})


@sessions_bp.route("/api/sessions/<int:sid>", methods=["PATCH"])
@require_login
def api_patch_session(sid):
    """Edit session fields — extended to support all relevant fields."""
    if not has_permission(_current_user(), "sessions:edit"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: sessions:edit"}), 403
    data = request.get_json(force=True) or {}
    # Track which cost fields the user explicitly sent (before filtering)
    _user_sent_cost_fields = {"price_per_kwh", "cost_eur"} & set(data.keys())
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

    # ── Load existing session (404 if not found) ──────────────────────────────
    con = _get_db()
    existing = con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not existing:
        close_db_if_owned(con)
        return jsonify({"ok": False, "error": "Session nicht gefunden"}), 404
    existing = dict(existing)

    # ── Enum validation ───────────────────────────────────────────────────────
    if "location" in fields and fields["location"] not in ("home", "extern", "unknown"):
        close_db_if_owned(con)
        return jsonify({"ok": False, "error": "location muss home, extern oder unknown sein"}), 400
    if "charger_type" in fields and fields["charger_type"] not in ("ac", "dc", "unknown"):
        close_db_if_owned(con)
        return jsonify({"ok": False, "error": "charger_type muss ac, dc oder unknown sein"}), 400

    # ── Numeric range validation ──────────────────────────────────────────────
    def _fv(key):
        """Get float value from fields or existing row."""
        if key in fields:
            try:
                return float(fields[key])
            except (TypeError, ValueError):
                return None
        v = existing.get(key)
        return float(v) if v is not None else None

    for key in ("kwh_charged", "cost_eur", "price_per_kwh", "charger_power_kw", "max_power_kw"):
        if key in fields:
            try:
                val = float(fields[key])
            except (TypeError, ValueError):
                close_db_if_owned(con)
                return jsonify({"ok": False, "error": f"{key} muss eine Zahl sein"}), 400
            if val < 0:
                close_db_if_owned(con)
                return jsonify({"ok": False, "error": f"{key} darf nicht negativ sein"}), 400

    for key in ("soc_start", "soc_end"):
        if key in fields:
            try:
                val = float(fields[key])
            except (TypeError, ValueError):
                close_db_if_owned(con)
                return jsonify({"ok": False, "error": f"{key} muss eine Zahl sein"}), 400
            if not (0 <= val <= 100):
                close_db_if_owned(con)
                return jsonify({"ok": False, "error": f"{key} muss zwischen 0 und 100 liegen"}), 400

    odo_start = _fv("odo_start")
    odo_end   = _fv("odo_end")
    if odo_start is not None and odo_end is not None and odo_end < odo_start:
        close_db_if_owned(con)
        return jsonify({"ok": False, "error": "odo_end darf nicht kleiner als odo_start sein"}), 400

    # ── Timestamp validation ──────────────────────────────────────────────────
    start_ts = fields.get("start_ts") or existing.get("start_ts")
    end_ts   = fields.get("end_ts")   or existing.get("end_ts")
    if start_ts and end_ts:
        try:
            if end_ts <= start_ts:
                close_db_if_owned(con)
                return jsonify({"ok": False, "error": "end_ts muss nach start_ts liegen"}), 400
        except TypeError:
            pass

    # ── Meter validation + delta recalculation ────────────────────────────────
    meter_old = _fv("meter_old")
    meter_new = _fv("meter_new")
    if meter_old is not None and meter_new is not None:
        if meter_new < meter_old:
            close_db_if_owned(con)
            return jsonify({"ok": False, "error": "meter_new darf nicht kleiner als meter_old sein"}), 400
        fields["meter_delta_kwh"] = round(meter_new - meter_old, 3)
        fields["meter_used"] = 1
    elif "meter_old" in fields and meter_new is None:
        # Only meter_old changed — fetch current meter_new and recalculate
        cur_new = existing.get("meter_new")
        if cur_new is not None:
            try:
                delta = float(cur_new) - float(fields["meter_old"])
                if delta >= 0:
                    fields["meter_delta_kwh"] = round(delta, 3)
            except (TypeError, ValueError):
                pass
    elif "meter_new" in fields and meter_old is None:
        cur_old = existing.get("meter_old")
        if cur_old is not None:
            try:
                delta = float(fields["meter_new"]) - float(cur_old)
                if delta >= 0:
                    fields["meter_delta_kwh"] = round(delta, 3)
                    fields["meter_used"] = 1
            except (TypeError, ValueError):
                pass

    # ── Auto-compute cost ─────────────────────────────────────────────────────
    if "kwh_charged" in fields and "price_per_kwh" in fields and "cost_eur" not in fields:
        try:
            fields["cost_eur"] = round(float(fields["kwh_charged"]) * float(fields["price_per_kwh"]), 2)
        except (ValueError, TypeError):
            pass

    if _user_sent_cost_fields:
        # User explicitly provided price/cost → mark as manual
        fields["cost_manual"] = 1
    elif existing.get("cost_manual", 0) == 0:
        # Re-price automatically when location, charger_type, or kwh changes and no manual override
        _reprice_triggers = {"location", "charger_type", "kwh_charged"}
        if _reprice_triggers & set(fields.keys()):
            try:
                from services.pricing_service import resolve_session_price, calculate_session_cost
                from core.config import load_config
                _cfg = load_config()
                _loc = fields.get("location") or existing.get("location") or "unknown"
                _ct  = fields.get("charger_type") or existing.get("charger_type") or "ac"
                _kwh = float(fields.get("kwh_charged") or existing.get("kwh_charged") or 0)
                _pr  = resolve_session_price(_loc, _ct, _cfg, con, sid)
                if _pr["price_per_kwh"] is not None:
                    fields["price_per_kwh"] = _pr["price_per_kwh"]
                    fields["price_source"]   = _pr["price_source"]
                    fields["price_confidence"] = _pr["price_confidence"]
                    if _pr.get("contract_id"):
                        fields["charging_contract_id"]   = _pr["contract_id"]
                        fields["charging_contract_name"] = _pr["contract_name"]
                    if _kwh > 0:
                        fields["cost_eur"] = calculate_session_cost(_kwh, _pr["price_per_kwh"])
            except Exception as _e:
                log.debug("PATCH auto-repricing failed: %s", _e)

    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [sid]
    con.execute(f"UPDATE sessions SET {set_clause} WHERE id=?", values)
    con.commit()
    _audit("session_edited", f"session_id={sid} fields={list(fields.keys())}", ip=request.remote_addr)
    close_db_if_owned(con)
    return jsonify({"ok": True, "id": sid})


@sessions_bp.route("/api/sessions/<int:sid>/recalculate-cost", methods=["POST"])
@require_login
def api_session_recalculate_cost(sid):
    """
    Clears cost_manual flag and re-prices the session using current config
    (contract, fallback prices, home tariff). Does not change kwh_charged.
    """
    user = _current_user()
    if not has_permission(user, "sessions:edit"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: sessions:edit"}), 403

    con = _get_db()
    row = con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not row:
        close_db_if_owned(con)
        return jsonify({"ok": False, "error": "Session nicht gefunden"}), 404
    session = dict(row)

    location     = session.get("location") or "unknown"
    charger_type = session.get("charger_type") or "ac"
    kwh          = session.get("kwh_charged")

    try:
        from services.pricing_service import resolve_session_price, calculate_session_cost
        from core.config import load_config
        cfg = load_config()
        pr  = resolve_session_price(location, charger_type, cfg, con, sid)
    except Exception as _e:
        close_db_if_owned(con)
        return jsonify({"ok": False, "error": f"Preisberechnung fehlgeschlagen: {_e}"}), 500

    if pr["price_per_kwh"] is None:
        close_db_if_owned(con)
        return jsonify({"ok": False, "error": "Kein Preis ermittelbar für diesen Standort/Ladetyp."}), 422

    cost = calculate_session_cost(kwh, pr["price_per_kwh"])
    con.execute(
        """UPDATE sessions
           SET cost_manual=0, price_per_kwh=?, cost_eur=?,
               price_source=?, price_confidence=?,
               charging_contract_id=COALESCE(?,charging_contract_id),
               charging_contract_name=COALESCE(?,charging_contract_name)
           WHERE id=?""",
        (pr["price_per_kwh"], cost,
         pr.get("price_source"), pr.get("price_confidence"),
         pr.get("contract_id"), pr.get("contract_name"),
         sid),
    )
    con.commit()
    _audit("session_recalculated", f"session_id={sid} source={pr.get('price_source')}", ip=request.remote_addr)
    close_db_if_owned(con)
    return jsonify({
        "ok": True,
        "id": sid,
        "price_per_kwh": pr["price_per_kwh"],
        "cost_eur": cost,
        "price_source": pr.get("price_source"),
        "price_confidence": pr.get("price_confidence"),
    })


@sessions_bp.route("/api/stats/monthly")
@require_login
def api_monthly_stats():
    user = _current_user()
    if not has_permission(user, "sessions:view") and not has_permission(user, "analytics:view"):
        return jsonify({"error": "Keine Berechtigung: sessions:view oder analytics:view"}), 403
    return jsonify(_get_monthly_stats())
