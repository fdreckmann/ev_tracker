"""
Tests for PR 11 — Fahrzeug-zentriertes Profil-UI.

Covers:
  1. Two-vehicle isolation: saving Car B's meter config must not touch Car A.
  2. home_charge_vehicle_assignment_mode regression: old values must survive
     a round-trip through /api/config.
  3. vehicle_user_assignments table migration: table must exist after init_db.
  4. Permission gate: PUT /api/vehicles/<vid> requires vehicles:edit.
"""
import json
from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# 1 — Two-vehicle isolation
# ---------------------------------------------------------------------------

class TestTwoVehicleIsolation:
    def test_car_b_save_does_not_overwrite_car_a(self, authed_client, app):
        """Saving meter config for Car B must leave Car A's dict unchanged."""
        with app.app_context():
            from core.config import load_config, save_config, _config_cache

            cfg = load_config()
            cfg["extra_vehicles"] = [
                {"id": "car_a", "name": "Car A", "provider": "manual",
                 "meter_source": "shelly", "meter_device_ip": "192.168.1.10",
                 "active": True, "archived": False},
                {"id": "car_b", "name": "Car B", "provider": "manual",
                 "meter_source": "none",
                 "active": True, "archived": False},
            ]
            save_config(cfg)
            _config_cache["data"] = None

        # Save new meter config for Car B only
        payload = {
            "meter_source": "go_e",
            "meter_device_ip": "10.0.0.99",
            "meter_scope": "home_only",
        }
        rv = authed_client.put(
            "/api/vehicles/car_b",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert rv.status_code == 200
        data = rv.get_json()
        assert data.get("ok") is True

        # Car A must be unchanged
        with app.app_context():
            from core.config import load_config, _config_cache
            _config_cache["data"] = None
            cfg2 = load_config()
            vehicles = {v["id"]: v for v in cfg2.get("extra_vehicles", [])}

        assert "car_a" in vehicles
        assert vehicles["car_a"]["meter_source"] == "shelly"
        assert vehicles["car_a"]["meter_device_ip"] == "192.168.1.10"

        # Car B must have the new values
        assert "car_b" in vehicles
        assert vehicles["car_b"]["meter_source"] == "go_e"
        assert vehicles["car_b"]["meter_device_ip"] == "10.0.0.99"


# ---------------------------------------------------------------------------
# 2 — home_charge_vehicle_assignment_mode regression
# ---------------------------------------------------------------------------

class TestAssignmentModeRegression:
    @pytest.mark.parametrize("old_value", [
        "always_ask_if_unclear",
        "default_vehicle_always",
        "always_default_vehicle",
        "default_if_api_confirms",
        "require_rfid_or_api",
    ])
    def test_old_assignment_mode_values_preserved(self, authed_client, app, old_value):
        """Old home_charge_vehicle_assignment_mode values survive a POST round-trip."""
        payload = {"home_charge_vehicle_assignment_mode": old_value}
        rv = authed_client.post(
            "/api/config",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert rv.status_code == 200

        with app.app_context():
            from core.config import load_config, _config_cache
            _config_cache["data"] = None
            cfg = load_config()

        assert cfg.get("home_charge_vehicle_assignment_mode") == old_value

    def test_allow_probable_assignment_saved_independently(self, authed_client, app):
        """home_charge_allow_probable_assignment can be set without touching
        home_charge_vehicle_assignment_mode."""
        # First set an old-style mode
        authed_client.post("/api/config",
                           data=json.dumps({"home_charge_vehicle_assignment_mode": "always_ask_if_unclear"}),
                           content_type="application/json")
        # Now set the new boolean flag
        rv = authed_client.post("/api/config",
                                data=json.dumps({"home_charge_allow_probable_assignment": True}),
                                content_type="application/json")
        assert rv.status_code == 200

        with app.app_context():
            from core.config import load_config, _config_cache
            _config_cache["data"] = None
            cfg = load_config()

        # Old mode must still be there
        assert cfg.get("home_charge_vehicle_assignment_mode") == "always_ask_if_unclear"
        # New flag must be set
        assert cfg.get("home_charge_allow_probable_assignment") is True


# ---------------------------------------------------------------------------
# 3 — vehicle_user_assignments migration
# ---------------------------------------------------------------------------

class TestVehicleUserAssignmentsMigration:
    def test_table_exists_after_init_db(self, app):
        """vehicle_user_assignments table is created by init_db."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            row = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='vehicle_user_assignments'"
            ).fetchone()
            close_db_if_owned(con)
        assert row is not None, "vehicle_user_assignments table missing"

    def test_table_has_expected_columns(self, app):
        """vehicle_user_assignments has id, vehicle_id, user_id, created_at."""
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            con = _get_db()
            cols = {row[1] for row in con.execute(
                "PRAGMA table_info(vehicle_user_assignments)"
            ).fetchall()}
            close_db_if_owned(con)
        assert {"id", "vehicle_id", "user_id", "created_at"}.issubset(cols)


# ---------------------------------------------------------------------------
# 4 — Permission gate
# ---------------------------------------------------------------------------

class TestProfilePermissionGate:
    def _make_limited_client(self, app):
        """Return a test client logged in as a readonly user (no vehicles:edit).

        We assign the 'readonly' role explicitly via user_roles to prevent the
        user-migration code in init_db from upgrading them to the 'user' role.
        """
        client = app.test_client()
        with app.app_context():
            from core.db import _get_db, close_db_if_owned
            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            con = _get_db()
            con.execute("""INSERT OR IGNORE INTO users
                (name, email, password_hash, role, status, totp_secret,
                 totp_enabled, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                ("ReadOnly", "readonly@test.local",
                 "pbkdf2:sha256:1$test$" + "0" * 64,
                 "readonly", "active", "", 0, now, now))
            con.commit()
            uid_row = con.execute(
                "SELECT id FROM users WHERE email='readonly@test.local'"
            ).fetchone()
            uid = uid_row["id"] if uid_row else 99
            # Assign the 'readonly' role explicitly so the migration doesn't
            # re-assign 'user' role (which has vehicles:edit).
            role_row = con.execute(
                "SELECT id FROM roles WHERE name='readonly'"
            ).fetchone()
            if role_row:
                con.execute(
                    "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?,?)",
                    (uid, role_row["id"])
                )
                con.commit()
            close_db_if_owned(con)

        with client.session_transaction() as sess:
            sess["user_id"] = uid
            sess["user_email"] = "readonly@test.local"
            sess["user_role"] = "readonly"
            sess["_fresh"] = True
            sess["csrf_token"] = "test-csrf-token"
        return client, uid

    def test_put_vehicle_without_edit_permission_returns_403(self, app):
        """A user without vehicles:edit cannot PUT /api/vehicles/<vid>."""
        with app.app_context():
            from core.config import load_config, save_config, _config_cache
            cfg = load_config()
            cfg["extra_vehicles"] = [
                {"id": "test_car", "name": "Test", "provider": "manual",
                 "active": True, "archived": False},
            ]
            save_config(cfg)
            _config_cache["data"] = None

        client, _ = self._make_limited_client(app)
        payload = {"meter_source": "go_e"}
        rv = client.put(
            "/api/vehicles/test_car",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        assert rv.status_code == 403

    def test_put_vehicle_v0_without_edit_permission_returns_403(self, app):
        """A user without vehicles:edit cannot PUT /api/vehicles/v0 either."""
        client, _ = self._make_limited_client(app)
        payload = {"car_name": "Hacked Name"}
        rv = client.put(
            "/api/vehicles/v0",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        assert rv.status_code == 403


# ---------------------------------------------------------------------------
# 5 — line_meter threshold keys round-trip via /api/config
# ---------------------------------------------------------------------------

class TestLineMeterConfigKeys:
    def test_line_meter_thresholds_saved_and_loaded(self, authed_client, app):
        """home_charge_line_meter_power_*_threshold_kw round-trip through /api/config."""
        payload = {
            "home_charge_line_meter_power_start_threshold_kw": 1.8,
            "home_charge_line_meter_power_stop_threshold_kw": 0.4,
        }
        rv = authed_client.post(
            "/api/config",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert rv.status_code == 200

        with app.app_context():
            from core.config import load_config, _config_cache
            _config_cache["data"] = None
            cfg = load_config()

        assert cfg["home_charge_line_meter_power_start_threshold_kw"] == 1.8
        assert cfg["home_charge_line_meter_power_stop_threshold_kw"] == 0.4

    def test_line_meter_defaults_preserved_when_not_sent(self, authed_client, app):
        """Default 1.4 / 0.3 are in DEFAULT_CONFIG and returned when not overridden."""
        from core.config import DEFAULT_CONFIG
        assert DEFAULT_CONFIG["home_charge_line_meter_power_start_threshold_kw"] == 1.4
        assert DEFAULT_CONFIG["home_charge_line_meter_power_stop_threshold_kw"] == 0.3


# ---------------------------------------------------------------------------
# 6 — PR 13 regression: vehicle config still round-trips via the profile path
#     after the old cfgsec-zaehler / cfgsec-tarif / cfgsec-verbindung sections
#     were removed.
# ---------------------------------------------------------------------------

class TestProfilePathRegression:
    def _seed_two_vehicles(self, app):
        """Create Car A and Car B with distinct provider/wallbox/tariff values."""
        with app.app_context():
            from core.config import load_config, save_config, _config_cache
            cfg = load_config()
            cfg["extra_vehicles"] = [
                {"id": "car_a", "name": "Car A", "provider": "manual",
                 "home_charger_power_kw": 11.0,
                 "tariff_provider": "fixed", "tariff_price_home": 0.30,
                 "active": True, "archived": False},
                {"id": "car_b", "name": "Car B", "provider": "manual",
                 "home_charger_power_kw": 22.0,
                 "tariff_provider": "fixed", "tariff_price_home": 0.40,
                 "active": True, "archived": False},
            ]
            save_config(cfg)
            _config_cache["data"] = None

    def _load_vehicles(self, app):
        with app.app_context():
            from core.config import load_config, _config_cache
            _config_cache["data"] = None
            cfg = load_config()
            return {v["id"]: v for v in cfg.get("extra_vehicles", [])}

    def test_provider_wallbox_tariff_roundtrip_via_profile(self, authed_client, app):
        """Provider, wallbox and tariff values saved via the profile path
        (PUT /api/vehicles/<vid>) are read back identically."""
        self._seed_two_vehicles(app)
        payload = {
            "provider": "ha",
            "home_charger_power_kw": 7.4,
            "tariff_provider": "tibber",
            "tariff_price_home": 0.28,
            "tariff_fallback_price": 0.35,
        }
        rv = authed_client.put(
            "/api/vehicles/car_a",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert rv.status_code == 200
        assert rv.get_json().get("ok") is True

        vehicles = self._load_vehicles(app)
        a = vehicles["car_a"]
        assert a["provider"] == "ha"
        assert a["home_charger_power_kw"] == 7.4
        assert a["tariff_provider"] == "tibber"
        assert a["tariff_price_home"] == 0.28
        assert a["tariff_fallback_price"] == 0.35

    def test_saving_car_b_does_not_change_car_a(self, authed_client, app):
        """Saving provider/wallbox/tariff for Car B must leave Car A untouched."""
        self._seed_two_vehicles(app)
        payload = {
            "provider": "ha",
            "home_charger_power_kw": 4.6,
            "tariff_provider": "octopus",
            "tariff_price_home": 0.19,
        }
        rv = authed_client.put(
            "/api/vehicles/car_b",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert rv.status_code == 200

        vehicles = self._load_vehicles(app)
        # Car A keeps its seeded values
        a = vehicles["car_a"]
        assert a["provider"] == "manual"
        assert a["home_charger_power_kw"] == 11.0
        assert a["tariff_provider"] == "fixed"
        assert a["tariff_price_home"] == 0.30
        # Car B has the new values
        b = vehicles["car_b"]
        assert b["provider"] == "ha"
        assert b["home_charger_power_kw"] == 4.6
        assert b["tariff_provider"] == "octopus"
        assert b["tariff_price_home"] == 0.19


# ---------------------------------------------------------------------------
# 7 — PR 13: no duplicate HTML element IDs in the rendered page, and the
#     vehicle-specific profile fields each appear exactly once.
# ---------------------------------------------------------------------------

class TestNoDuplicateHtmlIds:
    import re as _re
    _ID_RE = _re.compile(r'id="([^"${}\'+`]+)"')

    def _render(self, authed_client):
        rv = authed_client.get("/")
        assert rv.status_code == 200
        return rv.get_data(as_text=True)

    def _static_ids(self, html):
        """All concrete element IDs, ignoring JS/template-dynamic ones."""
        return self._ID_RE.findall(html)

    def test_no_duplicate_ids_in_rendered_page(self, authed_client):
        from collections import Counter
        html = self._render(authed_client)
        counts = Counter(self._static_ids(html))
        dupes = {k: n for k, n in counts.items() if n > 1}
        assert not dupes, f"Duplicate HTML IDs in rendered page: {dupes}"

    def test_vehicle_profile_fields_unique(self, authed_client):
        from collections import Counter
        html = self._render(authed_client)
        counts = Counter(self._static_ids(html))
        # Vehicle-specific profile fields live only in the profile tabs now.
        for prefix in ("vp_z_", "vp_h_"):
            offenders = {k: n for k, n in counts.items()
                         if k.startswith(prefix) and n > 1}
            assert not offenders, f"Duplicate {prefix}* IDs: {offenders}"

    def test_old_global_vehicle_ids_removed(self, authed_client):
        """The old duplicated global IDs (c_hc_*, c_meter_source, tariff_*)
        no longer exist after PR 13 — vehicle config lives in the profile."""
        html = self._render(authed_client)
        ids = set(self._static_ids(html))
        assert not any(i.startswith("c_hc_") for i in ids)
        assert "c_meter_source" not in ids
        assert not any(i.startswith("tariff_") for i in ids)


# ---------------------------------------------------------------------------
# 8 — PR 13: global API overview section renders with the providers table.
# ---------------------------------------------------------------------------

class TestGlobalStatusSection:
    def test_fahrzeug_status_section_renders(self, authed_client):
        rv = authed_client.get("/")
        assert rv.status_code == 200
        html = rv.get_data(as_text=True)
        assert 'id="cfgsec-fahrzeug-status"' in html
        # Nav button points at the new section
        assert "cfgSection('fahrzeug-status'" in html
        # The old section id is gone
        assert 'id="cfgsec-verbindung"' not in html

    def test_providers_table_present(self, authed_client):
        rv = authed_client.get("/")
        html = rv.get_data(as_text=True)
        # The all_providers loop renders the API capability table headers
        assert "API-Funktionsübersicht" in html
        assert "<th>Provider</th>" in html
        assert "<th>Ladestatus</th>" in html

