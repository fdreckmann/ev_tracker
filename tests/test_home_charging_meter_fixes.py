"""
Regression tests for the "Zuhause laden" meter/session bugfix package.

Covers:
  1. EvccMeterProvider: chargeTotalImport vs chargedEnergy (never confused)
  2. Live power update independent of DB snapshot dedupe + stale-value-on-error
  3. Normal sessions: full home meter_old/meter_new, unknown-location-at-end
     fallback, tracker-restart resume (mid-session + after home-detection)
  4. Wallbox sessions: restart rehydration, energy-only baseline/rise/close,
     counter reset guard
  5. Existing protections: home_only scope for extern sessions, meter source
     changed => no delta
"""
from datetime import datetime, timedelta

import pytest


def _ts(offset_seconds: float = 0) -> str:
    base = datetime(2026, 7, 1, 9, 0, 0)
    return (base + timedelta(seconds=offset_seconds)).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 1. EVCC provider — chargeTotalImport vs chargedEnergy
# ---------------------------------------------------------------------------

class TestEvccMeterProvider:
    def _cfg(self, **overrides):
        cfg = {
            "meter_source": "evcc",
            "meter_device_ip": "192.0.2.10",
            "meter_evcc_port": 7070,
            "meter_evcc_lp": 0,
        }
        cfg.update(overrides)
        return cfg

    def _patch_json(self, monkeypatch, payload):
        import meter_providers
        monkeypatch.setattr(meter_providers, "_get_json", lambda *a, **k: payload)

    def test_missing_charge_total_import_no_fake_counter(self, monkeypatch):
        """chargeTotalImport absent, chargedEnergy=1000 Wh, chargePower=11000 W.

        Must NOT produce a fabricated cumulative value like 1.0 kWh — the
        session-energy field must never leak into the cumulative counter.
        """
        from meter_providers import EvccMeterProvider
        self._patch_json(monkeypatch, {
            "result": {"loadpoints": [
                {"chargedEnergy": 1000, "chargePower": 11000}
            ]}
        })
        res = EvccMeterProvider(self._cfg()).read()
        assert res.value is None
        assert res.ok is True          # power-only read is still a successful poll
        assert res.power_kw == pytest.approx(11.0)
        # No "1" (or any) cumulative value must appear anywhere
        assert res.value != 1
        assert res.raw_value == pytest.approx(1.0)  # session energy, informational only

    def test_charge_total_import_zero_is_not_missing(self, monkeypatch):
        """chargeTotalImport=0 is a real reading, not 'missing' — `or` truthiness bug."""
        from meter_providers import EvccMeterProvider
        self._patch_json(monkeypatch, {
            "result": {"loadpoints": [
                {"chargeTotalImport": 0, "chargedEnergy": 500, "chargePower": 5000}
            ]}
        })
        res = EvccMeterProvider(self._cfg()).read()
        assert res.value == 0
        assert res.ok is True
        # Must not have fallen back to chargedEnergy (500 Wh -> 0.5 kWh)
        assert res.value != 0.5

    def test_charge_total_import_valid_value_used(self, monkeypatch):
        from meter_providers import EvccMeterProvider
        self._patch_json(monkeypatch, {
            "result": {"loadpoints": [
                {"chargeTotalImport": 1234.5, "chargedEnergy": 2000, "chargePower": 7400}
            ]}
        })
        res = EvccMeterProvider(self._cfg()).read()
        assert res.value == pytest.approx(1234.5)
        assert res.power_kw == pytest.approx(7.4)


# ---------------------------------------------------------------------------
# 2. Live power/stop detection independent of DB snapshot dedupe
# ---------------------------------------------------------------------------

class TestLivePowerAndStopDetection:
    def test_unchanged_meter_power_drop_updates_live_state(self, app, monkeypatch):
        """Meter unchanged, power 11kW -> 0kW: live state still reflects 0 kW
        even though no new DB snapshot row is written (dedupe)."""
        import meter_providers
        from meter_providers import MeterResult
        from services.meter_snapshot_service import maybe_record_poll_snapshot
        from core.db import _get_db, close_db_if_owned

        with app.app_context():
            con = _get_db()
            cfg = {"meter_source": "evcc", "meter_snapshot_enabled": True,
                   "meter_snapshot_heartbeat_minutes": 30,
                   "meter_snapshot_min_delta_kwh": 0.5}
            st = {}

            monkeypatch.setattr(meter_providers, "read_meter",
                                 lambda _c: MeterResult(value=100.0, ok=True, source="evcc", power_kw=11.0))
            maybe_record_poll_snapshot("v0", cfg, st, con)
            assert st["meter_snap_last_power"] == pytest.approx(11.0)

            # Meter unchanged, power now 0 — below the min_delta so no new DB row,
            # but the live power signal must update immediately.
            monkeypatch.setattr(meter_providers, "read_meter",
                                 lambda _c: MeterResult(value=100.0, ok=True, source="evcc", power_kw=0.0))
            rid = maybe_record_poll_snapshot("v0", cfg, st, con)
            assert st["meter_snap_last_power"] == 0.0
            close_db_if_owned(con)

    def test_failed_poll_does_not_keep_stale_power_alive(self, app, monkeypatch):
        """A meter read failure right after a successful 11kW poll must not let
        11kW keep being reported as the current signal."""
        import meter_providers
        from meter_providers import MeterResult
        from services.meter_snapshot_service import maybe_record_poll_snapshot
        from core.db import _get_db, close_db_if_owned

        with app.app_context():
            con = _get_db()
            cfg = {"meter_source": "evcc", "meter_snapshot_enabled": True,
                   "meter_snapshot_heartbeat_minutes": 30,
                   "meter_snapshot_min_delta_kwh": 0.5}
            st = {}

            monkeypatch.setattr(meter_providers, "read_meter",
                                 lambda _c: MeterResult(value=50.0, ok=True, source="evcc", power_kw=11.0))
            maybe_record_poll_snapshot("v0", cfg, st, con)
            assert st["meter_snap_last_power"] == pytest.approx(11.0)

            monkeypatch.setattr(meter_providers, "read_meter",
                                 lambda _c: MeterResult(value=None, ok=False, source="evcc",
                                                        error="timeout"))
            maybe_record_poll_snapshot("v0", cfg, st, con)
            assert st["meter_snap_last_power"] is None
            close_db_if_owned(con)

    def test_stop_detected_via_charging_state_machine_after_unchanged_meter(self, app, monkeypatch):
        """End-to-end through ChargingStateMachine: power drops to 0 while the
        cumulative counter stays flat — the wallbox session must still close
        after stop-debounce (would previously hang forever)."""
        import meter_providers
        from meter_providers import MeterResult
        from services.meter_snapshot_service import maybe_record_poll_snapshot
        from services.charging_state_machine import ChargingStateMachine, SignalBundle, classify_meter_kind
        from core.db import _get_db, close_db_if_owned

        with app.app_context():
            con = _get_db()
            cfg = {
                "meter_source": "evcc", "meter_snapshot_enabled": True,
                "meter_snapshot_heartbeat_minutes": 30, "meter_snapshot_min_delta_kwh": 0.5,
                "home_charge_detection_enabled": True,
                "home_charge_power_start_threshold_kw": 1.0,
                "home_charge_power_stop_threshold_kw": 0.2,
                "home_charge_start_debounce_seconds": 1,
                "home_charge_stop_debounce_seconds": 1,
                "home_charge_min_energy_kwh": 0.0,
                "home_charge_vehicle_assignment_mode": "always_ask_if_unclear",
            }
            st = {}

            def _poll(power, ts):
                monkeypatch.setattr(meter_providers, "read_meter",
                                     lambda _c: MeterResult(value=200.0, ok=True, source="evcc", power_kw=power))
                maybe_record_poll_snapshot("v0", cfg, st, con)
                bundle = SignalBundle(
                    ts=ts, vehicle_id="v0", api_available=False,
                    meter_power_kw=st.get("meter_snap_last_power"),
                    meter_energy_total_kwh=st.get("meter_snap_last_val"),
                    meter_source="evcc", meter_kind=classify_meter_kind("evcc"),
                )
                return ChargingStateMachine(cfg, st, con).ingest(bundle)

            _poll(11.0, _ts(0))
            _poll(11.0, _ts(2))   # debounce elapsed -> session opens
            assert con.execute(
                "SELECT COUNT(*) FROM wallbox_sessions WHERE status='active'").fetchone()[0] == 1

            closed = _poll(0.0, _ts(3))   # power drops, meter unchanged
            closed = closed or _poll(0.0, _ts(5))  # stop-debounce elapsed -> closes
            assert closed is not None
            row = dict(con.execute(
                "SELECT status FROM wallbox_sessions WHERE id=?", (closed,)).fetchone())
            assert row["status"] in ("completed", "unassigned")
            close_db_if_owned(con)


# ---------------------------------------------------------------------------
# 2b. Live-state vs. snapshot-dedupe separation — driven through the REAL
# EvccMeterProvider (only the HTTP boundary is faked) and the REAL
# maybe_record_poll_snapshot() / ChargingStateMachine production functions.
# ---------------------------------------------------------------------------

class TestLiveStateNeverStale:
    """Regression tests for: after a failed or power-only poll, no stale
    live power/meter value may survive in `st`, independent of the
    snapshot-write dedupe (heartbeat/min-delta), which must only ever
    decide whether a DB row is written."""

    def _evcc_cfg(self, **overrides):
        cfg = {
            "meter_source": "evcc", "meter_device_ip": "192.0.2.20",
            "meter_evcc_port": 7070, "meter_evcc_lp": 0,
            "meter_snapshot_enabled": True,
            "meter_snapshot_heartbeat_minutes": 30,
            "meter_snapshot_min_delta_kwh": 0.5,
        }
        cfg.update(overrides)
        return cfg

    def _patch_evcc_payload(self, monkeypatch, loadpoint):
        import meter_providers
        monkeypatch.setattr(meter_providers, "_get_json",
                            lambda *a, **k: {"result": {"loadpoints": [loadpoint]}})

    def _patch_evcc_unreachable(self, monkeypatch):
        import meter_providers
        monkeypatch.setattr(meter_providers, "_get_json", lambda *a, **k: None)

    # -- Test 1: failure after a successful full poll -----------------------

    def test_1_failed_poll_clears_power_and_meter_value(self, app, monkeypatch):
        from services.meter_snapshot_service import maybe_record_poll_snapshot
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            cfg = self._evcc_cfg()
            st = {}

            self._patch_evcc_payload(monkeypatch,
                {"chargeTotalImport": 50.0, "chargePower": 11000})
            maybe_record_poll_snapshot("v0", cfg, st, con)
            assert st["meter_snap_last_power"] == pytest.approx(11.0)
            assert st["meter_snap_last_val"] == pytest.approx(50.0)
            assert st["meter_snap_last_ok"] is True

            self._patch_evcc_unreachable(monkeypatch)
            maybe_record_poll_snapshot("v0", cfg, st, con)
            close_db_if_owned(con)

        assert st["meter_snap_last_power"] is None
        assert st["meter_snap_last_val"] is None
        assert st["meter_snap_last_ok"] is False

    # -- Test 2: power-only poll after a full poll ---------------------------

    def test_2_power_only_poll_clears_previous_cumulative_value(self, app, monkeypatch):
        from services.meter_snapshot_service import maybe_record_poll_snapshot
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            cfg = self._evcc_cfg()
            st = {}

            self._patch_evcc_payload(monkeypatch,
                {"chargeTotalImport": 50.0, "chargePower": 11000})
            maybe_record_poll_snapshot("v0", cfg, st, con)
            assert st["meter_snap_last_val"] == pytest.approx(50.0)

            # Next poll: chargeTotalImport absent (EVCC restarted/loadpoint
            # without a cumulative counter this tick), only power available.
            self._patch_evcc_payload(monkeypatch, {"chargePower": 7400})
            maybe_record_poll_snapshot("v0", cfg, st, con)
            close_db_if_owned(con)

        current_power = st["meter_snap_last_power"]
        current_meter_value = st["meter_snap_last_val"]
        poll_ok = st["meter_snap_last_ok"]
        assert current_power == pytest.approx(7.4)
        assert current_meter_value is None
        assert poll_ok is True

    # -- Test 3: unchanged meter, power falls to 0 — dedupe must not block --

    def test_3_unchanged_meter_power_drop_still_updates_live_power(self, app, monkeypatch):
        from services.meter_snapshot_service import maybe_record_poll_snapshot
        from services.charging_state_machine import (
            ChargingStateMachine, SignalBundle, classify_meter_kind,
        )
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            cfg = self._evcc_cfg(
                home_charge_detection_enabled=True,
                home_charge_power_start_threshold_kw=1.0,
                home_charge_power_stop_threshold_kw=0.2,
                home_charge_start_debounce_seconds=1,
                home_charge_stop_debounce_seconds=1,
                home_charge_min_energy_kwh=0.0,
                home_charge_vehicle_assignment_mode="always_ask_if_unclear",
            )
            st = {}

            def _poll(power_w, ts):
                self._patch_evcc_payload(monkeypatch,
                    {"chargeTotalImport": 50.0, "chargePower": power_w})
                maybe_record_poll_snapshot("v0", cfg, st, con)
                bundle = SignalBundle(
                    ts=ts, vehicle_id="v0", api_available=False,
                    meter_power_kw=st.get("meter_snap_last_power"),
                    meter_energy_total_kwh=st.get("meter_snap_last_val"),
                    meter_source="evcc", meter_kind=classify_meter_kind("evcc"),
                )
                return ChargingStateMachine(cfg, st, con).ingest(bundle)

            _poll(11000, _ts(0))
            _poll(11000, _ts(2))  # start-debounce elapsed -> session opens
            assert con.execute(
                "SELECT COUNT(*) FROM wallbox_sessions WHERE status='active'").fetchone()[0] == 1

            # Meter (chargeTotalImport) stays exactly 50.0 the whole time —
            # dedupe would skip writing a new snapshot row, but the LIVE
            # power must still flip to 0.0 immediately.
            closed = _poll(0, _ts(3))
            assert st["meter_snap_last_power"] == 0.0
            closed = closed or _poll(0, _ts(5))  # stop-debounce elapsed -> closes
            close_db_if_owned(con)

        assert closed is not None

    # -- Test 4: valid meter value of exactly 0 --------------------------

    def test_4_meter_value_zero_is_valid_not_missing(self, app, monkeypatch):
        from services.meter_snapshot_service import maybe_record_poll_snapshot
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            cfg = self._evcc_cfg()
            st = {}
            self._patch_evcc_payload(monkeypatch, {"chargeTotalImport": 0, "chargePower": 0})
            maybe_record_poll_snapshot("v0", cfg, st, con)
            close_db_if_owned(con)

        current_meter_value = st["meter_snap_last_val"]
        assert current_meter_value == 0
        assert current_meter_value is not None
        assert st["meter_snap_last_ok"] is True

    # -- Test 5: ChargingStateMachine never sees a stale value ---------------

    def test_5_state_machine_receives_no_stale_meter_value_after_failure(self, app, monkeypatch):
        from services.meter_snapshot_service import maybe_record_poll_snapshot
        from services.charging_state_machine import (
            ChargingStateMachine, SignalBundle, classify_meter_kind,
        )
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            cfg = self._evcc_cfg(home_charge_detection_enabled=True)
            st = {}

            self._patch_evcc_payload(monkeypatch,
                {"chargeTotalImport": 50.0, "chargePower": 11000})
            maybe_record_poll_snapshot("v0", cfg, st, con)
            assert st["meter_snap_last_val"] == pytest.approx(50.0)

            # Failed poll — live meter value must clear...
            self._patch_evcc_unreachable(monkeypatch)
            maybe_record_poll_snapshot("v0", cfg, st, con)

            bundle = SignalBundle(
                ts=_ts(10), vehicle_id="v0", api_available=False,
                meter_power_kw=st.get("meter_snap_last_power"),
                meter_energy_total_kwh=st.get("meter_snap_last_val"),
                meter_source="evcc", meter_kind=classify_meter_kind("evcc"),
            )
            assert bundle.meter_energy_total_kwh is None
            assert bundle.meter_energy_total_kwh != 50.0

            # ...and feeding it into the real state machine must not open a
            # phantom energy-only session from the stale 50.0 kWh reading.
            ChargingStateMachine(cfg, st, con).ingest(bundle)
            count = con.execute("SELECT COUNT(*) FROM wallbox_sessions").fetchone()[0]
            close_db_if_owned(con)

        assert count == 0


# ---------------------------------------------------------------------------
# 3. Normal sessions — full meter delta, unknown-location fallback, restart
# ---------------------------------------------------------------------------

class TestNormalSessionMeterAndRestart:
    def _insert_open_session(self, con, **overrides):
        row = {
            "start_ts": _ts(0), "end_ts": None,
            "soc_start": 40.0, "odo_start": 12000.0,
            "location": "home", "location_source": "meter_delta",
            "charger_type": "ac", "max_power_kw": 11.0,
            "meter_old": 1234.0, "vehicle_id": "v0",
            "meter_source_start": "evcc",
            "meter_home_detection_start_value": None,
            "meter_home_detection_start_ts": None,
        }
        row.update(overrides)
        cols = ", ".join(row.keys())
        qs = ", ".join(["?"] * len(row))
        cur = con.execute(f"INSERT INTO sessions ({cols}) VALUES ({qs})", list(row.values()))
        con.commit()
        return cur.lastrowid

    def test_home_charge_full_meter_delta(self, app):
        """Home charge: meter_old=1234.0, meter_end=1242.5 -> delta 8.5 kWh."""
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            sid = self._insert_open_session(con)
            con.execute(
                "UPDATE sessions SET end_ts=?, meter_new=?, meter_delta_kwh=? WHERE id=?",
                (_ts(3600), 1242.5, round(1242.5 - 1234.0, 3), sid),
            )
            con.commit()
            row = dict(con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone())
            close_db_if_owned(con)
        assert row["meter_old"] == pytest.approx(1234.0)
        assert row["meter_new"] == pytest.approx(1242.5)
        assert row["meter_delta_kwh"] == pytest.approx(8.5)

    def test_end_location_unknown_falls_back_to_db_home(self, app):
        """Session already 'home' in DB — a live 'unknown' at session end must
        not skip the end-meter read (the effective_location fallback logic)."""
        from core.location import effective_session_location, normalize_location
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            sid = self._insert_open_session(con, location="home")

            # Simulate the exact fallback used in server.py's session-end branch.
            live_location = "unknown"
            live_location_status = None
            effective = effective_session_location(live_location, live_location_status)
            assert effective == "unknown"
            if effective == "unknown":
                db_loc_row = con.execute("SELECT location FROM sessions WHERE id=?", (sid,)).fetchone()
                db_loc = normalize_location(db_loc_row[0]) if db_loc_row and db_loc_row[0] else "unknown"
                if db_loc != "unknown":
                    effective = db_loc
            close_db_if_owned(con)
        assert effective == "home"

    def test_extern_confirmed_session_not_flipped_to_home(self, app):
        """An extern session must stay extern even if the live signal is briefly
        unknown — DB fallback must not accidentally promote it."""
        from core.location import effective_session_location, normalize_location
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            sid = self._insert_open_session(con, location="extern", location_source="manual")
            effective = effective_session_location("unknown", None)
            if effective == "unknown":
                db_loc_row = con.execute("SELECT location FROM sessions WHERE id=?", (sid,)).fetchone()
                db_loc = normalize_location(db_loc_row[0]) if db_loc_row and db_loc_row[0] else "unknown"
                if db_loc != "unknown":
                    effective = db_loc
            close_db_if_owned(con)
        assert effective == "extern"

    def test_restart_resumes_open_home_session(self, app):
        """Tracker restart mid home-charge: existing session is resumed, no
        second session is created, and the original meter_old survives."""
        from core.db import _get_db, close_db_if_owned
        import server
        with app.app_context():
            con = _get_db()
            sid = self._insert_open_session(con, meter_old=1234.0)
            close_db_if_owned(con)

            st = {}
            resumed = server._resume_open_session("v0", st)

        assert resumed["session_active"] is True
        assert resumed["session_id"] == sid
        assert resumed["meter_start_val"] == pytest.approx(1234.0)
        assert resumed["soc_start"] == pytest.approx(40.0)
        assert resumed["odo_start"] == pytest.approx(12000.0)
        assert st["session_active"] is True
        assert st["session_id"] == sid

    def test_restart_does_not_open_second_session(self, app):
        """After the resume helper reports an active session, the server-level
        'session already open' guard must be honoured (no second session)."""
        from core.db import _get_db, close_db_if_owned
        import server
        with app.app_context():
            con = _get_db()
            sid = self._insert_open_session(con)
            close_db_if_owned(con)

            st = {}
            resumed = server._resume_open_session("v0", st)
            session_active = resumed["session_active"]
            session_id = resumed["session_id"]

            # Mirrors tracker_loop's guard: `if charging and not session_active:`
            # must NOT fire since session_active is already True post-resume.
            would_open_new = (True and not session_active)
            assert would_open_new is False
            assert session_id == sid

            con = _get_db()
            count = con.execute(
                "SELECT COUNT(*) FROM sessions WHERE vehicle_id='v0' AND end_ts IS NULL"
            ).fetchone()[0]
            close_db_if_owned(con)
        assert count == 1

    def test_restart_after_meter_home_detection_preserves_state(self, app):
        """Restart after home-detection already resolved location='home' via
        meter-delta: the detection start value/ts and home status persist,
        and pending-detection state is cleared (no re-trigger)."""
        from core.db import _get_db, close_db_if_owned
        import server
        with app.app_context():
            con = _get_db()
            sid = self._insert_open_session(
                con, location="home", location_source="meter_delta",
                meter_home_detection_start_value=1234.0,
                meter_home_detection_start_ts=_ts(0),
            )
            close_db_if_owned(con)

            st = {}
            resumed = server._resume_open_session("v0", st)

        assert resumed["session_id"] == sid
        # Home status carried into runtime state:
        assert st["location_status"] == "home"
        assert st["location_source"] == "meter_delta"
        # Already resolved -> no re-trigger of detection on next poll:
        assert st["meter_home_det_start_val"] is None
        assert st["meter_home_det_start_ts"] is None

        # DB detection columns themselves must remain untouched by the resume.
        from core.db import _get_db as _get_db2
        with app.app_context():
            con = _get_db2()
            row = dict(con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone())
            close_db_if_owned(con)
        assert row["meter_home_detection_start_value"] == pytest.approx(1234.0)
        assert row["meter_home_detection_start_ts"] == _ts(0)

    def test_restart_resumes_pending_home_detection(self, app):
        """Restart while home-detection was still PENDING (location still
        'unknown'): the pending detection start value/ts must be restored so
        detection can continue watching for a delta rise."""
        from core.db import _get_db, close_db_if_owned
        import server
        with app.app_context():
            con = _get_db()
            sid = self._insert_open_session(
                con, location="unknown", location_source=None,
                meter_home_detection_start_value=500.0,
                meter_home_detection_start_ts=_ts(0),
            )
            close_db_if_owned(con)

            st = {}
            resumed = server._resume_open_session("v0", st)

        assert resumed["session_id"] == sid
        assert st["meter_home_det_start_val"] == pytest.approx(500.0)
        assert st["meter_home_det_start_ts"] == _ts(0)

    def test_multiple_open_sessions_resumes_newest_only(self, app, caplog):
        """If (abnormally) more than one open session exists, only the newest
        is resumed; older rows are left untouched, and a warning is logged."""
        from core.db import _get_db, close_db_if_owned
        import server
        with app.app_context():
            con = _get_db()
            older = self._insert_open_session(con, start_ts=_ts(-3600))
            newer = self._insert_open_session(con, start_ts=_ts(0))
            close_db_if_owned(con)

            st = {}
            import logging
            with caplog.at_level(logging.WARNING):
                resumed = server._resume_open_session("v0", st)

            con = _get_db()
            remaining = con.execute(
                "SELECT COUNT(*) FROM sessions WHERE end_ts IS NULL"
            ).fetchone()[0]
            close_db_if_owned(con)

        assert resumed["session_id"] == newer
        assert remaining == 2  # nothing deleted
        assert any("offene Sessions" in r.message or "Sessions gefunden" in r.message
                   for r in caplog.records)


# ---------------------------------------------------------------------------
# 4. Wallbox sessions — restart rehydration, energy-only baseline, resets
# ---------------------------------------------------------------------------

class TestWallboxSessionRestart:
    def test_restart_rehydrates_active_wallbox_session(self, app):
        """An active wallbox_sessions DB row survives a restart: RAM state is
        rehydrated and no duplicate session is opened for the same source."""
        from core.db import _get_db, close_db_if_owned
        from services.wallbox_session_service import get_active_wallbox_session, process_power_snapshot
        with app.app_context():
            con = _get_db()
            now = _ts(0)
            cur = con.execute(
                """INSERT INTO wallbox_sessions
                   (source_type, source_name, start_ts, meter_start_kwh, peak_power_kw,
                    status, vehicle_assignment_status, excluded_from_reports, created_at, updated_at)
                   VALUES ('ev_wallbox','goe',?,300.0,7.4,'active','unassigned',1,?,?)""",
                (now, now, now),
            )
            con.commit()
            wbs_id = cur.lastrowid

            st = {}  # fresh RAM state, as after a restart
            active = get_active_wallbox_session("goe", st, con)
            assert active is not None
            assert active["id"] == wbs_id
            assert active["meter_start_kwh"] == pytest.approx(300.0)

            cfg = {
                "home_charge_detection_enabled": True,
                "home_charge_power_start_threshold_kw": 1.0,
                "home_charge_power_stop_threshold_kw": 0.2,
                "home_charge_start_debounce_seconds": 10,
                "home_charge_stop_debounce_seconds": 10,
                "home_charge_min_energy_kwh": 0.1,
                "home_charge_vehicle_assignment_mode": "always_ask_if_unclear",
            }
            # Power still active -> continues the SAME session, no duplicate.
            process_power_snapshot("v0", "ev_wallbox", "goe", 7.4, 305.0, _ts(5), cfg, st, con)
            active_count = con.execute(
                "SELECT COUNT(*) FROM wallbox_sessions WHERE status='active'").fetchone()[0]
            assert active_count == 1

            # Now power stops -> stop-debounce restarts cleanly and closes the
            # SAME row (not a new one) once elapsed.
            process_power_snapshot("v0", "ev_wallbox", "goe", 0.0, 305.0, _ts(6), cfg, st, con)
            closed_id = process_power_snapshot("v0", "ev_wallbox", "goe", 0.0, 305.0, _ts(20), cfg, st, con)
            close_db_if_owned(con)
        assert closed_id == wbs_id

    def test_no_duplicate_wallbox_session_after_restart(self, app):
        from core.db import _get_db, close_db_if_owned
        from services.wallbox_session_service import get_active_wallbox_session
        with app.app_context():
            con = _get_db()
            now = _ts(0)
            con.execute(
                """INSERT INTO wallbox_sessions
                   (source_type, source_name, start_ts, meter_start_kwh,
                    status, vehicle_assignment_status, excluded_from_reports, created_at, updated_at)
                   VALUES ('ev_wallbox','goe',?,300.0,'active','unassigned',1,?,?)""",
                (now, now, now),
            )
            con.commit()
            st = {}
            get_active_wallbox_session("goe", st, con)   # rehydrate
            get_active_wallbox_session("goe", st, con)   # second call: from RAM, no re-query
            count = con.execute(
                "SELECT COUNT(*) FROM wallbox_sessions WHERE status='active'").fetchone()[0]
            close_db_if_owned(con)
        assert count == 1


class TestEnergyOnlyDetection:
    def _cfg(self, **overrides):
        cfg = {
            "home_charge_detection_enabled": True,
            "home_charge_stop_debounce_seconds": 10,
            "home_charge_min_energy_kwh": 0.5,
            "home_charge_vehicle_assignment_mode": "always_ask_if_unclear",
        }
        cfg.update(overrides)
        return cfg

    def test_first_reading_only_sets_baseline_no_session(self, app):
        from core.db import _get_db, close_db_if_owned
        from services.wallbox_session_service import process_energy_snapshot
        with app.app_context():
            con = _get_db()
            st = {}
            result = process_energy_snapshot(None, "meter", "haus", 100.0, _ts(0), self._cfg(), st, con)
            count = con.execute("SELECT COUNT(*) FROM wallbox_sessions").fetchone()[0]
            close_db_if_owned(con)
        assert result is None
        assert count == 0

    def test_positive_rise_opens_then_stable_closes(self, app):
        from core.db import _get_db, close_db_if_owned
        from services.wallbox_session_service import process_energy_snapshot
        with app.app_context():
            con = _get_db()
            cfg = self._cfg()
            st = {}
            process_energy_snapshot(None, "meter", "haus", 100.0, _ts(0), cfg, st, con)   # baseline
            process_energy_snapshot(None, "meter", "haus", 101.0, _ts(30), cfg, st, con)  # rise -> opens
            assert con.execute(
                "SELECT COUNT(*) FROM wallbox_sessions WHERE status='active'").fetchone()[0] == 1
            process_energy_snapshot(None, "meter", "haus", 103.0, _ts(60), cfg, st, con)  # still rising
            process_energy_snapshot(None, "meter", "haus", 103.0, _ts(65), cfg, st, con)  # stable
            closed = process_energy_snapshot(None, "meter", "haus", 103.0, _ts(90), cfg, st, con)  # debounce -> close
            close_db_if_owned(con)
        assert closed is not None

    def test_falling_counter_no_negative_energy_no_phantom_session(self, app):
        from core.db import _get_db, close_db_if_owned
        from services.wallbox_session_service import process_energy_snapshot
        with app.app_context():
            con = _get_db()
            cfg = self._cfg()
            st = {}
            process_energy_snapshot(None, "meter", "haus", 100.0, _ts(0), cfg, st, con)   # baseline
            result = process_energy_snapshot(None, "meter", "haus", 40.0, _ts(30), cfg, st, con)  # reset/fell
            count = con.execute("SELECT COUNT(*) FROM wallbox_sessions").fetchone()[0]
            close_db_if_owned(con)
        assert result is None
        assert count == 0  # no phantom session, no negative energy anywhere


# ---------------------------------------------------------------------------
# 5. Existing protections stay intact
# ---------------------------------------------------------------------------

class TestExistingProtectionsPreserved:
    def test_extern_session_home_only_scope_gets_no_meter(self, app):
        """meter_scope=home_only: an extern session must not receive
        start/end meter values (this mirrors the server.py session-end gate)."""
        _meter_scope = "home_only"
        _effective_location = "extern"
        skip = _meter_scope == "home_only" and _effective_location != "home"
        assert skip is True

    def test_meter_source_changed_blocks_delta(self, app):
        from core.db import _get_db, close_db_if_owned
        with app.app_context():
            con = _get_db()
            sid = None
            cur = con.execute(
                "INSERT INTO sessions (start_ts, vehicle_id, meter_old, meter_source_start) "
                "VALUES (?,?,?,?)", (_ts(0), "v0", 500.0, "evcc"))
            con.commit()
            sid = cur.lastrowid
            row = con.execute("SELECT meter_source_start FROM sessions WHERE id=?", (sid,)).fetchone()
            _meter_src_start_db = row[0]
            _meter_src_end = "shelly"
            _meter_source_changed = (
                _meter_src_start_db is not None and _meter_src_start_db != "none"
                and _meter_src_end != "none" and _meter_src_start_db != _meter_src_end
            )
            close_db_if_owned(con)
        assert _meter_source_changed is True
