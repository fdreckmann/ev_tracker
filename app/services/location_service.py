"""
Shared location detection helpers used by server.py and routes/vehicles.py.
Avoids duplication of _haversine_m and detect_location_status.
"""
from __future__ import annotations
import math as _math
import logging

log = logging.getLogger(__name__)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in metres between two GPS coordinates."""
    R = 6_371_000
    phi1, phi2 = _math.radians(lat1), _math.radians(lat2)
    dphi = _math.radians(lat2 - lat1)
    dlam = _math.radians(lon2 - lon1)
    a = _math.sin(dphi / 2) ** 2 + _math.cos(phi1) * _math.cos(phi2) * _math.sin(dlam / 2) ** 2
    return R * 2 * _math.atan2(_math.sqrt(a), _math.sqrt(1 - a))


def detect_location_status(vid: str, cfg: dict, vehicle_state: dict) -> dict:
    """
    Combine provider location and HA device_tracker entities to determine
    home/extern/unknown status.

    Returns dict with keys:
        status        – 'home' | 'extern' | 'unknown' | 'disabled'
        source        – 'provider' | 'ha' | 'combined' | 'manual' | 'none'
        source_detail – comma-separated contributing sources (max 3)
        latitude      – float | None
        longitude     – float | None
        accuracy_m    – float | None
        provider_has_latlon      – bool (True if provider supplied GPS coords)
        has_location_sensor      – bool (True if HA location_sensor configured)
        ha_entities_queried      – int
        ha_home_count            – int
        ha_extern_count          – int
        resolved_status          – str (same as status, for debug clarity)
    """
    from core.location import normalize_location

    location_mode = cfg.get("location_mode", "home_external")
    detect_mode   = cfg.get("home_detection_mode", "any")
    ha_entities   = cfg.get("location_ha_entities") or []
    home_lat      = cfg.get("home_lat", "")
    home_lon      = cfg.get("home_lon", "")
    home_radius_m = float(cfg.get("home_radius_m") or 200)

    result: dict = {
        "status": "unknown", "source": "none", "latitude": None,
        "longitude": None, "accuracy_m": None, "source_detail": "",
        "provider_has_latlon": False,
        "has_location_sensor": bool(cfg.get("location_sensor", "").strip()),
        "ha_entities_queried": 0,
        "ha_home_count": 0,
        "ha_extern_count": 0,
        "resolved_status": "unknown",
    }

    if location_mode == "disabled" or not cfg.get("location_enabled"):
        result["status"] = "disabled"
        result["resolved_status"] = "disabled"
        return result

    sources_home   = []
    sources_extern = []

    # ── Provider location ─────────────────────────────────────────────────────
    prov_lat = vehicle_state.get("location_lat")
    prov_lon = vehicle_state.get("location_lon")
    prov_status = "unknown"

    if prov_lat is not None and prov_lon is not None:
        result["provider_has_latlon"] = True
        result["latitude"]  = prov_lat
        result["longitude"] = prov_lon
        result["accuracy_m"] = vehicle_state.get("location_accuracy")
        if home_lat and home_lon:
            try:
                dist = haversine_m(float(home_lat), float(home_lon), prov_lat, prov_lon)
                prov_status = "home" if dist <= home_radius_m else "extern"
            except (ValueError, TypeError):
                prov_status = "unknown"
    else:
        prov_status = normalize_location(vehicle_state.get("location"))

    if prov_status == "home":
        sources_home.append("provider")
    elif prov_status == "extern":
        sources_extern.append("provider")

    # ── HA entity check ───────────────────────────────────────────────────────
    ha_url   = cfg.get("ha_url", "").rstrip("/")
    ha_token = cfg.get("ha_token", "")
    ha_home_count = 0
    ha_ext_count  = 0

    if ha_entities and ha_url and ha_token and detect_mode not in ("provider_only",):
        import urllib.request as _ur
        import json as _json
        result["ha_entities_queried"] = len(ha_entities)
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

    result["ha_home_count"]   = ha_home_count
    result["ha_extern_count"] = ha_ext_count

    # ── Combine ───────────────────────────────────────────────────────────────
    final_status = "unknown"
    source_desc  = "none"

    if detect_mode == "provider_only":
        final_status = prov_status
        source_desc  = "provider"
    elif detect_mode == "ha_only":
        if ha_home_count > 0:   final_status = "home"
        elif ha_ext_count > 0:  final_status = "extern"
        else:                   final_status = "unknown"
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
    else:  # "any" (default)
        if sources_home:      final_status = "home"
        elif sources_extern:  final_status = "extern"
        else:                 final_status = "unknown"
        source_desc = "combined" if (sources_home or sources_extern) else "none"

    result["status"]        = final_status
    result["resolved_status"] = final_status
    result["source"]        = source_desc
    result["source_detail"] = ", ".join((sources_home + sources_extern)[:3])
    return result
