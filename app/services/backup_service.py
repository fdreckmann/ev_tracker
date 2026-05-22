"""
Backup service — extracted from server.py.
Provides create_backup() and restore_backup() as reusable functions.
"""
from __future__ import annotations

import zipfile
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_RESTORE_ALLOWED_FILES = {
    "config.json", "sessions.db", "template.xlsx", "update_history.json",
}
_RESTORE_ALLOWED_DIRS = {
    "templates/", "signatures/", "vehicles/", "uploads/",
}


def create_backup(data_dir: Path, backup_dir: Path, label: str = "manual") -> Path:
    """Create a zip backup of data_dir contents."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = backup_dir / f"ev-tracker_backup_{label}_{ts}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in data_dir.rglob("*"):
            if item.is_file() and item.suffix not in (".lock",):
                zf.write(item, item.relative_to(data_dir))
    # Keep last 10 backups
    all_backups = sorted(backup_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime)
    while len(all_backups) > 10:
        all_backups.pop(0).unlink()
    log.info("Backup erstellt: %s", out.name)
    return out


def restore_backup(zip_path: Path, data_dir: Path, pre_restore_fn=None) -> None:
    """Zip-Slip-safe restore. Calls pre_restore_fn() before extraction if provided."""
    if pre_restore_fn:
        try:
            pre_restore_fn()
        except Exception as e:
            log.warning("Sicherheits-Backup vor Restore fehlgeschlagen: %s", e)

    data_dir_resolved = data_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.namelist()
        # Phase 1: validate all paths
        for member in members:
            if member.endswith("/"):
                continue
            parts = member.replace("\\", "/").split("/")
            if any(p in ("", "..") for p in parts):
                raise ValueError(f"Unsicherer ZIP-Eintrag: {member!r}")
            dest = (data_dir / member).resolve()
            if not str(dest).startswith(str(data_dir_resolved)):
                raise ValueError(f"Pfad außerhalb DATA_DIR: {member!r}")
        # Phase 2: extract allowed paths only
        for member in members:
            if member.endswith("/") or member.startswith("backups/"):
                continue
            is_allowed = (
                member in _RESTORE_ALLOWED_FILES or
                any(member.startswith(d) for d in _RESTORE_ALLOWED_DIRS)
            )
            if not is_allowed:
                log.debug("Restore: übersprungen %r (nicht in Allowlist)", member)
                continue
            dest = data_dir / member
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as dst:
                dst.write(src.read())
    log.info("Backup wiederhergestellt: %s", zip_path.name)


# ── Server re-exports (these have many dependencies on server.py globals) ──────

def get_backup_funcs():
    """Return server.py backup functions as a tuple."""
    from server import create_backup, restore_backup, parse_cron_next, schedule_backup
    BACKUP_DIR = get_backup_dir()
    _BACKUP_MAX_UPLOAD_BYTES = get_max_upload_bytes()
    return create_backup, restore_backup, parse_cron_next, schedule_backup, BACKUP_DIR, _BACKUP_MAX_UPLOAD_BYTES


# Module-level lazy accessors

def _srv_create_backup(label="manual"):
    from server import create_backup as _f
    return _f(label)


def _srv_restore_backup(zip_path):
    from server import restore_backup as _f
    return _f(zip_path)


def _srv_parse_cron_next(cron_expr):
    from server import parse_cron_next as _f
    return _f(cron_expr)


def _srv_schedule_backup():
    from server import schedule_backup as _f
    return _f()


def get_backup_dir():
    from core.db import DATA_DIR
    return DATA_DIR / "backups"


def get_max_upload_bytes():
    return 200 * 1024 * 1024  # 200 MB (same as server._BACKUP_MAX_UPLOAD_BYTES)


# Keep old name for backward compatibility
get_backup_max_upload_bytes = get_max_upload_bytes


def get_backup_timer():
    import server as _srv  # _backup_timer lives in server module-level scope
    return getattr(_srv, "_backup_timer", None)


# Public aliases matching server.py function names (used by routes)
def create_backup(label="manual"):
    return _srv_create_backup(label)


def restore_backup(zip_path):
    return _srv_restore_backup(zip_path)


def parse_cron_next(cron_expr):
    return _srv_parse_cron_next(cron_expr)


def schedule_backup():
    return _srv_schedule_backup()
