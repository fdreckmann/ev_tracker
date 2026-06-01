"""
Service for detecting missing charge sessions based on vehicle state snapshots.

After each successful provider poll, a snapshot is saved. Two independent signals
create a missing-charge candidate for user review:

  * SOC-gain     — SOC rose between two meaningful snapshots (a charge happened
                   while the vehicle was offline).
  * Energy-balance — SOC fell, but by far too little for the distance driven.
                   The trip is energetically implausible, so a short charge stop
                   probably happened mid-trip even though SOC ended lower.

The expected consumption used by the energy-balance check is derived primarily
from the vehicle's own historical driving snapshots, falling back to official
vehicle data and finally a global default (see get_expected_consumption).

Candidates are only *suggestions* — no real session is ever created automatically.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta


log = logging.getLogger(__name__)

# Plausibility bounds for a single historical driving segment.
_HIST_SEGMENT_MIN_KM = 10.0
_HIST_CONS_MIN = 8.0
_HIST_CONS_MAX = 35.0


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


# ── Expected-consumption resolution ──────────────────────────────────────────

def _battery_kwh(cfg: dict) -> float:
    return float(cfg.get("usable_battery_kwh") or cfg.get("battery_capacity_kwh") or 0)


def _global_default_consumption(cfg: dict) -> float:
    return float(
        cfg.get("missing_charge_expected_consumption_kwh_per_100km")
        or cfg.get("default_consumption_kwh_per_100km")
        or cfg.get("missing_charge_default_consumption_kwh_per_100km")
        or 18.0
    )


def _official_consumption(cfg: dict) -> float | None:
    """Official (catalog/WLTP) consumption, adjusted by a real-world factor.

    Uses official_consumption_kwh_per_100km directly when set, otherwise derives
    it from usable battery and official range. Returns None if no official data.
    """
    factor = float(cfg.get("official_consumption_factor") or 1.20)
    direct = cfg.get("official_consumption_kwh_per_100km")
    if direct:
        base = float(direct)
    else:
        batt = _battery_kwh(cfg)
        rng = cfg.get("official_range_km")
        if batt > 0 and rng:
            base = batt / float(rng) * 100.0
        else:
            return None
    return round(base * factor, 2)


def _historical_consumption(vehicle_id: str, cfg: dict, con,
                            before_id: int | None = None) -> dict | None:
    """Distance-weighted average consumption from plausible driving segments.

    A driving segment is a pair of consecutive snapshots where the odometer rose,
    SOC fell (no net charge), no charging session overlaps, the distance is large
    enough and the resulting consumption is within plausible bounds. Outliers are
    trimmed before averaging. Returns {avg, distance_km, segments} or None.

    When before_id is given, only snapshots strictly before it are considered so a
    gap currently under evaluation cannot bias its own expected value.
    """
    batt = _battery_kwh(cfg)
    if batt <= 0:
        return None
    days = int(cfg.get("missing_charge_consumption_history_days") or 90)
    cutoff = (datetime.now(timezone.utc).replace(tzinfo=None)
              - timedelta(days=days)).isoformat(timespec="seconds")
    cur = con.cursor()
    q = ("SELECT id,ts,soc,odometer_km FROM vehicle_snapshots "
         "WHERE vehicle_id=? AND ts>=?")
    params: list = [vehicle_id, cutoff]
    if before_id is not None:
        q += " AND id<?"
        params.append(before_id)
    q += " ORDER BY id ASC"
    rows = cur.execute(q, params).fetchall()

    segments: list[tuple[float, float]] = []  # (consumption, distance)
    for a, b in zip(rows, rows[1:]):
        _, ts_a, soc_a, odo_a = a
        _, ts_b, soc_b, odo_b = b
        if None in (soc_a, soc_b, odo_a, odo_b):
            continue
        dist = odo_b - odo_a
        if dist < _HIST_SEGMENT_MIN_KM:
            continue
        if soc_a <= soc_b:               # not a pure driving segment
            continue
        if con.execute(
            "SELECT 1 FROM sessions WHERE vehicle_id=? AND start_ts<=? AND end_ts>=? "
            "AND end_ts IS NOT NULL LIMIT 1",
            (vehicle_id, ts_b, ts_a),
        ).fetchone():
            continue
        cons = batt * (soc_a - soc_b) / 100.0 / dist * 100.0
        if cons < _HIST_CONS_MIN or cons > _HIST_CONS_MAX:
            continue
        segments.append((cons, dist))

    if not segments:
        return None

    # Trim the top & bottom 10% by consumption when there are enough samples.
    segments.sort(key=lambda s: s[0])
    n = len(segments)
    if n >= 10:
        k = max(1, n // 10)
        segments = segments[k:n - k]

    total_dist = sum(d for _, d in segments)
    if total_dist <= 0:
        return None
    avg = sum(c * d for c, d in segments) / total_dist
    return {"avg": round(avg, 2),
            "distance_km": round(total_dist, 1),
            "segments": len(segments)}


def get_expected_consumption(vehicle_id: str, cfg: dict, con,
                             before_id: int | None = None) -> dict:
    """Resolve expected consumption (kWh/100km) for energy-balance detection.

    Priority: historical vehicle data > official vehicle data > global default.
    Returns {value, source, confidence, sample_distance_km, sample_segments,
    fallback_used}. source is one of historical / blended_historical_official /
    official / global_default.
    """
    global_default = _global_default_consumption(cfg)
    official = _official_consumption(cfg)
    hist = _historical_consumption(vehicle_id, cfg, con, before_id=before_id)

    min_km = float(cfg.get("missing_charge_consumption_min_history_km") or 300)
    min_segs = int(cfg.get("missing_charge_consumption_min_segments") or 5)

    def _result(value, source, confidence, fallback):
        return {
            "value": round(float(value), 2),
            "source": source,
            "confidence": round(confidence, 2),
            "sample_distance_km": round(hist["distance_km"], 1) if hist else 0.0,
            "sample_segments": hist["segments"] if hist else 0,
            "fallback_used": fallback,
        }

    # Enough history → trust it outright.
    if hist and (hist["distance_km"] >= min_km or hist["segments"] >= min_segs):
        return _result(hist["avg"], "historical", 0.9, False)

    # Some history → blend with official (or global default) data.
    if hist and hist["distance_km"] >= 50:
        base = official if official is not None else global_default
        denom = max(min_km - 50.0, 1.0)
        w = max(0.0, min(1.0, (hist["distance_km"] - 50.0) / denom))
        value = w * hist["avg"] + (1 - w) * base
        return _result(value, "blended_historical_official", 0.5 + 0.3 * w,
                       official is None)

    # No usable history → official data, then global default.
    if official is not None:
        return _result(official, "official", 0.6, False)
    return _result(global_default, "global_default", 0.3, True)


# ── Snapshot navigation ──────────────────────────────────────────────────────

def _find_meaningful_previous(cur, vehicle_id: str, new_snap_id: int):
    """Return the last *meaningful* snapshot before new_snap_id.

    HA can report stale (identical) values repeatedly. Walk back over a contiguous
    run of snapshots sharing the same soc & odometer so the trip window starts
    where the values last actually changed, not at a stale duplicate.
    """
    prev = cur.execute(
        "SELECT id,ts,soc,odometer_km,location_status FROM vehicle_snapshots "
        "WHERE vehicle_id=? AND id<? ORDER BY id DESC LIMIT 1",
        (vehicle_id, new_snap_id),
    ).fetchone()
    if not prev:
        return None
    while True:
        earlier = cur.execute(
            "SELECT id,ts,soc,odometer_km,location_status FROM vehicle_snapshots "
            "WHERE vehicle_id=? AND id<? ORDER BY id DESC LIMIT 1",
            (vehicle_id, prev[0]),
        ).fetchone()
        if not earlier:
            break
        if earlier[2] == prev[2] and earlier[3] == prev[3]:
            prev = earlier   # stale duplicate — keep walking back
        else:
            break
    return prev


# ── Main detection ───────────────────────────────────────────────────────────

def check_for_missing_charge(vehicle_id: str, new_snap_id: int, cfg: dict, con) -> int | None:
    """Compare a new snapshot against the last meaningful one and create a
    candidate if either the SOC-gain or the energy-balance signal fires.

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

    prev = _find_meaningful_previous(cur, vehicle_id, new_snap_id)
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

    soc_gain = new_soc - prev_soc
    battery_kwh = _battery_kwh(cfg)

    driven_km: float | None = None
    if new_odo is not None and prev_odo is not None and new_odo >= prev_odo:
        driven_km = round(new_odo - prev_odo, 1)

    # Common candidate fields; the two detectors below fill in the specifics.
    cand: dict | None = None

    # ── A) SOC-gain detection ─────────────────────────────────────────────────
    min_soc_gain = float(cfg.get("missing_charge_min_soc_gain_percent", 3.0))
    min_gap = float(cfg.get("missing_charge_min_gap_minutes", 30))
    if soc_gain >= min_soc_gain and gap_minutes >= min_gap:
        consumption = _global_default_consumption(cfg)
        battery_delta_kwh = round(soc_gain / 100.0 * battery_kwh, 2) if battery_kwh > 0 else 0.0
        driving_kwh = round(driven_km * consumption / 100.0, 2) if driven_km else 0.0
        estimated_kwh = round(battery_delta_kwh + driving_kwh, 2)
        if estimated_kwh >= float(cfg.get("missing_charge_min_kwh", 2.0)):
            confidence = 50
            if soc_gain >= 10:
                confidence += 20
            elif soc_gain >= 5:
                confidence += 10
            if battery_kwh > 0:
                confidence += 10
            if driven_km is not None:
                confidence += 5
            reason_parts = [f"SOC {prev_soc:.0f}% → {new_soc:.0f}% (+{soc_gain:.0f}%)"]
            if driven_km:
                reason_parts.append(f"{driven_km:.0f} km gefahren")
            reason_parts.append(f"Offline {gap_minutes:.0f} min")
            cand = {
                "candidate_type": "soc_gain",
                "estimated_kwh": estimated_kwh,
                "estimated_consumption_kwh": driving_kwh,
                "estimated_battery_delta_kwh": battery_delta_kwh,
                "expected_consumption_kwh_per_100km": None,
                "observed_consumption_kwh_per_100km": None,
                "expected_energy_kwh": None,
                "observed_energy_kwh": None,
                "estimated_missing_soc_percent": None,
                "expected_consumption_source": None,
                "expected_consumption_confidence": None,
                "historical_sample_distance_km": None,
                "historical_sample_segments": None,
                "base_confidence": confidence,
                "reason": ", ".join(reason_parts),
            }

    # ── B) Energy-balance detection ───────────────────────────────────────────
    if (cand is None
            and cfg.get("missing_charge_energy_balance_enabled", True)
            and battery_kwh > 0
            and driven_km is not None
            and prev_soc > new_soc):
        distance_km = driven_km
        min_distance = float(cfg.get("missing_charge_min_distance_km", 30))
        observed_soc_drop = prev_soc - new_soc
        observed_energy_kwh = round(battery_kwh * observed_soc_drop / 100.0, 2)
        observed_consumption = (observed_energy_kwh / distance_km * 100.0
                                if distance_km > 0 else 0.0)

        exp = get_expected_consumption(vehicle_id, cfg, con, before_id=prev_id)
        expected_consumption = exp["value"]
        # Clamp expected into a plausible band to defend against misconfiguration.
        min_pl = float(cfg.get("missing_charge_min_plausible_consumption_kwh_per_100km") or 0)
        max_pl = cfg.get("missing_charge_max_plausible_consumption_kwh_per_100km")
        if min_pl:
            expected_consumption = max(expected_consumption, min_pl)
        if max_pl:
            expected_consumption = min(expected_consumption, float(max_pl))

        expected_energy_kwh = round(distance_km * expected_consumption / 100.0, 2)
        missing_energy_kwh = round(expected_energy_kwh - observed_energy_kwh, 2)
        missing_soc_percent = round(missing_energy_kwh / battery_kwh * 100.0, 1)

        deviation_pct = (
            (expected_consumption - observed_consumption) / expected_consumption * 100.0
            if expected_consumption > 0 else 0.0
        )
        min_deviation = float(cfg.get("missing_charge_energy_balance_min_deviation_percent", 25))
        min_missing_kwh = float(cfg.get("missing_charge_min_missing_kwh", 4))
        min_missing_soc = float(cfg.get("missing_charge_min_missing_soc_percent", 5))

        if (distance_km >= min_distance
                and deviation_pct >= min_deviation
                and missing_energy_kwh >= min_missing_kwh
                and missing_soc_percent >= min_missing_soc):
            confidence = 45
            if missing_soc_percent >= 15:
                confidence += 20
            elif missing_soc_percent >= 8:
                confidence += 10
            if distance_km >= 100:
                confidence += 10
            confidence += int(round(exp["confidence"] * 15))
            cand = {
                "candidate_type": "energy_balance",
                "estimated_kwh": missing_energy_kwh,
                "estimated_consumption_kwh": None,
                "estimated_battery_delta_kwh": None,
                "expected_consumption_kwh_per_100km": round(expected_consumption, 2),
                "observed_consumption_kwh_per_100km": round(observed_consumption, 2),
                "expected_energy_kwh": expected_energy_kwh,
                "observed_energy_kwh": observed_energy_kwh,
                "estimated_missing_soc_percent": missing_soc_percent,
                "expected_consumption_source": exp["source"],
                "expected_consumption_confidence": exp["confidence"],
                "historical_sample_distance_km": exp["sample_distance_km"],
                "historical_sample_segments": exp["sample_segments"],
                "base_confidence": confidence,
                "reason": (f"{distance_km:.0f} km gefahren, SOC "
                           f"{prev_soc:.0f}% → {new_soc:.0f}%; Verbrauch nur "
                           f"{observed_consumption:.1f} statt {expected_consumption:.1f} "
                           f"kWh/100km → ca. {missing_energy_kwh:.1f} kWh fehlen"),
            }

    if cand is None:
        return None

    estimated_kwh = cand["estimated_kwh"]

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
           AND status IN ('open','accepted','in_review')""",
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
    if "home" in (prev_loc, new_loc):
        suggested_location = "home"
    elif "extern" in (prev_loc, new_loc):
        suggested_location = "extern"
    else:
        suggested_location = "unknown"
    # An energy-balance charge happens mid-trip → almost certainly external.
    if cand["candidate_type"] == "energy_balance" and suggested_location == "unknown":
        suggested_location = "extern"

    # ── Charger type suggestion ───────────────────────────────────────────────
    gap_hours = gap_minutes / 60.0
    avg_power_kw = round(estimated_kwh / gap_hours, 2) if gap_hours > 0 else None
    if not avg_power_kw or avg_power_kw <= 0:
        suggested_charger_type = "unknown"
    elif avg_power_kw > 22:
        suggested_charger_type = "dc"
    else:
        suggested_charger_type = "ac"
    if gap_hours > 8 and avg_power_kw and avg_power_kw < 1.5 and estimated_kwh > 20:
        suggested_charger_type = "unknown"

    confidence = cand["base_confidence"]
    if suggested_location != "unknown":
        confidence += 10
    confidence = max(5, min(confidence, 95))

    # ── Insert candidate ──────────────────────────────────────────────────────
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    cur.execute(
        """INSERT INTO missing_charge_candidates
           (vehicle_id, snapshot_before_id, snapshot_after_id, start_ts, end_ts,
            soc_start, soc_end, odo_start, odo_end, driven_km,
            estimated_kwh, estimated_consumption_kwh, estimated_battery_delta_kwh,
            estimated_avg_power_kw, suggested_charger_type, suggested_location,
            confidence, reason, status, created_at, updated_at,
            candidate_type, expected_consumption_kwh_per_100km,
            observed_consumption_kwh_per_100km, expected_energy_kwh,
            observed_energy_kwh, estimated_missing_soc_percent,
            expected_consumption_source, expected_consumption_confidence,
            historical_sample_distance_km, historical_sample_segments)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            vehicle_id, prev_id, new_snap_id, prev_ts, new_ts,
            prev_soc, new_soc, prev_odo, new_odo, driven_km,
            estimated_kwh, cand["estimated_consumption_kwh"], cand["estimated_battery_delta_kwh"],
            avg_power_kw, suggested_charger_type, suggested_location,
            confidence, cand["reason"], "open", now, now,
            cand["candidate_type"], cand["expected_consumption_kwh_per_100km"],
            cand["observed_consumption_kwh_per_100km"], cand["expected_energy_kwh"],
            cand["observed_energy_kwh"], cand["estimated_missing_soc_percent"],
            cand["expected_consumption_source"], cand["expected_consumption_confidence"],
            cand["historical_sample_distance_km"], cand["historical_sample_segments"],
        ),
    )
    con.commit()
    cand_id = cur.lastrowid

    log.info(
        "[%s] Missing-Charge #%d (%s): %s (%.1f kWh, conf=%d%%)",
        vehicle_id, cand_id, cand["candidate_type"], cand["reason"],
        estimated_kwh, confidence,
    )

    try:
        from core.security import _audit
        _audit(
            "missing_charge_candidate_created",
            f"vehicle_id={vehicle_id} candidate_id={cand_id} "
            f"type={cand['candidate_type']} kwh={estimated_kwh}",
            ip="internal",
        )
    except Exception:
        pass

    try:
        from services.notification_service import notify
        st_label = prev_ts[:16] if prev_ts else "?"
        en_label = new_ts[:16] if new_ts else "?"
        if cand["candidate_type"] == "energy_balance":
            title = "Vermutlicher Zwischenladestopp erkannt"
            message = (f"Fahrt {st_label}–{en_label}: {driven_km:.0f} km bei SOC "
                       f"{prev_soc:.0f}% → {new_soc:.0f}%. Energetisch fehlen ca. "
                       f"{estimated_kwh:.1f} kWh — vermutlich unterwegs geladen.")
        else:
            title = "Möglicher fehlender Ladevorgang erkannt"
            message = (f"Das Fahrzeug war von {st_label} bis {en_label} offline. SOC "
                       f"stieg von {prev_soc:.0f}% auf {new_soc:.0f}%. Geschätzte "
                       f"Ladung: {estimated_kwh:.1f} kWh.")
        notify(
            type="missing_charge_candidate_created",
            severity="warning",
            title=title,
            message=message,
            vehicle_id=vehicle_id,
            data={"candidate_id": cand_id, "vehicle_id": vehicle_id,
                  "candidate_type": cand["candidate_type"],
                  "start_ts": prev_ts, "end_ts": new_ts,
                  "estimated_kwh": estimated_kwh, "soc_start": prev_soc, "soc_end": new_soc},
            dedupe_key=f"missing_charge:{vehicle_id}:{prev_ts}:{new_ts}",
            action_url="/",
        )
    except Exception as _ne:
        log.debug("notify missing_charge error: %s", _ne)

    return cand_id
