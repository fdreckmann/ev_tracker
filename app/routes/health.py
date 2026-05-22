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

health_bp = Blueprint("health", __name__)


@health_bp.route("/api/health")
def api_health():
    """Public health check — no auth required."""
    db_ok = True
    try:
        _c = sqlite3.connect(DB_PATH)
        _c.execute("SELECT 1")
        _c.close()
    except Exception:
        db_ok = False
    data_ok = DATA_DIR.exists()
    # APP_VERSION imported lazily to avoid circular import with server.py
    try:
        from server import APP_VERSION
    except ImportError:
        APP_VERSION = "unknown"
    return jsonify({
        "ok": db_ok and data_ok,
        "version": APP_VERSION,
        "db": "ok" if db_ok else "error",
        "data_dir": "ok" if data_ok else "error",
    }), 200 if (db_ok and data_ok) else 503


@health_bp.route("/api/system/status")
@require_login
def api_system_status():
    if not has_permission(_current_user(), "system:status"):
        return jsonify({"error": "Keine Berechtigung: system:status"}), 403
    try:
        from server import APP_VERSION, BACKUP_DIR
        from services.vehicle_service import get_all_vehicles
    except ImportError:
        APP_VERSION = "unknown"
        BACKUP_DIR = DATA_DIR / "backups"
        get_all_vehicles = lambda: []
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
        "version": APP_VERSION,
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
    })
