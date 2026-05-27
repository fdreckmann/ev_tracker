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


_MAX_TOTAL_UNCOMPRESSED = 500 * 1024 * 1024   # 500 MB total
_MAX_SINGLE_FILE       = 200 * 1024 * 1024   # 200 MB per file
_CHUNK_SIZE            = 64 * 1024            # 64 KB read chunks


def restore_backup(zip_path: Path, data_dir: Path, pre_restore_fn=None) -> None:
    """Zip-Slip-safe restore with size limits. Calls pre_restore_fn() before extraction."""
    if not zipfile.is_zipfile(zip_path):
        raise ValueError(f"Keine gültige ZIP-Datei: {zip_path.name}")

    if pre_restore_fn:
        try:
            pre_restore_fn()
        except Exception as e:
            log.warning("Sicherheits-Backup vor Restore fehlgeschlagen: %s", e)

    data_dir_resolved = data_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.namelist()
        # Phase 1: validate all paths + check total uncompressed size
        total_uncompressed = 0
        for info in zf.infolist():
            member = info.filename
            if member.endswith("/"):
                continue
            parts = member.replace("\\", "/").split("/")
            if any(p in ("", "..") for p in parts):
                raise ValueError(f"Unsicherer ZIP-Eintrag: {member!r}")
            dest = (data_dir / member).resolve()
            if not str(dest).startswith(str(data_dir_resolved)):
                raise ValueError(f"Pfad außerhalb DATA_DIR: {member!r}")
            # Symlink check (ZipInfo.external_attr has Unix mode in upper 16 bits)
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if unix_mode and (unix_mode & 0xA000) == 0xA000:
                raise ValueError(f"Symlink in ZIP abgelehnt: {member!r}")
            if info.file_size > _MAX_SINGLE_FILE:
                raise ValueError(
                    f"Datei zu groß ({info.file_size // (1024*1024)} MB): {member!r}")
            total_uncompressed += info.file_size
            if total_uncompressed > _MAX_TOTAL_UNCOMPRESSED:
                raise ValueError("ZIP-Gesamtgröße überschreitet 500 MB (mögliche Zip-Bombe)")
        # Phase 2: extract allowed paths only, chunked
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
            written = 0
            with zf.open(member) as src, open(dest, "wb") as dst:
                while True:
                    chunk = src.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > _MAX_SINGLE_FILE:
                        dst.close()
                        dest.unlink(missing_ok=True)
                        raise ValueError(f"Datei zu groß beim Entpacken: {member!r}")
                    dst.write(chunk)
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


# Public aliases matching server.py function names (used by routes).
# NOTE: create_backup and restore_backup with full signatures are defined at the
# top of this module (the real implementations). The wrappers below use different
# internal names to avoid shadowing.
def create_backup(label="manual"):
    return _srv_create_backup(label)


def restore_backup_via_server(zip_path):
    """Server.py wrapper — used by backup routes that rely on server globals."""
    return _srv_restore_backup(zip_path)


# Backward-compat alias: routes that call restore_backup(zip_path) get the server wrapper.
# The base implementation restore_backup(zip_path, data_dir) at module top is the safe core.
restore_backup = restore_backup_via_server


def parse_cron_next(cron_expr):
    return _srv_parse_cron_next(cron_expr)


def schedule_backup():
    return _srv_schedule_backup()
