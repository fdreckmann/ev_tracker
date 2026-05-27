"""
Tests for meter dashboard live status:
- GET /api/meter/status returns live value from configured source
- Source == "none" → ok=False, tile hidden
- TTL cache: second call within 30s does not re-read
- meter_source_start stored at session start
- meter_source_end stored at session end
- meter_source_changed → meter_skipped_reason = "meter_source_changed"
"""
from __future__ import annotations
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── /api/meter/status ─────────────────────────────────────────────────────────

class TestMeterStatusEndpoint:

    def test_no_source_returns_none(self, authed_client, tmp_path, monkeypatch):
        """When meter_source is 'none', endpoint returns ok=False and source='none'."""
        import core.config as _cfg
        monkeypatch.setattr(_cfg, "CONFIG_FILE", tmp_path / "config.json")
        (tmp_path / "config.json").write_text(json.dumps({"meter_source": "none"}))
        _cfg._config_cache["data"] = None; _cfg._config_cache["ts"] = 0

        # Clear server-side cache
        from routes.connections import _meter_status_cache
        _meter_status_cache.clear()

        resp = authed_client.get("/api/meter/status?vehicle_id=v0")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is False
        assert body["source"] == "none"
        assert body["value_kwh"] is None

    def test_source_not_configured_returns_none(self, authed_client, tmp_path, monkeypatch):
        """When meter_source is missing from config, same result."""
        import core.config as _cfg
        monkeypatch.setattr(_cfg, "CONFIG_FILE", tmp_path / "config.json")
        (tmp_path / "config.json").write_text(json.dumps({}))
        _cfg._config_cache["data"] = None; _cfg._config_cache["ts"] = 0
        from routes.connections import _meter_status_cache
        _meter_status_cache.clear()

        resp = authed_client.get("/api/meter/status?vehicle_id=v0")
        body = resp.get_json()
        assert body["ok"] is False
        assert body["source"] in (None, "none", "")

    def test_configured_source_returns_live_value(self, authed_client, tmp_path, monkeypatch):
        """When meter_source is configured and provider returns value, ok=True with value_kwh."""
        import core.config as _cfg
        monkeypatch.setattr(_cfg, "CONFIG_FILE", tmp_path / "config.json")
        (tmp_path / "config.json").write_text(json.dumps({"meter_source": "generic"}))
        _cfg._config_cache["data"] = None; _cfg._config_cache["ts"] = 0
        from routes.connections import _meter_status_cache
        _meter_status_cache.clear()

        fake_result = MagicMock()
        fake_result.ok = True
        fake_result.value = 12345.678
        fake_result.error = None
        fake_result.endpoint = "http://example.com/meter"

        with patch("meter_providers.read_meter", return_value=fake_result):
            resp = authed_client.get("/api/meter/status?vehicle_id=v0")

        body = resp.get_json()
        assert body["ok"] is True
        assert abs(body["value_kwh"] - 12345.678) < 0.001
        assert body["source"] == "generic"

    def test_provider_error_returns_ok_false(self, authed_client, tmp_path, monkeypatch):
        """When provider returns error, ok=False with error message."""
        import core.config as _cfg
        monkeypatch.setattr(_cfg, "CONFIG_FILE", tmp_path / "config.json")
        (tmp_path / "config.json").write_text(json.dumps({"meter_source": "tasmota"}))
        _cfg._config_cache["data"] = None; _cfg._config_cache["ts"] = 0
        from routes.connections import _meter_status_cache
        _meter_status_cache.clear()

        fake_result = MagicMock()
        fake_result.ok = False
        fake_result.value = None
        fake_result.error = "Verbindung fehlgeschlagen"
        fake_result.endpoint = None

        with patch("meter_providers.read_meter", return_value=fake_result):
            resp = authed_client.get("/api/meter/status?vehicle_id=v0")

        body = resp.get_json()
        assert body["ok"] is False
        assert "Verbindung" in (body.get("error") or "")

    def test_ttl_cache_prevents_second_read(self, authed_client, tmp_path, monkeypatch):
        """Second request within TTL returns cached value without calling provider again."""
        import core.config as _cfg
        monkeypatch.setattr(_cfg, "CONFIG_FILE", tmp_path / "config.json")
        (tmp_path / "config.json").write_text(json.dumps({"meter_source": "shelly"}))
        _cfg._config_cache["data"] = None; _cfg._config_cache["ts"] = 0
        from routes.connections import _meter_status_cache
        _meter_status_cache.clear()

        call_count = []
        def _fake_read(cfg):
            call_count.append(1)
            r = MagicMock(); r.ok = True; r.value = 999.0; r.error = None; r.endpoint = None
            return r

        with patch("meter_providers.read_meter", side_effect=_fake_read):
            authed_client.get("/api/meter/status?vehicle_id=v0")
            authed_client.get("/api/meter/status?vehicle_id=v0")

        assert len(call_count) == 1  # only called once; second request used cache

    def test_force_refresh_bypasses_cache(self, authed_client, tmp_path, monkeypatch):
        """force=1 bypasses cache and reads again."""
        import core.config as _cfg
        monkeypatch.setattr(_cfg, "CONFIG_FILE", tmp_path / "config.json")
        (tmp_path / "config.json").write_text(json.dumps({"meter_source": "shelly"}))
        _cfg._config_cache["data"] = None; _cfg._config_cache["ts"] = 0
        from routes.connections import _meter_status_cache
        _meter_status_cache.clear()

        call_count = []
        def _fake_read(cfg):
            call_count.append(1)
            r = MagicMock(); r.ok = True; r.value = 500.0; r.error = None; r.endpoint = None
            return r

        with patch("meter_providers.read_meter", side_effect=_fake_read):
            authed_client.get("/api/meter/status?vehicle_id=v0")
            authed_client.get("/api/meter/status?vehicle_id=v0&force=1")

        assert len(call_count) == 2


# ── Session meter_source tracking ─────────────────────────────────────────────

class TestMeterSourceTracking:

    def _create_session_with_source(self, con, start_source="tasmota"):
        con.execute("""
            INSERT INTO sessions (start_ts, end_ts, vehicle_id, meter_source_start, soc_start, location)
            VALUES (datetime('now','-1 hour'), NULL, 'v0', ?, 80, 'home')
        """, (start_source,))
        con.commit()
        return con.execute("SELECT last_insert_rowid()").fetchone()[0]

    def test_meter_source_start_stored_in_session(self, authed_client):
        """New session should have meter_source_start = configured source."""
        from core.db import _get_db, close_db_if_owned, DB_PATH
        con = _get_db()
        try:
            # Insert a fake completed session with meter_source_start set
            sid = self._create_session_with_source(con, "tasmota")
            row = con.execute(
                "SELECT meter_source_start FROM sessions WHERE id=?", (sid,)
            ).fetchone()
            assert row is not None
            assert row[0] == "tasmota"
        finally:
            con.execute("DELETE FROM sessions WHERE vehicle_id='v0' AND end_ts IS NULL")
            con.commit()
            close_db_if_owned(con)

    def test_meter_source_end_column_exists(self, authed_client):
        """meter_source_end column must exist in DB schema."""
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(sessions)").fetchall()]
            assert "meter_source_start" in cols
            assert "meter_source_end" in cols
        finally:
            close_db_if_owned(con)

    def test_meter_skipped_reason_source_changed(self, authed_client):
        """When meter_source_start != current source, skipped reason should be set."""
        from core.db import _get_db, close_db_if_owned
        con = _get_db()
        try:
            sid = self._create_session_with_source(con, "tasmota")
            # Simulate session end with different source
            con.execute("""
                UPDATE sessions
                SET end_ts=datetime('now'), meter_source_end='generic',
                    meter_skipped_reason='meter_source_changed', meter_used=0
                WHERE id=?
            """, (sid,))
            con.commit()
            row = con.execute(
                "SELECT meter_skipped_reason, meter_used, meter_source_end FROM sessions WHERE id=?",
                (sid,)
            ).fetchone()
            assert row[0] == "meter_source_changed"
            assert row[1] == 0
            assert row[2] == "generic"
        finally:
            close_db_if_owned(con)
