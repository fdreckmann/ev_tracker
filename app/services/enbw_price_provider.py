"""
Inoffizielle EnBW Public API für Live-Preise an Ladestationen.

ACHTUNG: Diese Schnittstelle ist NICHT offiziell und kann sich jederzeit ändern.
Nur aktivieren wenn der Nutzer enbw_price_lookup_enabled=true gesetzt hat
und einen gültigen enbw_api_subscription_key konfiguriert hat.
"""
from __future__ import annotations
import logging
import time

import requests

log = logging.getLogger(__name__)

# In-memory cache: evse_id → (timestamp, result_dict)
_CACHE: dict[str, tuple[float, dict | None]] = {}


def get_live_price(evse_id: str, cfg: dict) -> dict | None:
    """
    Queries the EnBW API for live prices at a given EVSE.

    Returns dict with keys ac_price_kwh / dc_price_kwh, or None on failure.
    Results are cached for enbw_price_cache_minutes (default 60).
    """
    cache_minutes = float(cfg.get("enbw_price_cache_minutes", 60))
    now = time.time()
    cached = _CACHE.get(evse_id)
    if cached and (now - cached[0]) < cache_minutes * 60:
        return cached[1]

    api_key = cfg.get("enbw_api_subscription_key", "").strip()
    if not api_key:
        log.debug("EnBW live price: kein API-Key konfiguriert")
        return None

    base_url = cfg.get(
        "enbw_api_base_url",
        "https://enbw-emp.azure-api.net/emobility-public-api/api/v1",
    ).rstrip("/")

    try:
        url = f"{base_url}/chargestations/{evse_id}"
        resp = requests.get(
            url,
            headers={"Ocp-Apim-Subscription-Key": api_key},
            timeout=10,
        )
        if resp.status_code == 404:
            log.debug("EnBW API: evse_id %s nicht gefunden", evse_id)
            _CACHE[evse_id] = (now, None)
            return None
        if resp.status_code != 200:
            log.warning("EnBW API returned %d für evse_id=%s", resp.status_code, evse_id)
            return None
        data = resp.json()
        result = _parse_price(data)
        _CACHE[evse_id] = (now, result)
        return result
    except requests.Timeout:
        log.warning("EnBW API Timeout für evse_id=%s", evse_id)
    except Exception as e:
        log.warning("EnBW API Fehler: %s", e)
    return None


def _parse_price(data: dict) -> dict | None:
    result: dict[str, float] = {}
    # Try different response structures
    connectors = (
        data.get("connectors")
        or data.get("evses")
        or data.get("chargingPoints")
        or []
    )
    for c in connectors:
        price = (
            c.get("pricePerKWh")
            or c.get("price_per_kwh")
            or c.get("currentPrice")
        )
        power = (
            c.get("maxPowerInKw")
            or c.get("maxPower")
            or c.get("maxChargingPower")
            or 0
        )
        if price is None:
            continue
        try:
            p = float(price)
            pw = float(power)
        except (TypeError, ValueError):
            continue
        if pw > 22:
            result["dc_price_kwh"] = p
        else:
            result["ac_price_kwh"] = p
    return result if result else None
