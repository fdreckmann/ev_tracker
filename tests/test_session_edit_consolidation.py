"""
Tests for consolidating charging-session editing into a single dialog.

Before this change, editing was split across two places:
  - the "✎ Bearbeiten" dialog (general session data)
  - a separate "🔌 Zählerstände" button + dialog in the read-only detail view

This bundles everything into the "✎ Bearbeiten" dialog and makes the detail
view (opened by clicking a session row) strictly read-only.

Covers the required cases:
  1.  detail view contains no edit buttons
  2.  detail view contains no meter input fields
  3.  edit dialog contains meter_old and meter_new fields
  4.  both meter values are saved together with the rest of the session data
  5.  start/end meter value can be changed
  6.  both values can be cleared
  7.  empty values are stored as NULL, not 0
  8.  comma decimals ("1234,5") are parsed correctly
  9.  negative values are rejected
  10. end < start is rejected
  11. the delta is recomputed server-side
  12. only start or only end can be saved
  13. a running (open) session does not allow meter editing
  14. manually changed values are not later overwritten automatically
  15. a meter-save failure does not produce a misleading general success message
  16. other session data is not touched by the meter change
  17. the old separate meter-edit function/dialog is no longer reachable
  18. the reported meter_old=1 bug can be cleared via the normal edit dialog
"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SESSIONS_JS = Path(__file__).parent.parent / "app" / "static" / "js" / "sessions.js"


def _ts(offset_seconds: float = 0) -> str:
    base = datetime(2026, 8, 5, 9, 0, 0)
    return (base + timedelta(seconds=offset_seconds)).isoformat(timespec="seconds")


def _create_closed_session(client, **overrides):
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


# ---------------------------------------------------------------------------
# 1+2+17. Detail view is read-only; old separate meter-edit path is gone
# ---------------------------------------------------------------------------

class TestDetailViewIsReadOnly:
    def test_1_no_edit_buttons_in_detail_view(self, authed_client):
        """showSessionDetail() must not build any edit-action buttons —
        modalActions is only ever cleared, never populated."""
        js = SESSIONS_JS.read_text()
        start = js.index("async function showSessionDetail")
        end = js.index("function closeModal", start)
        block = js[start:end]
        assert "editLocation" not in block
        assert "editCost" not in block
        assert "editMeterValues" not in block
        assert "createElement('button')" not in block
        assert "_modalActions.innerHTML = ''" in block

    def test_2_no_meter_input_fields_in_detail_view(self, authed_client):
        """The old dedicated meter-values modal (mv_old/mv_new inputs) must
        not exist anywhere in the rendered page."""
        rv = authed_client.get('/')
        assert rv.status_code == 200
        html = rv.get_data(as_text=True)
        assert 'id="mv_old"' not in html
        assert 'id="mv_new"' not in html
        assert 'id="meterValuesModal"' not in html

    def test_17_old_separate_meter_edit_function_not_reachable(self, authed_client):
        """editMeterValues/closeMeterValuesModal/submitMeterValues must not
        exist anywhere — not as functions, not as window exports, not as
        onclick handlers."""
        rv = authed_client.get('/')
        html = rv.get_data(as_text=True)
        js = SESSIONS_JS.read_text()
        for token in ("editMeterValues", "closeMeterValuesModal", "submitMeterValues"):
            assert token not in html, f"'{token}' still referenced in rendered HTML"
            assert token not in js, f"'{token}' still defined in sessions.js"


# ---------------------------------------------------------------------------
# 3. Edit dialog contains meter_old/meter_new fields
# ---------------------------------------------------------------------------

class TestEditDialogHasMeterFields:
    def test_3_edit_dialog_has_meter_fields(self, authed_client):
        rv = authed_client.get('/')
        html = rv.get_data(as_text=True)
        assert 'id="editSessionModal"' in html
        # Fields must live inside the edit-session modal, not a separate one
        start = html.index('id="editSessionModal"')
        end = html.index('id="meterValuesModal"') if 'id="meterValuesModal"' in html else len(html)
        block = html[start:end]
        assert 'id="es_meter_old"' in block
        assert 'id="es_meter_new"' in block


# ---------------------------------------------------------------------------
# 4-12. Backend behaviour of the unified save path (dedicated API endpoint,
# exercised the same way the consolidated frontend now calls it: general
# PATCH + PATCH .../meter-values, both against the real routes)
# ---------------------------------------------------------------------------

def _save_via_unified_flow(client, sid, general_body, meter_body):
    """Mirrors what submitEditSession() now does: one call for general
    fields, one call for meter values — both against the real backend."""
    rv1 = client.patch(f"/api/sessions/{sid}", json=general_body)
    if rv1.status_code != 200:
        return rv1, None
    rv2 = client.patch(f"/api/sessions/{sid}/meter-values", json=meter_body)
    return rv1, rv2


class TestUnifiedSaveBehaviour:
    def test_4_meter_values_saved_together_with_session_data(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        rv1, rv2 = _save_via_unified_flow(
            authed_client, sid,
            {"location": "extern", "charger_type": "dc"},
            {"meter_old": 1234.0, "meter_new": 1242.5},
        )
        assert rv1.status_code == 200
        assert rv2.status_code == 200
        row = _get_session_row(app, sid)
        assert row["location"] == "extern"
        assert row["charger_type"] == "dc"
        assert row["meter_old"] == pytest.approx(1234.0)
        assert row["meter_new"] == pytest.approx(1242.5)

    def test_5_start_and_end_value_changed(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        authed_client.patch(f"/api/sessions/{sid}/meter-values",
                            json={"meter_old": 100.0, "meter_new": 110.0})
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": 200.0, "meter_new": 220.0})
        assert rv.status_code == 200
        row = _get_session_row(app, sid)
        assert row["meter_old"] == pytest.approx(200.0)
        assert row["meter_new"] == pytest.approx(220.0)

    def test_6_both_values_cleared(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        authed_client.patch(f"/api/sessions/{sid}/meter-values",
                            json={"meter_old": 100.0, "meter_new": 110.0})
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": None, "meter_new": None})
        assert rv.status_code == 200
        row = _get_session_row(app, sid)
        assert row["meter_old"] is None
        assert row["meter_new"] is None

    def test_7_empty_values_stored_as_null_not_zero(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": None, "meter_new": None})
        assert rv.status_code == 200
        row = _get_session_row(app, sid)
        assert row["meter_old"] is None and row["meter_old"] != 0
        assert row["meter_new"] is None and row["meter_new"] != 0

    def test_8_comma_decimal_parsed(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": "1234,5", "meter_new": "1240,0"})
        assert rv.status_code == 200
        row = _get_session_row(app, sid)
        assert row["meter_old"] == pytest.approx(1234.5)
        assert row["meter_new"] == pytest.approx(1240.0)

    def test_9_negative_values_rejected(self, authed_client):
        sid = _create_closed_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": -1.0})
        assert rv.status_code == 400

    def test_10_end_less_than_start_rejected(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": 500.0, "meter_new": 400.0})
        assert rv.status_code == 400
        row = _get_session_row(app, sid)
        assert row["meter_old"] is None  # nothing saved

    def test_11_delta_recomputed_server_side(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": 1234.0, "meter_new": 1242.5})
        assert rv.get_json()["meter_delta_kwh"] == pytest.approx(8.5)
        row = _get_session_row(app, sid)
        assert row["meter_delta_kwh"] == pytest.approx(8.5)

    def test_12_only_start_or_only_end_saved(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        rv1 = authed_client.patch(f"/api/sessions/{sid}/meter-values", json={"meter_old": 50.0})
        assert rv1.status_code == 200
        row1 = _get_session_row(app, sid)
        assert row1["meter_old"] == pytest.approx(50.0)
        assert row1["meter_new"] is None

        sid2 = _create_closed_session(authed_client, start_ts=_ts(7200), end_ts=_ts(10800))
        rv2 = authed_client.patch(f"/api/sessions/{sid2}/meter-values", json={"meter_new": 90.0})
        assert rv2.status_code == 200
        row2 = _get_session_row(app, sid2)
        assert row2["meter_new"] == pytest.approx(90.0)
        assert row2["meter_old"] is None


# ---------------------------------------------------------------------------
# 13. Open sessions cannot have meter values edited
# ---------------------------------------------------------------------------

class TestOpenSessionMeterEditingBlocked:
    def test_13_running_session_rejects_meter_edit(self, authed_client, app):
        rv = authed_client.post("/api/sessions/manual", json={
            "start_ts": _ts(0), "kwh_charged": 5.0, "location": "home",
        })
        sid = rv.get_json()["id"]
        assert _get_session_row(app, sid)["end_ts"] is None
        rv2 = authed_client.patch(f"/api/sessions/{sid}/meter-values", json={"meter_old": 10.0})
        assert rv2.status_code == 400

    def test_edit_dialog_disables_meter_fields_for_open_sessions(self):
        """editSession() must disable the meter inputs and show the hint
        when the session has no end_ts."""
        js = SESSIONS_JS.read_text()
        start = js.index("async function editSession")
        end = js.index("function closeEditSessionModal", start)
        block = js[start:end]
        assert "_editSessionClosed" in block
        assert "meterOldEl.disabled" in block
        assert "es_meter_open_hint" in block


# ---------------------------------------------------------------------------
# 14. Manually changed values survive automatic reconciliation
# ---------------------------------------------------------------------------

class TestManualValuesProtected:
    def test_14_manual_values_not_overwritten_later(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        authed_client.patch(f"/api/sessions/{sid}/meter-values",
                            json={"meter_old": None, "meter_new": None})
        row = _get_session_row(app, sid)
        assert row["meter_values_manual"] == 1

        from core.db import _get_db, close_db_if_owned
        from services.reconciliation_service import enrich_session_with_meter
        with app.app_context():
            con = _get_db()
            enrich_session_with_meter(con, sid, {
                "id": 1, "meter_start_kwh": 999.0, "meter_end_kwh": 1010.0,
                "energy_kwh": 11.0, "source_name": "goe",
            })
            close_db_if_owned(con)

        row_after = _get_session_row(app, sid)
        assert row_after["meter_old"] is None
        assert row_after["meter_new"] is None


# ---------------------------------------------------------------------------
# 15. A meter-save failure must not trigger a misleading success message
# ---------------------------------------------------------------------------

class TestPartialFailureHandling:
    def test_15_meter_save_failure_yields_error_not_success(self, authed_client, app):
        """Backend behaviour that the frontend's error branch relies on:
        the meter-values call fails independently and clearly (400), so a
        caller chaining it after a successful general PATCH can tell the
        two outcomes apart and must not report overall success."""
        sid = _create_closed_session(authed_client)
        rv1 = authed_client.patch(f"/api/sessions/{sid}", json={"location": "extern"})
        assert rv1.status_code == 200
        rv2 = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                  json={"meter_old": 100.0, "meter_new": 50.0})
        assert rv2.status_code == 400
        assert rv2.get_json()["ok"] is False

    def test_frontend_checks_meter_response_before_success_toast(self):
        """submitEditSession() must gate the success toast on the
        meter-values response's `ok` flag, not just the general PATCH."""
        js = SESSIONS_JS.read_text()
        start = js.index("async function submitEditSession")
        block = js[start:start + 6000]
        assert "if (!mr.ok)" in block
        # The success toast must come after both checks, not before mr is awaited
        toast_pos = block.index("toast('✅ Session #' + id + ' gespeichert")
        mr_check_pos = block.index("if (!mr.ok)")
        assert mr_check_pos < toast_pos


# ---------------------------------------------------------------------------
# 16. Other session data untouched by meter edit
# ---------------------------------------------------------------------------

class TestOtherFieldsUntouched:
    def test_16_other_fields_unchanged_by_meter_patch(self, authed_client, app):
        sid = _create_closed_session(authed_client, location="home", charger_type="ac")
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            con.execute("UPDATE sessions SET soc_start=40, soc_end=60,"
                       " odo_start=10000, odo_end=10050 WHERE id=?", (sid,))
            con.commit()
            close_db_if_owned(con)
        before = _get_session_row(app, sid)

        rv = authed_client.patch(f"/api/sessions/{sid}/meter-values",
                                 json={"meter_old": 1234.0, "meter_new": 1242.5})
        assert rv.status_code == 200

        after = _get_session_row(app, sid)
        for key in ("start_ts", "end_ts", "soc_start", "soc_end",
                    "odo_start", "odo_end", "location", "charger_type", "vehicle_id"):
            assert after[key] == before[key], f"{key} changed unexpectedly"


# ---------------------------------------------------------------------------
# 18. The concrete legacy bug (meter_old=1) can be cleared via the normal
# edit dialog's flow
# ---------------------------------------------------------------------------

class TestLegacyBugFixableViaUnifiedDialog:
    def test_18_meter_old_1_cleared_via_edit_dialog_flow(self, authed_client, app):
        sid = _create_closed_session(authed_client)
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            con.execute("UPDATE sessions SET meter_old=1, meter_new=NULL,"
                       " meter_source_start='evcc' WHERE id=?", (sid,))
            con.commit()
            close_db_if_owned(con)
        assert _get_session_row(app, sid)["meter_old"] == 1

        # Exactly what the unified edit dialog now sends: a general PATCH
        # (unchanged fields resent, as the dialog always does) followed by
        # the meter-values PATCH with the field cleared.
        rv1, rv2 = _save_via_unified_flow(
            authed_client, sid,
            {"location": "home", "charger_type": "ac"},
            {"meter_old": None, "meter_new": None},
        )
        assert rv1.status_code == 200
        assert rv2.status_code == 200

        row = _get_session_row(app, sid)
        assert row["meter_old"] is None
        assert row["meter_new"] is None
        assert row["meter_values_manual"] == 1
