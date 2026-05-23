"""
Vehicle image suggestion service.
Matches a vehicle's brand/model/name against the local silhouette manifest
and returns the best matching image key.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

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


def resolve_vehicle_image_url(vehicle: dict) -> str:
    """
    Return the URL that best represents this vehicle's image.
    Priority: uploaded image > default_image_key silhouette > auto-suggest silhouette > placeholder
    """
    from pathlib import Path as _Path
    from core.db import DATA_DIR

    vid = vehicle.get("id", "v0")

    # 1. Uploaded image
    img_path = DATA_DIR / "vehicles" / vid / "car.webp"
    if img_path.exists():
        return f"/api/vehicles/{vid}/image/file"

    # 2. Saved default image key (v0 uses a prefixed config key name)
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
