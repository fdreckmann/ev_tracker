"""
Reconciliation service — merges wallbox_sessions with existing API-based sessions.

Design: keep it small. This service does one thing well: when a wallbox_session
is confirmed for a vehicle, check whether an overlapping normal session from the
vehicle API already exists. If one does, enrich it with meter data instead of
creating a duplicate. If none exists, delegate to wallbox.py's creator.

Dedupe criteria (all must hold):
- Same vehicle_id
- Overlapping time window (start/end intersect)
- Similar kWh (within KWH_TOLERANCE of the wallbox energy reading)
"""
from __future__ import annotations

import json
import logging
from typing import Optional

log = logging.getLogger(__name__)

# kWh tolerance for considering two sessions the "same charge"
KWH_TOLERANCE = 3.0


def find_overlapping_session(con, vehicle_id: str, start_ts: str, end_ts: str,
                              energy_kwh: Optional[float]) -> Optional[dict]:
    """Return an existing session that overlaps the given time window.

    Returns the best matching session dict, or None.
    """
    # Sessions overlap when they are not entirely before or after the window.
    rows = con.execute(
        """SELECT * FROM sessions
           WHERE vehicle_id = ?
             AND end_ts IS NOT NULL
             AND start_ts < ?
             AND end_ts   > ?
           ORDER BY ABS(COALESCE(kwh_charged,0) - ?) ASC
           LIMIT 5""",
        (vehicle_id, end_ts, start_ts, energy_kwh or 0.0),
    ).fetchall()
    if not rows:
        return None
    candidates = [dict(r) for r in rows]
    for c in candidates:
        if energy_kwh is None:
            return c
        sess_kwh = c.get("kwh_charged") or 0.0
        if abs(sess_kwh - energy_kwh) <= KWH_TOLERANCE:
            return c
    return None


def enrich_session_with_meter(con, session_id: int, wbs: dict) -> bool:
    """Add meter data from a wallbox_session to an existing normal session.

    Only overwrites meter_old/meter_new/kwh_charged when the wallbox reading
    is more reliable (meter_used=0 or no prior meter data).
    """
    row = con.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        return False
    sess = dict(row)
    meter_locked = bool(sess.get("meter_values_manual"))

    updates: dict = {}

    # Enrich meter values if not already set from a meter source — but never
    # when the user manually corrected/cleared them (meter_values_manual=1).
    # A cleared meter_old/meter_new (NULL) must stay NULL, not get silently
    # refilled by a later wallbox-session reconciliation.
    if not meter_locked:
        if sess.get("meter_old") is None and wbs.get("meter_start_kwh") is not None:
            updates["meter_old"] = wbs["meter_start_kwh"]
        if sess.get("meter_new") is None and wbs.get("meter_end_kwh") is not None:
            updates["meter_new"] = wbs["meter_end_kwh"]

    # Prefer wallbox kWh when no meter was used
    if wbs.get("energy_kwh") is not None and not sess.get("meter_used"):
        updates["kwh_charged"] = wbs["energy_kwh"]
        updates["kwh_source"]  = "meter"
        if not meter_locked:
            updates["meter_used"]  = 1

    # Mark source
    if sess.get("source_primary") is None:
        updates["source_primary"] = "wallbox"

    # Build evidence_json
    existing_ev = {}
    try:
        existing_ev = json.loads(sess.get("evidence_json") or "{}") or {}
    except Exception:
        pass
    existing_ev["wallbox_session_id"] = wbs["id"]
    existing_ev["wallbox_source"]     = wbs.get("source_name")
    updates["evidence_json"] = json.dumps(existing_ev)

    if not updates:
        return True

    sets = ", ".join(f"{k}=?" for k in updates)
    con.execute(f"UPDATE sessions SET {sets} WHERE id=?",
                list(updates.values()) + [session_id])
    con.commit()
    log.info("Enriched session %d with wallbox_session %d", session_id, wbs["id"])
    return True


def reconcile_charging_evidence(con, wbs: dict, vehicle_id: str) -> dict:
    """Main entry point: reconcile a confirmed wallbox_session with existing data.

    Returns a result dict:
        {
          "action":     "enriched" | "created" | "skipped",
          "session_id": int | None,
          "wbs_id":     int,
        }
    """
    wbs_id    = wbs["id"]
    start_ts  = wbs.get("start_ts") or ""
    end_ts    = wbs.get("end_ts")   or start_ts
    energy    = wbs.get("energy_kwh")

    # Step 1: look for an existing overlapping session
    existing = find_overlapping_session(con, vehicle_id, start_ts, end_ts, energy)

    if existing:
        enrich_session_with_meter(con, existing["id"], wbs)
        # Mark wallbox_session as linked
        con.execute(
            "UPDATE wallbox_sessions"
            " SET vehicle_id=?, vehicle_assignment_status='confirmed',"
            "     excluded_from_reports=0, updated_at=datetime('now')"
            " WHERE id=?",
            (vehicle_id, wbs_id),
        )
        con.commit()
        log.info("reconcile wbs=%d → enriched existing session %d", wbs_id, existing["id"])
        return {"action": "enriched", "session_id": existing["id"], "wbs_id": wbs_id}

    # Step 2: no existing session — create a new one via the wallbox route helper
    try:
        from routes.wallbox import _create_session_from_wallbox
        sid = _create_session_from_wallbox(con, wbs, vehicle_id)
    except Exception as e:
        log.warning("reconcile wbs=%d: session creation failed: %s", wbs_id, e)
        sid = None

    if sid:
        log.info("reconcile wbs=%d → created session %d", wbs_id, sid)
        return {"action": "created", "session_id": sid, "wbs_id": wbs_id}

    return {"action": "skipped", "session_id": None, "wbs_id": wbs_id}
