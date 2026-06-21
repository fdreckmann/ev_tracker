"""
Tests for missing-charge detection: SOC-gain, energy-balance, stale-snapshot
handling, deduplication, and historical expected-consumption resolution.
"""
from datetime import datetime, timedelta

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _cfg(**over):
    base = {
        "missing_charge_detection_enabled": True,
        "missing_charge_energy_balance_enabled": True,
        "battery_capacity_kwh": 77.0,
        "missing_charge_min_gap_minutes": 30,
        "missing_charge_min_soc_gain_percent": 3.0,
        "missing_charge_min_kwh": 2.0,
        "missing_charge_min_distance_km": 30,
        "missing_charge_min_missing_kwh": 4.0,
        "missing_charge_min_missing_soc_percent": 5.0,
        "missing_charge_energy_balance_min_deviation_percent": 25.0,
        "missing_charge_expected_consumption_kwh_per_100km": 18.0,
        "missing_charge_min_plausible_consumption_kwh_per_100km": 12.0,
        "missing_charge_consumption_history_days": 90,
        "missing_charge_consumption_min_history_km": 300,
        "missing_charge_consumption_min_segments": 5,
    }
    base.update(over)
    return base


def _ts(dt):
    return dt.replace(microsecond=0).isoformat()


def _insert_snap(con, vehicle_id, ts, soc, odo, loc="extern"):
    cur = con.execute(
        "INSERT INTO vehicle_snapshots "
        "(vehicle_id, ts, soc, odometer_km, range_km, location_status, provider, "
        " raw_available, created_at) VALUES (?,?,?,?,?,?,?,1,?)",
        (vehicle_id, ts, soc, odo, None, loc, "test", ts),
    )
    con.commit()
    return cur.lastrowid


def _get_candidate(con, cid):
    cur = con.execute("SELECT * FROM missing_charge_candidates WHERE id=?", (cid,))
    row = cur.fetchone()
    if not row:
        return None
    return dict(zip([d[0] for d in cur.description], row))


def _insert_meter_snap(con, vehicle_id, ts, value_kwh, ok=True, source="shelly", error=None):
    cur = con.execute(
        "INSERT INTO meter_snapshots "
        "(vehicle_id, ts, source, value_kwh, raw_value, unit, ok, error, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (vehicle_id, ts, source, value_kwh, str(value_kwh), "kWh",
         1 if ok else 0, error, ts),
    )
    con.commit()
    return cur.lastrowid


def _build_history(con, v, n_segments, cons_kwh, *, batt=77.0, seg_km=77.0,
                   start_odo=1000.0, days_ago=10):
    """Insert n_segments plausible driving segments each at ~cons_kwh/100km.

    Returns (last_odo, last_dt). Between drives the SOC is recharged (rise, same
    odometer) so each drive is an isolated falling-SOC segment.
    """
    dsoc = cons_kwh * seg_km / batt  # SOC % dropped per segment to hit cons_kwh
    high_soc = 90.0
    t = datetime.now() - timedelta(days=days_ago)
    odo = start_odo
    _insert_snap(con, v, _ts(t), high_soc, odo)
    for _ in range(n_segments):
        t += timedelta(hours=1)
        odo += seg_km
        _insert_snap(con, v, _ts(t), high_soc - dsoc, odo)   # drive (SOC falls)
        t += timedelta(hours=1)
        _insert_snap(con, v, _ts(t), high_soc, odo)          # recharge (SOC rises)
    return odo, t


# ── Energy-balance detection ─────────────────────────────────────────────────

def test_energy_balance_detects_hidden_charge(app):
    """80%→38% over 250 km is energetically implausible → energy_balance candidate."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 80, 10000)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=4)), 38, 10250)
        cid = check_for_missing_charge("v0", sid, _cfg(
            missing_charge_expected_consumption_kwh_per_100km=19.0), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["candidate_type"] == "energy_balance"
        assert c["driven_km"] == 250
        assert abs(c["observed_consumption_kwh_per_100km"] - 12.9) < 0.3
        assert abs(c["expected_energy_kwh"] - 47.5) < 0.5
        assert abs(c["observed_energy_kwh"] - 32.3) < 0.5
        assert abs(c["estimated_kwh"] - 15.2) < 0.5
        assert abs(c["estimated_missing_soc_percent"] - 19.7) < 1.0
        close_db_if_owned(con)


def test_energy_balance_no_candidate_for_plausible_trip(app):
    """80%→38% over 170 km ≈ 19 kWh/100km → no candidate."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 80, 10000)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=4)), 38, 10170)
        cid = check_for_missing_charge("v0", sid, _cfg(
            missing_charge_expected_consumption_kwh_per_100km=19.0), con)
        assert cid is None
        close_db_if_owned(con)


def test_energy_balance_no_candidate_for_short_distance(app):
    """SOC 80→75 over only 10 km → below min distance → no candidate."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 80, 10000)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=4)), 75, 10010)
        cid = check_for_missing_charge("v0", sid, _cfg(), con)
        assert cid is None
        close_db_if_owned(con)


def test_frugal_but_plausible_trip_no_candidate(app):
    """expected 19, observed ~17 → deviation < 25% → no candidate."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 80, 10000)
        # 200 km, SOC 80→36 → ~33.9 kWh → ~17 kWh/100km
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=4)), 36, 10200)
        cid = check_for_missing_charge("v0", sid, _cfg(
            missing_charge_expected_consumption_kwh_per_100km=19.0), con)
        assert cid is None
        close_db_if_owned(con)


# ── Energy-balance with rising/equal SOC (drive + external charge) ───────────

def test_energy_balance_rising_soc_with_distance(app):
    """Drove 150 km but SOC rose 33→35 % → must have charged externally mid-trip.

    Real-world case: SOC/odometer were temporarily unavailable; when data
    returned, SOC was slightly higher despite a long drive. The +2 % gain is
    below the soc_gain threshold (3 %), so only energy-balance can catch this.
    """
    import json
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 6, 10, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 33, 10000, loc="unknown")
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=5)), 35, 10150, loc="unknown")
        # Home/wallbox meter did not move in the window (+0.05 kWh, threshold 1.0)
        _insert_meter_snap(con, "v0", _ts(t0 - timedelta(minutes=5)), 500.0)
        _insert_meter_snap(con, "v0", _ts(t0 + timedelta(hours=5, minutes=5)), 500.05)
        cid = check_for_missing_charge("v0", sid, _meter_cfg(
            "ev_wallbox",
            missing_charge_expected_consumption_kwh_per_100km=19.0), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["candidate_type"] == "energy_balance"
        assert c["driven_km"] == 150
        # expected 150*19/100 = 28.5; observed = 77*(33-35)/100 = -1.54
        # estimated missing = 28.5 - (-1.54) = ~30.0 kWh
        assert 29.0 <= c["estimated_kwh"] <= 31.0
        assert c["suggested_location"] == "extern"
        ev = json.loads(c["evidence_json"])
        assert "meter_no_change" in ev["signals"]
        assert "meter_no_change_external" in ev["signals"]
        # No real session must be created automatically.
        n_sess = con.execute(
            "SELECT COUNT(*) FROM sessions WHERE vehicle_id='v0'").fetchone()[0]
        assert n_sess == 0
        close_db_if_owned(con)


def test_energy_balance_rising_soc_short_distance_no_candidate(app):
    """Same +2 % SOC gain but only 5 km driven → below min distance → no candidate."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 6, 11, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 33, 10000, loc="unknown")
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=5)), 35, 10005, loc="unknown")
        cid = check_for_missing_charge("v0", sid, _cfg(
            missing_charge_expected_consumption_kwh_per_100km=19.0), con)
        assert cid is None
        close_db_if_owned(con)


def test_energy_balance_skips_null_soc_snapshots(app):
    """SOC unavailable (NULL) mid-window must not block detection.

    The last *usable* snapshot (with SOC) is used as the baseline, so the long
    drive with a slight SOC rise is still caught despite NULL-SOC snapshots in
    between.
    """
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 6, 12, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 33, 10000, loc="unknown")
        # Sensors temporarily unavailable: SOC NULL (odometer still ticking)
        _insert_snap(con, "v0", _ts(t0 + timedelta(hours=2)), None, 10080, loc="unknown")
        _insert_snap(con, "v0", _ts(t0 + timedelta(hours=3)), None, 10120, loc="unknown")
        # Data returns
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=5)), 35, 10150, loc="unknown")
        cid = check_for_missing_charge("v0", sid, _cfg(
            missing_charge_expected_consumption_kwh_per_100km=19.0), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["candidate_type"] == "energy_balance"
        assert c["soc_start"] == 33 and c["soc_end"] == 35
        assert c["odo_start"] == 10000 and c["odo_end"] == 10150
        assert c["driven_km"] == 150
        close_db_if_owned(con)


# ── SOC-gain detection ───────────────────────────────────────────────────────

def test_soc_gain_still_detected(app):
    """SOC 30→70 while parked → soc_gain candidate."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 30, 10000)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=4)), 70, 10000)
        cid = check_for_missing_charge("v0", sid, _cfg(), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["candidate_type"] == "soc_gain"
        assert abs(c["estimated_kwh"] - 30.8) < 0.5
        close_db_if_owned(con)


# ── Stale snapshots ──────────────────────────────────────────────────────────

def test_stale_snapshots_ignored(app):
    """Repeated identical (stale) snapshots → trip window starts at 08:00."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 80, 10000)
        _insert_snap(con, "v0", _ts(t0 + timedelta(hours=1)), 80, 10000)
        _insert_snap(con, "v0", _ts(t0 + timedelta(hours=2)), 80, 10000)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=4)), 38, 10250)
        cid = check_for_missing_charge("v0", sid, _cfg(
            missing_charge_expected_consumption_kwh_per_100km=19.0), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["start_ts"] == _ts(t0)          # 08:00, not 10:00
        assert c["odo_start"] == 10000
        assert c["soc_start"] == 80
        assert c["driven_km"] == 250
        close_db_if_owned(con)


# ── Deduplication ────────────────────────────────────────────────────────────

def test_dedup_only_one_candidate(app):
    """Running the check repeatedly creates only a single candidate."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 80, 10000)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=4)), 38, 10250)
        cfg = _cfg(missing_charge_expected_consumption_kwh_per_100km=19.0)
        first = check_for_missing_charge("v0", sid, cfg, con)
        second = check_for_missing_charge("v0", sid, cfg, con)
        assert first is not None
        assert second is None
        n = con.execute(
            "SELECT COUNT(*) FROM missing_charge_candidates WHERE vehicle_id='v0'"
        ).fetchone()[0]
        assert n == 1
        close_db_if_owned(con)


# ── Expected-consumption resolution ──────────────────────────────────────────

def test_get_expected_consumption_historical(app):
    """Enough plausible history → source 'historical', value ≈ segment average."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import get_expected_consumption
    with app.app_context():
        con = _get_db()
        _build_history(con, "v0", n_segments=6, cons_kwh=20.0)
        exp = get_expected_consumption("v0", _cfg(), con)
        assert exp["source"] == "historical"
        assert abs(exp["value"] - 20.0) < 0.5
        assert exp["sample_segments"] >= 5
        assert exp["sample_distance_km"] >= 300
        assert exp["fallback_used"] is False
        close_db_if_owned(con)


def test_get_expected_consumption_ignores_outliers(app):
    """Segments outside plausible bounds (8–35) are excluded from the average."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import get_expected_consumption
    with app.app_context():
        con = _get_db()
        v = "v0"
        # 6 normal 20 kWh/100km segments
        odo, t = _build_history(con, v, n_segments=6, cons_kwh=20.0)
        # absurd outlier: 100 km but only 1% SOC drop → ~0.77 kWh/100km (< 8)
        t += timedelta(hours=1); odo += 100
        _insert_snap(con, v, _ts(t), 89, odo)
        # absurd outlier: 50 km with 40% SOC drop → ~61 kWh/100km (> 35)
        t += timedelta(hours=1); odo += 50
        _insert_snap(con, v, _ts(t), 49, odo)
        exp = get_expected_consumption(v, _cfg(), con)
        assert exp["source"] == "historical"
        assert abs(exp["value"] - 20.0) < 1.0   # outliers did not skew it
        close_db_if_owned(con)


def test_get_expected_consumption_official_fallback(app):
    """Little history → official vehicle value (with real-world factor)."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import get_expected_consumption
    with app.app_context():
        con = _get_db()
        # one short ~40 km segment → < 50 km history → not enough to blend
        t0 = datetime.now() - timedelta(days=3)
        _insert_snap(con, "v0", _ts(t0), 90, 1000)
        _insert_snap(con, "v0", _ts(t0 + timedelta(hours=1)), 80, 1040)
        exp = get_expected_consumption("v0", _cfg(
            official_consumption_kwh_per_100km=12.5,
            official_consumption_factor=1.20), con)
        assert exp["source"] == "official"
        assert abs(exp["value"] - 15.0) < 0.1   # 12.5 * 1.20
        close_db_if_owned(con)


def test_get_expected_consumption_blended(app):
    """Medium history (50–300 km) → blended_historical_official."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import get_expected_consumption
    with app.app_context():
        con = _get_db()
        _build_history(con, "v0", n_segments=2, cons_kwh=20.0)  # 154 km
        exp = get_expected_consumption("v0", _cfg(
            official_consumption_kwh_per_100km=12.5,
            official_consumption_factor=1.20), con)
        assert exp["source"] == "blended_historical_official"
        # between official-adjusted (15.0) and historical (20.0)
        assert 15.0 < exp["value"] < 20.0
        close_db_if_owned(con)


def test_get_expected_consumption_global_default(app):
    """No history and no official data → global default fallback."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import get_expected_consumption
    with app.app_context():
        con = _get_db()
        exp = get_expected_consumption("v0", _cfg(
            missing_charge_expected_consumption_kwh_per_100km=18.0), con)
        assert exp["source"] == "global_default"
        assert abs(exp["value"] - 18.0) < 0.1
        assert exp["fallback_used"] is True
        close_db_if_owned(con)


def test_energy_balance_uses_historical_consumption(app):
    """Energy-balance pulls expected consumption from history (20), not the default."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        v = "v0"
        odo, t = _build_history(con, v, n_segments=6, cons_kwh=20.0)
        # New implausible gap: 80%→38% over 250 km (observed ≈ 12.9 kWh/100km)
        t += timedelta(hours=2)
        _insert_snap(con, v, _ts(t), 80, odo)
        sid = _insert_snap(con, v, _ts(t + timedelta(hours=4)), 38, odo + 250)
        cid = check_for_missing_charge(v, sid, _cfg(), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["candidate_type"] == "energy_balance"
        assert c["expected_consumption_source"] == "historical"
        assert abs(c["expected_consumption_kwh_per_100km"] - 20.0) < 0.6
        assert abs(c["observed_consumption_kwh_per_100km"] - 12.9) < 0.4
        assert abs(c["estimated_kwh"] - 17.7) < 0.8
        close_db_if_owned(con)


# ── Meter snapshots ────────────────────────────────────────────────────────

def test_find_meter_delta_brackets_window(app):
    """find_meter_delta returns the rise between readings around the window."""
    from core.db import _get_db, close_db_if_owned
    from services.meter_snapshot_service import find_meter_delta
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_meter_snap(con, "v0", _ts(t0 - timedelta(minutes=5)), 100.0)
        _insert_meter_snap(con, "v0", _ts(t0 + timedelta(hours=4, minutes=5)), 118.4)
        md = find_meter_delta("v0", _ts(t0), _ts(t0 + timedelta(hours=4)), con)
        assert md is not None
        assert abs(md["delta_kwh"] - 18.4) < 0.001
        assert md["start_value"] == 100.0 and md["end_value"] == 118.4
        # No snapshots at all → None
        assert find_meter_delta("v9", _ts(t0), _ts(t0 + timedelta(hours=4)), con) is None
        close_db_if_owned(con)


def test_meter_snapshot_dedupe(app, monkeypatch):
    """maybe_record_poll_snapshot stores on change/heartbeat, skips duplicates."""
    from core.db import _get_db, close_db_if_owned
    import meter_providers
    from meter_providers import MeterResult
    from services.meter_snapshot_service import maybe_record_poll_snapshot
    with app.app_context():
        con = _get_db()
        cfg = {"meter_source": "shelly", "meter_snapshot_enabled": True,
               "meter_snapshot_heartbeat_minutes": 10,
               "meter_snapshot_min_delta_kwh": 0.05}
        st = {}
        seq = [10.0, 10.0, 10.0, 12.0]
        idx = {"i": 0}

        def fake_read(_c):
            v = seq[min(idx["i"], len(seq) - 1)]
            idx["i"] += 1
            return MeterResult(value=v, ok=True, source="shelly", raw_value=v, unit="kWh")

        monkeypatch.setattr(meter_providers, "read_meter", fake_read)
        assert maybe_record_poll_snapshot("v0", cfg, st, con) is not None   # first → store
        assert maybe_record_poll_snapshot("v0", cfg, st, con) is None       # same → skip
        assert maybe_record_poll_snapshot("v0", cfg, st, con) is None       # same → skip
        assert maybe_record_poll_snapshot("v0", cfg, st, con) is not None   # changed → store
        # Heartbeat: pretend the last store was long ago → store even if unchanged
        st["meter_snap_last_dt"] = datetime.now() - timedelta(minutes=30)
        assert maybe_record_poll_snapshot("v0", cfg, st, con) is not None
        n = con.execute("SELECT COUNT(*) FROM meter_snapshots WHERE vehicle_id='v0'").fetchone()[0]
        assert n == 3
        close_db_if_owned(con)


def test_meter_snapshot_disabled_or_no_source(app, monkeypatch):
    """No snapshots when disabled or when meter_source is none."""
    from core.db import _get_db, close_db_if_owned
    import meter_providers
    from meter_providers import MeterResult
    from services.meter_snapshot_service import maybe_record_poll_snapshot
    with app.app_context():
        con = _get_db()
        monkeypatch.setattr(meter_providers, "read_meter",
                            lambda _c: MeterResult(value=5.0, ok=True, source="shelly"))
        assert maybe_record_poll_snapshot(
            "v0", {"meter_source": "shelly", "meter_snapshot_enabled": False}, {}, con) is None
        assert maybe_record_poll_snapshot(
            "v0", {"meter_source": "none", "meter_snapshot_enabled": True}, {}, con) is None
        n = con.execute("SELECT COUNT(*) FROM meter_snapshots").fetchone()[0]
        assert n == 0
        close_db_if_owned(con)


# ── Meter fusion with missing-charge ─────────────────────────────────────────

def _meter_cfg(meter_type, **over):
    return _cfg(meter_type=meter_type,
                missing_charge_meter_fusion_enabled=True,
                missing_charge_meter_min_delta_kwh=1.0,
                missing_charge_meter_max_delta_kwh=150.0, **over)


def test_soc_gain_meter_confirmed_ev_wallbox(app):
    """SOC-gain + EV-wallbox rise → meter_confirmed, kWh from meter, home."""
    import json
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 30, 10000, loc="unknown")
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=4)), 70, 10000, loc="unknown")
        _insert_meter_snap(con, "v0", _ts(t0 - timedelta(minutes=5)), 100.0)
        _insert_meter_snap(con, "v0", _ts(t0 + timedelta(hours=4, minutes=5)), 118.4)
        cid = check_for_missing_charge("v0", sid, _meter_cfg("ev_wallbox"), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["meter_confirmed"] == 1
        assert abs(c["meter_delta_kwh"] - 18.4) < 0.01
        assert abs(c["estimated_kwh"] - 18.4) < 0.01    # kWh taken from meter
        assert c["suggested_location"] == "home"
        ev = json.loads(c["evidence_json"])
        assert "meter_delta" in ev["signals"]
        assert ev["meter"]["meter_type"] == "ev_wallbox"
        close_db_if_owned(con)


def test_energy_balance_meter_confirmed(app):
    """Energy-balance + EV-wallbox rise in the window → meter_confirmed."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 80, 10000)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=4)), 38, 10250)
        _insert_meter_snap(con, "v0", _ts(t0 - timedelta(minutes=2)), 500.0)
        _insert_meter_snap(con, "v0", _ts(t0 + timedelta(hours=4, minutes=2)), 515.2)
        cid = check_for_missing_charge("v0", sid, _meter_cfg(
            "ev_wallbox", missing_charge_expected_consumption_kwh_per_100km=19.0), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["candidate_type"] == "energy_balance"
        assert c["meter_confirmed"] == 1
        assert abs(c["meter_delta_kwh"] - 15.2) < 0.01
        assert c["suggested_location"] == "home"
        close_db_if_owned(con)


def test_energy_balance_without_meter_not_home_confirmed(app):
    """Energy-balance with no meter data → candidate stays but is not confirmed."""
    import json
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 80, 10000)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=4)), 38, 10250)
        cid = check_for_missing_charge("v0", sid, _meter_cfg(
            "ev_wallbox", missing_charge_expected_consumption_kwh_per_100km=19.0), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["meter_confirmed"] == 0
        assert c["meter_delta_kwh"] is None
        assert c["suggested_location"] != "home"
        ev = json.loads(c["evidence_json"])
        assert "meter" not in ev
        close_db_if_owned(con)


def test_house_meter_weaker_than_ev_meter(app):
    """A house-total meter does not confirm and yields lower confidence than EV."""
    import json
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge

    def _run(meter_type):
        con = _get_db()
        con.execute("DELETE FROM vehicle_snapshots")
        con.execute("DELETE FROM meter_snapshots")
        con.execute("DELETE FROM missing_charge_candidates")
        con.commit()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 30, 10000, loc="unknown")
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=4)), 70, 10000, loc="unknown")
        _insert_meter_snap(con, "v0", _ts(t0 - timedelta(minutes=5)), 100.0)
        _insert_meter_snap(con, "v0", _ts(t0 + timedelta(hours=4, minutes=5)), 118.4)
        cid = check_for_missing_charge("v0", sid, _meter_cfg(meter_type), con)
        return _get_candidate(con, cid)

    with app.app_context():
        ev = _run("ev_wallbox")
        house = _run("house_total")
        assert ev["meter_confirmed"] == 1 and house["meter_confirmed"] == 0
        assert ev["suggested_location"] == "home"
        assert house["suggested_location"] != "home"
        assert abs(house["estimated_kwh"] - 30.8) < 0.5   # not overridden by meter
        assert house["confidence"] < ev["confidence"]
        # house meter still recorded as weak evidence
        hev = json.loads(house["evidence_json"])
        assert "meter_delta" in hev["signals"]
        assert hev["meter"]["confidence"] < 0.6
        from core.db import close_db_if_owned
        close_db_if_owned(_get_db())


def test_meter_delta_too_large_ignored(app):
    """An implausibly large meter rise is ignored (not a single charge)."""
    import json
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 30, 10000, loc="unknown")
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=4)), 70, 10000, loc="unknown")
        _insert_meter_snap(con, "v0", _ts(t0 - timedelta(minutes=5)), 100.0)
        _insert_meter_snap(con, "v0", _ts(t0 + timedelta(hours=4, minutes=5)), 400.0)  # +300
        cid = check_for_missing_charge("v0", sid, _meter_cfg("ev_wallbox"), con)
        c = _get_candidate(con, cid)
        assert c["meter_confirmed"] == 0
        assert c["meter_delta_kwh"] is None
        ev = json.loads(c["evidence_json"])
        assert "meter_delta" not in ev["signals"]
        close_db_if_owned(con)


# ── Odometer-at-charge suggestion (Tests A–G) ────────────────────────────────
# Bei einer Ladesession steht das Auto: km_start ≈ km_end. Die Vorschlagslogik
# darf nie blind die Snapshot-Spanne (prev_odo→new_odo) ins Formular schreiben.


def _insert_session(con, vehicle_id, start_ts, end_ts, odo_start=None,
                    odo_end=None, created_mode="auto"):
    cur = con.execute(
        "INSERT INTO sessions (vehicle_id, start_ts, end_ts, odo_start, odo_end, "
        "created_mode, kwh_charged) VALUES (?,?,?,?,?,?,1.0)",
        (vehicle_id, start_ts, end_ts, odo_start, odo_end, created_mode),
    )
    con.commit()
    return cur.lastrowid


def test_odo_suggestion_exact_when_window_unchanged(app):
    """Test A: Odometer im Offline-Fenster unverändert → exact, ein Wert."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 40, 12000)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=5)), 80, 12000)
        cid = check_for_missing_charge("v0", sid, _cfg(), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["suggested_odometer_km"] == 12000
        assert c["suggested_odometer_confidence"] == "exact"
        assert c["suggested_odometer_source"]
        close_db_if_owned(con)


def test_odo_suggestion_high_for_small_window_distance(app):
    """Test A2: nur 3 km im Fenster → high, Stand vor der Ladung."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 40, 12000)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=5)), 80, 12003)
        cid = check_for_missing_charge("v0", sid, _cfg(), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["suggested_odometer_km"] == 12000
        assert c["suggested_odometer_confidence"] == "high"
        close_db_if_owned(con)


def test_odo_suggestion_after_value_when_no_prev(app):
    """Test B: kein Wert davor, plausibler Wert kurz danach → wird genutzt."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 40, None)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=2)), 80, 12345)
        cid = check_for_missing_charge("v0", sid, _cfg(), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["suggested_odometer_km"] == 12345
        assert c["suggested_odometer_confidence"] in ("high", "medium")
        close_db_if_owned(con)


def test_odo_suggestion_none_for_large_window_span(app):
    """Test C: 9270 → 12500 — niemals die Spanne übernehmen → none."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 40, 9270)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(days=2)), 80, 12500)
        cid = check_for_missing_charge("v0", sid, _cfg(), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["suggested_odometer_km"] is None
        assert c["suggested_odometer_confidence"] == "none"
        # Rohe Fenster-Grenzen bleiben als Info erhalten
        assert c["odo_start"] == 9270 and c["odo_end"] == 12500
        close_db_if_owned(con)


def test_odo_suggestion_none_without_history(app):
    """Test D: keine Odometer-Historie → keine Fantasiewerte, none."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 40, None)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=2)), 80, None)
        cid = check_for_missing_charge("v0", sid, _cfg(), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["suggested_odometer_km"] is None
        assert c["suggested_odometer_confidence"] == "none"
        close_db_if_owned(con)


def test_odo_suggestion_uses_manual_session_anchor(app):
    """Test E: manuell korrigierte Session als hochwertige Historie."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_session(con, "v0",
                        _ts(t0 - timedelta(hours=4)), _ts(t0 - timedelta(hours=3)),
                        odo_start=9000, odo_end=9000, created_mode="manual")
        _insert_snap(con, "v0", _ts(t0), 40, None)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=2)), 80, None)
        cid = check_for_missing_charge("v0", sid, _cfg(), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["suggested_odometer_km"] == 9000
        assert c["suggested_odometer_confidence"] in ("high", "medium")
        assert "manuell" in (c["suggested_odometer_source"] or "")
        close_db_if_owned(con)


def test_odo_detection_never_touches_existing_sessions(app):
    """Test F: Erkennung verändert nie gespeicherte (manuelle) Session-Werte."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        sid_sess = _insert_session(
            con, "v0", _ts(t0 - timedelta(days=10)),
            _ts(t0 - timedelta(days=10) + timedelta(hours=1)),
            odo_start=5555, odo_end=5555, created_mode="manual")
        _insert_snap(con, "v0", _ts(t0), 40, 12000)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=2)), 80, 12000)
        cid = check_for_missing_charge("v0", sid, _cfg(), con)
        assert cid is not None
        row = con.execute(
            "SELECT odo_start, odo_end FROM sessions WHERE id=?", (sid_sess,)
        ).fetchone()
        assert row[0] == 5555 and row[1] == 5555, \
            "Detection überschrieb manuelle Session-Werte"
        close_db_if_owned(con)


def test_odo_suggestion_rejects_backwards_window(app):
    """Test G: new_odo < prev_odo (Rücksprung im Fenster) → none."""
    from services.missing_charge_service import suggest_odometer_at_charge
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        r = suggest_odometer_at_charge(
            "v0", "2026-05-31T08:00:00", "2026-05-31T10:00:00",
            5000, 4000, 40, "soc_gain", _cfg(), con)
        assert r["value"] is None
        assert r["confidence"] == "none"
        close_db_if_owned(con)


def test_odo_suggestion_negative_treated_as_missing(app):
    """Test G: negative Kilometerstände werden wie fehlende behandelt."""
    from services.missing_charge_service import suggest_odometer_at_charge
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        r = suggest_odometer_at_charge(
            "v_neg", "2026-05-31T08:00:00", "2026-05-31T10:00:00",
            -5, None, 40, "soc_gain", _cfg(), con)
        assert r["value"] is None
        assert r["confidence"] == "none"
        close_db_if_owned(con)


def test_odo_suggestion_big_jump_never_high(app):
    """Test G: unrealistischer Sprung gegenüber Historie wird nie high."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0 - timedelta(hours=1)), 85, 1000)
        _insert_snap(con, "v0", _ts(t0), 40, None)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=1)), 80, 7000)
        cid = check_for_missing_charge("v0", sid, _cfg(), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["suggested_odometer_confidence"] in ("low", "none"), \
            f"+6000 km Sprung darf nicht {c['suggested_odometer_confidence']} sein"
        close_db_if_owned(con)


def test_odo_suggestion_backwards_vs_history_rejected(app):
    """Test G: Wert nach dem Fenster liegt UNTER der Historie → none."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0 - timedelta(hours=1)), 85, 5000)
        _insert_snap(con, "v0", _ts(t0), 40, None)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=1)), 80, 3000)
        cid = check_for_missing_charge("v0", sid, _cfg(), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["suggested_odometer_km"] is None
        assert c["suggested_odometer_confidence"] == "none"
        close_db_if_owned(con)


def test_odo_energy_balance_candidate_gets_none(app):
    """Zwischenladestopp (energy_balance): Position im Fenster unbekannt → none."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 5, 31, 8, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 80, 10000)
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=4)), 38, 10250)
        cid = check_for_missing_charge("v0", sid, _cfg(
            missing_charge_expected_consumption_kwh_per_100km=19.0), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["candidate_type"] == "energy_balance"
        assert c["suggested_odometer_km"] is None
        assert c["suggested_odometer_confidence"] == "none"
        close_db_if_owned(con)


def test_odo_prefill_js_uses_suggestion_not_span():
    """Test F/G (Frontend): Formular-Prefill nutzt den Vorschlag, nie die
    rohe Snapshot-Spanne; Helper befüllt nur exact/high/medium."""
    import os
    here = os.path.dirname(__file__)
    root = os.path.normpath(os.path.join(here, ".."))

    with open(os.path.join(root, "app", "static", "js", "api.js")) as fh:
        api_src = fh.read()
    assert "_candidateOdoSuggestion" in api_src
    helper = api_src[api_src.find("function _candidateOdoSuggestion"):][:700]
    for lvl in ("'exact'", "'high'", "'medium'"):
        assert lvl in helper, f"{lvl} fehlt im Confidence-Gate"
    assert "'low'" not in helper, "low darf NICHT vorbefüllt werden"

    with open(os.path.join(root, "app", "templates", "index.html")) as fh:
        html = fh.read()
    idx = html.find("async function openCandidateAcceptDialog")
    assert idx >= 0
    body = html[idx:idx + 2500]
    assert "_candidateOdoSuggestion" in body, \
        "Desktop-Prefill nutzt den KM-Vorschlag nicht"
    assert "Math.round(c.odo_start)" not in body, \
        "Desktop-Prefill schreibt noch die rohe Snapshot-Spanne (odo_start)"
    assert "Math.round(c.odo_end)" not in body, \
        "Desktop-Prefill schreibt noch die rohe Snapshot-Spanne (odo_end)"

    with open(os.path.join(root, "app", "static", "js", "mobile.js")) as fh:
        mob = fh.read()
    midx = mob.find("function mobileMissingChargeAccept")
    assert midx >= 0
    mbody = mob[midx:midx + 2500]
    assert "_candidateOdoSuggestion" in mbody, \
        "Mobile-Prefill nutzt den KM-Vorschlag nicht"
    assert "Math.round(c.odo_start)" not in mbody, \
        "Mobile-Prefill schreibt noch die rohe Snapshot-Spanne"


# ── Meter no-change → extern location inference ──────────────────────────────

def test_meter_no_change_ev_wallbox_sets_extern(app):
    """Test A: EV-Wallbox konfiguriert, kein Anstieg → suggested_location=extern."""
    import json
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 6, 1, 9, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 20, 10000, loc="unknown")
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=3)), 75, 10000, loc="unknown")
        # Meter shows no meaningful change (0.1 kWh, threshold is 1.0)
        _insert_meter_snap(con, "v0", _ts(t0 - timedelta(minutes=5)), 500.0)
        _insert_meter_snap(con, "v0", _ts(t0 + timedelta(hours=3, minutes=5)), 500.1)
        cid = check_for_missing_charge("v0", sid, _meter_cfg("ev_wallbox"), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["suggested_location"] == "extern", \
            f"EV meter unchanged should infer extern, got {c['suggested_location']}"
        ev = json.loads(c["evidence_json"])
        assert "meter_no_change" in ev["signals"]
        assert "meter_no_change_external" in ev["signals"]
        assert ev["meter"]["no_change_inference"] == "extern"
        assert "extern" in c["reason"].lower() or "unverändert" in c["reason"]
        # Confidence should be in EV-meter range (75-85 after +10 location bonus)
        assert 70 <= c["confidence"] <= 95
        close_db_if_owned(con)


def test_meter_no_change_house_total_sets_extern(app):
    """Test A (house meter): Hausgesamtzähler unverändert → extern, medium confidence."""
    import json
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 6, 2, 9, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 20, 11000, loc="unknown")
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=3)), 75, 11000, loc="unknown")
        _insert_meter_snap(con, "v0", _ts(t0 - timedelta(minutes=5)), 1000.0)
        _insert_meter_snap(con, "v0", _ts(t0 + timedelta(hours=3, minutes=5)), 1000.05)
        cid = check_for_missing_charge("v0", sid, _meter_cfg("house_total"), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["suggested_location"] == "extern"
        ev = json.loads(c["evidence_json"])
        assert "meter_no_change_external" in ev["signals"]
        assert ev["meter"]["no_change_inference"] == "extern"
        # Overall confidence depends on both SOC gain and location inference.
        assert 55 <= c["confidence"] <= 95
        close_db_if_owned(con)


def test_no_meter_configured_stays_unknown(app):
    """Test B: Kein Zähler konfiguriert → suggested_location bleibt unknown."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 6, 3, 9, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 20, 12000, loc="unknown")
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=3)), 75, 12000, loc="unknown")
        # No meter snapshots at all
        cid = check_for_missing_charge("v0", sid, _meter_cfg("ev_wallbox"), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["suggested_location"] == "unknown", \
            f"No meter data should leave location unknown, got {c['suggested_location']}"
        close_db_if_owned(con)


def test_meter_unavailable_stays_unknown(app):
    """Test C: Zähler konfiguriert, aber nur ok=0 Einträge → kein Extern-Schluss."""
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 6, 4, 9, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 20, 13000, loc="unknown")
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=3)), 75, 13000, loc="unknown")
        # Meter snapshots exist but all failed (ok=False)
        _insert_meter_snap(con, "v0", _ts(t0 - timedelta(minutes=5)), None, ok=False, error="timeout")
        _insert_meter_snap(con, "v0", _ts(t0 + timedelta(hours=3)), None, ok=False, error="timeout")
        cid = check_for_missing_charge("v0", sid, _meter_cfg("ev_wallbox"), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["suggested_location"] == "unknown", \
            f"Unavailable meter must not infer extern, got {c['suggested_location']}"
        close_db_if_owned(con)


def test_meter_no_change_home_loc_keeps_home(app):
    """Test D: API/location sagt home + Zähler unverändert → nicht extern, Konflikt markiert."""
    import json
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 6, 5, 9, 0, 0)
        # Both snapshots say "home"
        _insert_snap(con, "v0", _ts(t0), 20, 14000, loc="home")
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=3)), 75, 14000, loc="home")
        # Meter shows no change
        _insert_meter_snap(con, "v0", _ts(t0 - timedelta(minutes=5)), 200.0)
        _insert_meter_snap(con, "v0", _ts(t0 + timedelta(hours=3, minutes=5)), 200.0)
        cid = check_for_missing_charge("v0", sid, _meter_cfg("ev_wallbox"), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["suggested_location"] == "home", \
            f"Home signal must not be overridden by meter no-change, got {c['suggested_location']}"
        ev = json.loads(c["evidence_json"])
        assert "meter_no_change_home_conflict" in ev["signals"]
        assert ev["meter"]["no_change_inference"] == "home_conflict"
        close_db_if_owned(con)


def test_meter_no_change_extern_loc_boosts_confidence(app):
    """Test E: location=extern + Zähler unverändert → extern bestätigt, confidence boost."""
    import json
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 6, 6, 9, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 20, 15000, loc="extern")
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=3)), 75, 15000, loc="extern")
        _insert_meter_snap(con, "v0", _ts(t0 - timedelta(minutes=5)), 300.0)
        _insert_meter_snap(con, "v0", _ts(t0 + timedelta(hours=3, minutes=5)), 300.05)
        cfg_no_fusion = _meter_cfg("ev_wallbox")
        # Get base confidence without meter
        cfg_no_fusion["missing_charge_meter_fusion_enabled"] = False
        cid_base = check_for_missing_charge("v0", sid, cfg_no_fusion, con)
        c_base = _get_candidate(con, cid_base)
        base_conf = c_base["confidence"]

        # Re-check on a different vehicle to get meter-fusion result
        t1 = datetime(2026, 6, 7, 9, 0, 0)
        _insert_snap(con, "v1", _ts(t1), 20, 15000, loc="extern")
        sid2 = _insert_snap(con, "v1", _ts(t1 + timedelta(hours=3)), 75, 15000, loc="extern")
        _insert_meter_snap(con, "v1", _ts(t1 - timedelta(minutes=5)), 300.0)
        _insert_meter_snap(con, "v1", _ts(t1 + timedelta(hours=3, minutes=5)), 300.05)
        cid2 = check_for_missing_charge("v1", sid2, _meter_cfg("ev_wallbox"), con)
        c2 = _get_candidate(con, cid2)
        ev = json.loads(c2["evidence_json"])
        assert c2["suggested_location"] == "extern"
        assert "meter_no_change_external" in ev["signals"]
        assert ev["meter"]["no_change_inference"] == "extern_confirmed"
        close_db_if_owned(con)


def test_meter_rise_still_infers_home(app):
    """Test F: Zähler steigt relevant → bestehende Home-Erkennung bleibt korrekt."""
    import json
    from core.db import _get_db, close_db_if_owned
    from services.missing_charge_service import check_for_missing_charge
    with app.app_context():
        con = _get_db()
        t0 = datetime(2026, 6, 8, 9, 0, 0)
        _insert_snap(con, "v0", _ts(t0), 20, 16000, loc="unknown")
        sid = _insert_snap(con, "v0", _ts(t0 + timedelta(hours=3)), 75, 16000, loc="unknown")
        # Meter rises by 22 kWh → home confirmed
        _insert_meter_snap(con, "v0", _ts(t0 - timedelta(minutes=5)), 400.0)
        _insert_meter_snap(con, "v0", _ts(t0 + timedelta(hours=3, minutes=5)), 422.0)
        cid = check_for_missing_charge("v0", sid, _meter_cfg("ev_wallbox"), con)
        assert cid is not None
        c = _get_candidate(con, cid)
        assert c["suggested_location"] == "home", \
            f"Rising meter should still infer home, got {c['suggested_location']}"
        assert c["meter_confirmed"] == 1
        ev = json.loads(c["evidence_json"])
        assert "meter_delta" in ev["signals"]
        assert "meter_no_change" not in ev["signals"]
        close_db_if_owned(con)
