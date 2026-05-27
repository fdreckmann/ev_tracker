"""
Shared pytest fixtures for EV Tracker tests.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

# Make sure app/ is on the path
APP_DIR = Path(__file__).parent.parent / "app"
sys.path.insert(0, str(APP_DIR))

# Disable remote update checks for the entire test session so tests never make
# real HTTP calls to GitHub, which would cause hangs in air-gapped / CI envs.
os.environ.setdefault("EV_TRACKER_UPDATE_CHECK_ENABLED", "false")


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """Fresh Flask app with isolated DB and config for each test.

    All persistent paths (DB, config, exports, template, signature) are
    redirected to tmp_path.  No file in /data is created or modified.
    """
    db_path = tmp_path / "sessions.db"
    cfg_path = tmp_path / "config.json"
    sig_dir = tmp_path / "signatures"
    sig_dir.mkdir()
    (tmp_path / "exports").mkdir(exist_ok=True)

    cfg_path.write_text(json.dumps({
        "provider": "manual",
        "car_name": "TestEV",
        "price_per_kwh_home": 0.30,
        "price_per_kwh_ac": 0.45,
        "price_per_kwh_dc": 0.75,
    }))

    # Keep update check disabled per-test so monkeypatch can't accidentally
    # re-enable it through env-var restoration.
    monkeypatch.setenv("EV_TRACKER_UPDATE_CHECK_ENABLED", "false")

    import core.db as _db
    import core.config as _cfg_mod

    monkeypatch.setattr(_db, "DB_PATH", db_path)
    monkeypatch.setattr(_db, "DATA_DIR", tmp_path)

    # Patch config path — core.config uses CONFIG_FILE (not _CONFIG_PATH)
    monkeypatch.setattr(_cfg_mod, "CONFIG_FILE", cfg_path)
    # Reset the config cache so next load_config() reads the patched file
    _cfg_mod._config_cache["data"] = None
    _cfg_mod._config_cache["ts"] = 0

    # Patch export_excel DB path and dirs
    try:
        import export_excel as _xl
        monkeypatch.setattr(_xl, "DB_PATH", db_path)
        monkeypatch.setattr(_xl, "DATA_DIR", tmp_path)
        monkeypatch.setattr(_xl, "EXPORT_DIR", tmp_path / "exports")
        monkeypatch.setattr(_xl, "TEMPLATE_PATH", tmp_path / "template.xlsx")
    except Exception:
        pass

    # Patch template and signature paths
    try:
        import routes.templates_routes as _tpl_routes
        monkeypatch.setattr(_tpl_routes, "TEMPLATE_PATH", tmp_path / "template.xlsx")
        monkeypatch.setattr(_tpl_routes, "SIGNATURE_PATH", tmp_path / "signatures" / "default_signature.png")
        monkeypatch.setattr(_tpl_routes, "SIGNATURE_DIR", tmp_path / "signatures")
    except Exception:
        pass

    try:
        import routes.export as _exp_routes
        monkeypatch.setattr(_exp_routes, "_SIGNATURE_PATH", tmp_path / "signatures" / "default_signature.png")
    except Exception:
        pass

    # Patch update-service cache so no stale remote data leaks across tests
    try:
        import services.update_service as _upd
        monkeypatch.setattr(_upd, "_cache", {"data": None, "ts": 0.0})
    except Exception:
        pass

    import server as _srv

    # Prevent _get_secret_key() from writing to /data by patching it to a
    # fixed value.  The test config overrides app.secret_key anyway.
    try:
        import core.security as _sec
        monkeypatch.setattr(_sec, "_get_secret_key", lambda: "test-secret-key")
    except Exception:
        pass

    monkeypatch.setattr(_srv, "DB_PATH", db_path)
    monkeypatch.setattr(_srv, "DATA_DIR", tmp_path)
    try:
        monkeypatch.setattr(_srv, "_started_once", False)
    except Exception:
        pass

    flask_app = _srv.app
    flask_app.config.update({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key",
        "WTF_CSRF_ENABLED": False,
        "SESSION_COOKIE_SAMESITE": None,
        "SERVER_NAME": None,
    })

    with flask_app.app_context():
        _srv.init_db()

    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def authed_client(app):
    """Test client with an active admin session and CSRF header injection."""
    client = app.test_client()
    with app.app_context():
        from core.db import _get_db, close_db_if_owned
        from core.security import _hash_password
        now = datetime.utcnow().isoformat()
        pw = _hash_password("testpass123")
        con = _get_db()
        con.execute("""INSERT OR IGNORE INTO users
            (name, email, password_hash, role, status, totp_secret,
             totp_enabled, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            ("Admin", "admin@test.local", pw, "admin", "active", "", 0, now, now))
        con.commit()
        user_row = con.execute(
            "SELECT id FROM users WHERE email='admin@test.local'").fetchone()
        role_row = con.execute(
            "SELECT id FROM roles WHERE name='admin'").fetchone()
        uid = user_row["id"] if user_row else 1
        if user_row and role_row:
            con.execute("INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?,?)",
                        (uid, role_row["id"]))
            con.commit()
        close_db_if_owned(con)

    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["user_email"] = "admin@test.local"
        sess["user_role"] = "admin"
        sess["_fresh"] = True
        sess["csrf_token"] = "test-csrf-token"
    return _CsrfClient(client)


class _CsrfClient:
    """Thin wrapper that injects the CSRF header on every mutating request."""
    def __init__(self, client):
        self._c = client

    def _inject(self, kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("X-CSRF-Token", "test-csrf-token")
        kwargs["headers"] = headers
        return kwargs

    def get(self, *a, **kw):     return self._c.get(*a, **kw)
    def post(self, *a, **kw):    return self._c.post(*a, **self._inject(kw))
    def put(self, *a, **kw):     return self._c.put(*a, **self._inject(kw))
    def patch(self, *a, **kw):   return self._c.patch(*a, **self._inject(kw))
    def delete(self, *a, **kw):  return self._c.delete(*a, **self._inject(kw))
    # forward session_transaction for fixtures that need it
    def session_transaction(self): return self._c.session_transaction()

    @property
    def application(self): return self._c.application

    # Make the wrapper usable in `with authed_client.session_transaction()` contexts
    def __enter__(self): return self
    def __exit__(self, *a): pass
