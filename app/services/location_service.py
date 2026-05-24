"""
Shared location detection helpers.
Single source of truth for location logic — used by server.py, routes/vehicles.py,
routes/main_routes.py. Includes a 30-second TTL cache to avoid hammering HA on
concurrent requests from parallel JS fetches.
"""
from __future__ import annotations
import math as _math
import logging
import time as _time
from datetime import datetime

log = logging.getLogger(__name__)

# Per-vehicle TTL cache: if last refresh was < 30s ago, return cached vehicle_states value
_REFRESH_TTL = 30  # seconds
_location_refresh_ts: dict = {}  # vid -> float unix timestamp


def normalize_ha_entities(value) -> list:
    """Normalize location_ha_entities to a clean list of entity ID strings.

    Handles both legacy string configs ("entity1, entity2") and list configs
    (["entity1", "entity2"]). Guards against iterating over single characters.
    """
    if not value:
        return []
    if isinstance(value, str):
        parts = value.replace(",", "\n").splitlines()
        return [p.strip() for p in parts if p.strip()]
    if isinstance(value, list):
        return [str(p).strip() for p in value if str(p).strip()]
    return []


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in metres between two GPS coordinates."""
    R = 6_371_000
    phi1, phi2 = _math.radians(lat1), _math.radians(lat2)
    dphi = _math.radians(lat2 - lat1)
    dlam = _math.radians(lon2 - lon1)
    a = _math.sin(dphi / 2) ** 2 + _math.cos(phi1) * _math.cos(phi2) * _math.sin(dlam / 2) ** 2
    return R * 2 * _math.atan2(_math.sqrt(a), _math.sqrt(1 - a))


_SKIP_STATES = {"", "unknown", "unavailable", "none", "n/a", "null", "offline"}
_HOME_STATES  = {"home", "zuhause"}


def _classify_ha_state(entity_id: str, raw: str) -> str:
    """Map a raw HA state value to 'home', 'extern', or 'unknown'.

    device_tracker entities: any non-home, non-skip state is treated as extern
    (e.g. 'not_home', 'work', 'parking', zone names → extern).
    Other entities: use the canonical normalize_location mapping.
    """
    from core.location import normalize_location
    v = raw.lower().strip()
    if v in _SKIP_STATES:
        return "unknown"
    if v in _HOME_STATES:
        return "home"
    domain = entity_id.split(".", 1)[0]
    if domain == "device_tracker":
        # Any non-home, non-skip state means the car is away
        return "extern"
    return normalize_location(v)


def detect_location_status(vid: str, cfg: dict, vehicle_state: dict) -> dict:
    """Combine provider location and HA device_tracker entities.

    Returns dict with:
        status, source, source_detail, latitude, longitude, accuracy_m,
        provider_has_latlon, has_location_sensor,
        ha_entities_queried, ha_home_count, ha_extern_count,
        resolved_status, ha_debug (list of per-entity results)
    """
    from core.location import normalize_location

    location_mode = cfg.get("location_mode", "home_external")
    detect_mode   = cfg.get("home_detection_mode", "any")
    ha_entities   = normalize_ha_entities(cfg.get("location_ha_entities"))
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
        "ha_debug": [],
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
    ha_debug: list = []

    if ha_entities and ha_url and ha_token and detect_mode not in ("provider_only",):
        import urllib.request as _ur
        import json as _json
        result["ha_entities_queried"] = len(ha_entities)
        for entity_id in ha_entities:
            entry: dict = {"entity_id": entity_id, "ok": False}
            try:
                req = _ur.Request(
                    f"{ha_url}/api/states/{entity_id}",
                    headers={"Authorization": f"Bearer {ha_token}",
                             "Content-Type": "application/json"},
                )
                with _ur.urlopen(req, timeout=5) as resp:
                    data = _json.loads(resp.read())
                raw_state = str(data.get("state", "")).strip()
                counted_as = _classify_ha_state(entity_id, raw_state)
                entry.update({
                    "ok": True,
                    "raw_state": raw_state,
                    "counted_as": counted_as,
                })
                if counted_as == "home":
                    ha_home_count += 1
                    sources_home.append(f"ha:{entity_id}")
                    attrs = data.get("attributes", {})
                    if (attrs.get("latitude") and attrs.get("longitude")
                            and location_mode == "exact"):
                        result["latitude"]  = float(attrs["latitude"])
                        result["longitude"] = float(attrs["longitude"])
                        result["accuracy_m"] = attrs.get("gps_accuracy")
                elif counted_as == "extern":
                    ha_ext_count += 1
                    sources_extern.append(f"ha:{entity_id}")
                # "unknown" → neither counter incremented
            except Exception as _ha_e:
                entry["error"] = str(_ha_e)
                log.debug("HA entity %s: %s", entity_id, _ha_e)
            ha_debug.append(entry)

    result["ha_home_count"]   = ha_home_count
    result["ha_extern_count"] = ha_ext_count
    result["ha_debug"]        = ha_debug

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

    result["status"]          = final_status
    result["resolved_status"] = final_status
    result["source"]          = source_desc
    result["source_detail"]   = ", ".join((sources_home + sources_extern)[:3])
    return result


def refresh_vehicle_location_state(vid: str) -> dict:
    """Detect location for `vid`, write into _state.vehicle_states, and return result.

    Uses a 30-second TTL: if called more often (e.g. concurrent JS fetches),
    the second call within the TTL window returns the cached vehicle_states value
    instantly without making extra HA requests.

    Always writes location_status / location_source / location_timestamp to
    vehicle_states so /api/status always reflects the current state.
    """
    now = _time.time()
    last_ts = _location_refresh_ts.get(vid, 0)

    if now - last_ts < _REFRESH_TTL:
        # Return cached values without hitting HA again
        from core import state as _state
        st = _state.vehicle_states.get(vid, {})
        return {
            "status":       st.get("location_status", "unknown"),
            "source":       st.get("location_source", "none"),
            "source_detail": st.get("location_source_detail", ""),
            "timestamp":    st.get("location_timestamp", ""),
            "ha_debug":     [],
            "_cached":      True,
        }

    # Mark as refreshed before the HA calls to prevent stampede
    _location_refresh_ts[vid] = now

    try:
        from core.config import load_config
        from core import state as _state

        cfg = load_config()
        if vid == "v0":
            vcfg = cfg
        else:
            from services.vehicle_service import build_vehicle_config
            extras = cfg.get("extra_vehicles", [])
            vdata  = next((v for v in extras if v.get("id") == vid), {})
            vcfg   = build_vehicle_config(vdata, cfg)

        st  = _state.vehicle_states.get(vid, {})
        loc = detect_location_status(vid, vcfg, st)

        ts = datetime.now().isoformat(timespec="seconds")
        with _state.vehicle_states_lock:
            st_live = _state.vehicle_states.setdefault(vid, {})
            st_live["location_status"]        = loc.get("status", "unknown")
            st_live["location_source"]        = loc.get("source", "none")
            st_live["location_source_detail"] = loc.get("source_detail", "")
            st_live["location_timestamp"]     = ts
            if loc.get("latitude") is not None:
                st_live["location_lat"]      = loc["latitude"]
                st_live["location_lon"]      = loc["longitude"]
                st_live["location_accuracy"] = loc.get("accuracy_m")

        loc["timestamp"] = ts
        return loc

    except Exception as exc:
        log.warning("refresh_vehicle_location_state(%s) failed: %s", vid, exc)
        # Reset TTL so next call retries
        _location_refresh_ts.pop(vid, None)
        return {
            "status": "unknown", "source": "none", "source_detail": "",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "ha_debug": [], "error": str(exc),
        }
