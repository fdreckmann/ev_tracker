"""
PDF export routes.
"""
import time
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file, Response

from core.db import _get_db, close_db_if_owned
from core.config import load_config
from core.security import require_login, has_permission, _current_user, _audit
import core.state as _state

pdf_export_bp = Blueprint("pdf_export", __name__)


# ── POST /api/export/pdf ──────────────────────────────────────────────────────
@pdf_export_bp.route("/api/export/pdf", methods=["POST"])
@require_login
def api_export_pdf():
    from datetime import datetime
    if not has_permission(_current_user(), "export:pdf"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    data = request.get_json(force=True) or {}
    cfg  = load_config()
    try:
        from pdf_export import generate_report_pdf, _REPORTLAB_AVAILABLE
        if not _REPORTLAB_AVAILABLE:
            return jsonify({"error": "PDF nicht verfügbar — reportlab fehlt (requirements.txt anpassen und Docker-Image neu bauen)"}), 503
    except ImportError:
        return jsonify({"error": "pdf_export Modul nicht gefunden"}), 503

    stype      = data.get("schedule_type", cfg.get("report_email_schedule_type", "monthly"))
    pmode      = data.get("period_mode",   data.get("report_email_period_mode", "previous_period"))
    loc_filter = data.get("location_filter", cfg.get("report_email_location_filter", "all"))
    veh_filter = data.get("vehicle_filter",  cfg.get("report_email_vehicle_filter", "all"))
    lang       = data.get("lang", cfg.get("report_pdf_language", "de"))
    if lang == "auto":
        lang = "de"

    cfg["report_email_period_mode"]   = pmode
    cfg["report_email_schedule_type"] = stype
    cfg["report_email_single_month"]  = data.get("single_month", cfg.get("report_email_single_month", ""))
    cfg["report_email_months"]        = data.get("months", cfg.get("report_email_months", []))

    from server import calculate_report_periods, _get_report_sessions, SIGNATURE_PATH
    periods     = calculate_report_periods(stype, pmode, datetime.now(), cfg)
    period_info = periods[0]

    bc_con = _get_db()
    bc_row = bc_con.execute("SELECT * FROM billing_config WHERE vehicle_id=?", (veh_filter,)).fetchone()
    close_db_if_owned(bc_con)

    include_sig = bool(data.get("include_signature", cfg.get("report_pdf_include_signature")))
    sig_path    = str(SIGNATURE_PATH) if SIGNATURE_PATH.exists() and include_sig else None
    bc_dict     = dict(bc_row) if bc_row else None

    try:
        if len(periods) > 1:
            from pdf_export import generate_multi_month_report_pdf as _gen_multi
            periods_sessions = [
                (p, _get_report_sessions(p["start"], p["end"], loc_filter, veh_filter))
                for p in periods
            ]
            pdf_bytes = _gen_multi(periods_sessions, cfg, lang=lang,
                                   include_signature=include_sig,
                                   billing_config=bc_dict)
        else:
            sessions  = _get_report_sessions(period_info["start"], period_info["end"], loc_filter, veh_filter)
            pdf_bytes = generate_report_pdf(
                sessions, period_info, cfg, lang=lang,
                include_signature=include_sig, signature_path=sig_path,
                billing_config=bc_dict)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    import secrets as _sec
    token = _sec.token_urlsafe(32)
    _state.pdf_tokens[token] = {
        "bytes":   pdf_bytes,
        "expires": datetime.utcnow().timestamp() + 1800,
        "filename": "EV_Report.pdf",
    }

    if len(periods) > 1:
        month_keys = [p.get("period_key", "").replace("monthly:", "") for p in periods]
        label = "_".join(month_keys) if month_keys else f"{len(periods)}_Monate"
    else:
        label = (period_info.get("label_de") or "Report").replace(" ", "_")

    filename = f"EV_Report_{label}.pdf"
    _state.pdf_tokens[token]["filename"] = filename
    return jsonify({"ok": True, "token": token, "filename": filename,
                    "size_bytes": len(pdf_bytes)})


# ── GET /api/export/pdf/download/<token> ──────────────────────────────────────
@pdf_export_bp.route("/api/export/pdf/download/<token>")
def api_export_pdf_download(token):
    from datetime import datetime
    entry = _state.pdf_tokens.get(token)
    if not entry or datetime.utcnow().timestamp() > entry["expires"]:
        _state.pdf_tokens.pop(token, None)
        return jsonify({"error": "Token abgelaufen oder ungültig"}), 404
    fname = entry.get("filename", "EV_Report.pdf")
    return Response(entry["bytes"], mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})
