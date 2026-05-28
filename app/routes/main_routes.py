"""
Main page and app-level API routes: config, providers, status, state, mobile summary.
"""
import json
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, make_response

from core.db import _get_db, close_db_if_owned, DATA_DIR
from core.config import load_config, save_config, DEFAULT_CONFIG
from version import APP_VERSION, ASSET_VERSION, CHANGELOG
from core.security import (
    require_login, has_permission, _current_user, _audit,
    _has_users, _get_user_permissions, ALL_PERMISSIONS,
)
import core.state as _state
from core.secrets import mask_config as _mask_config, is_masked as _is_masked, SECRET_MASK as _SECRET_MASK

main_routes_bp = Blueprint("main_routes", __name__)


@main_routes_bp.route("/")
@require_login
def index():
    from providers import get_all_capabilities, get_config_fields, PROVIDERS
    from services.vehicle_service import get_all_vehicles
    _TEMPLATE_PATH = DATA_DIR / "template.xlsx"
    cfg = load_config()
    caps = get_all_capabilities()
    provider_fields = get_config_fields(cfg.get("provider","ha"))
    # Never expose secrets in the HTML source — pass a masked copy to the template
    cfg_safe = _mask_config(cfg)
    resp = make_response(render_template("index.html", cfg=cfg_safe, state=_state,
                           has_template=_TEMPLATE_PATH.exists(),
                           all_providers=caps,
                           provider_fields=provider_fields,
                           provider_names={k:v.PROVIDER_NAME for k,v in PROVIDERS.items()},
                           app_version=APP_VERSION,
                           asset_version=ASSET_VERSION,
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
    return jsonify(_mask_config(load_config()))


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
            if _is_masked(v):
                continue  # never overwrite stored secret with the mask placeholder
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
    # Invalidate meter status cache if meter config changed
    if any(str(k).startswith("meter_") for k in data):
        try:
            from routes.connections import _meter_status_cache
            _meter_status_cache.clear()
        except Exception:
            pass
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


def _compute_tracker_status(st: dict, cfg: dict | None = None) -> str:
    """Return one of: not_configured, stopped, provider_error, no_data, polling, charging, ready."""
    if not (st.get("running") or st.get("tracker_alive")):
        # Distinguish "not configured" (no provider set up) from plain "stopped"
        if cfg is not None:
            provider_id = cfg.get("provider", "ha")
            if not provider_id or provider_id == "none":
                return "not_configured"
            if provider_id == "ha" and not cfg.get("ha_url") and not cfg.get("ha_token"):
                return "not_configured"
            if provider_id == "manual":
                return "not_configured"
        elif not st:
            # Empty state dict with no config info → brand-new install
            return "not_configured"
        return "stopped"
    if st.get("last_fatal_error"):
        return "provider_error"
    if not st.get("provider_connected") and st.get("last_error"):
        return "provider_error"
    if not st.get("last_successful_poll"):
        return "no_data"
    if st.get("charging"):
        return "charging"
    # Detect sleeping vehicle: SOC not null, no activity for >60s
    last_poll = st.get("last_poll")
    if last_poll and not st.get("provider_connected"):
        return "polling"
    return "ready"


@main_routes_bp.route("/api/status")
@require_login
def api_status():
    from core.location import effective_session_location
    from services.location_service import refresh_vehicle_location_state
    from services.vehicle_service import get_all_vehicles
    vid = request.args.get("vehicle_id", "v0")
    st  = _state.vehicle_states.get(vid, _state.vehicle_states.get("v0", {}))
    result = dict(st)
    _cfg_for_status = load_config()
    result["tracker_status"] = _compute_tracker_status(st, _cfg_for_status)
    result["all_vehicles"] = [
        {"vehicle_id": k, "name": v.get("name", k), "running": v.get("running", False),
         "charging": v.get("charging", False), "session_active": v.get("session_active", False),
         "tracker_status": _compute_tracker_status(v)}
        for k, v in _state.vehicle_states.items()
    ]
    # Refresh location (TTL-cached, so concurrent calls don't hammer HA)
    try:
        loc = refresh_vehicle_location_state(vid)
        result["location_status"]        = loc.get("status", "unknown")
        result["location_source"]        = loc.get("source", "none")
        result["location_source_detail"] = loc.get("source_detail", "")
        result["location_timestamp"]     = loc.get("timestamp", "")
        result["effective_location"]     = loc.get("status", "unknown")
    except Exception as _loc_exc:
        result.setdefault("location_status", "unknown")
        result["location_error"] = str(_loc_exc)
        result["effective_location"] = effective_session_location(
            result.get("location"), result.get("location_status")
        )
    # Provider info for the requested vehicle
    try:
        from providers import PROVIDERS
        cfg = _cfg_for_status
        provider_id = cfg.get("provider", "ha")
        # For non-v0 vehicles, prefer vehicle-specific config
        if vid != "v0":
            for v in get_all_vehicles(cfg):
                if v.get("id") == vid:
                    provider_id = v.get("provider", provider_id)
                    break
        try:
            provider_name = PROVIDERS[provider_id].PROVIDER_NAME
        except (KeyError, AttributeError):
            provider_name = provider_id
        result["vehicle_id"]       = vid
        result["provider"]         = provider_id
        result["provider_id"]      = provider_id
        result["provider_name"]    = provider_name
        result["provider_connected"]   = st.get("provider_connected")
        result["provider_last_error"]  = st.get("last_error")
        result["provider_last_success"]= st.get("last_successful_poll")
    except Exception:
        pass
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
    from core.location import effective_session_location
    from services.location_service import refresh_vehicle_location_state
    try:
        from providers import PROVIDERS as _PROVIDERS
    except Exception:
        _PROVIDERS = {}

    vehicles_out = []
    for v in all_vehicles_cfg:
        vid = v.get("id", "v0")
        vs = _state.vehicle_states.get(vid, {})
        # Refresh location for primary vehicle; extras use cached state
        try:
            loc = refresh_vehicle_location_state(vid)
            loc_status  = loc.get("status", "unknown")
            loc_source  = loc.get("source", "none")
            loc_detail  = loc.get("source_detail", "")
            loc_ts      = loc.get("timestamp", "")
        except Exception:
            loc_status = vs.get("location_status", "unknown")
            loc_source = vs.get("location_source", "none")
            loc_detail = vs.get("location_source_detail", "")
            loc_ts     = vs.get("location_timestamp", "")
        v_provider_id = v.get("provider", cfg.get("provider", "ha"))
        try:
            v_provider_name = _PROVIDERS[v_provider_id].PROVIDER_NAME
        except (KeyError, AttributeError):
            v_provider_name = v_provider_id
        vehicles_out.append({
            "id": vid,
            "name": v.get("car_name", v.get("name", vid)),
            "charging": vs.get("charging", False),
            "soc": vs.get("soc_current"),
            "location": vs.get("location"),
            "location_status": loc_status,
            "location_source": loc_source,
            "location_source_detail": loc_detail,
            "location_timestamp": loc_ts,
            "effective_location": loc_status if loc_status not in ("unknown", "disabled") else effective_session_location(
                vs.get("location"), loc_status
            ),
            "power_kw": vs.get("power_kw"),
            "session_active": vs.get("session_active", False),
            "image_url": f"/api/vehicles/{vid}/image/file",
            "provider": v_provider_id,
            "provider_id": v_provider_id,
            "provider_name": v_provider_name,
            "provider_connected": vs.get("provider_connected"),
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
            "location_source": state.get("location_source"),
            "meter_home_det_active": state.get("meter_home_det_start_val") is not None,
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
