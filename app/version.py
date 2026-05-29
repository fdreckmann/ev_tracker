"""Central version — read once from version.json, cached as module-level constants.

Environment variables override version.json values (useful for Docker build-args):
  EV_TRACKER_VERSION, EV_TRACKER_BUILD, EV_TRACKER_CHANNEL,
  EV_TRACKER_COMMIT, EV_TRACKER_BRANCH, EV_TRACKER_IMAGE_TAG
"""
import json
import os
from pathlib import Path

_here = Path(__file__).parent
_vfile = next(
    (p for p in (_here / "version.json", _here.parent / "version.json") if p.exists()),
    None,
)
try:
    _data = json.loads(_vfile.read_text())
except Exception:
    _data = {}

def _env_or(key: str, fallback: str) -> str:
    v = os.getenv(key, "").strip()
    return v if v and v != "unknown" else fallback

APP_VERSION = _env_or("EV_TRACKER_VERSION",  _data.get("version", "unknown"))
BUILD_DATE  = _env_or("EV_TRACKER_BUILD",     _data.get("build",   ""))
CHANNEL     = _env_or("EV_TRACKER_CHANNEL",   _data.get("channel", "stable"))
GIT_COMMIT  = _env_or("EV_TRACKER_COMMIT",    _data.get("commit",  ""))
GIT_BRANCH  = _env_or("EV_TRACKER_BRANCH",    _data.get("branch",  ""))
IMAGE_TAG   = _env_or("EV_TRACKER_IMAGE_TAG", _data.get("image_tag", ""))

# Short commit hash (first 8 chars)
COMMIT_SHORT = GIT_COMMIT[:8] if GIT_COMMIT else ""

ASSET_VERSION = APP_VERSION + "-" + (COMMIT_SHORT or BUILD_DATE.replace("-", "") or "local")

# Display fallbacks — never empty, shown in UI and /api/update-info
DISPLAY_BRANCH    = GIT_BRANCH    or "local/source"
DISPLAY_COMMIT    = COMMIT_SHORT  or "unknown"
DISPLAY_IMAGE_TAG = IMAGE_TAG     or "unknown"

CHANGELOG = []  # Legacy — kept for import compatibility
