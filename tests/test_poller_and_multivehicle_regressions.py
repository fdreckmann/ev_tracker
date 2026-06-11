"""
Regressionstests — Poller-/Multi-Fahrzeug-Bugfixes (Deep-Audit 2026-06).

Abgedeckte Bugs:
  1. P0: `con` wurde im tracker_loop benutzt bevor es erzeugt wurde
     (maybe_record_poll_snapshot / ChargingStateMachine liefen nie).
  2. P1: resolve_session_price / resolve_public_charging_price bekamen
     globales cfg statt fahrzeugspezifischem vcfg.
  3. P2: request.json (Flask >=2.1: 415 ohne Content-Type-Header) in Routen.
  4. P2: /api/status lieferte bei unbekannter vehicle_id stillschweigend
     den Status von v0.
  5. P2: /api/missing-charges/check nutzte globales cfg statt vcfg.
"""
import json
import re

import pytest


def _server_source():
    import os
    here = os.path.dirname(__file__)
    path = os.path.normpath(os.path.join(here, "..", "app", "server.py"))
    with open(path) as fh:
        return fh.read()


def _tracker_loop_body(src: str) -> str:
    """Extract the tracker_loop function body from server.py source."""
    start = src.find("def tracker_loop(")
    assert start >= 0, "tracker_loop not found in server.py"
    # Next top-level def ends the function
    end = src.find("\ndef ", start + 1)
    return src[start:end if end > 0 else len(src)]


# ---------------------------------------------------------------------------
# 1 — P0: con muss VOR der Home-Charging-Erkennung erzeugt werden
# ---------------------------------------------------------------------------

class TestTrackerLoopConLifecycle:
    def test_con_created_before_meter_snapshot(self):
        body = _tracker_loop_body(_server_source())
        create_pos = body.find("con = sqlite3.connect(DB_PATH)")
        use_pos = body.find("maybe_record_poll_snapshot(vehicle_id, vcfg, st, con)")
        assert create_pos >= 0, "con creation not found in tracker_loop"
        assert use_pos >= 0, "maybe_record_poll_snapshot call not found"
        assert create_pos < use_pos, (
            "P0 regression: con is created AFTER maybe_record_poll_snapshot "
            f"(create@{create_pos}, use@{use_pos})"
        )

    def test_con_created_before_charging_state_machine(self):
        body = _tracker_loop_body(_server_source())
        create_pos = body.find("con = sqlite3.connect(DB_PATH)")
        use_pos = body.find("ChargingStateMachine(vcfg, st, con)")
        assert use_pos >= 0, "ChargingStateMachine call not found"
        assert create_pos < use_pos, (
            "P0 regression: con is created AFTER ChargingStateMachine.ingest"
        )

    def test_con_initialized_none_before_try(self):
        """con = None vor dem try-Block, damit der Outer-Except-Handler
        die Connection gefahrlos schließen kann."""
        body = _tracker_loop_body(_server_source())
        assert "con = None" in body, \
            "con = None initialization missing in tracker_loop"
        none_pos = body.find("con = None")
        create_pos = body.find("con = sqlite3.connect(DB_PATH)")
        assert none_pos < create_pos, "con = None must precede the connect call"

    def test_state_error_path_closes_con(self):
        """Der state.error-continue-Pfad muss die Connection schließen."""
        body = _tracker_loop_body(_server_source())
        m = re.search(
            r"if state\.error:.*?continue", body, re.DOTALL)
        assert m, "state.error block not found"
        assert "close_db_if_owned(con)" in m.group(0), \
            "state.error continue path does not close con (connection leak)"

    def test_outer_except_closes_con(self):
        """Der äußere Exception-Handler muss con schließen, wenn vorhanden."""
        body = _tracker_loop_body(_server_source())
        # Find the outer handler (logs 'Tracker error')
        idx = body.find('"Tracker error [%s]: %s"')
        assert idx >= 0, "outer tracker exception handler not found"
        tail = body[idx:idx + 800]
        assert "close_db_if_owned(con)" in tail, \
            "outer exception handler does not close con"

    def test_no_local_timezone_import_in_tracker_loop(self):
        """P0: Ein lokales `from datetime import ..., timezone` innerhalb von
        tracker_loop macht `timezone` zur lokalen Variable für die GESAMTE
        Funktion und schattiert den Modul-Import → UnboundLocalError im
        Charging-Intelligence-Block bei jedem Poll."""
        body = _tracker_loop_body(_server_source())
        local_tz_imports = [
            line.strip() for line in body.splitlines()
            if re.search(r"from datetime import.*\btimezone\b", line)
        ]
        assert not local_tz_imports, (
            "Local timezone import inside tracker_loop shadows the module-level "
            f"import: {local_tz_imports}"
        )

    def test_intelligence_errors_logged_at_warning(self):
        """Fehler der Home-Charging-Erkennung dürfen nicht auf debug-Level
        versteckt werden (so blieb der P0 monatelang unsichtbar)."""
        body = _tracker_loop_body(_server_source())
        assert 'log.warning("Meter snapshot error' in body, \
            "Meter snapshot errors not logged at warning level"
        assert 'log.warning("Charging intelligence error' in body, \
            "Charging intelligence errors not logged at warning level"

    def test_charging_intelligence_functional_with_real_con(self, app, tmp_path):
        """Funktional: Eine ChargingStateMachine-Ingest mit echter Connection
        und steigender Wallbox-Leistung erzeugt eine wallbox_session —
        genau der Pfad, der durch den con-Bug tot war."""
        import sqlite3 as _sq
        with app.app_context():
            from core.db import DB_PATH as _dbp
            from services.charging_state_machine import (
                ChargingStateMachine, SignalBundle, classify_meter_kind,
            )
            cfg = {
                "home_charge_detection_enabled": True,
                "home_charge_power_start_threshold_kw": 1.0,
                "home_charge_power_stop_threshold_kw": 0.2,
                "home_charge_start_debounce_seconds": 10,
                "home_charge_stop_debounce_seconds": 10,
                "home_charge_min_energy_kwh": 0.0,
                "home_charge_vehicle_assignment_mode": "always_ask_if_unclear",
                "home_charge_allow_probable_assignment": False,
                "goe_rfid_enabled": False,
                "meter_source": "goe",
            }
            st = {}
            con = _sq.connect(_dbp)
            try:
                def _bundle(ts):
                    return SignalBundle(
                        ts=ts, vehicle_id="v0",
                        api_available=False, api_charging=None, api_soc=None,
                        api_soc_rising=None, api_location=None, api_power_kw=None,
                        meter_power_kw=7.4, meter_energy_total_kwh=None,
                        meter_source="goe",
                        meter_kind=classify_meter_kind("goe", None),
                    )
                # Zwei Polls: erster startet Debounce, zweiter (nach Fenster)
                # öffnet die Session. Genau dieser Pfad war durch den con-Bug tot.
                sm = ChargingStateMachine(cfg, st, con)
                sm.ingest(_bundle("2026-06-10T10:00:00"))
                ChargingStateMachine(cfg, st, con).ingest(_bundle("2026-06-10T10:00:15"))
                rows = con.execute(
                    "SELECT COUNT(*) FROM wallbox_sessions WHERE status='active'"
                ).fetchone()
                assert rows[0] >= 1, \
                    "ChargingStateMachine.ingest did not open a wallbox session"
            finally:
                con.close()


# ---------------------------------------------------------------------------
# 2 — P1: Pricing muss vcfg (fahrzeugspezifisch) bekommen, nicht cfg
# ---------------------------------------------------------------------------

class TestTrackerLoopUsesVcfg:
    def test_resolve_session_price_gets_vcfg(self):
        body = _tracker_loop_body(_server_source())
        assert "resolve_session_price(_effective_location, charger_type, vcfg, con)" in body, \
            "resolve_session_price at session start does not receive vcfg"
        assert "resolve_session_price(_effective_location, charger_type, cfg, con)" not in body, \
            "cfg/vcfg regression: resolve_session_price called with global cfg"

    def test_resolve_public_charging_price_gets_vcfg(self):
        body = _tracker_loop_body(_server_source())
        assert "resolve_public_charging_price(session_id, charger_type, vcfg, con)" in body, \
            "resolve_public_charging_price does not receive vcfg"
        assert "resolve_public_charging_price(session_id, charger_type, cfg, con)" not in body, \
            "cfg/vcfg regression: resolve_public_charging_price called with global cfg"

    def test_no_global_cfg_home_price_in_session_paths(self):
        """Heimtarif-Fallbacks im Session-Pfad müssen aus vcfg kommen.
        Negative-Lookbehind, damit vcfg.get(...) nicht als cfg.get(...) zählt."""
        body = _tracker_loop_body(_server_source())
        bad = re.findall(r'(?<![a-zA-Z_])cfg(?:\.get\(|\[)"price_per_kwh_home"', body)
        assert not bad, \
            f'global cfg price_per_kwh_home still used in tracker_loop: {bad}'

    def test_calc_extern_price_gets_vcfg(self):
        body = _tracker_loop_body(_server_source())
        assert "calc_extern_price(cfg," not in body, \
            "calc_extern_price called with global cfg in tracker_loop"


# ---------------------------------------------------------------------------
# 3 — P2: Kein request.json mehr (415 ohne Content-Type in Flask >=2.1)
# ---------------------------------------------------------------------------

class TestNoRawRequestJson:
    def test_no_request_json_property_in_routes(self):
        """request.json wirft in Flask 3.x 415 wenn der Content-Type-Header
        fehlt — alle Routen müssen get_json(force/silent) verwenden."""
        import os
        here = os.path.dirname(__file__)
        routes_dir = os.path.normpath(os.path.join(here, "..", "app", "routes"))
        offenders = []
        for fname in os.listdir(routes_dir):
            if not fname.endswith(".py"):
                continue
            with open(os.path.join(routes_dir, fname)) as fh:
                src = fh.read()
            for i, line in enumerate(src.splitlines(), 1):
                if re.search(r"request\.json\b", line):
                    offenders.append(f"{fname}:{i}: {line.strip()}")
        assert not offenders, \
            "request.json property still used (415 without Content-Type):\n" + "\n".join(offenders)

    def test_config_save_works_without_content_type_header(self, authed_client, app):
        """POST /api/config mit JSON-Body aber ohne Content-Type muss
        funktionieren (kein 415, Wert wird gespeichert)."""
        with app.app_context():
            from core.config import _config_cache
            _config_cache["data"] = None
        rv = authed_client.post(
            "/api/config",
            data=json.dumps({"price_per_kwh_home": 0.42}),
            # Content-Type bewusst weggelassen
        )
        assert rv.status_code == 200, rv.get_data(as_text=True)
        with app.app_context():
            from core.config import load_config, _config_cache
            _config_cache["data"] = None
            assert load_config().get("price_per_kwh_home") == 0.42

    def test_missing_charges_check_works_without_body(self, authed_client):
        """POST /api/missing-charges/check ganz ohne Body darf nicht 415/400."""
        rv = authed_client.post("/api/missing-charges/check")
        assert rv.status_code == 200, rv.get_data(as_text=True)
        data = rv.get_json()
        assert data.get("ok") is True


# ---------------------------------------------------------------------------
# 4 — P2: /api/status leakt nicht v0-Status für unbekannte vehicle_id
# ---------------------------------------------------------------------------

class TestStatusVehicleIdIsolation:
    def test_unknown_vehicle_id_does_not_return_v0_state(self, authed_client):
        from core.state import vehicle_states
        marker = {"charging": True, "soc_current": 77,
                  "running": True, "tracker_alive": True,
                  "name": "V0Marker"}
        old = vehicle_states.get("v0")
        vehicle_states["v0"] = dict(marker)
        try:
            rv = authed_client.get("/api/status?vehicle_id=v_does_not_exist")
            assert rv.status_code == 200
            data = rv.get_json()
            assert data.get("charging") is not True, \
                "/api/status leaked v0 state for unknown vehicle_id"
            assert data.get("soc_current") != 77, \
                "/api/status leaked v0 soc for unknown vehicle_id"
        finally:
            if old is None:
                vehicle_states.pop("v0", None)
            else:
                vehicle_states["v0"] = old

    def test_v0_status_still_works(self, authed_client):
        rv = authed_client.get("/api/status?vehicle_id=v0")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "tracker_status" in data
        assert "all_vehicles" in data


# ---------------------------------------------------------------------------
# 5 — P2: /api/missing-charges/check baut vcfg für Extra-Fahrzeuge
# ---------------------------------------------------------------------------

class TestMissingChargesVcfg:
    def test_route_builds_vehicle_config_for_extras(self):
        import os
        here = os.path.dirname(__file__)
        path = os.path.normpath(os.path.join(
            here, "..", "app", "routes", "missing_charges.py"))
        with open(path) as fh:
            src = fh.read()
        idx = src.find("def api_trigger_check")
        assert idx >= 0
        body = src[idx:idx + 1500]
        assert "build_vehicle_config" in body, \
            "/api/missing-charges/check does not build vehicle-specific config"

    def test_check_for_extra_vehicle_returns_ok(self, authed_client, app):
        with app.app_context():
            from core.config import load_config, save_config, _config_cache
            cfg = load_config()
            cfg["extra_vehicles"] = [
                {"id": "mc_extra", "name": "MC Extra", "provider": "manual",
                 "battery_capacity_kwh": 58.0, "active": True, "archived": False},
            ]
            save_config(cfg)
            _config_cache["data"] = None
        rv = authed_client.post(
            "/api/missing-charges/check",
            data=json.dumps({"vehicle_id": "mc_extra"}),
            content_type="application/json",
        )
        assert rv.status_code == 200, rv.get_data(as_text=True)
        assert rv.get_json().get("ok") is True
