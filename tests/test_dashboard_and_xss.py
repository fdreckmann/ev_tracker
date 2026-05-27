"""
Tests for:
A) ENTSO-E Spot removed from dashboard
B) Mobile vehicle card XSS escaping
"""
from pathlib import Path

import pytest

_INDEX_HTML = Path(__file__).parent.parent / "app" / "templates" / "index.html"
_STATUS_JS  = Path(__file__).parent.parent / "app" / "static" / "js" / "status.js"


class TestEntsoeRemovedFromDashboard:

    def test_no_dspot_tile_in_html(self):
        """ENTSO-E Spot dashboard tile must not appear in index.html."""
        html = _INDEX_HTML.read_text(encoding="utf-8")
        assert 'id="dSpot"' not in html, "dSpot tile still present in dashboard HTML"

    def test_no_entso_e_spot_label_in_dashboard(self):
        """The 'ENTSO-E Spot' label must not appear as a visible dashboard stat tile."""
        html = _INDEX_HTML.read_text(encoding="utf-8")
        # ENTSO-E may still appear in settings section — only the dashboard stat tile must be gone
        # Check that it's not inside a <div class="stat"> tile
        import re
        # Find all stat tiles
        tiles = re.findall(r'<div class="stat"[^>]*>.*?</div>', html, re.DOTALL)
        for tile in tiles:
            assert "ENTSO-E Spot" not in tile, f"ENTSO-E Spot still in a stat tile: {tile[:120]}"

    def test_status_js_no_dspot_assignment(self):
        """status.js must not write to dSpot element."""
        js = _STATUS_JS.read_text(encoding="utf-8")
        assert "dSpot" not in js, "status.js still references dSpot"

    def test_status_js_no_entsoe_spot_in_session_table(self):
        """Session table in status.js must not show 'Spot:' label."""
        js = _STATUS_JS.read_text(encoding="utf-8")
        assert "Spot:" not in js, "Spot: label still present in status.js session table"


class TestMobileVehicleCardXSS:

    def _card_block(self):
        html = _INDEX_HTML.read_text(encoding="utf-8")
        # Grab from _buildVehicleCard definition to its closing brace
        idx = html.find("_buildVehicleCard = v =>")
        assert idx >= 0, "_buildVehicleCard not found in index.html"
        end = html.find("\n}", idx)
        return html[idx: end + 2] if end > 0 else html[idx: idx + 5000]

    def test_vehicle_name_uses_escapeHtml(self):
        """Mobile vehicle card must escape vehicle name via escapeHtml or _eh."""
        block = self._card_block()
        assert "${v.name" not in block, (
            "v.name interpolated raw (without escaping) in _buildVehicleCard"
        )
        assert "_eh(v.name" in block or "escapeHtml(v.name" in block, (
            "v.name not escaped in _buildVehicleCard"
        )

    def test_vehicle_provider_uses_escapeHtml(self):
        """Mobile vehicle card must escape provider field."""
        block = self._card_block()
        assert "${v.provider" not in block, "v.provider interpolated raw in _buildVehicleCard"
        assert "_eh(v.provider" in block or "escapeHtml(v.provider" in block, (
            "v.provider not escaped in _buildVehicleCard"
        )

    def test_vehicle_id_in_dom_uses_escapeHtml(self):
        """Vehicle ID shown as text in the card must go through _eh, not raw."""
        block = self._card_block()
        assert "ID: ${v.id}" not in block, "Raw v.id still present in ID display"
        assert "_eh(v.id)" in block or "escapeHtml(v.id)" in block, (
            "v.id not escaped in _buildVehicleCard"
        )

    def test_onclick_uses_data_attribute_not_inline_js(self):
        """Vehicle card click must use data-vid + addEventListener, not inline onclick with v.id."""
        block = self._card_block()
        assert "onclick=\"openMobileVehicleDetail('${v.id}')" not in block, (
            "Unsafe inline onclick with raw v.id still present"
        )
        assert "data-vid=" in block, "data-vid attribute missing from vehicle card"
