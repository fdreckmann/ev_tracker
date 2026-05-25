"""
Public charging price resolution for extern sessions.

Resolution chain:
  1. Session has cost_manual=1 → skip (caller handles this)
  2. Live EnBW API (if enabled + evse_id on session)
  3. Active default charging contract
  4. Global fallback prices from config
"""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)

# Built-in contract templates — shown in UI, user can import them into DB
BUILTIN_CONTRACTS: dict[str, dict] = {
    "enbw_s": {
        "id": "enbw_s",
        "name": "EnBW mobility+ S",
        "cpo": "enbw",
        "price_ac_kwh": 0.49,
        "price_dc_kwh": 0.49,
        "monthly_fee_eur": 0.00,
        "networks": ["EnBW", "EnBW mobility+"],
        "description": "Ohne Abo – einfacher Einstieg",
    },
    "enbw_m": {
        "id": "enbw_m",
        "name": "EnBW mobility+ M",
        "cpo": "enbw",
        "price_ac_kwh": 0.49,
        "price_dc_kwh": 0.35,
        "monthly_fee_eur": 4.99,
        "networks": ["EnBW", "EnBW mobility+"],
        "description": "4,99 €/Monat – für gelegentliche DC-Lader",
    },
    "enbw_l": {
        "id": "enbw_l",
        "name": "EnBW mobility+ L",
        "cpo": "enbw",
        "price_ac_kwh": 0.49,
        "price_dc_kwh": 0.30,
        "monthly_fee_eur": 17.99,
        "networks": ["EnBW", "EnBW mobility+"],
        "description": "17,99 €/Monat – für häufige DC-Lader",
    },
    "ionity_passport": {
        "id": "ionity_passport",
        "name": "IONITY Passport",
        "cpo": "ionity",
        "price_ac_kwh": None,
        "price_dc_kwh": 0.35,
        "monthly_fee_eur": 17.99,
        "networks": ["IONITY"],
        "description": "17,99 €/Monat – günstiger IONITY-Preis",
    },
    "ionity_direct": {
        "id": "ionity_direct",
        "name": "IONITY Direktladen",
        "cpo": "ionity",
        "price_ac_kwh": None,
        "price_dc_kwh": 0.79,
        "monthly_fee_eur": 0.00,
        "networks": ["IONITY"],
        "description": "Ohne Abo – Standardpreis IONITY",
    },
    "tesla_supercharging": {
        "id": "tesla_supercharging",
        "name": "Tesla Supercharging",
        "cpo": "tesla",
        "price_ac_kwh": None,
        "price_dc_kwh": 0.40,
        "monthly_fee_eur": 0.00,
        "networks": ["Tesla"],
        "description": "Ca. 0,35–0,45 €/kWh (variiert je Standort)",
    },
    "allego": {
        "id": "allego",
        "name": "Allego",
        "cpo": "allego",
        "price_ac_kwh": 0.59,
        "price_dc_kwh": 0.59,
        "monthly_fee_eur": 0.00,
        "networks": ["Allego"],
        "description": "Standardpreise ohne Abo",
    },
    "aral_pulse": {
        "id": "aral_pulse",
        "name": "Aral pulse / bp pulse",
        "cpo": "aral",
        "price_ac_kwh": 0.44,
        "price_dc_kwh": 0.44,
        "monthly_fee_eur": 0.00,
        "networks": ["Aral pulse", "bp pulse"],
        "description": "Aral/bp-Netz, Preis ca. 0,44 €/kWh",
    },
    "rewe": {
        "id": "rewe",
        "name": "REWE Laden",
        "cpo": "rewe",
        "price_ac_kwh": 0.00,
        "price_dc_kwh": None,
        "monthly_fee_eur": 0.00,
        "networks": ["REWE"],
        "description": "Kostenloses AC-Laden bei REWE",
    },
    "maingau": {
        "id": "maingau",
        "name": "Maingau EinfachStromLaden",
        "cpo": "maingau",
        "price_ac_kwh": 0.39,
        "price_dc_kwh": 0.49,
        "monthly_fee_eur": 0.00,
        "networks": ["Maingau"],
        "description": "Günstige Maingau-Tarife",
    },
}


def resolve_public_charging_price(
    session_id: int,
    charger_type: str,
    cfg: dict,
    con,
) -> dict | None:
    """
    Returns dict: {price_per_kwh, source, confidence, contract_id, contract_name}
    or None when no price can be determined.
    """
    if not cfg.get("public_charging_price_enabled", True):
        return None

    # 1. Live EnBW price (unofficial API, only when explicitly enabled)
    if cfg.get("enbw_price_lookup_enabled", False):
        try:
            row = con.execute(
                "SELECT evse_id FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            evse_id = row["evse_id"] if row and row["evse_id"] else None
            if evse_id:
                from services.enbw_price_provider import get_live_price
                live = get_live_price(evse_id, cfg)
                if live:
                    price = live.get("dc_price_kwh") if charger_type == "dc" else live.get("ac_price_kwh")
                    if price is not None:
                        return {
                            "price_per_kwh": round(float(price), 4),
                            "source": "enbw_live",
                            "confidence": 90,
                            "contract_id": None,
                            "contract_name": "EnBW Live",
                        }
        except Exception as e:
            log.debug("EnBW live price failed for session %d: %s", session_id, e)

    # 2. Active charging contract
    try:
        default_id = cfg.get("public_charging_default_contract_id")
        contract = None
        if default_id:
            row = con.execute(
                "SELECT * FROM charging_contracts WHERE id=? AND active=1", (default_id,)
            ).fetchone()
            if row:
                contract = dict(row)
        if contract is None:
            row = con.execute(
                "SELECT * FROM charging_contracts WHERE active=1 "
                "ORDER BY is_default DESC, id ASC LIMIT 1"
            ).fetchone()
            if row:
                contract = dict(row)
        if contract:
            price = (
                contract.get("price_dc_kwh") if charger_type == "dc"
                else contract.get("price_ac_kwh")
            )
            if price is None:
                price = contract.get("price_kwh")
            if price is not None and float(price) >= 0:
                return {
                    "price_per_kwh": round(float(price), 4),
                    "source": "contract",
                    "confidence": 70,
                    "contract_id": contract["id"],
                    "contract_name": contract["name"],
                }
    except Exception as e:
        log.debug("Contract price lookup failed for session %d: %s", session_id, e)

    # 3. Global fallback prices
    fallback_key = "public_charging_fallback_dc" if charger_type == "dc" else "public_charging_fallback_ac"
    fallback = cfg.get(fallback_key)
    if fallback is not None:
        try:
            return {
                "price_per_kwh": round(float(fallback), 4),
                "source": "fallback",
                "confidence": 30,
                "contract_id": None,
                "contract_name": None,
            }
        except (TypeError, ValueError):
            pass

    return None
