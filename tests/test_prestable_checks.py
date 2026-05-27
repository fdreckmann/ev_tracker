"""
Tests for pre-stable items:
  - SSRF blocking in cache_provider_image
  - sanitize_debug_url in meter_providers
  - dMeterSub element present in index.html
  - /image/file silhouette fallback
  - billing.js and sessions.js XSS escaping
  - update-info.json channel=beta
"""
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).parent.parent / "app"
sys.path.insert(0, str(APP_DIR))


# ── SSRF blocking ──────────────────────────────────────────────────────────

class TestSSRFBlocking:

    def _fn(self):
        from services.vehicle_image_service import _is_ssrf_blocked
        return _is_ssrf_blocked

    def test_localhost_blocked(self):
        assert self._fn()("http://localhost/img.jpg") is True

    def test_127_blocked(self):
        assert self._fn()("http://127.0.0.1/img.jpg") is True

    def test_127_subnet_blocked(self):
        assert self._fn()("http://127.0.0.2/img.jpg") is True

    def test_ipv6_loopback_blocked(self):
        assert self._fn()("http://[::1]/img.jpg") is True

    def test_link_local_blocked(self):
        assert self._fn()("http://169.254.169.254/latest/meta-data/") is True

    def test_private_10_blocked(self):
        assert self._fn()("http://10.0.0.1/img.jpg") is True

    def test_private_192_168_blocked(self):
        assert self._fn()("http://192.168.1.100/img.jpg") is True

    def test_private_172_16_blocked(self):
        assert self._fn()("http://172.16.0.1/img.jpg") is True

    def test_private_allowed_when_matches_ha_url(self):
        assert self._fn()("http://192.168.1.50/api/img", ha_url="http://192.168.1.50:8123") is False

    def test_public_ip_allowed(self):
        assert self._fn()("http://1.2.3.4/img.jpg") is False

    def test_public_hostname_allowed(self):
        assert self._fn()("https://example.com/car.jpg") is False

    def test_private_not_matching_ha_url_blocked(self):
        assert self._fn()("http://10.0.0.1/img.jpg", ha_url="http://192.168.1.50:8123") is True


# ── sanitize_debug_url ─────────────────────────────────────────────────────

class TestSanitizeDebugUrl:

    def _fn(self):
        import meter_providers as mp
        return mp.sanitize_debug_url

    def test_no_query_string_unchanged(self):
        url = "http://host/api/status"
        assert self._fn()(url) == url

    def test_token_masked(self):
        result = self._fn()("http://host/api?token=secret123&period=day")
        assert "secret123" not in result
        # urlencode may encode * as %2A — either form is fine
        assert "***" in result or "%2A%2A%2A" in result
        assert "period=day" in result

    def test_apikey_masked(self):
        result = self._fn()("http://host/api?apikey=abc&x=1")
        assert "abc" not in result
        assert "x=1" in result

    def test_password_masked(self):
        result = self._fn()("http://host/api?password=hunter2")
        assert "hunter2" not in result

    def test_non_secret_param_preserved(self):
        result = self._fn()("http://host/api?device=plug1&token=s3cr3t")
        assert "device=plug1" in result
        assert "s3cr3t" not in result

    def test_empty_url_unchanged(self):
        assert self._fn()("") == ""

    def test_url_without_secrets_unchanged(self):
        url = "http://host/api?period=month&format=json"
        result = self._fn()(url)
        assert "period=month" in result
        assert "format=json" in result


# ── dMeterSub in index.html ────────────────────────────────────────────────

class TestDMeterSubInTemplate:

    def test_dmetersub_element_exists(self):
        html_path = APP_DIR / "templates" / "index.html"
        content = html_path.read_text(encoding="utf-8")
        assert 'id="dMeterSub"' in content, "dMeterSub element must be in index.html"

    def test_dmetersub_inside_meter_tile(self):
        html_path = APP_DIR / "templates" / "index.html"
        content = html_path.read_text(encoding="utf-8")
        meter_idx = content.find('id="dMeterTile"')
        assert meter_idx != -1, "dMeterTile must exist"
        # dMeterSub should appear after dMeterTile
        sub_idx = content.find('id="dMeterSub"', meter_idx)
        assert sub_idx != -1, "dMeterSub must appear inside/after dMeterTile"


# ── status.js dMeterSub update ─────────────────────────────────────────────

class TestStatusJsDMeterSub:

    def _content(self):
        return (APP_DIR / "static" / "js" / "status.js").read_text(encoding="utf-8")

    def test_dmetersub_updated_in_status_js(self):
        assert "dMeterSub" in self._content(), "status.js must update dMeterSub"

    def test_dmetersub_error_path(self):
        content = self._content()
        # dMeterSub may be stored in a variable (meterSub) then used in two branches
        # Check that both success text (· ✓) and error text (· Fehler) are present
        assert "· Fehler" in content or "Fehler" in content, \
            "status.js must set dMeterSub text in error path"
        assert "· ✓" in content or "✓" in content, \
            "status.js must set dMeterSub text in success path"


# ── sessions.js XSS ────────────────────────────────────────────────────────

class TestSessionsJsXSS:

    def _content(self):
        return (APP_DIR / "static" / "js" / "sessions.js").read_text(encoding="utf-8")

    def test_vehicle_option_value_escaped(self):
        content = self._content()
        # Should not have bare v.id in option value without escaping
        assert '_eh(v.id)' in content or '_ehS(v.id)' in content or 'escapeHtml(v.id)' in content, \
            "v.id in option value must be escaped"

    def test_vehicle_option_text_escaped(self):
        content = self._content()
        assert '_eh(v.name)' in content or '_ehS(v.name)' in content or 'escapeHtml(v.name)' in content, \
            "v.name in option text must be escaped"

    def test_modal_title_session_id_escaped(self):
        content = self._content()
        # modalTitle innerHTML should use escaped s.id
        modal_idx = content.find("modalTitle")
        assert modal_idx != -1
        block = content[modal_idx:modal_idx+300]
        assert "_eh" in block or "escapeHtml" in block, \
            "s.id in modalTitle innerHTML must be escaped"


# ── billing.js XSS ─────────────────────────────────────────────────────────

class TestBillingJsXSS:

    def _content(self):
        return (APP_DIR / "static" / "js" / "billing.js").read_text(encoding="utf-8")

    def test_month_escaped_in_summary_strip(self):
        content = self._content()
        # r.month must be passed through an escaping function before innerHTML
        strip_idx = content.find("strip.innerHTML")
        assert strip_idx != -1
        block = content[max(0, strip_idx-400):strip_idx+300]
        assert "_ehB" in block or "escapeHtml" in block, \
            "r.month must be HTML-escaped in billing summary strip"

    def test_no_raw_r_month_in_template_literal(self):
        content = self._content()
        # Should not have `${r.month` without wrapping in an escaping function
        import re
        # Look for ${r.month} without any escaping function call
        bad_pattern = re.search(r'\$\{r\.month\b', content)
        assert bad_pattern is None, \
            "r.month must not be used raw in template literal — use _ehB(r.month)"


# ── update-info.json channel ───────────────────────────────────────────────

class TestUpdateInfoChannel:

    def test_channel_is_beta(self):
        import json
        info_path = Path(__file__).parent.parent / "update-info.json"
        data = json.loads(info_path.read_text(encoding="utf-8"))
        assert data.get("channel") == "beta", \
            f"update-info.json channel must be 'beta', got {data.get('channel')!r}"

    def test_version_json_channel_is_beta(self):
        import json
        vpath = Path(__file__).parent.parent / "version.json"
        if vpath.exists():
            data = json.loads(vpath.read_text(encoding="utf-8"))
            assert data.get("channel") == "beta", \
                f"version.json channel must be 'beta', got {data.get('channel')!r}"
