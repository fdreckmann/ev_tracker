"""
Vehicle image suggestion service.
Matches a vehicle's brand/model/name against the local silhouette manifest
and returns the best matching image key.
"""
from __future__ import annotations
import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

_MANIFEST_PATH = Path(__file__).parent.parent / "static" / "vehicle_images" / "manifest.json"

_manifest_cache: dict | None = None


def _load_manifest() -> dict:
    global _manifest_cache
    if _manifest_cache is None:
        try:
            _manifest_cache = json.loads(_manifest_path().read_text(encoding="utf-8"))
        except Exception:
            _manifest_cache = {"version": 1, "silhouettes": [], "models": []}
    return _manifest_cache


def _manifest_path() -> Path:
    return _MANIFEST_PATH


def _tok(s: str) -> str:
    """Lowercase, strip punctuation/spaces for fuzzy comparison."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def suggest_vehicle_image_key(brand: str = "", model: str = "", name: str = "") -> str:
    """
    Return the best matching silhouette/model key for the given vehicle info.
    Falls back to 'silhouette_suv' if nothing matched, or '' if manifest unavailable.
    """
    manifest = _load_manifest()
    models = manifest.get("models", [])

    brand_t = _tok(brand)
    model_t = _tok(model)
    name_t  = _tok(name)

    # Combined string for broader matching
    combined = brand_t + model_t + name_t

    best_key: str = ""
    best_score: int = 0

    for entry in models:
        eb = _tok(entry.get("brand", ""))
        em = _tok(entry.get("model", ""))
        aliases = [_tok(a) for a in entry.get("aliases", [])]
        key = entry.get("key", "")

        score = 0

        # Exact brand + model match
        if eb and em and eb == brand_t and em == model_t:
            score = 100
        # Brand matches, model substring
        elif eb and eb == brand_t and em and em in model_t:
            score = 80
        elif eb and eb == brand_t and em and model_t in em:
            score = 75
        # Alias match in combined string
        elif any(a and a in combined for a in aliases):
            score = 60
        # Model token in name
        elif em and em in name_t:
            score = 40
        # Brand in name
        elif eb and eb in name_t:
            score = 20

        if score > best_score:
            best_score = score
            best_key = key

    # Fallback: try name-based body type detection
    if not best_key:
        best_key = _detect_body_type(name_t + model_t)

    return best_key


def _detect_body_type(text: str) -> str:
    """Heuristic body-type detection from free text."""
    rules = [
        ("van",         ["van", "buzz", "transporter", "bus", "minivan"]),
        ("wagon",       ["tourer", "variant", "touring", "estate", "kombi", "avant", "sportwagon"]),
        ("suv",         ["suv", "crossover", "offroad", "4matic", "quattro", "allroad", "xdrive",
                         "awd", "4x4", "x1", "x3", "x5", "ix1", "ix3", "q4", "q6", "q8",
                         "mokka", "mach", "tang", "es8", "atto"]),
        ("hatchback",   ["hatchback", "hatch", "id3", "id.3", "mg4", "corsa", "208", "e208",
                         "golf", "polo", "born", "zoe"]),
        ("sedan",       ["sedan", "limousine", "saloon", "id7", "id.7", "model3", "seal", "eqs"]),
        ("crossover",   ["coupe", "gt", "ioniq", "ev6", "ev9", "modely", "enyaq", "tavascan"]),
    ]
    for key_suffix, keywords in rules:
        if any(kw in text for kw in keywords):
            return f"silhouette_{key_suffix}"
    return "silhouette_suv"


_VID_RE = __import__("re").compile(r'^[a-zA-Z0-9_-]{1,64}$')


def _safe_vid(vid: str) -> str | None:
    """Return vid if safe, else None."""
    if not vid or not _VID_RE.match(vid) or ".." in vid or "/" in vid:
        return None
    return vid


def resolve_vehicle_image_url(vehicle: dict) -> str:
    """
    Return the URL that best represents this vehicle's image.
    Priority: manual car.webp > auto auto.webp > default_image_key silhouette > auto-suggest > placeholder
    """
    from core.db import DATA_DIR

    raw_vid = vehicle.get("id", "v0")
    vid = _safe_vid(str(raw_vid)) if raw_vid else None

    if vid:
        base = DATA_DIR / "vehicles" / vid
        # 1. Manually uploaded image
        if (base / "car.webp").exists():
            return f"/api/vehicles/{vid}/image/file"
        # 2. Provider-cached image
        if (base / "auto.webp").exists():
            return f"/api/vehicles/{vid}/image/file"

    # 3. Saved default image key (v0 uses a prefixed config key name)
    key = vehicle.get("default_image_key", "") or vehicle.get("vehicle_default_image_key", "")

    # 3. Auto-suggest
    if not key:
        key = suggest_vehicle_image_key(
            brand=vehicle.get("brand", ""),
            model=vehicle.get("model", ""),
            name=vehicle.get("name", ""),
        )

    if key:
        return f"/static/vehicle_images/{key}.svg"

    return "/static/vehicle_images/placeholder_car.svg"


def get_manifest() -> dict:
    """Return the full manifest (cached)."""
    return _load_manifest()


_AUTO_IMG_FRESHNESS_SECS = 12 * 3600  # 12 hours


def _is_ssrf_blocked(url: str, ha_url: str = "") -> bool:
    """
    Return True if the URL targets a blocked host (SSRF protection).
    Blocked: localhost, 127.x, ::1, 169.254.x (link-local/metadata).
    Private ranges (10.x, 172.16-31.x, 192.168.x) are allowed only when
    they match the configured ha_url host, otherwise blocked.
    """
    import urllib.parse
    import ipaddress
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        # Always block localhost and loopback
        if host in ("localhost", "127.0.0.1", "::1") or host.startswith("127."):
            return True
        try:
            ip = ipaddress.ip_address(host)
            # Block link-local (169.254.x.x) and metadata IPs
            if ip.is_link_local:
                return True
            # Block private ranges unless they match ha_url
            if ip.is_private:
                if ha_url:
                    ha_parsed = urllib.parse.urlparse(ha_url)
                    ha_host = ha_parsed.hostname or ""
                    if ha_host == host:
                        return False  # matches configured HA host — allow
                return True
        except ValueError:
            pass  # not a plain IP — hostname, proceed
    except Exception:
        pass
    return False


def cache_provider_image(vid: str, image_url: str, source: str, config: dict) -> bool:
    """
    Download provider-supplied image URL server-side and save as auto.webp.
    Returns True on success, False otherwise.
    Security: http/https only, no file:// or SSRF vectors; 3 MB cap; MIME check; PIL validation.
    """
    from core.db import DATA_DIR

    safe_vid = _safe_vid(vid)
    if not safe_vid:
        log.warning("cache_provider_image: unsafe vid %r", vid)
        return False

    # Reject non-http(s) URLs
    if not image_url or not isinstance(image_url, str):
        return False
    scheme = image_url.split("://")[0].lower() if "://" in image_url else ""
    if scheme not in ("http", "https"):
        log.warning("cache_provider_image: rejected non-http scheme %r for vid %s", scheme, vid)
        return False

    # SSRF protection
    ha_url = config.get("ha_url", "") if config else ""
    if _is_ssrf_blocked(image_url, ha_url):
        log.warning("cache_provider_image: SSRF blocked for vid %s url %r", vid, image_url)
        return False

    base = DATA_DIR / "vehicles" / safe_vid
    auto_path = (base / "auto.webp").resolve()
    # Path traversal guard
    if not str(auto_path).startswith(str((DATA_DIR / "vehicles").resolve()) + "/"):
        log.warning("cache_provider_image: path traversal detected for vid %r", vid)
        return False

    # Freshness check — skip if recently cached
    if auto_path.exists():
        age = time.time() - auto_path.stat().st_mtime
        if age < _AUTO_IMG_FRESHNESS_SECS:
            return True

    try:
        import requests
        headers = {}
        if source == "ha" and config.get("ha_token"):
            headers["Authorization"] = f"Bearer {config['ha_token']}"

        resp = requests.get(image_url, headers=headers, timeout=15, stream=True)
        resp.raise_for_status()

        # MIME type check
        content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in ("image/jpeg", "image/png", "image/webp"):
            log.warning("cache_provider_image: rejected MIME %r for vid %s", content_type, vid)
            return False

        # Stream with 3 MB cap
        max_bytes = 3 * 1024 * 1024
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > max_bytes:
                log.warning("cache_provider_image: image too large for vid %s", vid)
                return False
            chunks.append(chunk)
        raw = b"".join(chunks)

        # PIL validation + save as WEBP
        from PIL import Image as _PilImage
        import io as _io
        img = _PilImage.open(_io.BytesIO(raw))
        img.verify()
        img = _PilImage.open(_io.BytesIO(raw))

        base.mkdir(parents=True, exist_ok=True)
        img.save(str(auto_path), "WEBP", quality=85)
        log.info("cache_provider_image: saved auto.webp for vid %s (source=%s)", vid, source)
        return True
    except Exception as e:
        log.warning("cache_provider_image: failed for vid %s: %s", vid, e)
        return False
