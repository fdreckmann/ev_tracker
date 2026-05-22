"""
Main page and app-level API routes: config, providers, status, state, mobile summary.
"""
import json
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, make_response

from core.db import _get_db, close_db_if_owned, DATA_DIR
from core.config import load_config, save_config, DEFAULT_CONFIG
from version import APP_VERSION
from core.security import (
    require_login, has_permission, _current_user, _audit,
    _has_users, _get_user_permissions, ALL_PERMISSIONS,
)
import core.state as _state

main_routes_bp = Blueprint("main_routes", __name__)

_SENSITIVE_CONFIG_KEYS = {
    "smtp_password", "oauth_google_client_secret", "oauth_microsoft_client_secret",
    "smtp_google_client_secret", "smtp_google_refresh_token", "smtp_google_access_token",
    "smtp_ms_client_secret", "smtp_ms_refresh_token", "smtp_ms_access_token",
    "meter_password", "meter_alfen_pass",
    "ha_token", "entsoe_api_key", "octopus_api_key", "tibber_token", "tariff_ha_token",
    "mqtt_password", "ntfy_token", "gotify_token",
}
_SECRET_MASK = "********"


@main_routes_bp.route("/")
@require_login
def index():
    from server import CHANGELOG
    from providers import get_all_capabilities, get_config_fields, PROVIDERS
    from services.vehicle_service import get_all_vehicles
    _TEMPLATE_PATH = DATA_DIR / "template.xlsx"
    cfg = load_config()
    caps = get_all_capabilities()
    provider_fields = get_config_fields(cfg.get("provider","ha"))
    resp = make_response(render_template("index.html", cfg=cfg, state=_state,
                           has_template=_TEMPLATE_PATH.exists(),
                           all_providers=caps,
                           provider_fields=provider_fields,
                           provider_names={k:v.PROVIDER_NAME for k,v in PROVIDERS.items()},
                           app_version=APP_VERSION,
                           changelog=CHANGELOG,
                           all_vehicles=get_all_vehicles(cfg),
                           current_user=_current_user()))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"]        = "no-cache"
    resp.headers["Expires"]       = "0"
    return resp


@main_routes_bp.route("/api/config", methods=["GET"])
@require_login
def api_get_config():
    cfg = dict(load_config())
    for k in _SENSITIVE_CONFIG_KEYS:
        if cfg.get(k):
            cfg[k] = _SECRET_MASK
    return jsonify(cfg)


@main_routes_bp.route("/api/config", methods=["POST"])
@require_login
def api_save_config():
    if not has_permission(_current_user(), "settings:edit"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: settings:edit"}), 403
    data = request.json or {}
    cfg  = load_config()
    floats = {"battery_capacity_kwh","price_per_kwh_home","price_per_kwh_ac","price_per_kwh_dc",
              "dc_threshold_kw","entsoe_ac_markup","entsoe_dc_markup","home_radius_m"}
    ints = {"poll_interval"}
    # Build set of accepted keys: DEFAULT_CONFIG + current provider's field IDs
    accepted_keys = set(DEFAULT_CONFIG.keys())
    try:
        from providers import get_config_fields
        provider_id = data.get("provider", cfg.get("provider","ha"))
        for f in get_config_fields(provider_id):
            accepted_keys.add(f["id"])
    except Exception:
        pass
    for key in accepted_keys:
        if key in data:
            v = data[key]
            if key in _SENSITIVE_CONFIG_KEYS and v == _SECRET_MASK:
                continue
            if key in floats and v != "":
                try: v = float(v)
                except (ValueError, TypeError): pass
            elif key in ints:
                try: v = int(v)
                except (ValueError, TypeError): pass
            cfg[key] = v
    save_config(cfg)
    if any(str(k).startswith("smtp_") for k in data):
        _audit("smtp_config_updated", ip=request.remote_addr)
    return jsonify({"ok":True})


@main_routes_bp.route("/api/providers")
@require_login
def api_providers():
    from providers import get_all_capabilities
    return jsonify(get_all_capabilities())


@main_routes_bp.route("/api/providers/<provider_id>/fields")
@require_login
def api_provider_fields(provider_id):
    from providers import get_config_fields
    return jsonify(get_config_fields(provider_id))


@main_routes_bp.route("/api/status")
@require_login
def api_status():
    vid = request.args.get("vehicle_id","v0")
    st  = _state.vehicle_states.get(vid, _state.vehicle_states.get("v0", {}))
    result = dict(st)
    result["all_vehicles"] = [
        {"vehicle_id": k, "name": v.get("name",k), "running": v.get("running",False),
         "charging": v.get("charging",False), "session_active": v.get("session_active",False)}
        for k, v in _state.vehicle_states.items()
    ]
    return jsonify(result)


@main_routes_bp.route("/api/state")
@require_login
def api_state_alias():
    """Alias for /api/status — mobile compatibility."""
    return api_status()


@main_routes_bp.route("/api/mobile/summary")
@require_login
def api_mobile_summary():
    """Single aggregated endpoint for mobile dashboard — reduces multiple API calls to one."""
    from services.vehicle_service import get_all_vehicles
    user = _current_user()
    cfg = load_config()

    # Current vehicle state (v0 primary)
    state = _state.vehicle_states.get("v0", {})
    charging = state.get("charging", False)
    session_id = state.get("session_id")

    # All vehicles (for vehicle switcher)
    all_vehicles_cfg = get_all_vehicles(include_archived=False)
    vehicles_out = []
    for v in all_vehicles_cfg:
        vid = v.get("id", "v0")
        vs = _state.vehicle_states.get(vid, {})
        vehicles_out.append({
            "id": vid,
            "name": v.get("car_name", v.get("name", vid)),
            "charging": vs.get("charging", False),
            "soc": vs.get("soc_current"),
            "location": vs.get("location"),
            "location_status": vs.get("location_status"),
            "power_kw": vs.get("power_kw"),
            "session_active": vs.get("session_active", False),
            "image_url": f"/api/vehicles/{vid}/image/file",
        })

    # Recent sessions (last 10)
    con = _get_db()
    try:
        recent_rows = con.execute(
            "SELECT id, start_ts, end_ts, location, charger_type, kwh_charged, cost_eur, soc_start, soc_end, max_power_kw, vehicle_id "
            "FROM sessions ORDER BY start_ts DESC LIMIT 10"
        ).fetchall()
        recent_sessions = [dict(r) for r in recent_rows]

        # Monthly stats (current month)
        from datetime import date
        today = date.today()
        month_prefix = today.strftime("%Y-%m")
        stats_row = con.execute(
            "SELECT COUNT(*) as cnt, SUM(kwh_charged) as kwh, SUM(cost_eur) as cost "
            "FROM sessions WHERE start_ts LIKE ? AND end_ts IS NOT NULL",
            (f"{month_prefix}%",)
        ).fetchone()
        monthly = {
            "sessions": stats_row["cnt"] or 0,
            "kwh": round(stats_row["kwh"] or 0, 2),
            "cost": round(stats_row["cost"] or 0, 2),
        }

        # Active session details
        active_session = None
        if session_id:
            sess_row = con.execute(
                "SELECT id, start_ts, soc_start, location, charger_type, meter_old FROM sessions WHERE id=?",
                (session_id,)
            ).fetchone()
            if sess_row:
                active_session = dict(sess_row)
    finally:
        close_db_if_owned(con)

    # Permissions summary for mobile UI gating
    perms = _get_user_permissions(user["id"]) if user else set()
    perm_summary = {
        "can_add_session": "sessions:manual_add" in perms or user.get("role") == "admin",
        "can_export": "export:download" in perms or user.get("role") == "admin",
        "can_backup": "backup:create" in perms or user.get("role") == "admin",
        "can_test_meter": "meter:test" in perms or user.get("role") == "admin",
        "can_test_connection": "providers:test" in perms or user.get("role") == "admin",
    }

    return jsonify({
        "ok": True,
        "primary_vehicle": vehicles_out[0] if vehicles_out else None,
        "vehicles": vehicles_out,
        "charging": charging,
        "active_session": active_session,
        "recent_sessions": recent_sessions,
        "monthly": monthly,
        "state": {
            "soc": state.get("soc_current"),
            "odo": state.get("odo_current"),
            "location": state.get("location"),
            "location_status": state.get("location_status"),
            "power_kw": state.get("power_kw"),
            "last_poll": state.get("last_poll"),
            "last_error": state.get("last_error"),
            "running": state.get("running", False),
        },
        "permissions": perm_summary,
    })


@main_routes_bp.route("/api/tracker/restart", methods=["POST"])
@require_login
def api_tracker_restart():
    if not has_permission(_current_user(), "settings:edit") and not has_permission(_current_user(), "providers:test"):
        return jsonify({"ok": False, "error": "Keine Berechtigung"}), 403
    vid = request.json.get("vehicle_id", "v0") if request.json else "v0"
    try:
        from services.vehicle_service import get_vehicle_tracker_funcs, start_tracker
        _start_vehicle_tracker, _stop_vehicle_tracker = get_vehicle_tracker_funcs()
        import time as _time
        if vid == "v0":
            _stop_vehicle_tracker("v0")
            _time.sleep(0.5)
            # Reset error state but keep history
            from core.state import vehicle_states
            if "v0" in vehicle_states:
                vehicle_states["v0"]["last_error"] = None
                vehicle_states["v0"]["last_fatal_error"] = None
                vehicle_states["v0"]["tracker_alive"] = False
            start_tracker()
        else:
            _stop_vehicle_tracker(vid)
            _time.sleep(0.5)
            _start_vehicle_tracker(vid)
        return jsonify({"ok": True, "message": f"Tracker {vid} neu gestartet"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
