"""Central version — read once from version.json, cached as module-level constant."""
import json
from pathlib import Path

_here = Path(__file__).parent
# Docker: version.json copied alongside app code; dev: one level up at project root
_vfile = next(
    (p for p in (_here / "version.json", _here.parent / "version.json") if p.exists()),
    None,
)
try:
    _data = json.loads(_vfile.read_text())
    APP_VERSION = _data["version"]
    CHANGELOG   = _data.get("changelog", [])
except Exception:
    APP_VERSION = "unknown"
    CHANGELOG   = []
