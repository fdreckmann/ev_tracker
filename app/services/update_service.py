"""
Update service — re-exports from server.py.
Provides update check and pull functions.
"""
from __future__ import annotations


def get_update_info():
    from server import get_update_info as _f
    return _f()


def fetch_remote_version(tag: str) -> dict:
    from server import fetch_remote_version as _f
    return _f(tag)


def docker_pull_and_restart(tag: str):
    from server import docker_pull_and_restart as _f
    return _f(tag)


def get_update_log() -> tuple[bool, list]:
    """Return (running, log_lines) from server update state."""
    import server as _srv
    return _srv._update_running, list(_srv._update_log)
