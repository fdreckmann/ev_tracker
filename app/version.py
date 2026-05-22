"""Central version — read once from version.json, cached as module-level constant."""
import json
from pathlib import Path

_vfile = Path(__file__).parent.parent / "version.json"
try:
    APP_VERSION = json.loads(_vfile.read_text())["version"]
except Exception:
    APP_VERSION = "unknown"
