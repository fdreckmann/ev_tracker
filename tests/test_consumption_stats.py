"""
Tests für die granulare Verbrauchsstatistik (/api/stats/consumption).

Netzverbrauch: geladene kWh einer Ladung werden der Strecke seit der
vorigen Ladung zugeordnet (Auto steht beim Laden, odo_start ≈ odo_end).
"""
import json
from datetime import datetime, timedelta

import pytest


def _ts(days_ago: float, hour: int = 12) -> str:
    d = datetime.now() - timedelta(days=days_ago)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def _ins_session(con, vid, start_ts, kwh, odo, *, end_ts=None, odo_start=None,
                 location="home", charger_type="ac", candidate_id=None):
    cur = con.execute(
        "INSERT INTO sessions (vehicle_id, start_ts, end_ts, kwh_charged, "
        "odo_start, odo_end, location, charger_type, missing_charge_candidate_id) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (vid, start_ts, end_ts or start_ts, kwh,
         odo_start if odo_start is not None else odo, odo,
         location, charger_type, candidate_id),
    )
    con.commit()
    return cur.lastrowid


def _stats(authed_client, vid="v0"):
    rv = authed_client.get(f"/api/stats/consumption?vehicle_id={vid}")
    assert rv.status_code == 200, rv.get_data(as_text=True)
    return rv.get_json()


def _rec_for(stats, sid):
    return next((r for r in stats["per_session"] if r["session_id"] == sid), None)


# ── Test A: Normalfall ───────────────────────────────────────────────────────

def test_normal_consumption_per_segment(authed_client, app):
    """40 kWh auf 200 km seit voriger Ladung → 20.0 kWh/100km."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        _ins_session(con, "v0", _ts(10), 30.0, 10000)          # Baseline
        sid = _ins_session(con, "v0", _ts(5), 40.0, 10200)     # 200 km später
        close_db_if_owned(con)
    d = _stats(authed_client)
    rec = _rec_for(d, sid)
    assert rec is not None
    assert rec["consumption_kwh_per_100km"] == 20.0
    assert rec["distance_km"] == 200.0
    assert rec["excluded_reason"] is None
    assert d["valid_count"] == 1
    assert d["label"].startswith("Netzverbrauch")


# ── Test B: Keine Strecke ────────────────────────────────────────────────────

def test_zero_distance_excluded(authed_client, app):
    """Gleicher Kilometerstand → kein Verbrauch, kein Division-durch-0."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        _ins_session(con, "v0", _ts(10), 30.0, 10000)
        sid = _ins_session(con, "v0", _ts(5), 40.0, 10000)
        close_db_if_owned(con)
    d = _stats(authed_client)
    rec = _rec_for(d, sid)
    assert rec["consumption_kwh_per_100km"] is None
    assert rec["excluded_reason"] == "no_distance"
    assert d["valid_count"] == 0
    assert d["excluded_reasons"].get("no_distance") == 1


# ── Test C: Fehlende kWh ─────────────────────────────────────────────────────

def test_missing_kwh_excluded(authed_client, app):
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        _ins_session(con, "v0", _ts(10), 30.0, 10000)
        sid = _ins_session(con, "v0", _ts(5), None, 10200)
        close_db_if_owned(con)
    d = _stats(authed_client)
    rec = _rec_for(d, sid)
    assert rec["consumption_kwh_per_100km"] is None
    assert rec["excluded_reason"] == "no_kwh"
    assert d["valid_count"] == 0


# ── Test D: Ausreißer ────────────────────────────────────────────────────────

def test_implausible_high_excluded_from_average(authed_client, app):
    """80 kWh/100km wird markiert und verfälscht den Durchschnitt nicht."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        _ins_session(con, "v0", _ts(12), 30.0, 10000)
        sid_bad = _ins_session(con, "v0", _ts(9), 40.0, 10050)   # 80 kWh/100km
        sid_ok = _ins_session(con, "v0", _ts(5), 20.0, 10150)    # 20 kWh/100km
        close_db_if_owned(con)
    d = _stats(authed_client)
    assert _rec_for(d, sid_bad)["excluded_reason"] == "implausible_high"
    assert _rec_for(d, sid_ok)["consumption_kwh_per_100km"] == 20.0
    assert d["valid_count"] == 1
    assert d["rolling_average_3"] == 20.0  # Ausreißer nicht eingerechnet


def test_implausible_low_excluded(authed_client, app):
    """2 kWh auf 100 km = 2 kWh/100km → unplausibel niedrig."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        _ins_session(con, "v0", _ts(10), 30.0, 10000)
        sid = _ins_session(con, "v0", _ts(5), 2.0, 10100)
        close_db_if_owned(con)
    d = _stats(authed_client)
    assert _rec_for(d, sid)["excluded_reason"] == "implausible_low"
    assert d["valid_count"] == 0


# ── Test E: Rolling Average (distanz-gewichtet) ──────────────────────────────

def test_rolling_averages(authed_client, app):
    """3 Segmente: 20@200km, 15@100km, 25@100km → gewichtet 80kWh/400km=20.0."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        _ins_session(con, "v0", _ts(20), 10.0, 10000)            # Baseline
        _ins_session(con, "v0", _ts(15), 40.0, 10200)            # 20.0
        _ins_session(con, "v0", _ts(10), 15.0, 10300)            # 15.0
        _ins_session(con, "v0", _ts(5), 25.0, 10400)             # 25.0
        close_db_if_owned(con)
    d = _stats(authed_client)
    assert d["valid_count"] == 3
    assert d["rolling_average_3"] == 20.0       # (40+15+25)/(200+100+100)*100
    assert d["rolling_average_5"] == 20.0
    assert d["rolling_average_10"] == 20.0
    assert d["average_30_days"] == 20.0
    assert d["average_90_days"] == 20.0


def test_trend_vs_previous_30_days(authed_client, app):
    """Aktuelle 30 Tage 22.0 vs. vorherige 30 Tage 20.0 → +10 %."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        _ins_session(con, "v0", _ts(55), 10.0, 9000)             # Baseline
        _ins_session(con, "v0", _ts(45), 20.0, 9100)             # alt: 20.0
        _ins_session(con, "v0", _ts(10), 22.0, 9200)             # neu: 22.0
        close_db_if_owned(con)
    d = _stats(authed_client)
    assert d["average_30_days"] == 22.0
    assert d["previous_30_days_average"] == 20.0
    assert d["trend_30_days_pct"] == 10.0


# ── Test F: Multi-Fahrzeug ───────────────────────────────────────────────────

def test_multi_vehicle_isolation(authed_client, app):
    """Fahrzeug A und B werden getrennt berechnet — keine Vermischung."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        _ins_session(con, "v0", _ts(10), 30.0, 10000)
        _ins_session(con, "v0", _ts(5), 40.0, 10200)             # v0: 20.0
        _ins_session(con, "vB", _ts(10), 30.0, 50000)
        _ins_session(con, "vB", _ts(5), 30.0, 50100)             # vB: 30.0
        close_db_if_owned(con)
    da = _stats(authed_client, "v0")
    db = _stats(authed_client, "vB")
    assert da["rolling_average_3"] == 20.0
    assert db["rolling_average_3"] == 30.0
    assert da["valid_count"] == 1 and db["valid_count"] == 1
    # Kein v0-Eintrag mit vB-Distanzen (50000er-Odometer würde 40000 km Sprung erzeugen)
    assert all((r["distance_km"] or 0) < 1000 for r in da["per_session"])


# ── Test G: Geschätzte KM-Werte (Missing-Charge-Confidence) ──────────────────

def _ins_candidate(con, vid, confidence):
    cur = con.execute(
        "INSERT INTO missing_charge_candidates (vehicle_id, status, created_at, "
        "updated_at, suggested_odometer_confidence) VALUES (?,?,?,?,?)",
        (vid, "accepted", _ts(5), _ts(5), confidence),
    )
    con.commit()
    return cur.lastrowid


def test_medium_confidence_marked_as_estimated(authed_client, app):
    """medium-Confidence-KM: eingerechnet, aber als geschätzt markiert."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cid = _ins_candidate(con, "v0", "medium")
        _ins_session(con, "v0", _ts(10), 30.0, 10000)
        sid = _ins_session(con, "v0", _ts(5), 40.0, 10200, candidate_id=cid)
        close_db_if_owned(con)
    d = _stats(authed_client)
    rec = _rec_for(d, sid)
    assert rec["consumption_kwh_per_100km"] == 20.0   # eingerechnet
    assert rec["odo_estimated"] is True               # aber markiert
    assert d["valid_count"] == 1


def test_exact_confidence_not_marked(authed_client, app):
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cid = _ins_candidate(con, "v0", "exact")
        _ins_session(con, "v0", _ts(10), 30.0, 10000)
        sid = _ins_session(con, "v0", _ts(5), 40.0, 10200, candidate_id=cid)
        close_db_if_owned(con)
    d = _stats(authed_client)
    assert _rec_for(d, sid)["odo_estimated"] is False


# ── Kette: Session ohne Odometer gibt kWh ans nächste Segment weiter ─────────

def test_missing_odo_session_chains_kwh_to_next_segment(authed_client, app):
    """Ladung ohne KM-Stand: ihre kWh zählen zum nächsten Segment."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        _ins_session(con, "v0", _ts(15), 10.0, 10000)            # Baseline
        sid_mid = _ins_session(con, "v0", _ts(10), 15.0, None,
                               odo_start=None)                   # kein Odometer
        sid_end = _ins_session(con, "v0", _ts(5), 25.0, 10200)
        close_db_if_owned(con)
    d = _stats(authed_client)
    mid = _rec_for(d, sid_mid)
    end = _rec_for(d, sid_end)
    assert mid["excluded_reason"] == "no_odometer"
    assert end["consumption_kwh_per_100km"] == 20.0   # (15+25)/200*100
    assert end["kwh"] == 40.0
    assert end["sessions_in_segment"] == 2


# ── Plausibilität: negative Odometer, Rücksprung ─────────────────────────────

def test_negative_odo_treated_missing(authed_client, app):
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        _ins_session(con, "v0", _ts(10), 30.0, 10000)
        sid = _ins_session(con, "v0", _ts(5), 40.0, -7)
        close_db_if_owned(con)
    d = _stats(authed_client)
    assert _rec_for(d, sid)["excluded_reason"] == "no_odometer"


def test_odo_backwards_no_negative_consumption(authed_client, app):
    """Odometer-Rücksprung → no_distance, niemals negativer Verbrauch."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        _ins_session(con, "v0", _ts(10), 30.0, 10000)
        sid = _ins_session(con, "v0", _ts(5), 40.0, 9500)
        close_db_if_owned(con)
    d = _stats(authed_client)
    rec = _rec_for(d, sid)
    assert rec["excluded_reason"] == "no_distance"
    assert rec["consumption_kwh_per_100km"] is None


# ── Reportable-Filter und Response-Form ──────────────────────────────────────

def test_excluded_from_reports_sessions_ignored(authed_client, app):
    """Sessions mit excluded_from_reports=1 fließen nicht ein."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        _ins_session(con, "v0", _ts(10), 30.0, 10000)
        sid = _ins_session(con, "v0", _ts(5), 40.0, 10200)
        con.execute("UPDATE sessions SET excluded_from_reports=1 WHERE id=?", (sid,))
        con.commit()
        close_db_if_owned(con)
    d = _stats(authed_client)
    assert _rec_for(d, sid) is None
    assert d["valid_count"] == 0


def test_response_contains_all_documented_fields(authed_client):
    d = _stats(authed_client)
    for key in ("per_session", "rolling_average_3", "rolling_average_5",
                "rolling_average_10", "average_30_days", "average_90_days",
                "current_month_average", "previous_month_average",
                "previous_30_days_average", "trend_30_days_pct",
                "valid_count", "excluded_count", "excluded_reasons",
                "vehicle_id", "label"):
        assert key in d, f"Feld '{key}' fehlt in /api/stats/consumption"


def test_dashboard_consumption_modal_present_in_page(authed_client):
    rv = authed_client.get("/")
    html = rv.get_data(as_text=True)
    assert 'id="consumptionModal"' in html
    assert 'id="consumptionTiles"' in html
    assert 'id="consumptionTbl"' in html
    assert "Netzverbrauch" in html
    assert "openConsumptionModal" in html
