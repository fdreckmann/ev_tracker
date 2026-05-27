"""
Signature management routes.
"""
from datetime import datetime
from flask import Blueprint, jsonify, request, send_file

from core.db import _get_db, close_db_if_owned, DATA_DIR
from core.config import load_config, save_config
from core.security import require_login, has_permission, _current_user, _audit

signatures_bp = Blueprint("signatures", __name__)

SIGNATURE_DIR  = DATA_DIR / "signatures"
SIGNATURE_PATH = SIGNATURE_DIR / "default_signature.png"


def _normalize_signature_image(img, padding=None):
    """Crop to visible content bbox, then add transparent padding."""
    from PIL import Image as _PILImage, ImageOps as _ImageOps
    cfg = load_config()
    if padding is None:
        padding = int(cfg.get("signature_padding_px", 24))
    img = img.convert("RGBA")
    alpha = img.getchannel("A")
    bbox = alpha.getbbox()
    if bbox:
        img = img.crop(bbox)
    img = _ImageOps.expand(img, border=padding, fill=(255, 255, 255, 0))
    return img


@signatures_bp.route("/api/signature")
@require_login
def api_signature_info():
    if not has_permission(_current_user(), "signature:view"):
        return jsonify({"error": "Keine Berechtigung: signature:view"}), 403
    cfg = load_config()
    sig = cfg.get("signature") or {}
    exists = SIGNATURE_PATH.exists()
    return jsonify({
        "ok": True,
        "has_signature": exists,
        "signature_url": "/api/signature/image" if exists else None,
        "source": sig.get("source"),
        "created_at": sig.get("created_at"),
    })


@signatures_bp.route("/api/signature/image")
@require_login
def api_signature_image():
    if not has_permission(_current_user(), "signature:view"):
        return jsonify({"error": "Keine Berechtigung: signature:view"}), 403
    if not SIGNATURE_PATH.exists():
        return jsonify({"error": "Keine Unterschrift"}), 404
    return send_file(str(SIGNATURE_PATH), mimetype="image/png")


@signatures_bp.route("/api/signature/upload", methods=["POST"])
@require_login
def api_signature_upload():
    if not has_permission(_current_user(), "signature:upload"):
        return jsonify({"error": "Keine Berechtigung: signature:upload"}), 403
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Keine Datei"}), 400
    f = request.files["file"]
    ext = (f.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ("png", "jpg", "jpeg", "webp"):
        return jsonify({"ok": False, "error": "Nur PNG, JPG oder WebP erlaubt"}), 400
    data = f.read()
    if len(data) > 2 * 1024 * 1024:
        return jsonify({"ok": False, "error": "Datei zu groß (max 2 MB)"}), 400
    try:
        from PIL import Image as _PILImage
        import io as _io
        img = _PILImage.open(_io.BytesIO(data))
        img = _normalize_signature_image(img)
        SIGNATURE_DIR.mkdir(parents=True, exist_ok=True)
        img.save(str(SIGNATURE_PATH), "PNG")
    except Exception as e:
        return jsonify({"ok": False, "error": f"Bildverarbeitung fehlgeschlagen: {e}"}), 500
    cfg = load_config()
    cfg["signature"] = {"source": "upload", "created_at": datetime.utcnow().isoformat()}
    save_config(cfg)
    _audit("signature_upload", ip=request.remote_addr)
    return jsonify({"ok": True})


@signatures_bp.route("/api/signature/draw", methods=["POST"])
@require_login
def api_signature_draw():
    if not has_permission(_current_user(), "signature:draw"):
        return jsonify({"error": "Keine Berechtigung: signature:draw"}), 403
    import base64 as _b64, io as _io
    body = request.get_json(force=True) or {}
    image_data = body.get("image_data", "")
    if not image_data or "base64," not in image_data:
        return jsonify({"ok": False, "error": "Ungültige Bilddaten"}), 400
    _MAX_DECODED = 2 * 1024 * 1024  # 2 MB decoded limit
    _b64_part = image_data.split("base64,", 1)[1]
    # Fast pre-check: base64 encodes 3 bytes as 4 chars → max encoded length for 2 MB
    if len(_b64_part) > (_MAX_DECODED * 4 // 3) + 64:
        return jsonify({"ok": False, "error": "Bilddaten zu groß (max 2 MB)"}), 413
    try:
        raw = _b64.b64decode(_b64_part)
        if len(raw) > _MAX_DECODED:
            return jsonify({"ok": False, "error": "Bilddaten zu groß (max 2 MB)"}), 413
        from PIL import Image as _PILImage
        img = _PILImage.open(_io.BytesIO(raw))
        img = _normalize_signature_image(img)
        SIGNATURE_DIR.mkdir(parents=True, exist_ok=True)
        img.save(str(SIGNATURE_PATH), "PNG")
    except Exception as e:
        return jsonify({"ok": False, "error": f"Fehler beim Speichern: {e}"}), 500
    cfg = load_config()
    cfg["signature"] = {"source": "draw", "created_at": datetime.utcnow().isoformat()}
    save_config(cfg)
    _audit("signature_draw", ip=request.remote_addr)
    return jsonify({"ok": True})


@signatures_bp.route("/api/signature", methods=["DELETE"])
@require_login
def api_signature_delete():
    if not has_permission(_current_user(), "signature:delete"):
        return jsonify({"error": "Keine Berechtigung: signature:delete"}), 403
    if SIGNATURE_PATH.exists():
        SIGNATURE_PATH.unlink()
    cfg = load_config()
    cfg["signature"] = {"source": None, "created_at": None}
    save_config(cfg)
    _audit("signature_delete", ip=request.remote_addr)
    return jsonify({"ok": True})
