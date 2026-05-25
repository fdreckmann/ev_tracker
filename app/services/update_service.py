"""
Read-only update info service.

Fetches remote update metadata from a hardcoded GitHub URL.
Never executes updates, never uses Docker socket.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime

import requests

log = logging.getLogger(__name__)

# The only external URL this service ever contacts — hardcoded, not user-configurable.
_REMOTE_URL = "https://raw.githubusercontent.com/fdreckmann/ev_tracker/main/update-info.json"
_REQUEST_TIMEOUT = 5  # seconds
_CACHE_TTL = 6 * 3600  # 6 hours

_cache: dict = {"data": None, "ts": 0.0}


def _is_enabled() -> bool:
    return os.getenv("EV_TRACKER_UPDATE_CHECK_ENABLED", "true").lower() not in ("false", "0", "no")


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse semver-like version string into a comparable tuple.

    Handles: 1.2.3, 1.2.3-beta, 1.2.3-rc1.
    Pre-release suffixes sort before the release version.
    """
    import re
    v = str(v).strip()
    m = re.match(r"^(\d+)\.(\d+)\.?(\d*)(.*)$", v)
    if not m:
        return (0,)
    major = int(m.group(1))
    minor = int(m.group(2))
    patch = int(m.group(3)) if m.group(3) else 0
    pre   = -1 if m.group(4) else 0  # pre-release sorts below release
    return (major, minor, patch, pre)


def _is_newer(remote: str, current: str) -> bool:
    """Return True if remote is strictly newer than current."""
    try:
        return _parse_version(remote) > _parse_version(current)
    except Exception:
        log.warning("Version comparison failed: remote=%r current=%r", remote, current)
        return False


def _is_older(remote: str, current: str) -> bool:
    """Return True if remote is strictly older than current."""
    try:
        return _parse_version(remote) < _parse_version(current)
    except Exception:
        return False


def fetch_remote_info(force: bool = False) -> tuple[dict, bool]:
    """Fetch update metadata from GitHub with caching.

    Returns (data, cache_hit). Never raises.
    """
    now = time.time()
    if not force and _cache["data"] is not None and now - _cache["ts"] < _CACHE_TTL:
        return _cache["data"], True

    try:
        resp = requests.get(_REMOTE_URL, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        _cache["data"] = data
        _cache["ts"] = now
        return data, False
    except Exception as exc:
        log.warning("Update-Check fehlgeschlagen: %s", exc)
        # Return stale cache if available rather than empty dict
        if _cache["data"] is not None:
            return _cache["data"], True
        return {}, False


def get_update_info(force: bool = False) -> dict:
    """Return the full update-info dict for the /api/update-info endpoint."""
    from version import APP_VERSION, BUILD_DATE, CHANNEL

    current = APP_VERSION
    base: dict = {
        "ok": False,
        "current_version": current,
        "build_date": BUILD_DATE,
        "channel": CHANNEL,
        "update_available": False,
        "checked_at": datetime.utcnow().isoformat(timespec="seconds"),
        "remote_url": _REMOTE_URL,
        "cache_hit": False,
        "reason": None,
    }

    if not _is_enabled():
        base["ok"] = True
        base["error"] = None
        base["update_check_disabled"] = True
        base["reason"] = "update_check_disabled"
        return base

    try:
        remote, cache_hit = fetch_remote_info(force=force)
    except Exception as exc:
        log.warning("fetch_remote_info raised unexpectedly: %s", exc)
        remote, cache_hit = {}, False

    base["cache_hit"] = cache_hit

    if not remote:
        base["error"] = "Update-Informationen konnten nicht geladen werden."
        base["reason"] = "remote_unreachable"
        return base

    latest = remote.get("latest_version", "")
    if not latest:
        base["error"] = "Remote-Metadaten enthalten keine Versionsnummer."
        base["reason"] = "invalid_remote_json"
        return base

    try:
        update_available = _is_newer(latest, current)
        remote_older = _is_older(latest, current)
    except Exception:
        base["error"] = "Versionsvergleich fehlgeschlagen."
        base["reason"] = "version_compare_failed"
        return base

    base.update({
        "ok": True,
        "error": None,
        "latest_version": latest,
        "release_date":   remote.get("release_date", ""),
        "title":          remote.get("title", ""),
        "summary":        remote.get("summary", []),
        "fixes":          remote.get("fixes", []),
        "breaking_changes": remote.get("breaking_changes", []),
        "migration_notes":  remote.get("migration_notes", []),
        "update_instructions": remote.get("update_instructions", []),
        "release_url":    remote.get("release_url", ""),
        "update_available": update_available,
    })

    if update_available:
        base["reason"] = "update_available"
        try:
            from services.notification_service import notify
            notify(
                type="update_available",
                severity="info",
                title=f"EV Tracker Update verfügbar: v{latest}",
                message=f"Version {current} → {latest}. " + (base.get("title") or ""),
                dedupe_key=f"update_available:{latest}",
                action_url=remote.get("release_url", ""),
            )
        except Exception:
            pass
    elif remote_older:
        base["reason"] = "remote_metadata_older_than_current"
        base["warning"] = (
            f"Remote-Update-Informationen sind älter als die installierte Version. "
            f"Installiert: v{current}, Remote: v{latest}"
        )
    else:
        base["reason"] = "current_is_latest"

    return base
