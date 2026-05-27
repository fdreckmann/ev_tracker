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
    kwh       = data.get("kwh_charged")
    price_kwh = data.get("price_per_kwh")
    cost_eur  = data.get("cost_eur")
    cost_manual = 0
    _price_source = None; _price_conf = 0; _contract_id = None; _contract_name = None

    if cost_eur is not None:
        cost_manual = 1
    elif kwh is not None and price_kwh is not None:
        try: cost_eur = round(float(kwh) * float(price_kwh), 2); cost_manual = 1
        except (ValueError, TypeError): pass
    elif kwh is not None:
        # Auto-price via pricing_service
        try:
            from services.pricing_service import resolve_session_price, calculate_session_cost
            cfg = load_config()
            con_tmp = _get_db()
            location     = data.get("location", "unknown")
            charger_type = data.get("charger_type", "unknown")
            _pr = resolve_session_price(location, charger_type, cfg, con_tmp)
            close_db_if_owned(con_tmp)
            if _pr["price_per_kwh"] is not None:
                price_kwh     = _pr["price_per_kwh"]
                cost_eur      = calculate_session_cost(float(kwh), price_kwh)
                _price_source = _pr.get("price_source")
                _price_conf   = _pr.get("price_confidence", 0)
                _contract_id  = _pr.get("contract_id")
                _contract_name= _pr.get("contract_name")
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("api_v1 auto-pricing failed: %s", _e)

    # Normalize location/charger_type before storing
    try:
        from services.pricing_service import normalize_location as _nl, normalize_charger_type as _nct
        _loc_store = _nl(data.get("location", "unknown"))
        _ctype_store = _nct(data.get("charger_type", "unknown"))
    except Exception:
        _loc_store = data.get("location", "unknown")
        _ctype_store = data.get("charger_type", "unknown")

    con = _get_db()
    cur = con.execute("""INSERT INTO sessions
        (start_ts, end_ts, kwh_charged, cost_eur, cost_manual, price_per_kwh,
         location, charger_type, charger_power_kw, max_power_kw,
         soc_start, soc_end, odo_start, odo_end,
         meter_old, meter_new, vehicle_id, provider, kwh_source, created_mode,
         manual_note, manual_reason,
         price_source, price_confidence, charging_contract_id, charging_contract_name)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (data.get("start_ts"), data.get("end_ts"),
         kwh, cost_eur, cost_manual, price_kwh,
         _loc_store, _ctype_store,
         data.get("charger_power_kw"), data.get("max_power_kw"),
         data.get("soc_start"), data.get("soc_end"),
         data.get("odo_start"), data.get("odo_end"),
         data.get("meter_old"), data.get("meter_new"),
         data.get("vehicle_id","v0"), "api", "api", "api",
         data.get("manual_note"), data.get("manual_reason"),
         _price_source, _price_conf, _contract_id, _contract_name))
    sid = cur.lastrowid
    con.commit(); close_db_if_owned(con)
    return jsonify({"ok": True, "id": sid}), 201


@api_v1_bp.route("/api/v1/sessions/<int:session_id>", methods=["PUT"])
def api_v1_session_update(session_id):
    token_row, err_resp, code = _require_api_token("sessions:write")
    if err_resp: return err_resp, code
    data = request.get_json(force=True) or {}
    allowed = {"kwh_charged","cost_eur","price_per_kwh","location","end_ts","soc_end","odo_end","charger_type"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "Keine gültigen Felder"}), 400

    # Normalize location and charger_type before storing
    try:
        from services.pricing_service import normalize_location as _nl, normalize_charger_type as _nct
        if "location" in updates:
            updates["location"] = _nl(updates["location"])
        if "charger_type" in updates:
            updates["charger_type"] = _nct(updates["charger_type"])
    except Exception:
        pass

    # Explicit price/cost from caller → mark as manual
    user_sent_cost = bool({"cost_eur", "price_per_kwh"} & set(data.keys()))
    if user_sent_cost:
        updates["cost_manual"] = 1

    con = _get_db()
    existing = con.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not existing:
        close_db_if_owned(con); return jsonify({"error": "Session nicht gefunden"}), 404
    existing = dict(existing)

    # If only price_per_kwh was given (no cost_eur), calculate cost_eur from stored kwh
    if user_sent_cost and "price_per_kwh" in updates and "cost_eur" not in updates:
        try:
            kwh_val = updates.get("kwh_charged") or existing.get("kwh_charged")
            if kwh_val is not None:
                from services.pricing_service import calculate_session_cost
                updates["cost_eur"] = calculate_session_cost(float(kwh_val), float(updates["price_per_kwh"]))
        except Exception:
            pass

    # Reprice when location/charger_type/kwh_charged changes and cost was not manually set
    _reprice_triggers = {"location", "charger_type", "kwh_charged"}
    if not user_sent_cost and existing.get("cost_manual", 0) == 0 and _reprice_triggers & set(updates.keys()):
        try:
            from services.pricing_service import resolve_session_price, calculate_session_cost
            cfg = load_config()
            new_loc = updates.get("location", existing.get("location", "unknown"))
            new_ctype = updates.get("charger_type", existing.get("charger_type", "unknown"))
            new_kwh = updates.get("kwh_charged", existing.get("kwh_charged"))
            _pr = resolve_session_price(new_loc, new_ctype, cfg, con, session_id)
            if _pr["price_per_kwh"] is not None and new_kwh is not None:
                updates["price_per_kwh"] = _pr["price_per_kwh"]
                updates["cost_eur"] = calculate_session_cost(float(new_kwh), _pr["price_per_kwh"])
                updates["price_source"] = _pr.get("price_source")
                updates["price_confidence"] = _pr.get("price_confidence", 0)
                updates["charging_contract_id"] = _pr.get("contract_id")
                updates["charging_contract_name"] = _pr.get("contract_name")
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("api_v1 PUT reprice failed: %s", _e)

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
            from core.location import normalize_location as _nl
            xl_loc = _nl(loc_filter) if loc_filter not in ("all",) else loc_filter
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
