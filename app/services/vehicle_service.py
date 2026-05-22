"""
Vehicle service — extracted from server.py.
Provides vehicle config helpers and tracker function accessors.
"""
from __future__ import annotations

from core.config import load_config, VEHICLE_SPECIFIC_KEYS


def get_all_vehicles(cfg=None, include_archived: bool = False) -> list[dict]:
    """Returns primary vehicle (v0) plus extra vehicles. Archived excluded by default."""
    if cfg is None:
        cfg = load_config()
    primary = {
        "id":   "v0",
        "name": cfg.get("car_name", "Mein EV"),
        "provider": cfg.get("provider", "ha"),
        "active": True,
        "archived": False,
        **{k: cfg[k] for k in VEHICLE_SPECIFIC_KEYS if k in cfg and k not in ("provider", "car_name")},
    }
    extras = cfg.get("extra_vehicles", [])
    if not include_archived:
        extras = [v for v in extras if not v.get("archived", False)]
    return [primary] + extras


def build_vehicle_config(vehicle: dict, cfg=None) -> dict:
    """Merge app-level config with vehicle-specific fields for provider initialization."""
    if cfg is None:
        cfg = load_config()
    merged = dict(cfg)
    merged.update(vehicle)
    if "name" in vehicle:
        merged["car_name"] = vehicle["name"]
    return merged


def get_vehicle_tracker_funcs():
    """Return (_start_vehicle_tracker, _stop_vehicle_tracker) from server (shared state)."""
    from server import _start_vehicle_tracker, _stop_vehicle_tracker
    return _start_vehicle_tracker, _stop_vehicle_tracker
