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


# ---------------------------------------------------------------------------
# 9 — PR-UI-2: all fields from the old Bearbeiten-Modal are reachable via the
#     profile path (PUT /api/vehicles/<vid>).  No field loss.
# ---------------------------------------------------------------------------

class TestPrUi2ProfileFieldRoundtrip:
    """Round-trip: every field the old modal saved is now saved/loaded via the
    profile path (vprofilSaveVerbindung + vprofilSaveHeimladung =
    PUT /api/vehicles/<vid>)."""

    def _seed(self, app, vid="test_rt"):
        with app.app_context():
            from core.config import load_config, save_config, _config_cache
            cfg = load_config()
            cfg["extra_vehicles"] = [
                {"id": vid, "name": "Round-trip Car", "provider": "manual",
                 "active": True, "archived": False},
            ]
            save_config(cfg)
            _config_cache["data"] = None

    def _load(self, app, vid="test_rt"):
        with app.app_context():
            from core.config import load_config, _config_cache
            _config_cache["data"] = None
            cfg = load_config()
            for v in cfg.get("extra_vehicles", []):
                if v["id"] == vid:
                    return v
        return {}

    def test_name_battery_poll_roundtrip(self, authed_client, app):
        """name, battery_capacity_kwh, poll_interval survive PUT round-trip."""
        self._seed(app)
        rv = authed_client.put(
            "/api/vehicles/test_rt",
            data=json.dumps({
                "name": "My EV",
                "battery_capacity_kwh": 82.0,
                "poll_interval": 45,
            }),
            content_type="application/json",
        )
        assert rv.status_code == 200
        assert rv.get_json().get("ok") is True

        v = self._load(app)
        assert v["name"] == "My EV"
        assert v["battery_capacity_kwh"] == 82.0
        assert v["poll_interval"] == 45

    def test_home_coords_roundtrip(self, authed_client, app):
        """home_lat and home_lon survive PUT round-trip."""
        self._seed(app)
        rv = authed_client.put(
            "/api/vehicles/test_rt",
            data=json.dumps({"home_lat": "51.5074", "home_lon": "7.4653"}),
            content_type="application/json",
        )
        assert rv.status_code == 200
        v = self._load(app)
        assert v["home_lat"] == "51.5074"
        assert v["home_lon"] == "7.4653"

    def test_location_fields_roundtrip(self, authed_client, app):
        """All location_* fields survive a PUT round-trip."""
        self._seed(app)
        payload = {
            "location_enabled": True,
            "location_mode": "exact",
            "location_source": "provider",
            "home_detection_mode": "all",
            "home_radius_m": 250,
            "location_ha_entities": ["device_tracker.car_a", "device_tracker.car_b"],
            "location_history_enabled": True,
        }
        rv = authed_client.put(
            "/api/vehicles/test_rt",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert rv.status_code == 200
        assert rv.get_json().get("ok") is True

        v = self._load(app)
        assert v["location_enabled"] is True
        assert v["location_mode"] == "exact"
        assert v["location_source"] == "provider"
        assert v["home_detection_mode"] == "all"
        assert v["home_radius_m"] == 250
        assert v["location_ha_entities"] == ["device_tracker.car_a", "device_tracker.car_b"]
        assert v["location_history_enabled"] is True

    def test_two_vehicle_isolation_with_new_fields(self, authed_client, app):
        """Saving home/location fields for Car B must not touch Car A."""
        with app.app_context():
            from core.config import load_config, save_config, _config_cache
            cfg = load_config()
            cfg["extra_vehicles"] = [
                {"id": "iso_a", "name": "Car A", "provider": "manual",
                 "home_lat": "48.1351", "home_lon": "11.5820",
                 "location_enabled": False, "location_mode": "home_external",
                 "home_radius_m": 100,
                 "active": True, "archived": False},
                {"id": "iso_b", "name": "Car B", "provider": "manual",
                 "active": True, "archived": False},
            ]
            save_config(cfg)
            _config_cache["data"] = None

        rv = authed_client.put(
            "/api/vehicles/iso_b",
            data=json.dumps({
                "home_lat": "52.5200", "home_lon": "13.4050",
                "location_enabled": True, "home_radius_m": 300,
            }),
            content_type="application/json",
        )
        assert rv.status_code == 200

        with app.app_context():
            from core.config import load_config, _config_cache
            _config_cache["data"] = None
            vehicles = {v["id"]: v for v in load_config().get("extra_vehicles", [])}

        a = vehicles["iso_a"]
        assert a["home_lat"] == "48.1351"
        assert a["home_lon"] == "11.5820"
        assert a["location_enabled"] is False
        assert a["home_radius_m"] == 100

        b = vehicles["iso_b"]
        assert b["home_lat"] == "52.5200"
        assert b["location_enabled"] is True
        assert b["home_radius_m"] == 300

    def test_create_vehicle_and_load_in_profile(self, authed_client, app):
        """POST /api/vehicles with name+provider creates a vehicle; it is
        then retrievable via GET /api/vehicles."""
        rv = authed_client.post(
            "/api/vehicles",
            data=json.dumps({"name": "New Car", "provider": "ha"}),
            content_type="application/json",
        )
        assert rv.status_code in (200, 201)
        data = rv.get_json()
        assert data.get("ok") is True
        new_id = data.get("id") or data.get("vehicle_id")
        assert new_id is not None

        rv2 = authed_client.get("/api/vehicles")
        vehicles = rv2.get_json()
        ids = [v["id"] for v in vehicles]
        assert new_id in ids

    def test_image_routes_reachable(self, authed_client, app):
        """Image upload and delete endpoints return non-404 (smoke test)."""
        with app.app_context():
            from core.config import load_config, save_config, _config_cache
            cfg = load_config()
            cfg["extra_vehicles"] = [
                {"id": "img_car", "name": "Image Car", "provider": "manual",
                 "active": True, "archived": False},
            ]
            save_config(cfg)
            _config_cache["data"] = None

        # DELETE should return ok or a meaningful error, never 404 for the route
        rv = authed_client.delete("/api/vehicles/img_car/image")
        assert rv.status_code != 404

    def test_old_modal_location_ids_absent_from_page(self, authed_client):
        """vm_home_lat, vm_home_lon, vm_loc_* must not exist as element IDs
        in the rendered page (they were removed from the modal in PR-UI-2)."""
        rv = authed_client.get("/")
        assert rv.status_code == 200
        html = rv.get_data(as_text=True)
        import re
        ids = set(re.findall(r'id="([^"${}\'+`]+)"', html))
        assert "vm_home_lat" not in ids, "vm_home_lat still in page"
        assert "vm_home_lon" not in ids, "vm_home_lon still in page"
        assert not any(i.startswith("vm_loc_") for i in ids), \
            f"vm_loc_* IDs still in page: {[i for i in ids if i.startswith('vm_loc_')]}"

    def test_profile_fields_present_in_page(self, authed_client):
        """New profile-tab IDs for name, image, location must exist in the rendered page."""
        rv = authed_client.get("/")
        html = rv.get_data(as_text=True)
        assert 'id="vprofil_name"' in html
        assert 'id="vmImageSection"' in html
        assert 'id="vp_h_home_lat"' in html
        assert 'id="vp_h_home_lon"' in html
        assert 'id="vp_h_loc_enabled"' in html
        assert 'id="vp_h_loc_mode"' in html
        assert 'id="vp_h_loc_ha_entities"' in html
        assert 'id="vp_h_loc_history_enabled"' in html


# ---------------------------------------------------------------------------
# 10 — Bugfix PR: Provider-Karten bleiben leer (ReferenceError _eh + silent catch)
# ---------------------------------------------------------------------------

class TestProviderRenderFix:
    """Backend routes are functional and the HTML wiring for the provider
    section is correct (no duplicate IDs, required containers present)."""

    def test_api_providers_returns_array_with_ha(self, authed_client):
        """/api/providers returns a JSON list that includes provider_id 'ha'."""
        rv = authed_client.get('/api/providers')
        assert rv.status_code == 200
        data = rv.get_json()
        assert isinstance(data, list), "Expected list from /api/providers"
        ids = [p.get('provider_id') for p in data]
        assert 'ha' in ids, f"'ha' missing from providers: {ids[:5]}"

    def test_api_providers_ha_fields_contains_required_fields(self, authed_client):
        """/api/providers/ha/fields returns ha_url, ha_token, and sensor fields."""
        rv = authed_client.get('/api/providers/ha/fields')
        assert rv.status_code == 200
        fields = rv.get_json()
        assert isinstance(fields, list)
        field_ids = [f.get('id') for f in fields]
        for expected in ('ha_url', 'ha_token', 'charging_sensor', 'soc_sensor', 'odo_sensor'):
            assert expected in field_ids, f"'{expected}' missing from HA fields: {field_ids}"

    def test_provider_containers_exist_exactly_once(self, authed_client):
        """vprofil_providerGrid, vprofil_provider, vprofil_providerFields,
        vprofil_connRes each appear exactly once in the rendered page."""
        import re
        rv = authed_client.get('/')
        assert rv.status_code == 200
        html = rv.get_data(as_text=True)
        ids = re.findall(r'id="([^"${}\'`+]+)"', html)
        from collections import Counter
        counts = Counter(ids)
        for required in ('vprofil_providerGrid', 'vprofil_provider',
                         'vprofil_providerFields', 'vprofil_connRes'):
            assert counts[required] == 1, \
                f"'{required}' appears {counts[required]}x (expected exactly 1)"

    def test_no_silent_catch_in_provider_load_functions(self, authed_client):
        """_vprofilLoadVerbindung and _vprofilLoadProviderFields must not use
        the silent .catch(()=>[]) / .catch(function(){return [];}) pattern."""
        rv = authed_client.get('/')
        html = rv.get_data(as_text=True)
        # Locate the two functions in the rendered HTML
        import re
        # Extract the block between _vprofilLoadVerbindung and vprofilSaveVerbindung
        block = re.search(
            r'async function _vprofilLoadVerbindung.*?async function vprofilSaveVerbindung',
            html, re.DOTALL)
        assert block, "_vprofilLoadVerbindung not found in rendered HTML"
        block_text = block.group(0)
        # The silent catch pattern must not appear in these functions
        silent = re.search(r'\.catch\(function\(\)\s*\{\s*return \[\]', block_text)
        assert not silent, "Silent .catch(()=>[]) still present in provider load functions"

    def test_vprofil_fields_use_vpf_prefix_not_vmf(self, authed_client):
        """Provider fields in the profile must use vpf_ prefix; vmf_ belongs
        to the add-modal and must NOT appear in _vprofilLoadProviderFields."""
        rv = authed_client.get('/')
        html = rv.get_data(as_text=True)
        import re
        block = re.search(
            r'async function _vprofilLoadProviderFields.*?_vproviderFieldsLoaded = true;\s*\}',
            html, re.DOTALL)
        assert block, "_vprofilLoadProviderFields not found in rendered HTML"
        block_text = block.group(0)
        assert 'vpf_' in block_text, "vpf_ prefix not found in _vprofilLoadProviderFields"
        assert 'vmf_' not in block_text, \
            "vmf_ (add-modal prefix) leaked into _vprofilLoadProviderFields"

    def test_vprofil_connres_display_set_on_test(self, authed_client):
        """vprofilTestConn must set style.display='block' so the result is
        visible (conn-res CSS class uses display:none by default)."""
        rv = authed_client.get('/')
        html = rv.get_data(as_text=True)
        # Find the function start, then extract ~3000 chars as its body
        idx = html.find('async function vprofilTestConn()')
        assert idx >= 0, "vprofilTestConn not found in rendered HTML"
        body = html[idx:idx+3000]
        assert "style.display = 'block'" in body, \
            "vprofilTestConn does not set resBox.style.display='block'"

    def test_vprofilsaveverbindung_guards_on_fields_not_loaded(self, authed_client):
        """vprofilSaveVerbindung must check _vproviderFieldsLoaded and abort
        when fields were never successfully loaded."""
        rv = authed_client.get('/')
        html = rv.get_data(as_text=True)
        idx = html.find('async function vprofilSaveVerbindung()')
        assert idx >= 0, "vprofilSaveVerbindung not found"
        body = html[idx:idx+2000]
        assert '_vproviderFieldsLoaded' in body, \
            "vprofilSaveVerbindung does not check _vproviderFieldsLoaded"
