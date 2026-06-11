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
import json
import logging
from datetime import datetime, timezone, timedelta


log = logging.getLogger(__name__)

# Plausibility bounds for a single historical driving segment.
_HIST_SEGMENT_MIN_KM = 10.0
_HIST_CONS_MIN = 8.0
_HIST_CONS_MAX = 35.0


def suggest_charger_type(
    location: str | None,
    power_kw: float | None,
    kwh: float | None,
    duration_hours: float | None,
    meter_confirmed: bool,
    cfg: dict,
) -> dict:
    """Suggest charger type from available signals.

    Returns {"type": "ac"|"dc"|"unknown", "source": str, "confidence": int}.
    Priority: home location / meter → peak power_kw → estimated average power → unknown.
    Caller must check manual overrides before calling this function.
    """
    threshold = float(cfg.get("dc_threshold_kw", 22.0))

    # Home location or meter-confirmed home charge → always AC
    if meter_confirmed or location == "home":
        src = "meter_home" if meter_confirmed else "location_home"
        return {"type": "ac", "source": src, "confidence": 88 if meter_confirmed else 75}

    # Real-time or peak power known
    if power_kw and power_kw > 0:
        ctype = "dc" if power_kw >= threshold else "ac"
        margin = abs(power_kw - threshold) / max(threshold, 1.0)
        conf = min(90, 70 + int(margin * 25))
        return {"type": ctype, "source": "power_kw", "confidence": conf}

    # Estimated average power from kWh + duration
    if kwh and kwh > 0 and duration_hours and duration_hours > 0:
        avg = kwh / duration_hours
        if avg > 0.3:
            ctype = "dc" if avg >= threshold else "ac"
            return {"type": ctype, "source": "estimated_power", "confidence": 50}

    return {"type": "unknown", "source": "none", "confidence": 0}


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


# ── Odometer-at-charge suggestion ────────────────────────────────────────────
# Bei einer Ladesession steht das Auto: km_start ≈ km_end. Die Snapshot-Grenzen
# prev_odo/new_odo sind nur Fenster-Grenzen — dazwischen kann gefahren worden
# sein. Diese Logik schlägt EINEN plausiblen Kilometerstand zum Ladezeitpunkt
# vor, mit Quelle und Confidence (exact / high / medium / low / none).
# Bei Unsicherheit lieber none als ein falscher Wert.

_ODO_EXACT_MAX_KM    = 1.0   # Fensterdistanz bis zu der der Wert als exakt gilt
_ODO_HIGH_MAX_KM     = 5.0
_ODO_MEDIUM_MAX_KM   = 30.0
_ODO_ANCHOR_MAX_DAYS = 7     # ältere Anker-Werte werden nicht mehr verwendet


def _odo_none(reason: str) -> dict:
    return {"value": None, "confidence": "none", "source": reason}


def _fmt_age(minutes: float) -> str:
    if minutes < 90:
        return f"{minutes:.0f} min"
    if minutes < 48 * 60:
        return f"{minutes / 60:.0f} h"
    return f"{minutes / 1440:.0f} Tagen"


def _age_confidence(age_minutes: float) -> str:
    if age_minutes <= 120:
        return "high"
    if age_minutes <= 24 * 60:
        return "medium"
    if age_minutes <= _ODO_ANCHOR_MAX_DAYS * 24 * 60:
        return "low"
    return "none"


def _last_known_odo_before(vehicle_id: str, ts: str, con) -> dict | None:
    """Jüngster bekannter Kilometerstand vor ts — aus Snapshots und Sessions.

    Manuell erfasste/korrigierte Sessions gelten als hochwertige Historie.
    Returns {odo, ts, kind, soc} mit kind in snapshot / session / session_manual.
    """
    cur = con.cursor()
    snap = cur.execute(
        "SELECT odometer_km, ts, soc FROM vehicle_snapshots "
        "WHERE vehicle_id=? AND odometer_km IS NOT NULL AND odometer_km>=0 AND ts<=? "
        "ORDER BY ts DESC LIMIT 1",
        (vehicle_id, ts),
    ).fetchone()
    sess = cur.execute(
        "SELECT odo_end, end_ts, created_mode FROM sessions "
        "WHERE vehicle_id=? AND odo_end IS NOT NULL AND odo_end>=0 "
        "AND end_ts IS NOT NULL AND end_ts<=? "
        "ORDER BY end_ts DESC LIMIT 1",
        (vehicle_id, ts),
    ).fetchone()
    best = None
    if snap:
        best = {"odo": float(snap[0]), "ts": snap[1], "kind": "snapshot", "soc": snap[2]}
    if sess and (best is None or sess[1] > best["ts"]):
        kind = "session_manual" if sess[2] == "manual" else "session"
        best = {"odo": float(sess[0]), "ts": sess[1], "kind": kind, "soc": None}
    return best


def suggest_odometer_at_charge(vehicle_id: str, prev_ts: str, new_ts: str,
                               prev_odo, new_odo, prev_soc,
                               candidate_type: str, cfg: dict, con) -> dict:
    """Plausibler Kilometerstand zum Ladezeitpunkt einer erkannten Ladung.

    Prioritäten:
      1. Beide Fenstergrenzen bekannt → Fensterdistanz entscheidet
         (≈0 km → exact, ≤5 km → high, ≤30 km → medium, sonst none).
      2. Letzter bekannter Stand vor Fensterbeginn (Confidence nach Alter).
      3. Stand kurz nach Fensterende (nur wenn plausibel zur Historie).
      4. Frühere Sessions (manuelle Nutzerwerte = hochwertige Historie).
      5. SOC-/Verbrauchsschätzung als schwacher Zuschlag auf einen alten Anker.

    Returns {"value": float|None, "confidence": exact|high|medium|low|none,
             "source": str}.
    """
    if prev_odo is not None and prev_odo < 0:
        prev_odo = None
    if new_odo is not None and new_odo < 0:
        new_odo = None

    if prev_odo is not None and new_odo is not None and new_odo < prev_odo:
        return _odo_none("Kilometerstand-Rücksprung im Offline-Fenster — kein Vorschlag")

    # Zwischenladestopp: Ladung mitten in der Fahrt, Position im Fenster
    # nicht bestimmbar → keine Fantasiewerte.
    if candidate_type == "energy_balance":
        return _odo_none("Zwischenladestopp während der Fahrt — "
                         "Kilometerstand nicht bestimmbar")

    try:
        gap_minutes = (datetime.fromisoformat(new_ts)
                       - datetime.fromisoformat(prev_ts)).total_seconds() / 60.0
    except Exception:
        gap_minutes = None

    # ── Priorität 1: beide Fenstergrenzen bekannt ─────────────────────────────
    if prev_odo is not None and new_odo is not None:
        driven = float(new_odo) - float(prev_odo)
        if driven <= _ODO_EXACT_MAX_KM:
            return {"value": round(float(new_odo), 1), "confidence": "exact",
                    "source": "Kilometerstand im Offline-Fenster unverändert"}
        if driven <= _ODO_HIGH_MAX_KM:
            return {"value": round(float(prev_odo), 1), "confidence": "high",
                    "source": f"Nur {driven:.0f} km im Offline-Fenster — "
                              "Stand vor der Ladung"}
        if driven <= _ODO_MEDIUM_MAX_KM:
            return {"value": round(float(prev_odo), 1), "confidence": "medium",
                    "source": f"{driven:.0f} km im Offline-Fenster gefahren — "
                              "Stand vor der Ladung (unsicher)"}
        return _odo_none(f"{driven:.0f} km im Offline-Fenster gefahren — "
                         "Kilometerstand bei Ladung nicht bestimmbar")

    # ── Priorität 2/4: letzter bekannter Stand vor Fensterbeginn ─────────────
    if prev_odo is not None:
        anchor = {"odo": float(prev_odo), "ts": prev_ts, "kind": "snapshot",
                  "soc": prev_soc}
    else:
        anchor = _last_known_odo_before(vehicle_id, prev_ts, con)

    _kind_label = {
        "snapshot": "Fahrzeug-Snapshot",
        "session": "letzter Session",
        "session_manual": "letzter manueller Session",
    }

    # ── Priorität 3: Stand kurz nach Fensterende ─────────────────────────────
    if new_odo is not None:
        # Rücksprung gegen Historie → Datenfehler, kein Vorschlag
        if anchor is not None and float(new_odo) < anchor["odo"] - 1.0:
            return _odo_none("Kilometerstand-Rücksprung gegenüber Historie — "
                             "kein Vorschlag")
        conf = _age_confidence(gap_minutes) if gap_minutes is not None else "low"
        # Unrealistisch große Sprünge gegenüber der Historie nie als high
        if anchor is not None:
            jump = float(new_odo) - anchor["odo"]
            if jump > 1000 and conf == "high":
                conf = "medium"
            if jump > 5000:
                conf = "low"
        if conf == "none":
            return _odo_none("Kilometerstand nach der Ladung zu weit vom "
                             "Ladezeitpunkt entfernt")
        age_txt = _fmt_age(gap_minutes) if gap_minutes is not None else "?"
        return {"value": round(float(new_odo), 1), "confidence": conf,
                "source": f"Kilometerstand {age_txt} nach Fensterbeginn "
                          "(kein Wert davor bekannt)"}

    # ── Kein Wert nach dem Fenster: Anker vor dem Fenster verwenden ──────────
    if anchor is None:
        return _odo_none("Keine Kilometerstand-Historie vorhanden")

    try:
        ref_ts = datetime.fromisoformat(prev_ts)
        anchor_age_min = (ref_ts - datetime.fromisoformat(anchor["ts"])
                          ).total_seconds() / 60.0
    except Exception:
        anchor_age_min = None
    # Die Ladung liegt irgendwo im Fenster — konservativ das halbe Fenster
    # zum Anker-Alter addieren.
    if anchor_age_min is not None and gap_minutes is not None:
        anchor_age_min += gap_minutes / 2.0
    conf = _age_confidence(anchor_age_min) if anchor_age_min is not None else "low"
    if conf == "none":
        return _odo_none("Letzter bekannter Kilometerstand ist zu alt — "
                         "kein zuverlässiger Vorschlag möglich")

    value = anchor["odo"]
    src = (f"Kilometerstand aus {_kind_label.get(anchor['kind'], anchor['kind'])} "
           f"(vor {_fmt_age(anchor_age_min) if anchor_age_min is not None else '?'})")

    # ── Priorität 5: schwache SOC-/Verbrauchsschätzung auf alten Anker ───────
    # Nur wenn der Anker bereits unsicher ist und seither SOC verfahren wurde.
    if (conf == "low" and anchor["kind"] == "snapshot"
            and anchor.get("soc") is not None and prev_soc is not None
            and anchor["soc"] > prev_soc + 2):
        batt = _battery_kwh(cfg)
        if batt > 0:
            try:
                exp = get_expected_consumption(vehicle_id, cfg, con)
                est_km = (anchor["soc"] - prev_soc) / 100.0 * batt / exp["value"] * 100.0
                value = anchor["odo"] + est_km
                src = (f"Geschätzt aus SOC-/Verbrauchshistorie "
                       f"(+~{est_km:.0f} km seit letztem bekannten Stand)")
            except Exception:
                pass

    return {"value": round(float(value), 1), "confidence": conf, "source": src}


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

    # ── Meter fusion: did a Wallbox/Zähler rise in the same window? ────────────
    evidence: dict = {"signals": [cand["candidate_type"]]}
    if cand["candidate_type"] == "energy_balance":
        evidence["expected_consumption"] = {
            "value": cand["expected_consumption_kwh_per_100km"],
            "observed": cand["observed_consumption_kwh_per_100km"],
            "source": cand["expected_consumption_source"],
            "confidence": cand["expected_consumption_confidence"],
        }
    base_confidence = cand["base_confidence"]
    meter_delta_kwh: float | None = None
    meter_confirmed = 0
    _meter_no_change_reason: str | None = None
    if cfg.get("missing_charge_meter_fusion_enabled", True):
        try:
            from services.meter_snapshot_service import (
                find_meter_delta, meter_type_confidence, is_ev_meter)
            md = find_meter_delta(vehicle_id, prev_ts, new_ts, con)
        except Exception:
            md = None
        if md:
            delta = md["delta_kwh"]
            min_d = float(cfg.get("missing_charge_meter_min_delta_kwh", 1.0))
            max_d = float(cfg.get("missing_charge_meter_max_delta_kwh", 150.0))
            mtype = cfg.get("meter_type", "unknown")
            if min_d <= delta <= max_d:
                mconf = meter_type_confidence(mtype)
                meter_delta_kwh = delta
                evidence["signals"].append("meter_delta")
                evidence["meter"] = {
                    "source": md["source"], "meter_type": mtype,
                    "start_value": md["start_value"], "end_value": md["end_value"],
                    "delta_kwh": delta, "confidence": mconf,
                    "start_ts": md["start_ts"], "end_ts": md["end_ts"],
                }
                if is_ev_meter(mtype):
                    # Strong confirmation: trust the measured energy and home location.
                    meter_confirmed = 1
                    estimated_kwh = delta
                    suggested_location = "home"
                    base_confidence = max(base_confidence, 90)
                else:
                    # House-total / unknown: weak evidence, never force home.
                    base_confidence += int(round(mconf * 10))
            elif delta < min_d:
                # No meaningful rise on a home meter → supports an external stop.
                mconf = meter_type_confidence(mtype)
                evidence["signals"].append("meter_no_change")
                evidence["meter"] = {
                    "source": md["source"], "meter_type": mtype,
                    "delta_kwh": delta, "confidence": 0.0,
                    "start_ts": md["start_ts"], "end_ts": md["end_ts"],
                }
                if suggested_location == "unknown":
                    # Meter available and no home rise → charge was external.
                    suggested_location = "extern"
                    evidence["signals"].append("meter_no_change_external")
                    evidence["meter"]["no_change_inference"] = "extern"
                    # Confidence targets: EV/wallbox 75–85, house_total 60–70, unknown 50–60.
                    # The +10 for known location is applied below, so aim 10 pts lower here.
                    if is_ev_meter(mtype):
                        base_confidence = max(base_confidence, 72)
                    elif mtype == "house_total":
                        base_confidence = max(base_confidence, 57)
                    else:
                        base_confidence = max(base_confidence, 47)
                    _meter_no_change_reason = (
                        f"Home-/Wallbox-Zähler ({mtype}) im Ladezeitraum "
                        f"unverändert (+{delta:.2f} kWh) → extern vermutet"
                    )
                elif suggested_location == "extern":
                    # Location source already said extern — meter no-change confirms it.
                    evidence["signals"].append("meter_no_change_external")
                    evidence["meter"]["no_change_inference"] = "extern_confirmed"
                    base_confidence += int(round(mconf * 10))
                    _meter_no_change_reason = (
                        f"Home-/Wallbox-Zähler ({mtype}) bestätigt extern "
                        f"(unverändert, +{delta:.2f} kWh)"
                    )
                elif suggested_location == "home":
                    # Conflict: location source says home but meter didn't change.
                    evidence["signals"].append("meter_no_change_home_conflict")
                    evidence["meter"]["no_change_inference"] = "home_conflict"
                    _meter_no_change_reason = (
                        f"Standortquelle sagt home, aber Home-/Wallbox-Zähler "
                        f"({mtype}) im Ladezeitraum unverändert (+{delta:.2f} kWh)"
                    )

    # ── Charger type suggestion ───────────────────────────────────────────────
    gap_hours = gap_minutes / 60.0
    avg_power_kw = round(estimated_kwh / gap_hours, 2) if gap_hours > 0 else None
    _sct = suggest_charger_type(
        location=suggested_location,
        power_kw=avg_power_kw,
        kwh=estimated_kwh,
        duration_hours=gap_hours,
        meter_confirmed=meter_confirmed,
        cfg=cfg,
    )
    suggested_charger_type = _sct["type"]
    suggested_charger_type_source = _sct["source"]
    suggested_charger_type_confidence = _sct["confidence"]
    # Long gap + implausibly low avg power + large kWh: estimate is unreliable
    if (gap_hours > 8 and avg_power_kw is not None
            and avg_power_kw < 1.5 and estimated_kwh > 20):
        suggested_charger_type = "unknown"
        suggested_charger_type_source = "none"
        suggested_charger_type_confidence = 0

    confidence = base_confidence
    if suggested_location != "unknown":
        confidence += 10
    confidence = max(5, min(confidence, 95))

    reason = cand["reason"]
    if meter_confirmed and meter_delta_kwh is not None:
        reason = f"{reason} · Zähler bestätigt +{meter_delta_kwh:.1f} kWh"
    elif _meter_no_change_reason:
        reason = f"{reason} · {_meter_no_change_reason}"
    evidence_json = json.dumps(evidence, ensure_ascii=False)

    # ── Plausibler Kilometerstand zum Ladezeitpunkt ───────────────────────────
    # odo_start/odo_end bleiben die rohen Fenster-Grenzen (Anzeige); für das
    # Formular wird EIN plausibler Wert mit Quelle/Confidence vorgeschlagen.
    try:
        odo_sug = suggest_odometer_at_charge(
            vehicle_id, prev_ts, new_ts, prev_odo, new_odo, prev_soc,
            cand["candidate_type"], cfg, con)
    except Exception as _os_err:
        log.warning("[%s] Odometer-Vorschlag fehlgeschlagen: %s", vehicle_id, _os_err)
        odo_sug = _odo_none("Vorschlag fehlgeschlagen")

    # ── Insert candidate ──────────────────────────────────────────────────────
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    cur.execute(
        """INSERT INTO missing_charge_candidates
           (vehicle_id, snapshot_before_id, snapshot_after_id, start_ts, end_ts,
            soc_start, soc_end, odo_start, odo_end, driven_km,
            estimated_kwh, estimated_consumption_kwh, estimated_battery_delta_kwh,
            estimated_avg_power_kw, suggested_charger_type, suggested_charger_type_source,
            suggested_charger_type_confidence, suggested_location,
            confidence, reason, status, created_at, updated_at,
            candidate_type, expected_consumption_kwh_per_100km,
            observed_consumption_kwh_per_100km, expected_energy_kwh,
            observed_energy_kwh, estimated_missing_soc_percent,
            expected_consumption_source, expected_consumption_confidence,
            historical_sample_distance_km, historical_sample_segments,
            evidence_json, meter_delta_kwh, meter_confirmed,
            suggested_odometer_km, suggested_odometer_confidence,
            suggested_odometer_source)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            vehicle_id, prev_id, new_snap_id, prev_ts, new_ts,
            prev_soc, new_soc, prev_odo, new_odo, driven_km,
            estimated_kwh, cand["estimated_consumption_kwh"], cand["estimated_battery_delta_kwh"],
            avg_power_kw, suggested_charger_type, suggested_charger_type_source,
            suggested_charger_type_confidence, suggested_location,
            confidence, reason, "open", now, now,
            cand["candidate_type"], cand["expected_consumption_kwh_per_100km"],
            cand["observed_consumption_kwh_per_100km"], cand["expected_energy_kwh"],
            cand["observed_energy_kwh"], cand["estimated_missing_soc_percent"],
            cand["expected_consumption_source"], cand["expected_consumption_confidence"],
            cand["historical_sample_distance_km"], cand["historical_sample_segments"],
            evidence_json, meter_delta_kwh, meter_confirmed,
            odo_sug["value"], odo_sug["confidence"], odo_sug["source"],
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
        if meter_confirmed and meter_delta_kwh is not None:
            message += f" Per Zähler bestätigt (+{meter_delta_kwh:.1f} kWh)."
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
