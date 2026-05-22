"""
External REST API v1 routes (Bearer token auth).
"""
import json
from datetime import datetime

from flask import Blueprint, jsonify, request

from core.db import _get_db, close_db_if_owned
from core.config import load_config
from core.tokens import _require_api_token
from version import APP_VERSION

api_v1_bp = Blueprint("api_v1", __name__)


@api_v1_bp.route("/api/v1/status")
def api_v1_status():

    token_row, err_resp, code = _require_api_token("system:read")
    if err_resp: return err_resp, code
    return jsonify({"status": "ok", "version": APP_VERSION, "ts": datetime.utcnow().isoformat()})


@api_v1_bp.route("/api/v1/vehicles")
def api_v1_vehicles():
    token_row, err_resp, code = _require_api_token("vehicles:read")
    if err_resp: return err_resp, code
    cfg = load_config()
    vehicles = [{"id": "v0", "name": cfg.get("car_name","EV"), "provider": cfg.get("provider","ha")}]
    for v in cfg.get("extra_vehicles", []):
        vehicles.append({"id": v.get("id"), "name": v.get("car_name"), "provider": v.get("provider")})
    return jsonify(vehicles)


@api_v1_bp.route("/api/v1/sessions")
def api_v1_sessions():
    token_row, err_resp, code = _require_api_token("sessions:read")
    if err_resp: return err_resp, code
    try: limit = min(int(request.args.get("limit", 50)), 500)
    except (ValueError, TypeError): limit = 50
    try: offset = int(request.args.get("offset", 0))
    except (ValueError, TypeError): offset = 0
    vid    = request.args.get("vehicle_id")
    con    = _get_db()
    if vid:
        rows = con.execute(
            "SELECT * FROM sessions WHERE vehicle_id=? ORDER BY start_ts DESC LIMIT ? OFFSET ?",
            (vid, limit, offset)).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM sessions ORDER BY start_ts DESC LIMIT ? OFFSET ?",
            (limit, offset)).fetchall()
    close_db_if_owned(con)
    return jsonify([dict(r) for r in rows])


@api_v1_bp.route("/api/v1/sessions/<int:session_id>")
def api_v1_session_get(session_id):
    token_row, err_resp, code = _require_api_token("sessions:read")
    if err_resp: return err_resp, code
    con = _get_db()
    row = con.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    close_db_if_owned(con)
    if not row: return jsonify({"error": "Session nicht gefunden"}), 404
    return jsonify(dict(row))


@api_v1_bp.route("/api/v1/sessions", methods=["POST"])
def api_v1_session_create():
    token_row, err_resp, code = _require_api_token("sessions:write")
    if err_resp: return err_resp, code
    data = request.get_json(force=True) or {}
    con  = _get_db()
    cur  = con.execute("""INSERT INTO sessions
        (start_ts,end_ts,kwh_charged,cost_eur,location,vehicle_id,provider)
        VALUES (?,?,?,?,?,?,?)""",
        (data.get("start_ts"), data.get("end_ts"),
         data.get("kwh_charged"), data.get("cost_eur"),
         data.get("location","unknown"), data.get("vehicle_id","v0"), "api"))
    sid = cur.lastrowid
    con.commit(); close_db_if_owned(con)
    return jsonify({"ok": True, "id": sid}), 201


@api_v1_bp.route("/api/v1/sessions/<int:session_id>", methods=["PUT"])
def api_v1_session_update(session_id):
    token_row, err_resp, code = _require_api_token("sessions:write")
    if err_resp: return err_resp, code
    data = request.get_json(force=True) or {}
    allowed = {"kwh_charged","cost_eur","location","end_ts","soc_end","odo_end"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "Keine gültigen Felder"}), 400
    con = _get_db()
    if not con.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
        close_db_if_owned(con); return jsonify({"error": "Session nicht gefunden"}), 404
    sets = ", ".join(f"{k}=?" for k in updates)
    con.execute(f"UPDATE sessions SET {sets} WHERE id=?",
                list(updates.values()) + [session_id])
    con.commit(); close_db_if_owned(con)
    return jsonify({"ok": True})


@api_v1_bp.route("/api/v1/reports")
def api_v1_reports():
    token_row, err_resp, code = _require_api_token("reports:read")
    if err_resp: return err_resp, code
    try: limit = min(int(request.args.get("limit", 50)), 200)
    except (ValueError, TypeError): limit = 50
    con   = _get_db()
    rows  = con.execute(
        "SELECT id,created_at,vehicle_id,period_label,status,sent_at FROM reports ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    close_db_if_owned(con)
    return jsonify([dict(r) for r in rows])


@api_v1_bp.route("/api/v1/reports/create", methods=["POST"])
def api_v1_reports_create():
    """Create a report via API token. Requires scope 'reports:create'.
    Supports period_mode: previous_period, current_period, single_month, multiple_months.
    """
    from services.report_service import calculate_report_periods, _get_report_sessions, _save_report_record
    from core.security import _audit
    from core.db import DB_PATH
    token_row, err_resp, code = _require_api_token("reports:create")
    if err_resp: return err_resp, code
    data        = request.get_json(force=True) or {}
    cfg         = load_config()
    # Allow request to override period config temporarily
    for k in ["report_email_single_month", "report_email_months"]:
        if k in data: cfg[k] = data[k]
    vehicle_id  = data.get("vehicle_id", "v0")
    loc_filter  = data.get("location_filter", "all")
    veh_filter  = data.get("vehicle_filter", vehicle_id)
    period_mode = data.get("period_mode", cfg.get("report_email_period_mode", "previous_period"))
    stype       = data.get("schedule_type", cfg.get("report_email_schedule_type", "monthly"))
    lang        = data.get("lang", cfg.get("report_email_language", "de"))
    if lang == "auto": lang = "de"

    periods = calculate_report_periods(stype, period_mode, datetime.now(), cfg)
    if not periods:
        return jsonify({"ok": False, "error": "Ungültiger Zeitraum"}), 400

    if period_mode == "multiple_months" and len(periods) > 1:
        combined_key = "months:" + ",".join(
            p["period_key"].replace("monthly:", "") for p in periods)
        period_label = f"{len(periods)} Monate"
    else:
        combined_key = periods[0]["period_key"]
        period_label = periods[0].get("label_de", combined_key)

    all_sessions = []
    for p in periods:
        all_sessions.extend(_get_report_sessions(p["start"], p["end"], loc_filter, veh_filter))

    period_info = dict(periods[0])
    if len(periods) > 1:
        period_info["period_key"] = combined_key
        period_info["label_de"]   = period_label
        period_info["end"]        = periods[-1]["end"]

    excel_bytes = None
    try:
        if len(periods) > 1:
            from export_excel import export_multi_month_bytes as _emm
            ps_list = [(p, _get_report_sessions(p["start"], p["end"], loc_filter, veh_filter))
                       for p in periods]
            excel_bytes, _ = _emm(periods_sessions=ps_list, loc_filter=loc_filter,
                                   config=cfg, lang=lang)
        else:
            from export_excel import export as _exp
            xl_loc = "extern" if loc_filter == "external" else loc_filter
            excel_bytes, _ = _exp(year=period_info["start"].year,
                                   month=period_info["start"].month,
                                   location=xl_loc, config=cfg, lang=lang, return_warnings=True)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("API v1 report Excel fehlgeschlagen: %s", e)

    summary = {
        "sessions": len(all_sessions),
        "total_kwh": round(sum(s.get("kwh_charged") or 0 for s in all_sessions), 3),
        "total_cost": round(sum(s.get("cost_eur") or 0 for s in all_sessions), 2),
        "period_key": combined_key, "period_label": period_label,
    }
    report_id = _save_report_record(vehicle_id, period_info, loc_filter, veh_filter,
                                    "created", token_row.get("id"),
                                    excel_bytes=excel_bytes, summary=summary)
    _audit("api_report_created",
           f"token={token_row.get('name')} report={report_id} period={combined_key}",
           ip=request.remote_addr)
    try:
        from notification_manager import fire_event as _fe
        _fe("report_created", {"report_id": report_id, "via": "api_v1",
                                "period": combined_key}, cfg, db_path=DB_PATH)
    except Exception: pass
    return jsonify({"ok": True, "report_id": report_id, "summary": summary}), 201
