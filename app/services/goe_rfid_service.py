"""
go-e Charger RFID/Card mapping service.

Reads the card energy counters from a go-e charger API (v2) and identifies
which card was used during a charging session. Maps card slots to vehicle_ids
via the ``goe_card_vehicle_map`` config key.

go-e v2 API fields used:
  ``cae``   — array of card energy totals (0.1 Wh each), one entry per card slot
  ``cards`` — array of card objects: {``name``: str, ``energy``: float (Wh), ``rfid``: str}

Resolution logic
----------------
1. Read card counters at session start (snapshot_before) and session end (snapshot_after).
2. Identify slots where the energy increased (delta > CARD_DELTA_MIN_WH).
3. If exactly one card increased and is mapped → confirmed assignment.
4. If multiple cards increased → conflict → unassigned.
5. If no card increased → unassigned.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

# Minimum energy delta (Wh) to consider a card as "used this session"
CARD_DELTA_MIN_WH = 100  # 0.1 kWh

# go-e v2 converts cae to Wh this way: raw_value * 10 = Wh
# (cae values are stored in units of 0.1 Wh = 100 mWh)
_GOE_CAE_UNIT_WH = 10.0


def _goe_base_url(cfg: dict) -> str:
    """Resolve go-e base URL from config."""
    url = (cfg.get("goe_url") or "").strip().rstrip("/")
    if not url:
        return ""
    if not url.startswith("http"):
        url = "http://" + url
    return url


def _goe_auth(cfg: dict):
    """Return (user, password) tuple or None."""
    token = (cfg.get("goe_auth") or "").strip()
    if not token:
        return None
    return (token, "")  # go-e uses token as username with empty password


def read_goe_status(cfg: dict, timeout: int = 8) -> Optional[dict]:
    """Fetch go-e /api/status (v2) and return the JSON dict, or None on error."""
    base_url = _goe_base_url(cfg)
    if not base_url:
        return None
    import requests
    auth = _goe_auth(cfg)
    try:
        r = requests.get(f"{base_url}/api/status", timeout=timeout,
                         auth=auth if auth else None)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug("go-e status fetch failed: %s", e)
    # Fallback: v1 endpoint
    try:
        r = requests.get(f"{base_url}/status", timeout=timeout,
                         auth=auth if auth else None)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug("go-e v1 status fetch failed: %s", e)
    return None


def read_card_energy_snapshot(cfg: dict) -> Optional[dict]:
    """Return a snapshot of card energy counters.

    Returns a dict like::

        {
          "cards": [
            {"slot": 0, "name": "Card 1", "energy_wh": 12500.0},
            ...
          ],
          "raw": <full api response>,
        }

    Returns None when go-e is not configured or not reachable.
    """
    if not cfg.get("goe_rfid_enabled", False):
        return None
    data = read_goe_status(cfg)
    if data is None:
        return None
    return _parse_card_snapshot(data)


def _parse_card_snapshot(data: dict) -> dict:
    """Parse go-e API response into a card snapshot dict."""
    cards = []

    # go-e v2: ``cae`` = array of raw energy values (unit: 0.1 Wh)
    cae = data.get("cae") or []
    card_info = data.get("cards") or []  # may have names

    for slot, raw in enumerate(cae):
        try:
            energy_wh = float(raw) * _GOE_CAE_UNIT_WH
        except (TypeError, ValueError):
            energy_wh = 0.0
        name = None
        if slot < len(card_info):
            c = card_info[slot]
            if isinstance(c, dict):
                name = c.get("name") or c.get("rfid") or f"Karte {slot + 1}"
            elif isinstance(c, str):
                name = c
        if name is None:
            name = f"Karte {slot + 1}"
        cards.append({"slot": slot, "name": name, "energy_wh": round(energy_wh, 1)})

    # Fallback: v2 ``cards`` array with built-in energy field
    if not cards and card_info:
        for slot, c in enumerate(card_info):
            if not isinstance(c, dict):
                continue
            energy_wh = 0.0
            raw_e = c.get("energy")
            if raw_e is not None:
                try:
                    energy_wh = float(raw_e) * _GOE_CAE_UNIT_WH
                except (TypeError, ValueError):
                    pass
            cards.append({
                "slot": slot,
                "name": c.get("name") or c.get("rfid") or f"Karte {slot + 1}",
                "energy_wh": round(energy_wh, 1),
            })

    return {"cards": cards, "raw": data}


def resolve_vehicle_from_cards(
    cfg: dict,
    snapshot_before: dict,
    snapshot_after: dict,
) -> tuple[Optional[str], str, Optional[str]]:
    """Identify which vehicle charged based on card energy deltas.

    Returns ``(vehicle_id, assignment_status, card_name)`` where:
    - ``vehicle_id`` is the matched vehicle or None
    - ``assignment_status`` is one of: confirmed / unassigned / conflict
    - ``card_name`` is the name of the matched card or None

    Logic:
    - Exactly one card increased AND is in goe_card_vehicle_map → confirmed
    - Multiple cards increased → conflict → unassigned
    - No card increased → unassigned
    """
    card_map: dict = {}
    raw_map = cfg.get("goe_card_vehicle_map") or {}
    if isinstance(raw_map, str):
        import json
        try:
            raw_map = json.loads(raw_map)
        except Exception:
            raw_map = {}
    for k, v in raw_map.items():
        try:
            card_map[int(k)] = str(v)
        except (ValueError, TypeError):
            pass

    cards_before = {c["slot"]: c["energy_wh"] for c in (snapshot_before.get("cards") or [])}
    cards_after  = {c["slot"]: c["energy_wh"] for c in (snapshot_after.get("cards") or [])}
    names_after  = {c["slot"]: c["name"] for c in (snapshot_after.get("cards") or [])}

    active_slots = []
    for slot, energy_after in cards_after.items():
        energy_before = cards_before.get(slot, energy_after)
        delta = energy_after - energy_before
        if delta >= CARD_DELTA_MIN_WH:
            active_slots.append(slot)

    if len(active_slots) == 0:
        return None, "unassigned", None

    if len(active_slots) > 1:
        log.info("go-e RFID: multiple cards active (%s) — conflict/unassigned", active_slots)
        return None, "unassigned", None  # conflict

    slot = active_slots[0]
    card_name = names_after.get(slot, f"Karte {slot + 1}")

    if slot not in card_map:
        log.info("go-e RFID: card slot %d active but not in goe_card_vehicle_map", slot)
        return None, "unassigned", card_name

    vehicle_id = card_map[slot]
    log.info("go-e RFID: card slot %d (%s) → vehicle %s (confirmed)", slot, card_name, vehicle_id)
    return vehicle_id, "confirmed", card_name


def get_cards_for_ui(cfg: dict) -> list[dict]:
    """Return card list for UI display (slot, name, energy_kwh, vehicle_id).

    Returns empty list when not configured or not reachable.
    """
    snapshot = read_card_energy_snapshot(cfg)
    if not snapshot:
        return []
    card_map: dict = {}
    raw_map = cfg.get("goe_card_vehicle_map") or {}
    if isinstance(raw_map, str):
        import json
        try:
            raw_map = json.loads(raw_map)
        except Exception:
            raw_map = {}
    for k, v in raw_map.items():
        try:
            card_map[int(k)] = str(v)
        except (ValueError, TypeError):
            pass

    result = []
    for c in snapshot["cards"]:
        slot = c["slot"]
        result.append({
            "slot": slot,
            "name": c["name"],
            "energy_kwh": round(c["energy_wh"] / 1000, 3),
            "vehicle_id": card_map.get(slot),
        })
    return result
