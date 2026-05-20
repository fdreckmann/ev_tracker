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
