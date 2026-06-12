"""
Tests for the missing-charge candidate → manual session accept flow.

Covers:
  A: Accepting a candidate saves the session and sets candidate to accepted
  B: accepted_session_id is set on the candidate
  C: sessions.missing_charge_candidate_id is set on the session
  D: Candidate does not stay in_review after session is saved
  E: Revert route resets in_review → open
  F: Regular closeModal without saving reverts in_review → open (via /revert route)
  G: Saving session without candidate_id does not touch candidates
  H: Odometer suggestion: large snapshot span is not used (low confidence → no prefill)
  I: Odometer suggestion: exact confidence → suggested value used
"""
import json
from datetime import datetime, timezone


def _ts_now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _insert_candidate(con, vehicle_id="v0", status="open",
                      start_ts=None, end_ts=None, estimated_kwh=22.0,
                      soc_start=20.0, soc_end=80.0,
                      suggested_odometer_km=None, suggested_odometer_confidence="none",
                      suggested_odometer_source=None,
                      odo_start=9270.0, odo_end=12500.0):
    now = _ts_now()
    start_ts = start_ts or "2026-01-10T08:00:00"
    end_ts = end_ts or "2026-01-10T10:00:00"
    cur = con.execute(
        """INSERT INTO missing_charge_candidates
           (vehicle_id, status, start_ts, end_ts, estimated_kwh,
            soc_start, soc_end, candidate_type, reason,
            suggested_odometer_km, suggested_odometer_confidence, suggested_odometer_source,
            odo_start, odo_end,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (vehicle_id, status, start_ts, end_ts, estimated_kwh,
         soc_start, soc_end, "soc_gain", "SOC gain detected",
         suggested_odometer_km, suggested_odometer_confidence, suggested_odometer_source,
         odo_start, odo_end,
         now, now),
    )
    con.commit()
    return cur.lastrowid


def _get_candidate(con, cid):
    cur = con.execute("SELECT * FROM missing_charge_candidates WHERE id=?", (cid,))
    row = cur.fetchone()
    return dict(row) if row else None


def _get_session(con, sid):
    cur = con.execute("SELECT * FROM sessions WHERE id=?", (sid,))
    row = cur.fetchone()
    return dict(row) if row else None


def _post_json(client, url, data):
    return client.post(url, data=json.dumps(data), content_type="application/json")


# ─── Test A+B+C+D: Full accept flow ─────────────────────────────────────────

def test_accept_candidate_full_flow(authed_client, app):
    """Accepting a candidate and saving the session links both records correctly."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cid = _insert_candidate(con)
        close_db_if_owned(con)

    # Step 1: mark candidate as in_review
    r = authed_client.post(f"/api/missing-charges/{cid}/accept")
    d = json.loads(r.data)
    assert d["ok"] is True
    assert d["candidate_id"] == cid
    assert "prefill" in d

    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cand = _get_candidate(con, cid)
        assert cand["status"] == "in_review", "Expected in_review after /accept"
        close_db_if_owned(con)

    # Step 2: save manual session including missing_charge_candidate_id
    r2 = _post_json(authed_client, "/api/sessions/manual", {
        "start_ts": "2026-01-10 08:00",
        "end_ts": "2026-01-10 10:00",
        "kwh_charged": 22.0,
        "location": "extern",
        "missing_charge_candidate_id": cid,
    })
    assert r2.status_code == 201
    d2 = json.loads(r2.data)
    assert d2["ok"] is True
    sid = d2["id"]

    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()

        # B: accepted_session_id set on candidate
        cand = _get_candidate(con, cid)
        assert cand["status"] == "accepted", f"Expected accepted, got {cand['status']}"
        assert cand["accepted_session_id"] == sid

        # C: sessions.missing_charge_candidate_id set on session
        sess = _get_session(con, sid)
        assert sess["missing_charge_candidate_id"] == cid

        close_db_if_owned(con)


def test_accept_also_accepts_via_candidate_id_key(authed_client, app):
    """Backend accepts both 'missing_charge_candidate_id' and 'candidate_id' keys."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cid = _insert_candidate(con)
        authed_client.post(f"/api/missing-charges/{cid}/accept")  # set in_review
        close_db_if_owned(con)

    r = _post_json(authed_client, "/api/sessions/manual", {
        "start_ts": "2026-01-10 08:00",
        "kwh_charged": 22.0,
        "location": "extern",
        "candidate_id": cid,  # alternate key name
    })
    assert r.status_code == 201
    d = json.loads(r.data)
    sid = d["id"]

    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cand = _get_candidate(con, cid)
        assert cand["status"] == "accepted"
        assert cand["accepted_session_id"] == sid
        sess = _get_session(con, sid)
        assert sess["missing_charge_candidate_id"] == cid
        close_db_if_owned(con)


# ─── Test D: Candidate does not stay in_review ───────────────────────────────

def test_candidate_not_in_review_after_session_saved(authed_client, app):
    """After session is saved, candidate must NOT be in_review."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cid = _insert_candidate(con)
        close_db_if_owned(con)

    authed_client.post(f"/api/missing-charges/{cid}/accept")

    _post_json(authed_client, "/api/sessions/manual", {
        "start_ts": "2026-01-10 08:00",
        "kwh_charged": 22.0,
        "location": "extern",
        "missing_charge_candidate_id": cid,
    })

    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cand = _get_candidate(con, cid)
        assert cand["status"] != "in_review", "Candidate must not stay in_review after session is saved"
        close_db_if_owned(con)


# ─── Test E: Revert route ────────────────────────────────────────────────────

def test_revert_resets_in_review_to_open(authed_client, app):
    """POST /api/missing-charges/<cid>/revert resets in_review → open."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cid = _insert_candidate(con)
        close_db_if_owned(con)

    # Mark as in_review
    authed_client.post(f"/api/missing-charges/{cid}/accept")

    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        assert _get_candidate(con, cid)["status"] == "in_review"
        close_db_if_owned(con)

    # Revert
    r = authed_client.post(f"/api/missing-charges/{cid}/revert")
    d = json.loads(r.data)
    assert d["ok"] is True
    assert d["reverted"] is True

    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cand = _get_candidate(con, cid)
        assert cand["status"] == "open", f"Expected open after revert, got {cand['status']}"
        close_db_if_owned(con)


def test_revert_on_already_open_is_noop(authed_client, app):
    """Reverting an already-open candidate succeeds but reverted=False."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cid = _insert_candidate(con, status="open")
        close_db_if_owned(con)

    r = authed_client.post(f"/api/missing-charges/{cid}/revert")
    d = json.loads(r.data)
    assert d["ok"] is True
    assert d["reverted"] is False  # already open, no update happened


def test_revert_on_accepted_is_noop(authed_client, app):
    """Reverting an accepted candidate is a no-op (only reverts in_review)."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cid = _insert_candidate(con, status="accepted")
        close_db_if_owned(con)

    r = authed_client.post(f"/api/missing-charges/{cid}/revert")
    d = json.loads(r.data)
    assert d["ok"] is True
    assert d["reverted"] is False  # accepted stays accepted

    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cand = _get_candidate(con, cid)
        assert cand["status"] == "accepted"
        close_db_if_owned(con)


# ─── Test G: No candidate — no candidate table change ────────────────────────

def test_session_without_candidate_id_doesnt_touch_candidates(authed_client, app):
    """Saving a session without candidate_id leaves all candidates untouched."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cid = _insert_candidate(con)
        close_db_if_owned(con)

    r = _post_json(authed_client, "/api/sessions/manual", {
        "start_ts": "2026-01-10 08:00",
        "kwh_charged": 10.0,
        "location": "home",
        # no missing_charge_candidate_id
    })
    assert r.status_code == 201

    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cand = _get_candidate(con, cid)
        assert cand["status"] == "open"
        assert cand["accepted_session_id"] is None
        close_db_if_owned(con)


# ─── Test H+I: Odometer suggestion in accept prefill ─────────────────────────

def test_accept_prefill_contains_odometer_suggestion(authed_client, app):
    """Accept endpoint returns suggested_odometer_km and confidence in prefill."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cid = _insert_candidate(
            con,
            suggested_odometer_km=10500.0,
            suggested_odometer_confidence="high",
            suggested_odometer_source="prev_session",
        )
        close_db_if_owned(con)

    r = authed_client.post(f"/api/missing-charges/{cid}/accept")
    d = json.loads(r.data)
    assert d["ok"] is True
    pf = d["prefill"]
    assert pf["suggested_odometer_km"] == 10500.0
    assert pf["suggested_odometer_confidence"] == "high"
    assert pf["suggested_odometer_source"] == "prev_session"


def test_accept_prefill_large_snapshot_span_has_none_confidence(authed_client, app):
    """Large odo_start/end difference means none confidence — no prefill of km_start/end."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cid = _insert_candidate(
            con,
            suggested_odometer_km=None,
            suggested_odometer_confidence="none",
            odo_start=9270.0,
            odo_end=12500.0,
        )
        close_db_if_owned(con)

    r = authed_client.post(f"/api/missing-charges/{cid}/accept")
    d = json.loads(r.data)
    pf = d["prefill"]
    # confidence is none — frontend must NOT use odo_start/end as km_start/km_end
    assert pf.get("suggested_odometer_confidence") == "none"
    assert pf.get("suggested_odometer_km") is None
    # The raw window values should NOT be confused with a reliable suggested odo
    assert pf.get("odo_start") == 9270.0
    assert pf.get("odo_end") == 12500.0


# ─── Test: /api/missing-charges/<cid>/accept returns candidate_id field ──────

def test_accept_returns_candidate_id(authed_client, app):
    """The /accept response must include candidate_id for the frontend to store."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cid = _insert_candidate(con)
        close_db_if_owned(con)

    r = authed_client.post(f"/api/missing-charges/{cid}/accept")
    d = json.loads(r.data)
    assert "candidate_id" in d, "Response must include candidate_id for frontend state"
    assert d["candidate_id"] == cid


# ─── Test: double-accept is harmless ─────────────────────────────────────────

def test_double_accept_and_save_does_not_break(authed_client, app):
    """Accepting twice and saving once should still result in accepted status."""
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cid = _insert_candidate(con)
        close_db_if_owned(con)

    authed_client.post(f"/api/missing-charges/{cid}/accept")
    authed_client.post(f"/api/missing-charges/{cid}/accept")  # second accept

    r = _post_json(authed_client, "/api/sessions/manual", {
        "start_ts": "2026-01-10 08:00",
        "kwh_charged": 22.0,
        "location": "extern",
        "missing_charge_candidate_id": cid,
    })
    assert r.status_code == 201
    sid = json.loads(r.data)["id"]

    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        cand = _get_candidate(con, cid)
        assert cand["status"] == "accepted"
        assert cand["accepted_session_id"] == sid
        close_db_if_owned(con)
