"""
Database connection management — shared across server.py and all blueprints.

Usage:
    from core.db import _get_db, close_db_if_owned, DB_PATH, DATA_DIR
"""
import os
import sqlite3
from pathlib import Path

from flask import g

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH  = DATA_DIR / "sessions.db"


def _get_db():
    """Return a DB connection.

    Inside a request context: reuses a single connection stored in flask.g;
    teardown_appcontext closes it automatically after each request.
    Outside request context (background threads): returns a new connection
    that the caller must close.
    """
    try:
        db = g.get("_db")
        if db is None:
            db = sqlite3.connect(DB_PATH)
            db.row_factory = sqlite3.Row
            g._db = db
        else:
            try:
                db.execute("SELECT 1")
            except Exception:
                db = sqlite3.connect(DB_PATH)
                db.row_factory = sqlite3.Row
                g._db = db
        return db
    except RuntimeError:
        # Outside request context (background threads) — caller owns the connection
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        return con


def close_db_if_owned(con):
    """Close a DB connection only if it is NOT the flask.g-managed request connection.

    Call instead of con.close() in all request-context code so that
    the g-managed connection stays open until teardown_appcontext.
    Background threads use sqlite3.connect() directly and must close their own connections.
    """
    try:
        if g.get("_db") is con:
            return  # teardown_appcontext closes this after the request
    except RuntimeError:
        pass  # outside request context — always close
    try:
        con.close()
    except Exception:
        pass
