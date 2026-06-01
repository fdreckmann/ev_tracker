"""
Health check and system status routes.
"""
import sqlite3
import time
from pathlib import Path

from flask import Blueprint, jsonify

from core.db import _get_db, close_db_if_owned, DB_PATH, DATA_DIR
from core.config import load_config
from core.security import require_login, has_permission, _current_user
import core.state as _state
from version import APP_VERSION, ASSET_VERSION, GIT_BRANCH, GIT_COMMIT, COMMIT_SHORT, CHANNEL, IMAGE_TAG, DISPLAY_BRANCH, DISPLAY_COMMIT, DISPLAY_COMMIT_SHORT, DISPLAY_IMAGE_TAG, BUILD_DATE, BUILD_DATE_UTC, BUILD_SOURCE

health_bp = Blueprint("health", __name__)


@health_bp.route("/api/health")
def api_health():
    """Public health check — no auth required."""
    import os as _os
    db_ok = True
    db_error = None
    users_table_exists = False
    users_count = None
    db_writable = False
    startup_error = None

    # Check startup error from server module
    try:
        import server as _srv
        _se = getattr(_srv, "_startup_error", None)
        if _se is not None:
            startup_error = str(_se)
    except Exception:
        pass

    try:
        _c = sqlite3.connect(DB_PATH)
        _c.row_factory = sqlite3.Row
        _c.execute("SELECT 1")
        # Check users table
        tbl = _c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        users_table_exists = tbl is not None
        if users_table_exists:
            row = _c.execute("SELECT COUNT(*) AS c FROM users").fetchone()
            users_count = int(row["c"])
        _c.close()
    except Exception as e:
        db_ok = False
        db_error = str(e)

    data_ok = DATA_DIR.exists()

    # Check /data writability
    try:
        _test = DATA_DIR / ".write_test"
        _test.write_text("ok")
        _test.unlink()
        db_writable = True
    except Exception:
        db_writable = False

    overall_ok = db_ok and data_ok and db_writable and startup_error is None
    result = {
        "ok": overall_ok,
        "version": APP_VERSION,
        "db": "ok" if db_ok else "error",
        "data_dir": "ok" if data_ok else "error",
        "db_writable": db_writable,
        "users_table_exists": users_table_exists,
    }
    if not db_writable:
        result["message"] = "/data oder Datenbank ist nicht beschreibbar."
    if users_count is not None:
        result["users_count"] = users_count
    if db_error:
        result["db_error"] = db_error
    if startup_error:
        result["startup_error"] = startup_error
    return jsonify(result), 200 if overall_ok else 503


@health_bp.route("/api/system/status")
@require_login
def api_system_status():
    if not has_permission(_current_user(), "system:status"):
        return jsonify({"error": "Keine Berechtigung: system:status"}), 403
    from services.vehicle_service import get_all_vehicles
    BACKUP_DIR = DATA_DIR / "backups"
    try:
        con = _get_db()
        sessions_count = con.execute("SELECT COUNT(*) FROM sessions WHERE end_ts IS NOT NULL").fetchone()[0]
        vehicles_count = len(get_all_vehicles())
        reports_count  = con.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        backup_files   = sorted(BACKUP_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime)
        backup_count   = len(backup_files)
        last_backup    = backup_files[-1].name if backup_files else None
        db_size        = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        close_db_if_owned(con)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    cfg = load_config()
    warnings = []
    if not cfg.get("report_email_recipients"):
        warnings.append("Keine E-Mail-Empfänger konfiguriert")
    if not cfg.get("backup_cron"):
        warnings.append("Kein automatisches Backup konfiguriert")
    install_type = "docker"
    try:
        with open("/proc/1/cgroup") as _f:
            if "docker" not in _f.read() and "container" not in _f.read():
                install_type = "direct"
    except Exception:
        install_type = "unknown"
    return jsonify({
        "ok": True,
        # Canonical names
        "version": APP_VERSION,
        "channel": CHANNEL,
        "branch": DISPLAY_BRANCH,
        "commit": DISPLAY_COMMIT,
        "commit_short": DISPLAY_COMMIT_SHORT,
        "image_tag": DISPLAY_IMAGE_TAG,
        "build_date": BUILD_DATE,
        "build_utc": BUILD_DATE_UTC,
        "build_source": BUILD_SOURCE,
        # Mobile-compat aliases
        "app_version": APP_VERSION,
        "asset_version": ASSET_VERSION,
        "db_size_mb": round(db_size / 1024 / 1024, 2),
        "session_count": sessions_count,
        # Original fields kept for backward compat
        "install_type": install_type,
        "db_size": db_size,
        "sessions_count": sessions_count,
        "vehicles_count": vehicles_count,
        "reports_count": reports_count,
        "backup_count": backup_count,
        "last_backup": last_backup,
        "mqtt_enabled": bool(cfg.get("mqtt_enabled")),
        "notifications_enabled": bool(cfg.get("notifications_enabled")),
        "warnings": warnings,
    })


@health_bp.route("/api/diagnostics")
@require_login
def api_diagnostics():
    if not has_permission(_current_user(), "system:status"):
        return jsonify({"error": "Keine Berechtigung: system:status"}), 403
    cfg = load_config()
    vehicles = []
    for vid, st in _state.vehicle_states.items():
        vehicles.append({
            "vehicle_id": vid,
            "name": st.get("name", vid),
            "running": st.get("running", False),
            "charging": st.get("charging", False),
            "soc": st.get("soc_current"),
            "last_poll": st.get("last_poll"),
            "last_error": st.get("last_error"),
            "last_successful_poll": st.get("last_successful_poll"),
            "provider_debug": st.get("provider_debug"),
        })
    provider = cfg.get("provider", "ha")
    provider_configured = bool(
        cfg.get("ha_token") if provider == "ha" else cfg.get(f"{provider}_token", cfg.get(f"{provider}_api_key",""))
    )
    import sys as _sys
    import core.state as _cs
    module_info = {
        "main_module_id": id(_sys.modules.get("__main__")),
        "server_module_id": id(_sys.modules.get("server")),
        "same_module": _sys.modules.get("__main__") is _sys.modules.get("server"),
        "vehicle_states_id": id(_cs.vehicle_states),
    }
    # Tracker state for primary vehicle (v0)
    v0_st = _state.vehicle_states.get("v0", {})
    tracker_state = {
        "tracker_alive": v0_st.get("tracker_alive"),
        "tracker_started": v0_st.get("tracker_started"),
        "tracker_thread_id": v0_st.get("tracker_thread_id"),
        "tracker_start_time": v0_st.get("tracker_start_time"),
        "poll_count": v0_st.get("poll_count"),
        "successful_poll_count": v0_st.get("successful_poll_count"),
        "failed_poll_count": v0_st.get("failed_poll_count"),
        "last_exception_type": v0_st.get("last_exception_type"),
        "provider_connected": v0_st.get("provider_connected"),
        "provider_debug": v0_st.get("provider_debug"),
    }
    # Config summary — no secrets
    config_summary = {
        "provider_set": bool(cfg.get("provider")),
        "ha_url_set": bool(cfg.get("ha_url")),
        "ha_token_set": bool(cfg.get("ha_token")),
        "charging_sensor_set": bool(cfg.get("charging_sensor")),
        "soc_sensor_set": bool(cfg.get("soc_sensor")),
    }
    # Meter-based home detection state per vehicle
    meter_home_detection = {}
    for vid, st in _state.vehicle_states.items():
        meter_home_detection[vid] = {
            "enabled": cfg.get("meter_home_detection_enabled", True),
            "min_delta_kwh": cfg.get("meter_home_detection_min_delta_kwh", 0.2),
            "window_minutes": cfg.get("meter_home_detection_window_minutes", 10),
            "override_external": cfg.get("meter_home_detection_override_external", False),
            "max_delta_kwh_per_hour": cfg.get("meter_home_detection_max_delta_kwh_per_hour", 30.0),
            "start_value": st.get("meter_home_det_start_val"),
            "start_ts": st.get("meter_home_det_start_ts"),
            "location_status": st.get("location_status"),
            "location_source": st.get("location_source"),
        }
    return jsonify({
        "ok": True,
        "provider": provider,
        "provider_configured": provider_configured,
        "ha_url": cfg.get("ha_url","") if provider == "ha" else None,
        "charging_sensor": cfg.get("charging_sensor","") if provider == "ha" else None,
        "poll_interval": cfg.get("poll_interval", 30),
        "vehicles": vehicles,
        "server_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "module_info": module_info,
        "tracker_state": tracker_state,
        "config_summary": config_summary,
        "meter_home_detection": meter_home_detection,
    })
