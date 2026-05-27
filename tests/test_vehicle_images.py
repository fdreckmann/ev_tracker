"""
Tests for vehicle image auto-fetch feature:
- HA provider image URL extraction
- auto.webp caching (cache_provider_image)
- Manual upload via POST /api/vehicles/<vid>/image
- File serving priority (manual > auto > placeholder)
- DELETE keeps auto.webp
- Validation: file://, oversized, no image → placeholder
"""
import io
import struct
import zlib
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Minimal valid PNG helper ───────────────────────────────────────────────────

def _make_png_bytes() -> bytes:
    """Return a minimal valid 1×1 white PNG without PIL dependency."""
    def _chunk(name: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + name + data
        return c + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw  = b"\x00\xff\xff\xff"          # filter byte + 1 RGB pixel
    idat = _chunk(b"IDAT", zlib.compress(raw))
    iend = _chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


# ── HA Provider image extraction ──────────────────────────────────────────────

class TestHAProviderImageExtraction:

    def _make_provider(self, extra_config=None):
        import sys
        from pathlib import Path as P
        sys.path.insert(0, str(P(__file__).parent.parent / "app"))
        from providers.ha_provider import HomeAssistantProvider
        cfg = {
            "ha_url": "http://ha.local:8123",
            "ha_token": "test-token",
            "charging_sensor": "",
            "soc_sensor": "",
        }
        if extra_config:
            cfg.update(extra_config)
        return HomeAssistantProvider(cfg)

    def test_ha_entity_image_url_extracted(self):
        prov = self._make_provider({"vehicle_image_entity": "image.mein_auto"})
        entity_data = {
            "state": "idle",
            "attributes": {"entity_picture": "/api/image/proxy/abc123"},
        }
        with patch.object(prov, "_get_entity", return_value=entity_data):
            with patch.object(prov, "_location", return_value="unknown"):
                state = prov.get_state()
        assert state.image_url == "http://ha.local:8123/api/image/proxy/abc123"
        assert state.image_source == "ha"

    def test_ha_relative_url_resolved(self):
        prov = self._make_provider({"vehicle_image_entity": "camera.car"})
        entity_data = {
            "state": "idle",
            "attributes": {"entity_picture": "/local/car.jpg"},
        }
        with patch.object(prov, "_get_entity", return_value=entity_data):
            with patch.object(prov, "_location", return_value="unknown"):
                state = prov.get_state()
        assert state.image_url.startswith("http://ha.local:8123/local/car.jpg")

    def test_ha_absolute_url_unchanged(self):
        prov = self._make_provider({"vehicle_image_entity": "image.car"})
        entity_data = {
            "state": "idle",
            "attributes": {"url": "https://example.com/car.png"},
        }
        with patch.object(prov, "_get_entity", return_value=entity_data):
            with patch.object(prov, "_location", return_value="unknown"):
                state = prov.get_state()
        assert state.image_url == "https://example.com/car.png"

    def test_no_image_entity_configured(self):
        prov = self._make_provider()  # no vehicle_image_entity
        with patch.object(prov, "_get_entity", return_value=None):
            with patch.object(prov, "_location", return_value="unknown"):
                state = prov.get_state()
        assert state.image_url is None
        assert state.image_source is None


# ── cache_provider_image ──────────────────────────────────────────────────────

class TestCacheProviderImage:

    def _import(self):
        import sys
        from pathlib import Path as P
        sys.path.insert(0, str(P(__file__).parent.parent / "app"))
        from services.vehicle_image_service import cache_provider_image
        return cache_provider_image

    def test_file_url_rejected(self, tmp_path, monkeypatch):
        cache_provider_image = self._import()
        import core.db as _db
        monkeypatch.setattr(_db, "DATA_DIR", tmp_path)
        result = cache_provider_image("v0", "file:///etc/passwd", "manual", {})
        assert result is False

    def test_non_http_url_rejected(self, tmp_path, monkeypatch):
        cache_provider_image = self._import()
        import core.db as _db
        monkeypatch.setattr(_db, "DATA_DIR", tmp_path)
        result = cache_provider_image("v0", "ftp://example.com/car.jpg", "manual", {})
        assert result is False

    def test_downloads_and_saves_auto_webp(self, tmp_path, monkeypatch):
        cache_provider_image = self._import()
        import core.db as _db
        monkeypatch.setattr(_db, "DATA_DIR", tmp_path)

        png_bytes = _make_png_bytes()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}
        mock_resp.iter_content = lambda chunk_size: iter([png_bytes])
        mock_resp.raise_for_status = lambda: None

        with patch("requests.get", return_value=mock_resp):
            result = cache_provider_image("v0", "http://ha.local/img.png", "ha", {"ha_token": "tok"})

        assert result is True
        assert (tmp_path / "vehicles" / "v0" / "auto.webp").exists()

    def test_freshness_check_skips_recent_file(self, tmp_path, monkeypatch):
        cache_provider_image = self._import()
        import core.db as _db
        import services.vehicle_image_service as _svc
        monkeypatch.setattr(_db, "DATA_DIR", tmp_path)
        monkeypatch.setattr(_svc, "_AUTO_IMG_FRESHNESS_SECS", 3600)

        auto_path = tmp_path / "vehicles" / "v0" / "auto.webp"
        auto_path.parent.mkdir(parents=True, exist_ok=True)
        auto_path.write_bytes(b"fake")

        called = []
        with patch("requests.get", side_effect=lambda *a, **kw: called.append(1) or MagicMock()):
            result = cache_provider_image("v0", "http://ha.local/img.png", "ha", {})

        assert result is True
        assert len(called) == 0  # no HTTP call made

    def test_bad_mime_rejected(self, tmp_path, monkeypatch):
        cache_provider_image = self._import()
        import core.db as _db
        monkeypatch.setattr(_db, "DATA_DIR", tmp_path)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/svg+xml"}
        mock_resp.iter_content = lambda chunk_size: iter([b"<svg/>"])
        mock_resp.raise_for_status = lambda: None

        with patch("requests.get", return_value=mock_resp):
            result = cache_provider_image("v0", "http://ha.local/car.svg", "ha", {})

        assert result is False


# ── Manual upload + file serving ─────────────────────────────────────────────

class TestVehicleImageManual:

    def test_manual_upload_and_served(self, authed_client, tmp_path, monkeypatch):
        import routes.vehicles as _vr
        monkeypatch.setattr(_vr, "_VEH_IMG_DIR", tmp_path / "vehicles")
        import services.vehicle_image_service as _svc
        import core.db as _db
        monkeypatch.setattr(_db, "DATA_DIR", tmp_path)
        monkeypatch.setattr(_svc, "_manifest_cache", {"version": 1, "silhouettes": [], "models": []})

        png = _make_png_bytes()
        data = {"file": (io.BytesIO(png), "car.png", "image/png")}
        resp = authed_client.post(
            "/api/vehicles/v0/image",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("ok") is True

        # File should be retrievable
        resp2 = authed_client.get("/api/vehicles/v0/image/file")
        assert resp2.status_code == 200
        assert resp2.content_type in ("image/webp", "image/svg+xml")

    def test_delete_manual_falls_back_to_auto(self, authed_client, tmp_path, monkeypatch):
        import routes.vehicles as _vr
        veh_dir = tmp_path / "vehicles" / "v0"
        veh_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(_vr, "_VEH_IMG_DIR", tmp_path / "vehicles")
        import core.db as _db
        monkeypatch.setattr(_db, "DATA_DIR", tmp_path)

        # Create both car.webp and auto.webp
        png = _make_png_bytes()
        from PIL import Image
        img = Image.open(io.BytesIO(png))
        img.save(str(veh_dir / "car.webp"), "WEBP")
        img.save(str(veh_dir / "auto.webp"), "WEBP")

        resp = authed_client.delete("/api/vehicles/v0/image")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("ok") is True
        assert body.get("active_source") == "auto"

        # car.webp gone, auto.webp still there
        assert not (veh_dir / "car.webp").exists()
        assert (veh_dir / "auto.webp").exists()

    def test_delete_with_no_auto_clears_image(self, authed_client, tmp_path, monkeypatch):
        import routes.vehicles as _vr
        veh_dir = tmp_path / "vehicles" / "v0"
        veh_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(_vr, "_VEH_IMG_DIR", tmp_path / "vehicles")
        import core.db as _db
        monkeypatch.setattr(_db, "DATA_DIR", tmp_path)

        png = _make_png_bytes()
        from PIL import Image
        img = Image.open(io.BytesIO(png))
        img.save(str(veh_dir / "car.webp"), "WEBP")

        resp = authed_client.delete("/api/vehicles/v0/image")
        body = resp.get_json()
        assert body.get("active_source") == "none"
        assert not (veh_dir / "car.webp").exists()


# ── Validation ────────────────────────────────────────────────────────────────

class TestVehicleImageValidation:

    def test_placeholder_served_when_no_image(self, authed_client, tmp_path, monkeypatch):
        import routes.vehicles as _vr
        monkeypatch.setattr(_vr, "_VEH_IMG_DIR", tmp_path / "vehicles")
        # No image files created
        resp = authed_client.get("/api/vehicles/v0/image/file")
        assert resp.status_code == 200
        # Either an SVG placeholder or webp — must not be 404
        assert "image/" in resp.content_type

    def test_oversized_file_rejected(self, authed_client, tmp_path, monkeypatch):
        import routes.vehicles as _vr
        monkeypatch.setattr(_vr, "_VEH_IMG_DIR", tmp_path / "vehicles")
        monkeypatch.setattr(_vr, "_VEH_IMG_MAX_BYTES", 10)  # tiny limit for test

        big = io.BytesIO(b"x" * 20)
        resp = authed_client.post(
            "/api/vehicles/v0/image",
            data={"file": (big, "big.png", "image/png")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body.get("ok") is False

    def test_svg_file_rejected(self, authed_client, tmp_path, monkeypatch):
        """SVG must be rejected even with correct MIME type."""
        import routes.vehicles as _vr
        monkeypatch.setattr(_vr, "_VEH_IMG_DIR", tmp_path / "vehicles")

        svg_bytes = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="10"/></svg>'
        resp = authed_client.post(
            "/api/vehicles/v0/image",
            data={"file": (io.BytesIO(svg_bytes), "car.svg", "image/svg+xml")},
            content_type="multipart/form-data",
        )
        # Must be rejected: PIL cannot open SVG as a valid raster image
        assert resp.status_code == 400

    def test_broken_image_rejected(self, authed_client, tmp_path, monkeypatch):
        """Truncated/corrupt image bytes must be rejected."""
        import routes.vehicles as _vr
        monkeypatch.setattr(_vr, "_VEH_IMG_DIR", tmp_path / "vehicles")

        broken = io.BytesIO(b"\x89PNG\r\n\x1a\nNOT_VALID_DATA")
        resp = authed_client.post(
            "/api/vehicles/v0/image",
            data={"file": (broken, "corrupt.png", "image/png")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body.get("ok") is False


# ── HA Provider additional edge cases ────────────────────────────────────────

class TestHAProviderImageEdgeCases:

    def _make_provider(self, extra_config=None):
        import sys
        from pathlib import Path as P
        sys.path.insert(0, str(P(__file__).parent.parent / "app"))
        from providers.ha_provider import HomeAssistantProvider
        cfg = {
            "ha_url": "http://ha.local:8123",
            "ha_token": "test-token",
            "charging_sensor": "",
            "soc_sensor": "",
            "vehicle_image_entity": "image.car",
        }
        if extra_config:
            cfg.update(extra_config)
        return HomeAssistantProvider(cfg)

    def test_state_with_http_url_used_as_image(self):
        """When entity state is an http URL, it should be used as image_url."""
        prov = self._make_provider()
        entity_data = {
            "state": "https://example.com/car.png",
            "attributes": {},
        }
        with patch.object(prov, "_get_entity", return_value=entity_data):
            with patch.object(prov, "_location", return_value="unknown"):
                state = prov.get_state()
        assert state.image_url == "https://example.com/car.png"
        assert state.image_source == "ha"

    def test_unavailable_state_produces_no_image(self):
        """State 'unavailable' must not produce an image_url."""
        prov = self._make_provider()
        entity_data = {
            "state": "unavailable",
            "attributes": {},
        }
        with patch.object(prov, "_get_entity", return_value=entity_data):
            with patch.object(prov, "_location", return_value="unknown"):
                state = prov.get_state()
        assert state.image_url is None

    def test_unknown_state_produces_no_image(self):
        """State 'unknown' must not produce an image_url."""
        prov = self._make_provider()
        entity_data = {
            "state": "unknown",
            "attributes": {},
        }
        with patch.object(prov, "_get_entity", return_value=entity_data):
            with patch.object(prov, "_location", return_value="unknown"):
                state = prov.get_state()
        assert state.image_url is None


# ── Image priority (resolve_vehicle_image_url) ─────────────────────────────

class TestVehicleImagePriority:

    def _import_resolve(self):
        import sys
        from pathlib import Path as P
        sys.path.insert(0, str(P(__file__).parent.parent / "app"))
        from services.vehicle_image_service import resolve_vehicle_image_url
        return resolve_vehicle_image_url

    def test_manual_wins_over_auto(self, tmp_path, monkeypatch):
        """car.webp (manual) takes priority over auto.webp."""
        import core.db as _db
        monkeypatch.setattr(_db, "DATA_DIR", tmp_path)
        veh_dir = tmp_path / "vehicles" / "v0"
        veh_dir.mkdir(parents=True, exist_ok=True)
        (veh_dir / "car.webp").write_bytes(b"manual")
        (veh_dir / "auto.webp").write_bytes(b"auto")

        resolve = self._import_resolve()
        url = resolve({"id": "v0"})
        assert url == "/api/vehicles/v0/image/file"
        # Both exist — car.webp wins; the file endpoint checks car.webp first

    def test_auto_wins_over_silhouette(self, tmp_path, monkeypatch):
        """auto.webp takes priority over any silhouette key."""
        import core.db as _db
        monkeypatch.setattr(_db, "DATA_DIR", tmp_path)
        veh_dir = tmp_path / "vehicles" / "v0"
        veh_dir.mkdir(parents=True, exist_ok=True)
        (veh_dir / "auto.webp").write_bytes(b"auto")

        resolve = self._import_resolve()
        url = resolve({"id": "v0", "default_image_key": "silhouette_suv"})
        assert url == "/api/vehicles/v0/image/file"

    def test_silhouette_key_wins_over_placeholder(self, tmp_path, monkeypatch):
        """A saved default_image_key is used when no webp files exist."""
        import core.db as _db
        monkeypatch.setattr(_db, "DATA_DIR", tmp_path)

        resolve = self._import_resolve()
        url = resolve({"id": "v0", "default_image_key": "silhouette_suv"})
        assert "silhouette_suv" in url
        assert url.endswith(".svg")

    def test_placeholder_when_nothing(self, tmp_path, monkeypatch):
        """Placeholder returned when no webp, no key, no match."""
        import core.db as _db
        monkeypatch.setattr(_db, "DATA_DIR", tmp_path)
        import services.vehicle_image_service as _svc
        monkeypatch.setattr(_svc, "_manifest_cache", {"version": 1, "silhouettes": [], "models": []})

        resolve = self._import_resolve()
        url = resolve({"id": "v0", "name": "", "brand": "", "model": ""})
        assert "placeholder" in url or "silhouette" in url
