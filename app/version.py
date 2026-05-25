"""Central version — read once from version.json, cached as module-level constant."""
import json
from pathlib import Path

_here = Path(__file__).parent
_vfile = next(
    (p for p in (_here / "version.json", _here.parent / "version.json") if p.exists()),
    None,
)
try:
    _data = json.loads(_vfile.read_text())
    APP_VERSION = _data["version"]
    BUILD_DATE  = _data.get("build", "")
    CHANNEL     = _data.get("channel", "stable")
    CHANGELOG   = []  # Legacy — kept for import compatibility
except Exception:
    APP_VERSION = "unknown"
    BUILD_DATE  = ""
    CHANNEL     = "stable"
    CHANGELOG   = []
