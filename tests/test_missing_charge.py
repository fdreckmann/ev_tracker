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
