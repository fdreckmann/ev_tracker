"""
Health + system status routes — extracted from server.py.
Import and register via app.register_blueprint(health_bp) once extraction is complete.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Blueprint, jsonify

health_bp = Blueprint("health", __name__)


def _make_health_view(app_version: str, db_path: Path, data_dir: Path):
    """Factory so the blueprint can access app-level constants."""

    @health_bp.route("/api/health")
    def api_health():
        db_ok = True
        try:
            c = sqlite3.connect(db_path); c.execute("SELECT 1"); c.close()
        except Exception:
            db_ok = False
        data_ok = data_dir.exists()
        return jsonify({
            "ok": db_ok and data_ok,
            "version": app_version,
            "db": "ok" if db_ok else "error",
            "data_dir": "ok" if data_ok else "error",
        }), 200 if (db_ok and data_ok) else 503

    return health_bp
