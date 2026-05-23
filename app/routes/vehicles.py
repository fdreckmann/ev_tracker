"""
Vehicle management routes (CRUD, location, images).
"""
import json
import math as _math
import os
import re
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from core.db import _get_db, close_db_if_owned, DATA_DIR
from core.config import load_config, save_config, DEFAULT_CONFIG, VEHICLE_SPECIFIC_KEYS
from core.security import require_login, has_permission, _current_user, _audit
import core.state as _state

vehicles_bp = Blueprint("vehicles", __name__)

# Aliases for shared runtime state
_vehicle_states      = _state.vehicle_states
_vehicle_states_lock = _state.vehicle_states_lock
_vehicle_stops       = _state.vehicle_stops

import logging
log = logging.getLogger(__name__)

# ── Vehicle Images ─────────────────────────────────────────────────────────────

_VEH_IMG_DIR = DATA_DIR / "vehicles"
_VEH_IMG_MAX_BYTES = 3 * 1024 * 1024  # 3 MB
_VEH_IMG_ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp"}
_VEH_ID_RE = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')

def _validate_vehicle_id(vid: str) -> bool:
    """Reject IDs with slashes, dots, traversal sequences or unsafe chars."""
    if not vid or not _VEH_ID_RE.match(vid):
        return False
    if ".." in vid or "/" in vid or "\\" in vid:
        return False
    return True

def _vehicle_exists(vid: str) -> bool:
    """Return True if vid is the primary vehicle or a configured extra vehicle."""
    if vid == "v0":
        return True
    cfg = load_config()
    return any(v.get("id") == vid for v in cfg.get("extra_vehicles", []))

def _safe_veh_img_path(vid: str) -> "Path":
    """Return resolved car.webp path; raises ValueError for unsafe IDs or traversal."""
    if not _validate_vehicle_id(vid):
        raise ValueError(f"Ungültige vehicle_id: {vid!r}")
    base = _VEH_IMG_DIR.resolve()
    target = (base / vid / "car.webp").resolve()
    if not str(target).startswith(str(base) + "/"):
        raise ValueError(f"Pfad-Traversal verhindert für vehicle_id: {vid!r}")
    return target

def _update_vehicle_image_meta(vid: str, mode: str, path: str,
                                source: str = "", attribution: str = "",
                                default_image_key: str = "") -> None:
    """Persist image metadata for v0 (top-level config keys) or extra_vehicles entry."""
    cfg = load_config()
    meta = {"image_mode": mode, "image_path": path,
            "image_source": source, "image_attribution": attribution,
            "default_image_key": default_image_key}
    if vid == "v0":
        cfg["vehicle_image_mode"]        = mode
        cfg["vehicle_image_path"]        = path
        cfg["vehicle_image_source"]      = source
        cfg["vehicle_image_attribution"] = attribution
        cfg["vehicle_default_image_key"] = default_image_key
    else:
        extras = cfg.get("extra_vehicles", [])
        for v in extras:
            if v.get("id") == vid:
                v.update(meta)
                break
        cfg["extra_vehicles"] = extras
    save_config(cfg)


# ── Location Helpers ────────────────────────────────────────────────────────────

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in metres between two GPS coordinates."""
    R = 6_371_000
    phi1, phi2 = _math.radians(lat1), _math.radians(lat2)
    dphi  = _math.radians(lat2 - lat1)
    dlam  = _math.radians(lon2 - lon1)
    a = _math.sin(dphi/2)**2 + _math.cos(phi1)*_math.cos(phi2)*_math.sin(dlam/2)**2
    return R * 2 * _math.atan2(_math.sqrt(a), _math.sqrt(1-a))

def _detect_location_status(vid: str, cfg: dict, vehicle_state: dict) -> dict:
    """
    Combine provider location and HA device_tracker entities to determine
    home/extern/unknown status. Returns dict with status, source, lat, lon, accuracy_m.
    """
    from core.location import normalize_location
    location_mode   = cfg.get("location_mode", "home_external")
    detect_mode     = cfg.get("home_detection_mode", "any")
    ha_entities     = cfg.get("location_ha_entities") or []
    home_lat        = cfg.get("home_lat", "")
    home_lon        = cfg.get("home_lon", "")
    home_radius_m   = float(cfg.get("home_radius_m") or 200)

    result = {"status": "unknown", "source": "none", "latitude": None,
              "longitude": None, "accuracy_m": None, "source_detail": ""}

    if location_mode == "disabled" or not cfg.get("location_enabled"):
        result["status"] = "disabled"
        return result

    sources_home = []
    sources_extern = []

    # --- Provider location check ---
    prov_lat = vehicle_state.get("location_lat")
    prov_lon = vehicle_state.get("location_lon")
    prov_status = "unknown"
    if prov_lat is not None and prov_lon is not None:
        result["latitude"]  = prov_lat
        result["longitude"] = prov_lon
        result["accuracy_m"] = vehicle_state.get("location_accuracy")
        if home_lat and home_lon:
            try:
                dist = _haversine_m(float(home_lat), float(home_lon), prov_lat, prov_lon)
                prov_status = "home" if dist <= home_radius_m else "extern"
            except (ValueError, TypeError):
                prov_status = "unknown"
    else:
        prov_status = normalize_location(vehicle_state.get("location"))

    if prov_status == "home":
        sources_home.append("provider")
    elif prov_status == "extern":
        sources_extern.append("provider")

    # --- Home Assistant entity check ---
    ha_url   = cfg.get("ha_url", "").rstrip("/")
    ha_token = cfg.get("ha_token", "")
    ha_home_count = 0
    ha_ext_count  = 0

    if ha_entities and ha_url and ha_token and detect_mode not in ("provider_only",):
        import urllib.request as _ur, json as _json
        for entity_id in ha_entities:
            try:
                req = _ur.Request(
                    f"{ha_url}/api/states/{entity_id}",
                    headers={"Authorization": f"Bearer {ha_token}",
                             "Content-Type": "application/json"},
                )
                with _ur.urlopen(req, timeout=5) as resp:
                    data = _json.loads(resp.read())
                state_val = data.get("state", "").lower().strip()
                ha_loc = normalize_location(state_val)
                if ha_loc == "home":
                    ha_home_count += 1
                    sources_home.append(f"ha:{entity_id}")
                    # Try to get exact coords from attributes
                    attrs = data.get("attributes", {})
                    if (attrs.get("latitude") and attrs.get("longitude")
                            and location_mode == "exact"):
                        result["latitude"]  = float(attrs["latitude"])
                        result["longitude"] = float(attrs["longitude"])
                        result["accuracy_m"] = attrs.get("gps_accuracy")
                elif ha_loc == "extern" or state_val not in ("", "unknown", "unavailable", "none"):
                    ha_ext_count += 1
                    sources_extern.append(f"ha:{entity_id}")
            except Exception as _ha_e:
                log.debug("HA entity %s: %s", entity_id, _ha_e)

    # --- Combine results ---
    final_status = "unknown"
    source_desc  = "none"

    if detect_mode == "provider_only":
        final_status = prov_status
        source_desc  = "provider"
    elif detect_mode == "ha_only":
        if ha_home_count > 0:  final_status = "home"
        elif ha_ext_count > 0: final_status = "extern"
        else:                  final_status = "unknown"
        source_desc = "ha"
    elif detect_mode == "all":
        all_sources = sources_home + sources_extern
        if all_sources and all(s in sources_home for s in all_sources):
            final_status = "home"
        elif sources_extern:
            final_status = "extern"
        else:
            final_status = "unknown"
        source_desc = "combined"
    elif detect_mode == "manual":
        final_status = normalize_location(cfg.get("location_status_manual", "unknown"))
        source_desc  = "manual"
    else:  # any (default)
        if sources_home:        final_status = "home"
        elif sources_extern:    final_status = "extern"
        else:                   final_status = "unknown"
        source_desc = "combined" if (sources_home or sources_extern) else "none"

    result["status"]        = final_status
    result["source"]        = source_desc
    result["source_detail"] = ", ".join((sources_home + sources_extern)[:3])
    return result


# ── Vehicle CRUD Routes ─────────────────────────────────────────────────────────

@vehicles_bp.route("/api/vehicles", methods=["GET"])
@require_login
def api_get_vehicles():
    if not has_permission(_current_user(), "vehicles:view"):
        return jsonify({"error": "Keine Berechtigung: vehicles:view"}), 403
    from services.vehicle_service import get_all_vehicles
    include_archived = request.args.get("include_archived", "false").lower() == "true"
    return jsonify(get_all_vehicles(include_archived=include_archived))

@vehicles_bp.route("/api/vehicles", methods=["POST"])
@require_login
def api_add_vehicle():
    if not has_permission(_current_user(), "vehicles:create"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: vehicles:create"}), 403
    from services.vehicle_service import get_vehicle_tracker_funcs
    _start_vehicle_tracker, _stop_vehicle_tracker = get_vehicle_tracker_funcs()
    data = request.json or {}
    cfg  = load_config()
    extras = list(cfg.get("extra_vehicles", []))
    vid = f"v{int(time.time())}"
    data["id"] = vid
    data.setdefault("active", True)
    data.setdefault("archived", False)
    extras.append(data)
    cfg["extra_vehicles"] = extras
    save_config(cfg)
    if data.get("active", True):
        _start_vehicle_tracker(vid)
    _audit("vehicle_created", f"vehicle_id={vid} name={data.get('name','')}", ip=request.remote_addr)
    return jsonify({"ok": True, "id": vid})

@vehicles_bp.route("/api/vehicles/<vid>", methods=["PUT"])
@require_login
def api_update_vehicle(vid):
    if not has_permission(_current_user(), "vehicles:edit"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: vehicles:edit"}), 403
    from services.vehicle_service import get_vehicle_tracker_funcs
    _start_vehicle_tracker, _stop_vehicle_tracker = get_vehicle_tracker_funcs()
    _MASK = "********"
    _LOCATION_KEYS = {
        "location_enabled","location_mode","location_source","home_detection_mode",
        "location_ha_entities","location_history_enabled","location_history_precision",
        "location_history_retention_days","location_status_manual",
    }
    if vid == "v0":
        data = request.get_json(silent=True) or {}
        cfg  = load_config()
        for k, val in data.items():
            if k in VEHICLE_SPECIFIC_KEYS or k == "car_name":
                # Location fields: False, [], "disabled" are valid — only skip mask/empty string
                if k not in _LOCATION_KEYS and val in ("", _MASK):
                    continue
                cfg[k] = val
        save_config(cfg)
        return jsonify({"ok": True})
    data   = request.get_json(silent=True) or {}
    # Strip empty/masked password fields so stored secrets survive
    from providers import get_config_fields
    try:
        provider_id = data.get("provider") or "ha"
        pw_keys = {f["id"] for f in get_config_fields(provider_id) if f.get("type") == "password"}
    except Exception:
        pw_keys = set()
    data = {k: v for k, v in data.items()
            if not (k in pw_keys and v in ("", _MASK))}
    cfg    = load_config()
    extras = list(cfg.get("extra_vehicles", []))
    for i, v in enumerate(extras):
        if v["id"] == vid:
            was_active = v.get("active", True)
            extras[i] = {**v, **data, "id": vid}
            cfg["extra_vehicles"] = extras
            save_config(cfg)
            now_active = extras[i].get("active", True)
            if was_active:
                _stop_vehicle_tracker(vid)
            if now_active:
                _start_vehicle_tracker(vid)
            return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Fahrzeug nicht gefunden"}), 404

@vehicles_bp.route("/api/vehicles/<vid>/delete-check")
@require_login
def api_vehicle_delete_check(vid):
    if not has_permission(_current_user(), "vehicles:delete"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: vehicles:delete"}), 403
    if not _validate_vehicle_id(vid):
        return jsonify({"ok": False, "error": "Ungültige Fahrzeug-ID"}), 400
    if vid == "v0":
        return jsonify({"ok": False, "error": "Primärfahrzeug kann nicht gelöscht werden"}), 400
    if not _vehicle_exists(vid):
        return jsonify({"ok": False, "error": "Fahrzeug nicht gefunden"}), 404
    con = _get_db()
    sess_count    = con.execute("SELECT COUNT(*) FROM sessions WHERE vehicle_id=?", (vid,)).fetchone()[0]
    rep_count     = con.execute("SELECT COUNT(*) FROM reports  WHERE vehicle_id=?", (vid,)).fetchone()[0]
    billing_count = con.execute("SELECT COUNT(*) FROM billing_config WHERE vehicle_id=?", (vid,)).fetchone()[0]
    close_db_if_owned(con)
    return jsonify({
        "ok": True,
        "vehicle_id": vid,
        "sessions": sess_count,
        "reports": rep_count,
        "billing_configs": billing_count,
        "can_hard_delete": True,
    })


@vehicles_bp.route("/api/vehicles/<vid>/location")
@require_login
def api_vehicle_location(vid):
    if not has_permission(_current_user(), "vehicles:location_view"):
        return jsonify({"error": "Keine Berechtigung: vehicles:location_view"}), 403
    if not _validate_vehicle_id(vid):
        return jsonify({"error": "Ungültige Fahrzeug-ID"}), 400
    if not _vehicle_exists(vid):
        return jsonify({"error": "Fahrzeug nicht gefunden"}), 404
    from services.vehicle_service import build_vehicle_config
    cfg  = load_config()
    vcfg = cfg if vid == "v0" else build_vehicle_config(
        next((v for v in cfg.get("extra_vehicles",[]) if v["id"]==vid), {}), cfg)
    st   = _vehicle_states.get(vid, {})
    loc  = _detect_location_status(vid, vcfg, st)
    has_exact = has_permission(_current_user(), "vehicles:location_exact_view")
    ha_entities = vcfg.get("location_ha_entities") or []
    resp = {
        "ok": True, "vehicle_id": vid,
        "status": loc["status"], "source": loc["source"],
        "source_detail": loc["source_detail"],
        "last_update": st.get("location_timestamp"),
        "has_exact_location": bool(loc.get("latitude")),
        # Debug fields
        "location_enabled": vcfg.get("location_enabled", False),
        "location_mode": vcfg.get("location_mode", "home_external"),
        "home_detection_mode": vcfg.get("home_detection_mode", "any"),
        "ha_entities_count": len(ha_entities),
        "ha_entities_configured": [e for e in ha_entities if e],
        "tracker_location": st.get("location"),
        "tracker_location_status": st.get("location_status"),
    }
    if has_exact and loc.get("latitude") is not None:
        resp["latitude"]   = loc["latitude"]
        resp["longitude"]  = loc["longitude"]
        resp["accuracy_m"] = loc.get("accuracy_m")
    return jsonify(resp)


@vehicles_bp.route("/api/vehicles/<vid>/location/config", methods=["POST"])
@require_login
def api_vehicle_location_config(vid):
    if not has_permission(_current_user(), "vehicles:location_configure"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: vehicles:location_configure"}), 403
    if not _validate_vehicle_id(vid):
        return jsonify({"ok": False, "error": "Ungültige Fahrzeug-ID"}), 400
    body = request.get_json(force=True) or {}
    allowed = {"location_enabled", "location_mode", "location_source",
               "home_detection_mode", "home_radius_m", "location_ha_entities",
               "location_history_enabled", "location_history_precision",
               "location_history_retention_days"}
    cfg = load_config()
    if vid == "v0":
        for k in allowed:
            if k in body:
                cfg[k] = body[k]
    else:
        extras = cfg.get("extra_vehicles", [])
        found  = False
        for v in extras:
            if v.get("id") == vid:
                for k in allowed:
                    if k in body:
                        v[k] = body[k]
                found = True
                break
        if not found:
            return jsonify({"ok": False, "error": "Fahrzeug nicht gefunden"}), 404
        cfg["extra_vehicles"] = extras
    save_config(cfg)
    _audit("vehicle_location_config_updated", f"vehicle_id={vid}", ip=request.remote_addr)
    return jsonify({"ok": True})


@vehicles_bp.route("/api/vehicles/<vid>/location/test", methods=["POST"])
@require_login
def api_vehicle_location_test(vid):
    if not has_permission(_current_user(), "vehicles:location_configure"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: vehicles:location_configure"}), 403
    if not _validate_vehicle_id(vid):
        return jsonify({"ok": False, "error": "Ungültige Fahrzeug-ID"}), 400
    if not _vehicle_exists(vid):
        return jsonify({"ok": False, "error": "Fahrzeug nicht gefunden"}), 404
    from services.vehicle_service import build_vehicle_config
    cfg  = load_config()
    vcfg = cfg if vid == "v0" else build_vehicle_config(
        next((v for v in cfg.get("extra_vehicles",[]) if v["id"]==vid), {}), cfg)
    st   = _vehicle_states.get(vid, {})

    # Early diagnostics before running detection
    if not vcfg.get("location_enabled"):
        return jsonify({"ok": False, "status": "disabled",
                        "error": "Standortanzeige ist deaktiviert. Bitte aktivieren und speichern."})
    ha_entities = [e for e in (vcfg.get("location_ha_entities") or []) if e]
    provider_has_loc = (st.get("location_lat") is not None
                        or vcfg.get("location_sensor","").strip())
    if not ha_entities and not provider_has_loc:
        return jsonify({"ok": False, "status": "unknown",
                        "error": "Keine Standortquelle konfiguriert. Bitte HA-Entities oder Standort-Sensor eintragen."})

    try:
        loc = _detect_location_status(vid, vcfg, st)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    _audit("vehicle_location_tested", f"vehicle_id={vid} status={loc['status']}", ip=request.remote_addr)
    has_exact = has_permission(_current_user(), "vehicles:location_exact_view")
    resp = {"ok": True, "status": loc["status"], "source": loc["source"],
            "source_detail": loc["source_detail"],
            "location_enabled": vcfg.get("location_enabled", False),
            "home_detection_mode": vcfg.get("home_detection_mode", "any"),
            "ha_entities_configured": ha_entities}
    if has_exact and loc.get("latitude") is not None:
        resp["latitude"]  = loc["latitude"]
        resp["longitude"] = loc["longitude"]
    return jsonify(resp)


@vehicles_bp.route("/api/vehicles/<vid>/location/history")
@require_login
def api_vehicle_location_history(vid):
    if not has_permission(_current_user(), "vehicles:location_history_view"):
        return jsonify({"error": "Keine Berechtigung: vehicles:location_history_view"}), 403
    if not _validate_vehicle_id(vid):
        return jsonify({"error": "Ungültige Fahrzeug-ID"}), 400
    if not _vehicle_exists(vid):
        return jsonify({"error": "Fahrzeug nicht gefunden"}), 404
    try: limit = min(int(request.args.get("limit", 100)), 500)
    except (ValueError, TypeError): limit = 100
    con = _get_db()
    rows = con.execute(
        "SELECT timestamp, location_status, source, latitude, longitude, accuracy_m FROM vehicle_location_history "
        "WHERE vehicle_id=? ORDER BY timestamp DESC LIMIT ?", (vid, limit)
    ).fetchall()
    close_db_if_owned(con)
    has_exact = has_permission(_current_user(), "vehicles:location_exact_view")
    items = []
    for row in rows:
        item = dict(row)
        if not has_exact:
            item.pop("latitude", None)
            item.pop("longitude", None)
        items.append(item)
    return jsonify({"ok": True, "vehicle_id": vid, "history": items})


@vehicles_bp.route("/api/vehicles/<vid>", methods=["DELETE"])
@require_login
def api_delete_vehicle(vid):
    """Archive (default) or hard-delete a vehicle.
    ?mode=hard — hard delete, blocked if sessions/reports exist.
    ?mode=hard&delete_sessions=true — hard delete including sessions (future use).
    """
    if not has_permission(_current_user(), "vehicles:delete"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: vehicles:delete"}), 403
    from services.vehicle_service import get_vehicle_tracker_funcs
    _start_vehicle_tracker, _stop_vehicle_tracker = get_vehicle_tracker_funcs()

    if vid == "v0":
        return jsonify({"ok": False, "error": "Primärfahrzeug kann nicht gelöscht werden"}), 400

    if not _validate_vehicle_id(vid):
        return jsonify({"ok": False, "error": "Ungültige Fahrzeug-ID"}), 400

    cfg    = load_config()
    extras = cfg.get("extra_vehicles", [])
    target = next((v for v in extras if v["id"] == vid), None)
    if target is None:
        _audit("vehicle_delete_failed", f"vehicle_id={vid} reason=not_found", ip=request.remote_addr)
        return jsonify({"ok": False, "error": "Fahrzeug nicht gefunden"}), 404

    mode   = request.args.get("mode", "archive")
    user   = _current_user()
    vname  = target.get("name", vid)

    if mode == "hard":
        # Block if dependent data exists (unless delete_sessions=true)
        allow_del_sessions = request.args.get("delete_sessions", "false").lower() == "true"
        con = _get_db()
        sess_count   = con.execute("SELECT COUNT(*) FROM sessions WHERE vehicle_id=?", (vid,)).fetchone()[0]
        rep_count    = con.execute("SELECT COUNT(*) FROM reports  WHERE vehicle_id=?", (vid,)).fetchone()[0]
        billing_row  = con.execute("SELECT id FROM billing_config WHERE vehicle_id=?", (vid,)).fetchone()
        close_db_if_owned(con)
        if (sess_count or rep_count or billing_row) and not allow_del_sessions:
            return jsonify({
                "ok": False,
                "error": (f"Fahrzeug hat noch {sess_count} Ladevorgänge und/oder {rep_count} Reports. "
                          "Bitte archivieren oder Löschung inkl. Daten mit delete_sessions=true bestätigen."),
                "sessions": sess_count, "reports": rep_count,
            }), 409
        # Hard delete: remove from config, clean DB and image dir
        cfg["extra_vehicles"] = [v for v in extras if v["id"] != vid]
        save_config(cfg)
        _stop_vehicle_tracker(vid)
        with _vehicle_states_lock:
            _vehicle_states.pop(vid, None)
            _vehicle_stops.pop(vid, None)
        if allow_del_sessions:
            con = _get_db()
            con.execute("DELETE FROM session_points WHERE session_id IN "
                        "(SELECT id FROM sessions WHERE vehicle_id=?)", (vid,))
            con.execute("DELETE FROM sessions WHERE vehicle_id=?", (vid,))
            con.execute("DELETE FROM reports  WHERE vehicle_id=?", (vid,))
            con.execute("DELETE FROM billing_config WHERE vehicle_id=?", (vid,))
            con.commit(); close_db_if_owned(con)
        # Remove image directory
        try:
            img_dir = (_VEH_IMG_DIR / vid).resolve()
            if str(img_dir).startswith(str(_VEH_IMG_DIR.resolve())):
                import shutil as _shutil
                if img_dir.exists():
                    _shutil.rmtree(str(img_dir))
        except Exception as _ie:
            log.warning("Fahrzeugbild-Ordner konnte nicht gelöscht werden: %s", _ie)
        _audit("vehicle_hard_deleted",
               f"vehicle_id={vid} name={vname} sessions_deleted={allow_del_sessions}",
               ip=request.remote_addr)
        return jsonify({"ok": True, "action": "deleted", "vehicle_id": vid})

    # Default: archive (soft delete)
    new_extras = []
    for v in extras:
        if v["id"] == vid:
            new_extras.append({**v,
                "active": False,
                "archived": True,
                "deleted_at": datetime.utcnow().isoformat(timespec="seconds"),
            })
        else:
            new_extras.append(v)
    cfg["extra_vehicles"] = new_extras
    save_config(cfg)
    _stop_vehicle_tracker(vid)
    with _vehicle_states_lock:
        _vehicle_states.pop(vid, None)
        _vehicle_stops.pop(vid, None)
    _audit("vehicle_archived", f"vehicle_id={vid} name={vname}", ip=request.remote_addr)
    return jsonify({"ok": True, "action": "archived", "vehicle_id": vid})


# ── Vehicle Image Routes ────────────────────────────────────────────────────────

@vehicles_bp.route("/api/vehicles/<vid>/image")
@require_login
def api_vehicle_image_meta(vid):
    if not _vehicle_exists(vid):
        return jsonify({"error": "Fahrzeug nicht gefunden"}), 404
    if not has_permission(_current_user(), "vehicles:view"):
        return jsonify({"error": "Keine Berechtigung: vehicles:view"}), 403
    try:
        img_path = _safe_veh_img_path(vid)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"exists": img_path.exists(), "vehicle_id": vid,
                    "url": f"/api/vehicles/{vid}/image/file" if img_path.exists() else None})

@vehicles_bp.route("/api/vehicles/<vid>/image/file")
@require_login
def api_vehicle_image_file(vid):
    ph = Path(__file__).parent.parent / "static" / "vehicle_images" / "placeholder_car.svg"
    if not _vehicle_exists(vid):
        if ph.exists():
            return send_file(str(ph), mimetype="image/svg+xml")
        return jsonify({"error": "Fahrzeug nicht gefunden"}), 404
    if not has_permission(_current_user(), "vehicles:view"):
        # Return placeholder instead of 403 so <img> tags degrade gracefully
        if ph.exists():
            return send_file(str(ph), mimetype="image/svg+xml")
        return jsonify({"error": "Keine Berechtigung: vehicles:view"}), 403
    try:
        img_path = _safe_veh_img_path(vid)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not img_path.exists():
        if ph.exists():
            return send_file(str(ph), mimetype="image/svg+xml")
        return jsonify({"error": "Kein Bild vorhanden"}), 404
    return send_file(str(img_path), mimetype="image/webp")

@vehicles_bp.route("/api/vehicles/<vid>/image/upload", methods=["POST"])
@require_login
def api_vehicle_image_upload(vid):
    if not _vehicle_exists(vid):
        return jsonify({"ok": False, "error": "Fahrzeug nicht gefunden"}), 404
    if not has_permission(_current_user(), "vehicles:image_manage"):
        return jsonify({"error": "Keine Berechtigung: vehicles:image_manage"}), 403
    try:
        img_path = _safe_veh_img_path(vid)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Keine Datei"}), 400
    f = request.files["file"]
    raw = f.read(_VEH_IMG_MAX_BYTES + 1)
    if len(raw) > _VEH_IMG_MAX_BYTES:
        return jsonify({"ok": False, "error": "Datei zu groß (max. 3 MB)"}), 400
    try:
        from PIL import Image as _PilImage
        import io as _io
        _img = _PilImage.open(_io.BytesIO(raw))
        _img.verify()
        _img = _PilImage.open(_io.BytesIO(raw))
        img_path.parent.mkdir(parents=True, exist_ok=True)
        _img.save(str(img_path), "WEBP", quality=85)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Ungültiges Bild: {e}"}), 400
    _update_vehicle_image_meta(vid, mode="upload",
                               path=f"/api/vehicles/{vid}/image/file")
    _audit("vehicle_image_uploaded", f"vehicle_id={vid}", ip=request.remote_addr)
    return jsonify({"ok": True, "url": f"/api/vehicles/{vid}/image/file"})

@vehicles_bp.route("/api/vehicles/<vid>/image", methods=["DELETE"])
@require_login
def api_vehicle_image_delete(vid):
    if not _vehicle_exists(vid):
        return jsonify({"error": "Fahrzeug nicht gefunden"}), 404
    if not has_permission(_current_user(), "vehicles:image_manage"):
        return jsonify({"error": "Keine Berechtigung: vehicles:image_manage"}), 403
    try:
        img_path = _safe_veh_img_path(vid)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if img_path.exists():
        img_path.unlink()
        _audit("vehicle_image_deleted", f"vehicle_id={vid}", ip=request.remote_addr)
    _update_vehicle_image_meta(vid, mode="none", path="")
    return jsonify({"ok": True})


# ── Vehicle Image Suggestion / Manifest ────────────────────────────────────────

@vehicles_bp.route("/api/vehicles/<vid>/image/suggest")
@require_login
def api_vehicle_image_suggest(vid):
    """Return the best auto-suggested image key for a vehicle."""
    if not has_permission(_current_user(), "vehicles:view"):
        return jsonify({"error": "Keine Berechtigung: vehicles:view"}), 403
    if not _vehicle_exists(vid):
        return jsonify({"error": "Fahrzeug nicht gefunden"}), 404
    from services.vehicle_service import get_all_vehicles
    cfg = load_config()
    vehicles = get_all_vehicles(cfg=cfg, include_archived=True)
    vehicle = next((v for v in vehicles if v.get("id") == vid), None)
    if vehicle is None:
        return jsonify({"error": "Fahrzeug nicht gefunden"}), 404
    # v0 stores image key under a different config name
    saved_key = (cfg.get("vehicle_default_image_key", "")
                 if vid == "v0" else vehicle.get("default_image_key", ""))
    from services.vehicle_image_service import suggest_vehicle_image_key, resolve_vehicle_image_url
    key = suggest_vehicle_image_key(
        brand=vehicle.get("brand", ""),
        model=vehicle.get("model", ""),
        name=vehicle.get("name", ""),
    )
    return jsonify({
        "ok": True,
        "vehicle_id": vid,
        "suggested_key": key,
        "suggested_url": f"/static/vehicle_images/{key}.svg" if key else "/static/vehicle_images/placeholder_car.svg",
        "resolved_url": resolve_vehicle_image_url(vehicle),
        "saved_key": saved_key,
    })


@vehicles_bp.route("/api/vehicle-images/manifest")
@require_login
def api_vehicle_images_manifest():
    """Return the local vehicle images manifest."""
    if not has_permission(_current_user(), "vehicles:view"):
        return jsonify({"error": "Keine Berechtigung: vehicles:view"}), 403
    from services.vehicle_image_service import get_manifest
    return jsonify(get_manifest())


@vehicles_bp.route("/api/vehicles/<vid>/image/default-key", methods=["POST"])
@require_login
def api_vehicle_image_set_default_key(vid):
    """Save a chosen silhouette key as the vehicle's default image."""
    if not has_permission(_current_user(), "vehicles:image_manage"):
        return jsonify({"error": "Keine Berechtigung: vehicles:image_manage"}), 403
    if not _vehicle_exists(vid):
        return jsonify({"error": "Fahrzeug nicht gefunden"}), 404
    key = (request.json or {}).get("key", "")
    # Validate key is in manifest (or empty to clear)
    if key:
        from services.vehicle_image_service import get_manifest
        manifest = get_manifest()
        valid_keys = {s["key"] for s in manifest.get("silhouettes", [])}
        if key not in valid_keys:
            return jsonify({"error": f"Unbekannter Bildschlüssel: {key}"}), 400
    _update_vehicle_image_meta(vid, mode="default_key" if key else "none",
                               path=f"/static/vehicle_images/{key}.svg" if key else "",
                               source="local", default_image_key=key)
    _audit("vehicle_image_key_set", f"vehicle_id={vid} key={key}", ip=request.remote_addr)
    return jsonify({"ok": True, "key": key})
