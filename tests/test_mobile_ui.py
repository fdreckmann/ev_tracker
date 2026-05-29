"""
Mobile UI smoke tests — verify that known-broken patterns no longer exist in
the frontend source files, and that required helpers are present.
"""

from pathlib import Path

_ROOT = Path(__file__).parent.parent
_INDEX = (_ROOT / "app" / "templates" / "index.html").read_text()
_MOBILE_JS = (_ROOT / "app" / "static" / "js" / "mobile.js").read_text()
_API_JS = (_ROOT / "app" / "static" / "js" / "api.js").read_text()


class TestQuickActions:
    def test_no_show_vehicle_selector(self):
        """showVehicleSelector was removed — replaced with mobileNavTo('vehicles')."""
        assert "showVehicleSelector&&showVehicleSelector" not in _INDEX
        assert "showVehicleSelector&&showVehicleSelector" not in _MOBILE_JS

    def test_no_cfg_section_meter(self):
        """cfgSection('meter') does not exist — old quick-action was wrong."""
        assert "cfgSection('meter')" not in _INDEX
        assert "cfgSection('meter')" not in _MOBILE_JS

    def test_meter_test_quick_action_uses_openMobileMeterTest(self):
        """Quick action 'Zähler testen' must call openMobileMeterTest()."""
        assert "openMobileMeterTest()" in _INDEX

    def test_vehicle_quick_action_uses_mobileNavTo(self):
        """Quick action 'Fahrzeug' must call mobileNavTo('vehicles')."""
        assert "mobileNavTo('vehicles')" in _INDEX

    def test_ladevorgang_quick_action_navigates_to_sessions(self):
        """Quick action 'Ladevorgang' must navigate to sessions tab before opening form."""
        assert "mobileNavTo('sessions')" in _INDEX
        # The button must navigate first, then open the form
        assert "mobileNavTo('sessions');setTimeout(mobileQuickAddSession" in _INDEX


class TestMobileMoreMenu:
    def test_openDesktopConfigSection_defined(self):
        """openDesktopConfigSection must be defined in mobile.js."""
        assert "function openDesktopConfigSection" in _MOBILE_JS

    def test_initMobileMore_uses_native_functions(self):
        """initMobileMore must wire native mobile functions, not broken cfgSection calls."""
        assert "openMobileMeterTest" in _MOBILE_JS
        assert "openMobileConnectionTest" in _MOBILE_JS
        assert "openMobileSignatureSheet" in _MOBILE_JS
        assert "openMobileSystemStatus" in _MOBILE_JS
        assert "openMobileBackupCreate" in _MOBILE_JS

    def test_initMobileMore_vehicles_uses_mobileNavTo(self):
        """Fahrzeugliste in Mehr-Menü must call mobileNavTo('vehicles')."""
        assert "mobileNavTo('vehicles')" in _MOBILE_JS

    def test_openDesktopConfigSection_switches_tab(self):
        """openDesktopConfigSection must invoke tab('config', ...) and cfgSection."""
        assert "tab('config'" in _MOBILE_JS
        assert "cfgSection(sectionId)" in _MOBILE_JS


class TestMissingChargeAccept:
    def test_uses_camelCase_ids(self):
        """mobileMissingChargeAccept must use camelCase form IDs (msStart, msEnd, …)."""
        assert "'msStart'" in _MOBILE_JS
        assert "'msEnd'" in _MOBILE_JS
        assert "'msKwh'" in _MOBILE_JS
        assert "'msSocStart'" in _MOBILE_JS
        assert "'msSocEnd'" in _MOBILE_JS
        assert "'msOdoStart'" in _MOBILE_JS
        assert "'msOdoEnd'" in _MOBILE_JS
        assert "'msLoc'" in _MOBILE_JS
        assert "'msType'" in _MOBILE_JS

    def test_no_snake_case_ids(self):
        """Old snake_case IDs (ms_start, ms_end, …) must not appear in mobile.js."""
        assert "ms_start" not in _MOBILE_JS
        assert "ms_end" not in _MOBILE_JS
        assert "ms_kwh" not in _MOBILE_JS
        assert "ms_soc_start" not in _MOBILE_JS
        assert "ms_location" not in _MOBILE_JS
        assert "ms_charger_type" not in _MOBILE_JS


class TestSessionReload:
    def test_loadMobileSessions_defined(self):
        """loadMobileSessions() must be defined in mobile.js."""
        assert "function loadMobileSessions" in _MOBILE_JS

    def test_submit_uses_loadMobileSessions(self):
        """submitMobileSession must call loadMobileSessions after save."""
        assert "loadMobileSessions" in _INDEX

    def test_edit_location_uses_loadMobileSessions(self):
        """mobileEditLocation must call loadMobileSessions."""
        assert "loadMobileSessions" in _MOBILE_JS


class TestXSS:
    def test_vehicle_detail_escapes_name(self):
        """openMobileVehicleDetail must escape v.name."""
        assert "escapeHtml(v.name" in _INDEX

    def test_vehicle_detail_escapes_provider(self):
        """openMobileVehicleDetail must escape v.provider."""
        assert "escapeHtml(v.provider" in _INDEX

    def test_vehicle_detail_no_raw_vid_in_onclick(self):
        """openEditVehicleModal must not be called with raw '${vid}' in an onclick string."""
        assert "openEditVehicleModal('${vid}')" not in _INDEX

    def test_normalizeLocation_intern(self):
        """normalizeLocation must map 'intern' to home."""
        assert "'intern'" in _API_JS

    def test_normalizeLocation_internal(self):
        """normalizeLocation must map 'internal' to home."""
        assert "'internal'" in _API_JS

    def test_normalizeLocation_zuhause_laden(self):
        """normalizeLocation must map 'zuhause_laden' to home."""
        assert "'zuhause_laden'" in _API_JS

    def test_normalizeLocation_oeffentlich(self):
        """normalizeLocation must map 'öffentlich' to extern."""
        assert "'öffentlich'" in _API_JS


class TestMobileNavPanelHiding:
    def test_setDesktopPanelsVisible_defined(self):
        """setDesktopPanelsVisible must be defined in mobile.js."""
        assert "function setDesktopPanelsVisible" in _MOBILE_JS

    def test_setDesktopPanelsVisible_targets_panel_class(self):
        """setDesktopPanelsVisible must use querySelectorAll('.panel')."""
        assert "querySelectorAll('.panel')" in _MOBILE_JS

    def test_no_broken_mainContent_selector_in_mobile_js(self):
        """mobile.js must not use the defunct getElementById('mainContent') pattern."""
        assert "getElementById('mainContent')" not in _MOBILE_JS

    def test_no_broken_mainContent_selector_in_index(self):
        """index.html must not use getElementById('mainContent') for show/hide."""
        assert "getElementById('mainContent')" not in _INDEX

    def test_mobileNavTo_uses_setDesktopPanelsVisible(self):
        """mobileNavTo must call setDesktopPanelsVisible."""
        assert "setDesktopPanelsVisible" in _MOBILE_JS

    def test_switchToDesktopSettings_uses_setDesktopPanelsVisible(self):
        """switchToDesktopSettings must call setDesktopPanelsVisible(true)."""
        assert "setDesktopPanelsVisible(true)" in _MOBILE_JS
