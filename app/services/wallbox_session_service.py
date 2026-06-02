"""
Wallbox/meter-based home-charge detection service.

Detects charging sessions from power/energy snapshots without requiring a vehicle
API. Produces wallbox_sessions records that must be explicitly assigned to a
vehicle before they become reportable normal sessions.

Key design invariants:
- A rising meter value means "energy was drawn at the wallbox", NOT "the tracked
  vehicle was charged". No automatic session creation without vehicle confirmation.
- Vehicle API offline/stale must not prevent detection.
- All wallbox_sessions start with excluded_from_reports = 1.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Tracker state helpers
# ---------------------------------------------------------------------------

def _wbs_state_key(source_name: str) -> str:
    return f"wbs_{source_name}"


def get_active_wallbox_session(source_name: str, st: dict) -> Optional[dict]:
    """Return the in-progress wallbox_session state dict, or None."""
    return st.get(_wbs_state_key(source_name))


def set_active_wallbox_session(source_name: str, st: dict, wbs: Optional[dict]) -> None:
    key = _wbs_state_key(source_name)
    if wbs is None:
        st.pop(key, None)
    else:
        st[key] = wbs


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _open_session(con, source_type: str, source_name: str, start_ts: str,
                  meter_start_kwh: Optional[float]) -> int:
    """Insert a new active wallbox_session. Returns the row id."""
    now = _now_iso()
    cur = con.execute(
        """INSERT INTO wallbox_sessions
           (source_type, source_name, start_ts, meter_start_kwh,
            status, vehicle_assignment_status, excluded_from_reports,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (source_type, source_name, start_ts, meter_start_kwh,
         "active", "unassigned", 1, now, now),
    )
    con.commit()
    return cur.lastrowid


def _close_session(con, wbs_id: int, end_ts: str, meter_end_kwh: Optional[float],
                   energy_kwh: Optional[float], peak_power_kw: Optional[float],
                   vehicle_id: Optional[str], assignment_status: str,
                   status: str) -> None:
    now = _now_iso()
    con.execute(
        """UPDATE wallbox_sessions
           SET end_ts=?, meter_end_kwh=?, energy_kwh=?, peak_power_kw=?,
               vehicle_id=?, vehicle_assignment_status=?, status=?,
               updated_at=?
           WHERE id=?""",
        (end_ts, meter_end_kwh, energy_kwh, peak_power_kw,
         vehicle_id, assignment_status, status, now, wbs_id),
    )
    con.commit()


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

def process_power_snapshot(
    vehicle_id: Optional[str],
    source_type: str,
    source_name: str,
    power_kw: Optional[float],
    energy_total_kwh: Optional[float],
    ts: str,
    cfg: dict,
    st: dict,
    con,
) -> Optional[int]:
    """Process one power/energy reading and open/close wallbox_sessions as needed.

    Returns the wallbox_session id if a session was just closed, else None.

    ``st`` is the mutable tracker state dict (in-process only; survives between
    polls in the same process lifetime). ``con`` is an open SQLite connection.
    """
    if not cfg.get("home_charge_detection_enabled", True):
        return None

    start_threshold = float(cfg.get("home_charge_power_start_threshold_kw", 1.0))
    stop_threshold  = float(cfg.get("home_charge_power_stop_threshold_kw", 0.2))
    start_debounce  = float(cfg.get("home_charge_start_debounce_seconds", 120))
    stop_debounce   = float(cfg.get("home_charge_stop_debounce_seconds", 300))
    min_energy_kwh  = float(cfg.get("home_charge_min_energy_kwh", 1.0))

    state = get_active_wallbox_session(source_name, st)

    # Determine if power indicates charging is ongoing
    above_start = power_kw is not None and power_kw >= start_threshold
    below_stop  = power_kw is None or power_kw < stop_threshold

    # Parse current timestamp
    try:
        ts_dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        ts_dt = _now()

    if state is None:
        # No active session — check whether to start one
        if above_start:
            debounce_since = st.get(f"wbs_{source_name}_above_since")
            if debounce_since is None:
                st[f"wbs_{source_name}_above_since"] = ts_dt
                st[f"wbs_{source_name}_peak_kw"] = power_kw
                # Remember energy at start of debounce so meter_start_kwh is accurate
                st[f"wbs_{source_name}_start_energy"] = energy_total_kwh
            else:
                elapsed = (ts_dt - debounce_since).total_seconds()
                peak = max(st.get(f"wbs_{source_name}_peak_kw") or 0.0, power_kw or 0.0)
                st[f"wbs_{source_name}_peak_kw"] = peak
                if elapsed >= start_debounce:
                    # Start a new wallbox_session using energy at debounce start
                    start_ts = debounce_since.isoformat(timespec="seconds")
                    start_energy = st.get(f"wbs_{source_name}_start_energy")
                    wbs_id = _open_session(con, source_type, source_name, start_ts, start_energy)
                    set_active_wallbox_session(source_name, st, {
                        "id": wbs_id,
                        "start_ts": start_ts,
                        "meter_start_kwh": start_energy,
                        "peak_power_kw": peak,
                        "last_above_ts": ts_dt,
                    })
                    st.pop(f"wbs_{source_name}_above_since", None)
                    st.pop(f"wbs_{source_name}_peak_kw", None)
                    st.pop(f"wbs_{source_name}_start_energy", None)
                    log.info("wallbox_session %d opened (source=%s, start=%s)", wbs_id, source_name, start_ts)
        else:
            # Not above threshold — clear debounce state
            st.pop(f"wbs_{source_name}_above_since", None)
            st.pop(f"wbs_{source_name}_peak_kw", None)
            st.pop(f"wbs_{source_name}_start_energy", None)
        return None

    # Active session — update peak, track last-above timestamp
    if not below_stop:
        state["last_above_ts"] = ts_dt
        if power_kw is not None:
            state["peak_power_kw"] = max(state.get("peak_power_kw") or 0.0, power_kw)

    # Check for stop condition
    last_above = state.get("last_above_ts")
    if last_above is not None:
        idle_seconds = (ts_dt - last_above).total_seconds()
    else:
        idle_seconds = 0

    if below_stop and idle_seconds >= stop_debounce:
        # Close the session
        wbs_id = state["id"]
        meter_start = state.get("meter_start_kwh")
        meter_end   = energy_total_kwh
        if meter_start is not None and meter_end is not None:
            energy_kwh = round(meter_end - meter_start, 3)
        else:
            energy_kwh = None

        if energy_kwh is not None and energy_kwh < min_energy_kwh:
            # Too little energy — discard the session
            log.info("wallbox_session %d discarded (energy %.3f kWh < min %.1f)",
                     wbs_id, energy_kwh, min_energy_kwh)
            con.execute("DELETE FROM wallbox_sessions WHERE id=?", (wbs_id,))
            con.commit()
            set_active_wallbox_session(source_name, st, None)
            return None

        # Determine vehicle assignment
        assigned_vid, assign_status = _resolve_vehicle(vehicle_id, cfg)

        _close_session(
            con, wbs_id,
            end_ts=ts,
            meter_end_kwh=meter_end,
            energy_kwh=energy_kwh,
            peak_power_kw=state.get("peak_power_kw"),
            vehicle_id=assigned_vid,
            assignment_status=assign_status,
            status="completed" if assign_status in ("confirmed", "probable") else "unassigned",
        )
        log.info("wallbox_session %d closed (energy=%.3f kWh, status=%s)",
                 wbs_id, energy_kwh or 0.0, assign_status)
        set_active_wallbox_session(source_name, st, None)
        return wbs_id

    return None


def _resolve_vehicle(vehicle_id: Optional[str], cfg: dict) -> tuple[Optional[str], str]:
    """Determine which vehicle to assign and at what confidence.

    Returns (vehicle_id_or_None, assignment_status).
    """
    mode = cfg.get("home_charge_vehicle_assignment_mode", "always_ask_if_unclear")
    allow_probable = bool(cfg.get("home_charge_allow_probable_assignment", False))
    default_vid = cfg.get("home_charge_default_vehicle_id") or None

    if mode == "default_vehicle_always" and default_vid:
        status = "probable" if not allow_probable else "probable"
        return default_vid, status

    if mode == "require_rfid_or_api":
        return None, "unassigned"

    if mode == "always_ask_if_unclear":
        return None, "unassigned"

    if mode == "default_vehicle_if_api_confirms" and vehicle_id:
        return vehicle_id, "probable"

    return None, "unassigned"


# ---------------------------------------------------------------------------
# Detection from cumulative energy (no power signal)
# ---------------------------------------------------------------------------

def process_energy_snapshot(
    vehicle_id: Optional[str],
    source_type: str,
    source_name: str,
    energy_total_kwh: float,
    ts: str,
    cfg: dict,
    st: dict,
    con,
) -> Optional[int]:
    """Open/close wallbox_sessions based on energy counter changes only.

    Used when only a cumulative kWh meter is available (no live power reading).
    Returns closed wallbox_session id or None.
    """
    if not cfg.get("home_charge_detection_enabled", True):
        return None

    min_energy_kwh = float(cfg.get("home_charge_min_energy_kwh", 1.0))
    stop_debounce  = float(cfg.get("home_charge_stop_debounce_seconds", 300))

    key_last_val = f"wbs_energy_{source_name}_last_val"
    key_last_ts  = f"wbs_energy_{source_name}_last_ts"
    key_rising_since = f"wbs_energy_{source_name}_rising_since"

    last_val = st.get(key_last_val)
    try:
        ts_dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        ts_dt = _now()

    if last_val is not None:
        delta = energy_total_kwh - last_val
        if delta > 0.01:
            # Rising energy
            if st.get(key_rising_since) is None:
                st[key_rising_since] = ts_dt
            st[key_last_ts] = ts_dt
        else:
            # Stable or falling — check if a session was rising before
            rising_since = st.get(key_rising_since)
            last_active_ts = st.get(key_last_ts)
            if rising_since and last_active_ts:
                idle = (ts_dt - last_active_ts).total_seconds()
                if idle >= stop_debounce:
                    # Attempt to find an open wallbox_session for this source
                    row = con.execute(
                        "SELECT id, meter_start_kwh FROM wallbox_sessions"
                        " WHERE source_name=? AND status='active'"
                        " ORDER BY id DESC LIMIT 1",
                        (source_name,),
                    ).fetchone()
                    if row:
                        wbs_id = row[0]
                        energy_kwh = round(energy_total_kwh - (row[1] or 0.0), 3)
                        if energy_kwh >= min_energy_kwh:
                            assigned_vid, assign_status = _resolve_vehicle(vehicle_id, cfg)
                            _close_session(
                                con, wbs_id,
                                end_ts=ts,
                                meter_end_kwh=energy_total_kwh,
                                energy_kwh=energy_kwh,
                                peak_power_kw=None,
                                vehicle_id=assigned_vid,
                                assignment_status=assign_status,
                                status="completed" if assign_status in ("confirmed", "probable") else "unassigned",
                            )
                            st.pop(key_rising_since, None)
                            st.pop(key_last_ts, None)
                            return wbs_id
                        else:
                            con.execute("DELETE FROM wallbox_sessions WHERE id=?", (wbs_id,))
                            con.commit()
                    st.pop(key_rising_since, None)
                    st.pop(key_last_ts, None)
    else:
        # First reading — open a session if energy seems active
        wbs_id = _open_session(con, source_type, source_name, ts, energy_total_kwh)
        st[key_rising_since] = ts_dt
        st[key_last_ts] = ts_dt
        set_active_wallbox_session(source_name, st, {"id": wbs_id,
                                                      "start_ts": ts,
                                                      "meter_start_kwh": energy_total_kwh,
                                                      "last_above_ts": ts_dt,
                                                      "peak_power_kw": None})

    st[key_last_val] = energy_total_kwh
    st[key_last_ts] = ts_dt
    return None


# ---------------------------------------------------------------------------
# Public query helpers
# ---------------------------------------------------------------------------

def get_open_wallbox_sessions(con, limit: int = 50) -> list[dict]:
    """Return wallbox_sessions that still need user action."""
    rows = con.execute(
        """SELECT * FROM wallbox_sessions
           WHERE vehicle_assignment_status IN ('unassigned')
             AND status IN ('active','completed','unassigned')
           ORDER BY start_ts DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def assign_wallbox_session(con, wbs_id: int, vehicle_id: str) -> bool:
    """Mark a wallbox_session as confirmed for a vehicle. Returns True on success."""
    now = _now_iso()
    cur = con.execute(
        """UPDATE wallbox_sessions
           SET vehicle_id=?, vehicle_assignment_status='confirmed',
               excluded_from_reports=1, updated_at=?
           WHERE id=?""",
        (vehicle_id, now, wbs_id),
    )
    con.commit()
    return cur.rowcount > 0


def mark_foreign_vehicle(con, wbs_id: int) -> bool:
    now = _now_iso()
    cur = con.execute(
        """UPDATE wallbox_sessions
           SET vehicle_assignment_status='foreign_vehicle', status='foreign_vehicle',
               excluded_from_reports=1, updated_at=?
           WHERE id=?""",
        (now, wbs_id),
    )
    con.commit()
    return cur.rowcount > 0


def ignore_wallbox_session(con, wbs_id: int) -> bool:
    now = _now_iso()
    cur = con.execute(
        """UPDATE wallbox_sessions
           SET vehicle_assignment_status='ignored', status='ignored',
               excluded_from_reports=1, updated_at=?
           WHERE id=?""",
        (now, wbs_id),
    )
    con.commit()
    return cur.rowcount > 0
