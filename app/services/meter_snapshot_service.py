"""
Historical meter (Wallbox/Zähler) snapshots and meter-delta lookup.

Snapshots are written from the tracker poll loop when a meter source is active,
with change/heartbeat dedupe so the DB does not explode. The missing-charge
detector later asks whether a meter rise happened inside a candidate's time
window — a confirmed rise from an EV/wallbox meter strongly validates a hidden
charge, whereas a house-total meter only counts as weak evidence.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# How strongly a meter rise confirms a charge, by meter classification.
_TYPE_CONFIDENCE = {
    "ev_wallbox":         0.95,
    "ev_dedicated_meter": 0.90,
    "house_total":        0.45,
    "unknown":            0.60,
}

# Meter types that measure (almost) only the car → a rise reliably means charging.
_EV_METER_TYPES = ("ev_wallbox", "ev_dedicated_meter")


def meter_type_confidence(meter_type: str | None) -> float:
    """Confidence weight (0–1) that a meter rise really was a vehicle charge."""
    return _TYPE_CONFIDENCE.get(meter_type or "unknown", 0.60)


def is_ev_meter(meter_type: str | None) -> bool:
    """True for meters that measure the car only (strong confirmation)."""
    return (meter_type or "unknown") in _EV_METER_TYPES


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def store_meter_snapshot(vehicle_id: str, source: str, value_kwh, raw_value,
                         unit, ok: bool, error, con, ts: str | None = None,
                         source_type: str | None = None,
                         source_name: str | None = None,
                         power_kw: float | None = None,
                         energy_total_kwh: float | None = None,
                         raw_json: str | None = None) -> int:
    """Insert one meter snapshot row. Returns the new row id.

    The new optional parameters (source_type, source_name, power_kw,
    energy_total_kwh, raw_json) are ignored gracefully when the DB columns
    don't exist yet (old migrations), so existing callers stay unchanged.
    """
    ts = ts or _now().isoformat(timespec="seconds")
    cur = con.cursor()
    cur.execute(
        """INSERT INTO meter_snapshots
           (vehicle_id, ts, source, value_kwh, raw_value, unit, ok, error, created_at,
            source_type, source_name, power_kw, energy_total_kwh, raw_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (vehicle_id, ts, source, value_kwh,
         None if raw_value is None else str(raw_value),
         unit, 1 if ok else 0, error, ts,
         source_type or "meter",
         source_name or source,
         power_kw,
         energy_total_kwh if energy_total_kwh is not None else value_kwh,
         raw_json),
    )
    con.commit()
    return cur.lastrowid


def maybe_record_poll_snapshot(vehicle_id: str, cfg: dict, st: dict, con) -> int | None:
    """Read the meter once per poll and store a snapshot with change/heartbeat
    dedupe. Updates the tracker state ``st`` in place. Never raises.

    Storage rules:
      * ok reading  → store when the value changed by >= min_delta, or once per
                      heartbeat interval.
      * failed read → stored at most once per heartbeat and capped per vehicle;
                      a failing meter also backs reads off to the heartbeat rate.
    """
    try:
        if not cfg.get("meter_snapshot_enabled", True):
            return None
        source = cfg.get("meter_source", "none") or "none"
        if source == "none":
            return None
        if cfg.get("meter_scope") == "disabled":
            return None

        hb_minutes = float(cfg.get("meter_snapshot_heartbeat_minutes", 10) or 10)
        min_delta = float(cfg.get("meter_snapshot_min_delta_kwh", 0.05) or 0.0)
        now = _now()
        last_dt = st.get("meter_snap_last_dt")
        last_val = st.get("meter_snap_last_val")
        last_ok = st.get("meter_snap_last_ok", True)
        mins_since = (now - last_dt).total_seconds() / 60.0 if last_dt else None
        heartbeat_due = mins_since is None or mins_since >= hb_minutes

        # Back off reads from a meter that is currently failing.
        if last_ok is False and not heartbeat_due:
            return None

        from meter_providers import read_meter
        res = read_meter(cfg)
        ts = now.isoformat(timespec="seconds")

        if res.ok and res.value is not None:
            changed = last_val is None or abs(res.value - last_val) >= min_delta
            if not (changed or heartbeat_due):
                return None
            rid = store_meter_snapshot(vehicle_id, source, res.value, res.raw_value,
                                       res.unit, True, None, con, ts=ts)
            st["meter_snap_last_dt"] = now
            st["meter_snap_last_val"] = res.value
            st["meter_snap_last_power"] = res.power_kw  # None when provider doesn't expose it
            st["meter_snap_last_ok"] = True
            return rid

        # Failed read — store sparingly and cap the number of error rows.
        if not heartbeat_due:
            return None
        cap = int(cfg.get("meter_snapshot_max_error_rows", 500) or 500)
        n_err = con.execute(
            "SELECT COUNT(*) FROM meter_snapshots WHERE vehicle_id=? AND ok=0",
            (vehicle_id,),
        ).fetchone()[0]
        if n_err >= cap:
            con.execute(
                "DELETE FROM meter_snapshots WHERE id IN "
                "(SELECT id FROM meter_snapshots WHERE vehicle_id=? AND ok=0 "
                " ORDER BY id ASC LIMIT 1)",
                (vehicle_id,),
            )
        rid = store_meter_snapshot(vehicle_id, source, None, res.raw_value,
                                   res.unit, False, res.error, con, ts=ts)
        st["meter_snap_last_dt"] = now
        st["meter_snap_last_ok"] = False
        return rid
    except Exception as e:  # never break the poll loop
        log.debug("meter snapshot error [%s]: %s", vehicle_id, e)
        return None


def find_meter_delta(vehicle_id: str, start_ts: str, end_ts: str, con) -> dict | None:
    """Energy added on the meter across [start_ts, end_ts].

    Picks the nearest valid reading at/before the start and at/after the end
    (falling back to the closest readings inside the window when the window is
    not fully bracketed). Returns {start_value, end_value, delta_kwh, start_ts,
    end_ts, source} or None when no usable pair exists.
    """
    cur = con.cursor()

    def _one(sql, params):
        return cur.execute(sql, params).fetchone()

    before = _one(
        "SELECT ts,value_kwh,source FROM meter_snapshots "
        "WHERE vehicle_id=? AND ok=1 AND value_kwh IS NOT NULL AND ts<=? "
        "ORDER BY ts DESC LIMIT 1", (vehicle_id, start_ts))
    if before is None:
        before = _one(
            "SELECT ts,value_kwh,source FROM meter_snapshots "
            "WHERE vehicle_id=? AND ok=1 AND value_kwh IS NOT NULL AND ts>=? AND ts<=? "
            "ORDER BY ts ASC LIMIT 1", (vehicle_id, start_ts, end_ts))

    after = _one(
        "SELECT ts,value_kwh,source FROM meter_snapshots "
        "WHERE vehicle_id=? AND ok=1 AND value_kwh IS NOT NULL AND ts>=? "
        "ORDER BY ts ASC LIMIT 1", (vehicle_id, end_ts))
    if after is None:
        after = _one(
            "SELECT ts,value_kwh,source FROM meter_snapshots "
            "WHERE vehicle_id=? AND ok=1 AND value_kwh IS NOT NULL AND ts<=? AND ts>=? "
            "ORDER BY ts DESC LIMIT 1", (vehicle_id, end_ts, start_ts))

    if not before or not after:
        return None
    if before[0] == after[0]:        # same single reading — no interval
        return None
    sv, ev = before[1], after[1]
    if sv is None or ev is None:
        return None
    return {
        "start_value": round(float(sv), 3),
        "end_value":   round(float(ev), 3),
        "delta_kwh":   round(float(ev) - float(sv), 3),
        "start_ts":    before[0],
        "end_ts":      after[0],
        "source":      after[2] or before[2],
    }
