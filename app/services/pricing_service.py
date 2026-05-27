"""
Unified pricing logic for charging sessions.

Resolution priority:
  Home:    price_per_kwh_home from config
  Extern:  resolve_public_charging_price() (contract → fallback → legacy)
  Unknown: keep existing price if any
"""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)

_CHARGER_ALIASES = {
    "ac": "ac", "ac1": "ac", "ac3": "ac", "slow": "ac", "type2": "ac",
    "dc": "dc", "fast": "dc", "ccs": "dc", "chademo": "dc",
}


def normalize_charger_type(value: str | None) -> str:
    """Normalize charger type strings to 'ac' or 'dc'; returns 'ac' as default."""
    if not value:
        return "ac"
    return _CHARGER_ALIASES.get(str(value).lower().strip(), "ac")


def normalize_location(loc: str | None) -> str:
    """Normalize location strings. Maps 'external' → 'extern'. Returns 'unknown' as default."""
    if not loc:
        return "unknown"
    loc = str(loc).lower().strip()
    if loc in ("external", "extern"):
        return "extern"
    if loc == "home":
        return "home"
    return "unknown"


def resolve_session_price(
    location: str,
    charger_type: str,
    cfg: dict,
    con,
    session_id: int | None = None,
) -> dict:
    """
    Returns:
      {price_per_kwh, price_source, price_confidence, contract_id, contract_name, reason}

    Never raises; on error falls back to config defaults with source='fallback'.
    cost_manual=1 sessions must be handled by the caller — this function does not check it.
    """
    location = normalize_location(location)
    ct = normalize_charger_type(charger_type)
    result = {
        "price_per_kwh": None,
        "price_source": "unknown_location",
        "price_confidence": 0,
        "contract_id": None,
        "contract_name": None,
        "reason": "unknown location — no automatic price",
    }

    if location == "home":
        price = cfg.get("price_per_kwh_home", 0.30)
        try:
            price = round(float(price), 4)
        except (TypeError, ValueError):
            price = 0.30
        result.update(
            price_per_kwh=price,
            price_source="home_config",
            price_confidence=100,
            reason="home tariff from config",
        )
        return result

    if location == "extern":
        try:
            from services.public_charging_price_service import resolve_public_charging_price
            pc = resolve_public_charging_price(
                session_id or 0, ct, cfg, con
            )
            if pc and pc.get("price_per_kwh") is not None:
                result.update(
                    price_per_kwh=pc["price_per_kwh"],
                    price_source=pc.get("source", "contract"),
                    price_confidence=pc.get("confidence", 50),
                    contract_id=pc.get("contract_id"),
                    contract_name=pc.get("contract_name"),
                    reason=f"public charging: {pc.get('source', 'contract')}",
                )
                return result
        except Exception as e:
            log.debug("resolve_public_charging_price failed: %s", e)

        # Legacy fallback (ENTSO-E was here before; now only config keys)
        legacy_key = "price_per_kwh_dc" if ct == "dc" else "price_per_kwh_ac"
        legacy = cfg.get(legacy_key)
        if legacy is not None:
            try:
                result.update(
                    price_per_kwh=round(float(legacy), 4),
                    price_source="legacy_config",
                    price_confidence=20,
                    reason=f"legacy config key {legacy_key}",
                )
                return result
            except (TypeError, ValueError):
                pass

        # Absolute last resort
        default = 0.75 if ct == "dc" else 0.45
        result.update(
            price_per_kwh=default,
            price_source="default",
            price_confidence=10,
            reason="hardcoded default (no config or contract found)",
        )
        return result

    # location == "unknown" or anything else: return None price so caller keeps existing
    return result


def calculate_session_cost(kwh: float | None, price_per_kwh: float | None) -> float | None:
    """Returns rounded cost or None if inputs are invalid."""
    if kwh is None or price_per_kwh is None:
        return None
    try:
        return round(float(kwh) * float(price_per_kwh), 2)
    except (TypeError, ValueError):
        return None


def get_session_duration_seconds(session: dict) -> int | None:
    """Compute duration from start_ts / end_ts strings. Returns None if not available."""
    start = session.get("start_ts")
    end = session.get("end_ts")
    if not start or not end:
        return None
    try:
        from datetime import datetime
        fmt = "%Y-%m-%dT%H:%M:%S"
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        secs = int((e - s).total_seconds())
        return secs if secs >= 0 else None
    except Exception:
        return None
