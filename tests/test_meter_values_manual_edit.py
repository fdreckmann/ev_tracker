"""
Tests for PATCH /api/sessions/<id>/meter-values — manual correction of
meter_old/meter_new on completed charging sessions.

Covers the required cases:
  1.  start + end value are saved
  2.  meter_delta_kwh is recomputed server-side
  3.  comma-decimal input ("1234,5") is parsed correctly
  4.  both values can be cleared to NULL
  5.  only meter_old can be saved
  6.  only meter_new can be saved
  7.  meter_new < meter_old is rejected
  8.  negative values are rejected
  9.  invalid text is rejected
  10. unknown session id -> 404
  11. unauthorized access is rejected
  12. manually corrected values survive a later automatic reconciliation
  13. the concrete meter_old=1 -> NULL correction works
  14. no other session fields are touched by this route
"""
from datetime import datetime, timedelta

import pytest


def _ts(offset_seconds: float = 0) -> str:
    base = datetime(2026, 8, 1, 9, 0, 0)
    return (base + timedelta(seconds=offset_seconds)).isoformat(timespec="seconds")


def _create_closed_session(client, **overrides):
    """Create a manual session and close it (end_ts set) so meter-value
    editing is allowed. Returns the session id."""
    data = {
        "start_ts": _ts(0), "end_ts": _ts(3600),
        "kwh_charged": 20.0, "location": "home", "charger_type": "ac",
    }
    data.update(overrides)
    rv = client.post("/api/sessions/manual", json=data)
    assert rv.status_code in (200, 201)
    return rv.get_json()["id"]


def _get_session_row(app, sid):
    from core.db import _get_db, close_db_if_owned
    with app.app_context():
        con = _get_db()
        row = con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        close_db_if_owned(con)
    return dict(row) if row else {}


class TestMeterValuesBasicSave:
    def test_1_start_and_end_value_are_saved(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": 1234.0, "meter_new": 1242.5})
        assert rv.status_code == 200
        body = rv.get_json()
        assert body["ok"] is True
        row = _get_session_row(app, sid)
        assert row["meter_old"] == pytest.approx(1234.0)
        assert row["meter_new"] == pytest.approx(1242.5)

    def test_2_delta_recomputed_server_side(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": 1234.0, "meter_new": 1242.5})
        body = rv.get_json()
        assert body["meter_delta_kwh"] == pytest.approx(8.5)
        row = _get_session_row(app, sid)
        assert row["meter_delta_kwh"] == pytest.approx(8.5)
        assert row["meter_used"] == 1
        assert row["meter_skipped_reason"] is None

    def test_3_comma_decimal_parsed_correctly(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": "1234,5", "meter_new": "1240,0"})
        assert rv.status_code == 200
        row = _get_session_row(app, sid)
        assert row["meter_old"] == pytest.approx(1234.5)
        assert row["meter_new"] == pytest.approx(1240.0)

    def test_4_both_values_cleared_to_null(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        authed_client.patch(f"/api/sessions/{sid}/meter-values",
                            json={"meter_old": 1234.0, "meter_new": 1242.5})
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": None, "meter_new": None})
        assert rv.status_code == 200
        row = _get_session_row(app, sid)
        assert row["meter_old"] is None
        assert row["meter_new"] is None
        assert row["meter_delta_kwh"] is None
        assert row["meter_used"] == 0
        assert row["meter_skipped_reason"] == "manual_meter_values_cleared"

    def test_5_only_start_value_saved(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": 500.0})
        assert rv.status_code == 200
        row = _get_session_row(app, sid)
        assert row["meter_old"] == pytest.approx(500.0)
        assert row["meter_new"] is None
        assert row["meter_delta_kwh"] is None
        assert row["meter_skipped_reason"] == "manual_meter_value_incomplete"

    def test_6_only_end_value_saved(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_new": 700.0})
        assert rv.status_code == 200
        row = _get_session_row(app, sid)
        assert row["meter_new"] == pytest.approx(700.0)
        assert row["meter_old"] is None
        assert row["meter_delta_kwh"] is None
        assert row["meter_skipped_reason"] == "manual_meter_value_incomplete"


class TestMeterValuesValidation:
    def test_7_end_less_than_start_rejected(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": 1000.0, "meter_new": 900.0})
        assert rv.status_code == 400
        body = rv.get_json()
        assert body["ok"] is False
        assert "error" in body
        # Nothing was saved — no negative consumption anywhere
        row = _get_session_row(app, sid)
        assert row["meter_old"] is None
        assert row["meter_new"] is None

    def test_8_negative_values_rejected(self, authed_client):
        sid = _create_closed_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": -5.0})
        assert rv.status_code == 400
        rv2 = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                  json={"meter_new": -1.0})
        assert rv2.status_code == 400

    def test_9_invalid_text_rejected(self, authed_client):
        sid = _create_closed_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": "not-a-number"})
        assert rv.status_code == 400
        assert rv.get_json()["ok"] is False

    def test_10_unknown_session_returns_404(self, authed_client):
        rv = authed_client.patch("/api/sessions/999999/meter-values",
                                 json={"meter_old": 100.0})
        assert rv.status_code == 404

    def test_11_unauthorized_access_rejected(self, client, app, authed_client):
        sid = _create_closed_session(authed_client)
        rv = client.patch(f"/api/sessions/{sid}/meter-values",
                          json={"meter_old": 100.0})
        assert rv.status_code == 401

    def test_open_session_cannot_be_edited(self, authed_client, app):
        """Meter values may only be edited on a completed (end_ts set) session."""
        rv = authed_client.post("/api/sessions/manual", json={
            "start_ts": _ts(0), "kwh_charged": 5.0, "location": "home",
        })
        sid = rv.get_json()["id"]
        assert _get_session_row(app, sid)["end_ts"] is None
        rv2 = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                  json={"meter_old": 100.0})
        assert rv2.status_code == 400


class TestMeterValuesNotOverwrittenByReconciliation:
    def test_12_manual_values_survive_reconciliation(self, authed_client, app):
        """A manually corrected + cleared meter_old must not get silently
        refilled by a later wallbox-session reconciliation."""
        sid = _create_closed_session(authed_client, start_ts=_ts(0), end_ts=_ts(3600))
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": None, "meter_new": None})
        assert rv.status_code == 200
        row = _get_session_row(app, sid)
        assert row["meter_old"] is None
        assert row["meter_values_manual"] == 1

        from core.db import _get_db, close_db_if_owned
        from services.reconciliation_service import enrich_session_with_meter
        with app.app_context():
            con = _get_db()
            wbs = {"id": 1, "meter_start_kwh": 999.0, "meter_end_kwh": 1010.0,
                   "energy_kwh": 11.0, "source_name": "goe"}
            enrich_session_with_meter(con, sid, wbs)
            close_db_if_owned(con)

        row_after = _get_session_row(app, sid)
        # Reconciliation must NOT have refilled the manually cleared value
        assert row_after["meter_old"] is None
        assert row_after["meter_new"] is None

    def test_manual_full_values_survive_reconciliation(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        authed_client.patch(f"/api/sessions/{sid}/meter-values",
                            json={"meter_old": 1234.0, "meter_new": 1242.5})

        from core.db import _get_db, close_db_if_owned
        from services.reconciliation_service import enrich_session_with_meter
        with app.app_context():
            con = _get_db()
            wbs = {"id": 2, "meter_start_kwh": 1.0, "meter_end_kwh": 5.0,
                   "energy_kwh": 4.0, "source_name": "goe"}
            enrich_session_with_meter(con, sid, wbs)
            close_db_if_owned(con)

        row = _get_session_row(app, sid)
        assert row["meter_old"] == pytest.approx(1234.0)
        assert row["meter_new"] == pytest.approx(1242.5)


class TestConcreteLegacyBugCorrection:
    def test_13_meter_old_1_corrected_to_null(self, authed_client, app):
        """The exact reported bug pattern: meter_old=1, meter_new empty."""
        sid = _create_closed_session(authed_client)
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            con.execute("UPDATE sessions SET meter_old=1, meter_new=NULL,"
                       " meter_source_start='evcc' WHERE id=?", (sid,))
            con.commit()
            close_db_if_owned(con)

        row_before = _get_session_row(app, sid)
        assert row_before["meter_old"] == 1
        assert row_before["meter_new"] is None

        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": None})
        assert rv.status_code == 200
        row_after = _get_session_row(app, sid)
        assert row_after["meter_old"] is None
        assert row_after["meter_new"] is None
        assert row_after["meter_values_manual"] == 1
        assert row_after["meter_skipped_reason"] == "manual_meter_values_cleared"


class TestMeterValuesDoNotTouchOtherFields:
    def test_14_other_session_fields_unchanged(self, authed_client, app):
        sid = _create_closed_session(
            authed_client, start_ts=_ts(0), end_ts=_ts(3600),
            location="home", charger_type="ac",
        )
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            con.execute(
                "UPDATE sessions SET soc_start=40, soc_end=60,"
                " odo_start=10000, odo_end=10050, vehicle_id='v0' WHERE id=?",
                (sid,),
            )
            con.commit()
            close_db_if_owned(con)

        before = _get_session_row(app, sid)

        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": 1234.0, "meter_new": 1242.5})
        assert rv.status_code == 200

        after = _get_session_row(app, sid)
        for key in ("start_ts", "end_ts", "soc_start", "soc_end",
                    "odo_start", "odo_end", "location", "vehicle_id"):
            assert after[key] == before[key], f"{key} changed unexpectedly"
