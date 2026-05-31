"""
Service for detecting missing charge sessions based on vehicle state snapshots.

After each successful provider poll, a snapshot is saved. When the gap between
two consecutive snapshots is large and SOC has risen (or didn't drop as expected
given driven distance), a missing-charge candidate is created for user review.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone


log = logging.getLogger(__name__)


def save_snapshot(vehicle_id: str, soc, odometer_km, range_km,
                  location_status: str, provider: str, con) -> int | None:
    """Persist a vehicle state snapshot. Returns the new row id, or None if skipped."""
    if soc is None and odometer_km is None:
        return None
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    cur = con.cursor()
    cur.execute(
        """INSERT INTO vehicle_snapshots
           (vehicle_id, ts, soc, odometer_km, range_km, location_status,
            provider, raw_available, created_at)
           VALUES (?,?,?,?,?,?,?,1,?)""",
        (vehicle_id, now, soc, odometer_km, range_km, location_status, provider, now),
    )
    con.commit()
    snap_id = cur.lastrowid
    log.debug("Snapshot vehicle=%s soc=%s odo=%s id=%d", vehicle_id, soc, odometer_km, snap_id)
    return snap_id


def check_for_missing_charge(vehicle_id: str, new_snap_id: int, cfg: dict, con) -> int | None:
    """Compare new snapshot against the previous one and create a candidate if needed.

    Returns the candidate id when one is created, None otherwise.
    """
    if not cfg.get("missing_charge_detection_enabled", True):
        return None

    cur = con.cursor()

    row = cur.execute(
        "SELECT id,ts,soc,odometer_km,location_status FROM vehicle_snapshots WHERE id=?",
        (new_snap_id,),
    ).fetchone()
    if not row:
        return None
    new_id, new_ts, new_soc, new_odo, new_loc = row

    prev = cur.execute(
        """SELECT id,ts,soc,odometer_km,location_status FROM vehicle_snapshots
           WHERE vehicle_id=? AND id<? ORDER BY id DESC LIMIT 1""",
        (vehicle_id, new_snap_id),
    ).fetchone()
    if not prev:
        return None
    prev_id, prev_ts, prev_soc, prev_odo, prev_loc = prev

    if new_soc is None or prev_soc is None:
        return None

    # ── Time gap ──────────────────────────────────────────────────────────────
    try:
        gap_minutes = (
            datetime.fromisoformat(new_ts) - datetime.fromisoformat(prev_ts)
        ).total_seconds() / 60.0
    except Exception:
        return None

    min_gap = float(cfg.get("missing_charge_min_gap_minutes", 30))
    if gap_minutes < min_gap:
        return None

    # ── SOC and odometer deltas ───────────────────────────────────────────────
    soc_gain = new_soc - prev_soc
    min_soc_gain = float(cfg.get("missing_charge_min_soc_gain_percent", 3.0))

    driven_km: float | None = None
    if new_odo is not None and prev_odo is not None and new_odo >= prev_odo:
        driven_km = round(new_odo - prev_odo, 1)

    # ── Energy estimation ─────────────────────────────────────────────────────
    battery_kwh = float(
        cfg.get("usable_battery_kwh")
        or cfg.get("battery_capacity_kwh")
        or 0
    )
    consumption = float(
        cfg.get("default_consumption_kwh_per_100km")
        or cfg.get("missing_charge_default_consumption_kwh_per_100km", 18.0)
    )

    battery_delta_kwh = 0.0
    if battery_kwh > 0 and soc_gain > 0:
        battery_delta_kwh = round(soc_gain / 100.0 * battery_kwh, 2)

    driving_kwh = 0.0
    if driven_km:
        driving_kwh = round(driven_km * consumption / 100.0, 2)

    estimated_kwh = round(battery_delta_kwh + driving_kwh, 2)
    min_kwh = float(cfg.get("missing_charge_min_kwh", 2.0))

    # ── Plausibility filter ───────────────────────────────────────────────────
    if soc_gain < min_soc_gain:
        # SOC didn't rise enough — check if it failed to drop as expected while driving
        if driven_km and battery_kwh > 0:
            expected_drop_pct = driving_kwh / battery_kwh * 100.0
            actual_drop_pct = -soc_gain  # positive = SOC fell
            hidden_kwh = round((expected_drop_pct - actual_drop_pct) / 100.0 * battery_kwh, 2)
            if hidden_kwh >= min_kwh:
                estimated_kwh = hidden_kwh
                battery_delta_kwh = hidden_kwh
            else:
                return None
        else:
            return None

    if estimated_kwh < min_kwh:
        return None

    # ── No existing session in the gap ───────────────────────────────────────
    existing = cur.execute(
        """SELECT id FROM sessions
           WHERE vehicle_id=? AND start_ts<=? AND end_ts>=? AND end_ts IS NOT NULL
           LIMIT 1""",
        (vehicle_id, new_ts, prev_ts),
    ).fetchone()
    if existing:
        return None

    # ── Deduplication ─────────────────────────────────────────────────────────
    if cur.execute(
        """SELECT id FROM missing_charge_candidates
           WHERE vehicle_id=? AND snapshot_before_id=? AND snapshot_after_id=?
           AND status IN ('open','accepted')""",
        (vehicle_id, prev_id, new_snap_id),
    ).fetchone():
        return None

    if cur.execute(
        """SELECT id FROM missing_charge_candidates
           WHERE vehicle_id=? AND start_ts=? AND end_ts=? AND status='ignored'""",
        (vehicle_id, prev_ts, new_ts),
    ).fetchone():
        return None

    # ── Location suggestion ───────────────────────────────────────────────────
    if prev_loc == "home" and new_loc == "home":
        suggested_location = "home"
    elif "home" in (prev_loc, new_loc):
        suggested_location = "home"
    elif "extern" in (prev_loc, new_loc):
        suggested_location = "extern"
    else:
        suggested_location = "unknown"

    # ── Charger type suggestion ───────────────────────────────────────────────
    gap_hours = gap_minutes / 60.0
    avg_power_kw = round(estimated_kwh / gap_hours, 2) if gap_hours > 0 else None

    if not avg_power_kw or avg_power_kw <= 0:
        suggested_charger_type = "unknown"
    elif avg_power_kw > 22:
        suggested_charger_type = "dc"
    else:
        suggested_charger_type = "ac"
    # Long gap with low apparent power but high kWh → probably unknown rhythm (overnight)
    if gap_hours > 8 and avg_power_kw and avg_power_kw < 1.5 and estimated_kwh > 20:
        suggested_charger_type = "unknown"

    # ── Confidence score ──────────────────────────────────────────────────────
    confidence = 50
    if soc_gain >= 10:
        confidence += 20
    elif soc_gain >= 5:
        confidence += 10
    if battery_kwh > 0:
        confidence += 10
    if driven_km is not None:
        confidence += 5
    if suggested_location != "unknown":
        confidence += 10
    confidence = min(confidence, 95)

    reason_parts = [f"SOC {prev_soc:.0f}% → {new_soc:.0f}% (+{soc_gain:.0f}%)"]
    if driven_km:
        reason_parts.append(f"{driven_km:.0f} km gefahren")
    reason_parts.append(f"Offline {gap_minutes:.0f} min")
    reason = ", ".join(reason_parts)

    # ── Insert candidate ──────────────────────────────────────────────────────
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    cur.execute(
        """INSERT INTO missing_charge_candidates
           (vehicle_id, snapshot_before_id, snapshot_after_id, start_ts, end_ts,
            soc_start, soc_end, odo_start, odo_end, driven_km,
            estimated_kwh, estimated_consumption_kwh, estimated_battery_delta_kwh,
            estimated_avg_power_kw, suggested_charger_type, suggested_location,
            confidence, reason, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            vehicle_id, prev_id, new_snap_id, prev_ts, new_ts,
            prev_soc, new_soc, prev_odo, new_odo, driven_km,
            estimated_kwh, driving_kwh, battery_delta_kwh,
            avg_power_kw, suggested_charger_type, suggested_location,
            confidence, reason, "open", now, now,
        ),
    )
    con.commit()
    cand_id = cur.lastrowid

    log.info(
        "[%s] Missing-Charge #%d: %s (%.1f kWh, conf=%d%%)",
        vehicle_id, cand_id, reason, estimated_kwh, confidence,
    )

    try:
        from core.security import _audit
        _audit(
            "missing_charge_candidate_created",
            f"vehicle_id={vehicle_id} candidate_id={cand_id} "
            f"kwh={estimated_kwh} reason={reason}",
            ip="internal",
        )
    except Exception:
        pass

    try:
        from services.notification_service import notify
        st_label = prev_ts[:16] if prev_ts else "?"
        en_label = new_ts[:16] if new_ts else "?"
        notify(
            type="missing_charge_candidate_created",
            severity="warning",
            title="Möglicher fehlender Ladevorgang erkannt",
            message=f"Das Fahrzeug war von {st_label} bis {en_label} offline. SOC stieg von {prev_soc:.0f}% auf {new_soc:.0f}%. Geschätzte Ladung: {estimated_kwh:.1f} kWh.",
            vehicle_id=vehicle_id,
            data={"candidate_id": cand_id, "vehicle_id": vehicle_id,
                  "start_ts": prev_ts, "end_ts": new_ts,
                  "estimated_kwh": estimated_kwh, "soc_start": prev_soc, "soc_end": new_soc},
            dedupe_key=f"missing_charge:{vehicle_id}:{prev_ts}:{new_ts}",
            action_url="/",
        )
    except Exception as _ne:
        log.debug("notify missing_charge error: %s", _ne)

    return cand_id
