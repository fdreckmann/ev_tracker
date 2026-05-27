"""
Excel export routes.
"""
import json
import threading
import time
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from core.db import _get_db, close_db_if_owned, DATA_DIR
from core.config import load_config
from core.security import require_login, has_permission, _current_user, _audit
import core.state as _state

_SIGNATURE_PATH = DATA_DIR / "signatures" / "default_signature.png"

export_bp = Blueprint("export", __name__)

# ── Export token store ────────────────────────────────────────────────────────
_export_tokens: dict = {}  # token -> {"path": str, "expires": float}


def _cleanup_export_tokens():
    """Delete expired token entries and their temp files."""
    import glob as _glob
    from pathlib import Path
    now = time.time()
    expired = [k for k, v in list(_export_tokens.items()) if v["expires"] < now]
    for k in expired:
        info = _export_tokens.pop(k, None)
        if info:
            try:
                Path(info["path"]).unlink(missing_ok=True)
            except Exception:
                pass
    # Also clean up orphaned /tmp/ev_export_*.xlsx
    for fp in _glob.glob("/tmp/ev_export_*.xlsx"):
        # only delete if not referenced by any token
        if not any(v["path"] == fp for v in _export_tokens.values()):
            try:
                if Path(fp).stat().st_mtime < now - 3600:
                    Path(fp).unlink(missing_ok=True)
            except Exception:
                pass


def _parse_export_params(args_or_body, is_json=False):
    """Parse and validate year/month/col_override from request args or JSON body.
    Returns (y, m, override, error_response) where error_response is None on success."""
    from datetime import datetime as _dt
    _now = _dt.now()
    _raw_y = args_or_body.get("year", _now.year)
    _raw_m = args_or_body.get("month", _now.month)
    try:
        y = int(_raw_y)
    except (ValueError, TypeError):
        return None, None, None, (jsonify({"ok": False, "error": "year muss eine Zahl sein"}), 400)
    try:
        m = int(_raw_m)
    except (ValueError, TypeError):
        return None, None, None, (jsonify({"ok": False, "error": "month muss eine Zahl sein"}), 400)
    if not (1 <= m <= 12):
        return None, None, None, (jsonify({"ok": False, "error": "month muss zwischen 1 und 12 liegen"}), 400)
    if y < 2000 or y > 2100:
        return None, None, None, (jsonify({"ok": False, "error": "year außerhalb des gültigen Bereichs"}), 400)
    # Parse col_override
    if is_json:
        raw_override = args_or_body.get("col_override")
    else:
        _raw_override_str = args_or_body.get("col_override", "null") or "null"
        try:
            raw_override = json.loads(_raw_override_str)
        except (json.JSONDecodeError, ValueError):
            return None, None, None, (jsonify({"ok": False, "error": "col_override enthält kein gültiges JSON"}), 400)
    if raw_override is not None and not isinstance(raw_override, dict):
        return None, None, None, (jsonify({"ok": False, "error": "col_override muss ein Objekt sein"}), 400)
    return y, m, raw_override, None


@export_bp.route("/api/export")
@require_login
def api_export():
    import io as _io_exp
    import secrets
    from datetime import datetime
    from pathlib import Path
    from export_excel import export
    user = _current_user()
    if not has_permission(user, "export:create"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: export:create"}), 403
    y, m, override, _err = _parse_export_params(request.args, is_json=False)
    if _err:
        return _err
    loc = request.args.get("location", "all")
    cfg = load_config()
    if override is None:
        saved = cfg.get("template_column_mapping") or cfg.get("template_mapping") or {}
        if isinstance(saved, dict) and saved:
            override = {k: v for k, v in saved.items() if v}
        else:
            override = None
    start_row = cfg.get("template_start_row")
    header_row = cfg.get("template_header_row")
    # Backward compat: template_start_row without header_row
    if start_row and not header_row:
        try:
            header_row = int(start_row) - 1
        except (ValueError, TypeError):
            pass
    lang = request.args.get("lang") or cfg.get("export_language", "de")
    _raw_cm = cfg.get("template_cell_mapping") or {}
    cell_mapping = _raw_cm if isinstance(_raw_cm, dict) else {}
    sheet = cfg.get("template_sheet") or None
    header_info = {
        "fahrer":            cfg.get("template_fahrer", ""),
        "kennzeichen":       cfg.get("template_kennzeichen", ""),
        "abteilung":         cfg.get("template_abteilung", ""),
        "kostenstelle":      cfg.get("template_kostenstelle", ""),
        "price_per_kwh":     cfg.get("price_per_kwh_home", 0.30),
        "meter_start_value": cfg.get("template_meter_start", 0.0),
    }
    include_sig_param = request.args.get("include_signature")
    if include_sig_param is not None:
        include_signature = include_sig_param.lower() in ("true", "1", "yes")
    else:
        include_signature = bool(cfg.get("export_include_signature", False))
    _tmpl_hash = (cfg.get("active_template") or {}).get("hash")
    _map_hash = cfg.get("template_mapping_hash")
    _has_column_mapping = bool(cfg.get("template_column_mapping") or cfg.get("template_mapping"))
    if _tmpl_hash and (_map_hash is None or _map_hash != _tmpl_hash) and _has_column_mapping:
        return jsonify({
            "ok": False,
            "error": "Neues Template erkannt — bitte Mapping prüfen und erneut speichern bevor der Export gestartet wird.",
            "hash_mismatch": True,
        }), 409
    elif _tmpl_hash and _map_hash and _tmpl_hash != _map_hash:
        import logging as _log_mod
        _log_mod.getLogger(__name__).warning(
            "Template hash mismatch: template=%s mapping=%s", _tmpl_hash, _map_hash)
    footer_start_row = cfg.get("template_footer_start_row")
    sig_mapping = cfg.get("signature_mapping") or {}
    # Backward compat: "cell" → "anchor_cell" in signature_mapping
    if sig_mapping and "cell" in sig_mapping and "anchor_cell" not in sig_mapping:
        sig_mapping = dict(sig_mapping)
        sig_mapping["anchor_cell"] = sig_mapping["cell"]

    SIGNATURE_PATH = _SIGNATURE_PATH

    try:
        xlsx_bytes = export(y, m, loc, col_override=override, start_row=start_row, header_row=header_row,
                      header_info=header_info,
                      cell_mapping=cell_mapping, sheet=sheet,
                      include_signature=include_signature,
                      signature_path=str(SIGNATURE_PATH) if SIGNATURE_PATH.exists() else None,
                      signature_mapping=sig_mapping, lang=lang,
                      footer_start_row=footer_start_row)
        filename = f"EV_Ladeprotokoll_{y:04d}-{m:02d}.xlsx"
        return send_file(_io_exp.BytesIO(xlsx_bytes), as_attachment=True,
                         download_name=filename,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Export fehlgeschlagen")
        return jsonify({"ok": False, "error": str(e)}), 500


@export_bp.route("/api/export/preview", methods=["POST"])
@require_login
def api_export_preview():
    import io as _io_prev
    import secrets
    import openpyxl as _opxl_prev
    from datetime import datetime
    from pathlib import Path
    from export_excel import export as _export_func
    user = _current_user()
    if not has_permission(user, "export:preview"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: export:preview"}), 403
    body = request.get_json(silent=True) or {}
    y, m, _body_override, _perr = _parse_export_params(body, is_json=True)
    if _perr:
        return _perr
    loc  = body.get("location", "all")
    cfg  = load_config()
    lang = body.get("lang") or cfg.get("export_language", "de")

    _raw_override = body.get("col_override") or cfg.get("template_column_mapping") or {}
    override     = _raw_override if isinstance(_raw_override, dict) else {}
    start_row    = body.get("start_row") or cfg.get("template_start_row")
    header_row   = body.get("header_row") or cfg.get("template_header_row")
    if start_row and not header_row:
        try:
            header_row = int(start_row) - 1
        except (ValueError, TypeError):
            pass
    _raw_cm      = body.get("cell_mapping") or cfg.get("template_cell_mapping") or {}
    cell_mapping = _raw_cm if isinstance(_raw_cm, dict) else {}
    sheet        = body.get("sheet") or cfg.get("template_sheet")
    footer_start_row_prev = body.get("footer_start_row") or cfg.get("template_footer_start_row")
    header_info  = {
        "fahrer":            cfg.get("template_fahrer", ""),
        "kennzeichen":       cfg.get("template_kennzeichen", ""),
        "abteilung":         cfg.get("template_abteilung", ""),
        "kostenstelle":      cfg.get("template_kostenstelle", ""),
        "price_per_kwh":     cfg.get("price_per_kwh_home", 0.30),
        "meter_start_value": cfg.get("template_meter_start", 0.0),
    }
    _inc_sig_param = body.get("include_signature")
    if _inc_sig_param is not None:
        include_signature = bool(_inc_sig_param)
    else:
        include_signature = bool(cfg.get("export_include_signature", False))
    sig_mapping       = cfg.get("signature_mapping") or {}
    if sig_mapping and "cell" in sig_mapping and "anchor_cell" not in sig_mapping:
        sig_mapping = dict(sig_mapping)
        sig_mapping["anchor_cell"] = sig_mapping["cell"]

    # Hash mismatch check: block preview when a column_mapping exists for a
    # different template hash so the user doesn't download a wrong export.
    _prev_tmpl_hash   = (cfg.get("active_template") or {}).get("hash")
    _prev_map_hash    = cfg.get("template_mapping_hash")
    _prev_has_mapping = bool(cfg.get("template_column_mapping") or cfg.get("template_mapping"))
    _prev_hash_mismatch = bool(
        _prev_tmpl_hash and
        (_prev_map_hash is None or _prev_map_hash != _prev_tmpl_hash) and
        _prev_has_mapping
    )
    if _prev_hash_mismatch:
        return jsonify({
            "ok": False,
            "error": "Neues Template erkannt — bitte Mapping prüfen und erneut speichern bevor die Vorschau gestartet wird.",
            "hash_mismatch": True,
        }), 409
    _preview_warnings = []

    SIGNATURE_PATH = _SIGNATURE_PATH

    # Cleanup old tokens first
    _cleanup_export_tokens()

    try:
        xlsx_bytes, _export_warnings = _export_func(
            y, m, loc,
            col_override=override, start_row=start_row, header_row=header_row,
            header_info=header_info, cell_mapping=cell_mapping, sheet=sheet,
            include_signature=include_signature,
            signature_path=str(SIGNATURE_PATH) if SIGNATURE_PATH.exists() else None,
            signature_mapping=sig_mapping, lang=lang,
            return_warnings=True,
            footer_start_row=footer_start_row_prev,
        )
        warnings = _preview_warnings + (_export_warnings or [])
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Export-Vorschau fehlgeschlagen")
        return jsonify({"ok": False, "error": str(e)}), 500

    # Save to temp file and issue download token
    token = secrets.token_urlsafe(16)
    tmp_path = f"/tmp/ev_export_{token}.xlsx"
    try:
        with open(tmp_path, "wb") as _tf:
            _tf.write(xlsx_bytes)
        _export_tokens[token] = {"path": tmp_path, "expires": time.time() + 1800}
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Konnte Token-Datei nicht speichern: {e}")
        token = None

    # Determine data_start_row from config
    _template_config = cfg.get("template_config") or {}
    _data_start_row = int(start_row) if start_row else int(_template_config.get("start_row", 1))

    # Build grid from xlsx_bytes
    sheets_out = []
    try:
        import datetime as _dt_prev
        wb_prev = _opxl_prev.load_workbook(_io_prev.BytesIO(xlsx_bytes), data_only=True)
        for ws_p in wb_prev.worksheets:
            rows_out = []
            for row_idx_0, row_p in enumerate(ws_p.iter_rows(max_row=200, max_col=30, values_only=True)):
                row_idx = row_idx_0 + 1  # 1-based
                cells = []
                for val in row_p:
                    if val is None:
                        cells.append("")
                    elif isinstance(val, (_dt_prev.datetime, _dt_prev.date)):
                        cells.append(val.strftime("%d.%m.%Y %H:%M") if isinstance(val, _dt_prev.datetime) else val.strftime("%d.%m.%Y"))
                    else:
                        cells.append(str(val))
                rows_out.append({
                    "row": row_idx,
                    "is_data": row_idx >= _data_start_row,
                    "cells": cells,
                })
            sheets_out.append({
                "name": ws_p.title,
                "data_start_row": _data_start_row,
                "rows": rows_out,
            })
    except Exception as e:
        warnings.append(f"Grid-Erzeugung fehlgeschlagen: {e}")

    result = {
        "ok":             True,
        "sheets":         sheets_out,
        "warnings":       warnings,
        "download_token": token,
    }
    return jsonify(result)


@export_bp.route("/api/export/download/<token>")
@require_login
def api_export_download_token(token):
    """Download a previously generated export XLSX by token."""
    import re as _re_tok
    from pathlib import Path
    # Validate token format (URL-safe base64)
    if not _re_tok.match(r'^[A-Za-z0-9_-]{10,64}$', token):
        return jsonify({"error": "Ungültiger Token"}), 404
    info = _export_tokens.get(token)
    if not info:
        return jsonify({"error": "Token nicht gefunden oder abgelaufen"}), 404
    if info["expires"] < time.time():
        _export_tokens.pop(token, None)
        try:
            Path(info["path"]).unlink(missing_ok=True)
        except Exception:
            pass
        return jsonify({"error": "Token abgelaufen"}), 404
    file_path = Path(info["path"])
    if not file_path.exists():
        return jsonify({"error": "Datei nicht mehr vorhanden"}), 404
    return send_file(
        str(file_path),
        as_attachment=True,
        download_name=file_path.name.replace(f"ev_export_{token}", "EV_Export"),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
