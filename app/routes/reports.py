"""
Report archive, creation, download, send and approval routes.
"""
import json
from datetime import datetime
from io import BytesIO

from flask import Blueprint, jsonify, request, Response

from core.db import _get_db, close_db_if_owned, DB_PATH
from core.config import load_config
from core.security import require_login, has_permission, _current_user, _audit

reports_bp = Blueprint("reports", __name__)


@reports_bp.route("/api/reports/archive")
@require_login
def api_reports_archive():
    if not has_permission(_current_user(), "reports:archive"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    try: limit = int(request.args.get("limit", 100))
    except (ValueError, TypeError): limit = 100
    vehicle_id = request.args.get("vehicle_id")
    con = _get_db()
    _cols = ("id,created_at,vehicle_id,period_start,period_end,period_label,period_mode,"
             "location_filter,vehicle_filter,status,created_by,sent_at,recipients,approval_status,"
             "(CASE WHEN excel_bytes IS NOT NULL THEN 1 ELSE 0 END) as has_excel,"
             "(CASE WHEN pdf_bytes   IS NOT NULL THEN 1 ELSE 0 END) as has_pdf")
    if vehicle_id:
        rows = con.execute(
            f"SELECT {_cols} FROM reports WHERE vehicle_id=? ORDER BY id DESC LIMIT ?",
            (vehicle_id, limit)).fetchall()
    else:
        rows = con.execute(
            f"SELECT {_cols} FROM reports ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
    close_db_if_owned(con)
    result = []
    for r in rows:
        d = dict(r)
        try: d["recipients"] = json.loads(d.get("recipients") or "[]")
        except Exception: pass
        result.append(d)
    return jsonify(result)


@reports_bp.route("/api/reports/create", methods=["POST"])
@require_login
def api_reports_create():
    from services.report_service import _save_report_record, _get_report_sessions, calculate_report_periods, _month_period
    import logging
    log = logging.getLogger(__name__)
    if not has_permission(_current_user(), "reports:send"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    data = request.get_json(force=True) or {}
    cfg  = load_config()

    # Direct parameters (manual archive creation) — no email config keys involved
    loc_filter = data.get("location_filter",  data.get("report_email_location_filter",  cfg.get("report_email_location_filter","all")))
    veh_filter = data.get("vehicle_filter",   data.get("report_email_vehicle_filter",   cfg.get("report_email_vehicle_filter","all")))
    lang       = data.get("lang", cfg.get("report_email_language","de"))
    if lang == "auto": lang = "de"

    # Period resolution: prefer explicit year+month, then single_month string, then mode
    pmode = None
    if data.get("year") and data.get("month"):
        from calendar import monthrange
        y, m = int(data["year"]), int(data["month"])
        period_info = _month_period(f"{y:04d}-{m:02d}")
        periods = [period_info] if period_info else []
    elif data.get("single_month"):                          # YYYY-MM from month picker
        period_info = _month_period(data["single_month"])
        periods = [period_info] if period_info else []
    else:
        # Fallback: use period_mode (previous/current) — no email config written
        pmode = data.get("period_mode", data.get("report_email_period_mode", "previous_period"))
        stype = data.get("schedule_type", "monthly")
        # Build a throw-away cfg copy so original config is never mutated
        tmp_cfg = dict(cfg)
        tmp_cfg["report_email_period_mode"]   = pmode
        tmp_cfg["report_email_schedule_type"] = stype
        if data.get("report_email_single_month"):
            tmp_cfg["report_email_single_month"] = data["report_email_single_month"]
        if data.get("report_email_months"):
            tmp_cfg["report_email_months"] = data["report_email_months"]
        periods = calculate_report_periods(stype, pmode, datetime.now(), tmp_cfg)

    if not periods:
        return jsonify({"error": "Ungültiger Zeitraum"}), 400

    user = _current_user()

    # Build combined period_key for multi-month
    if pmode == "multiple_months" and len(periods) > 1:
        combined_key = "months:" + ",".join(
            p["period_key"].replace("monthly:", "") for p in periods)
        month_labels = [p.get("label_de") or p["period_key"].replace("monthly:","") for p in periods]
        period_label = ", ".join(month_labels)
    else:
        combined_key = periods[0]["period_key"]
        period_label = periods[0].get("label_de", combined_key)

    # Collect sessions across all periods
    all_sessions = []
    months_summary = []
    for p in periods:
        ss = _get_report_sessions(p["start"], p["end"], loc_filter, veh_filter)
        all_sessions.extend(ss)
        months_summary.append({
            "period_key": p["period_key"],
            "label": p.get("label_de",""),
            "n": len(ss),
            "total_kwh": round(sum(s.get("kwh_charged") or 0 for s in ss), 3),
            "total_cost": round(sum(s.get("cost_eur") or 0 for s in ss), 2),
        })
    summary = {
        "n": len(all_sessions),
        "total_kwh": round(sum(s.get("kwh_charged") or 0 for s in all_sessions), 3),
        "total_cost": round(sum(s.get("cost_eur") or 0 for s in all_sessions), 2),
        "months": months_summary,
        "period_key": combined_key,
        "period_label": period_label,
    }

    # Build a representative period_info for the archive record
    period_info = dict(periods[0])
    if len(periods) > 1:
        period_info["period_key"] = combined_key
        period_info["label_de"]   = period_label
        period_info["end"]        = periods[-1]["end"]

    # Excel
    excel_bytes = None
    excel_warnings = []
    if data.get("include_excel", True):
        try:
            if len(periods) > 1:
                from export_excel import export_multi_month_bytes as _emm
                periods_sessions = [
                    (p, _get_report_sessions(p["start"], p["end"], loc_filter, veh_filter))
                    for p in periods
                ]
                excel_bytes, _ew = _emm(periods_sessions=periods_sessions,
                                        loc_filter=loc_filter, config=cfg, lang=lang)
                excel_warnings = _ew or []
            else:
                from export_excel import export as _export_func
                from core.location import normalize_location as _nl
                xl_loc = _nl(loc_filter) if loc_filter not in ("all",) else loc_filter
                # Load template settings from config (same as export.py)
                _saved_col = cfg.get("template_column_mapping") or cfg.get("template_mapping") or {}
                _col_override = {k: v for k, v in _saved_col.items() if v} if isinstance(_saved_col, dict) else None
                _start_row = cfg.get("template_start_row")
                _header_row = cfg.get("template_header_row")
                if _start_row and not _header_row:
                    try:
                        _header_row = int(_start_row) - 1
                    except (ValueError, TypeError):
                        pass
                _raw_cm = cfg.get("template_cell_mapping") or {}
                _cell_mapping = _raw_cm if isinstance(_raw_cm, dict) else {}
                _sheet = cfg.get("template_sheet") or None
                _header_info = {
                    "fahrer":            cfg.get("template_fahrer", ""),
                    "kennzeichen":       cfg.get("template_kennzeichen", ""),
                    "abteilung":         cfg.get("template_abteilung", ""),
                    "kostenstelle":      cfg.get("template_kostenstelle", ""),
                    "price_per_kwh":     cfg.get("price_per_kwh_home", 0.30),
                    "meter_start_value": cfg.get("template_meter_start", 0.0),
                }
                _sig_mapping = cfg.get("signature_mapping") or {}
                if _sig_mapping and "cell" in _sig_mapping and "anchor_cell" not in _sig_mapping:
                    _sig_mapping = dict(_sig_mapping)
                    _sig_mapping["anchor_cell"] = _sig_mapping["cell"]
                from core.db import DATA_DIR as _DATA_DIR
                _sig_path_obj = _DATA_DIR / "signatures" / "default_signature.png"
                _include_sig = bool(data.get("include_signature", cfg.get("export_include_signature", False)))
                excel_bytes, excel_warnings = _export_func(
                    year=period_info["start"].year, month=period_info["start"].month,
                    location=xl_loc,
                    col_override=_col_override, start_row=_start_row, header_row=_header_row,
                    header_info=_header_info, cell_mapping=_cell_mapping, sheet=_sheet,
                    include_signature=_include_sig,
                    signature_path=str(_sig_path_obj) if _sig_path_obj.exists() and _include_sig else None,
                    signature_mapping=_sig_mapping,
                    lang=lang, return_warnings=True)
        except Exception as e:
            log.warning("reports/create Excel: %s", e)
            excel_warnings.append(f"Excel-Fehler: {e}")

    # PDF
    pdf_bytes = None
    if data.get("include_pdf", False) and all_sessions:
        try:
            from pdf_export import generate_report_pdf
            bc_con = _get_db()
            bc_row = bc_con.execute("SELECT * FROM billing_config WHERE vehicle_id=?",
                                    (veh_filter,)).fetchone()
            close_db_if_owned(bc_con)
            if len(periods) > 1:
                from pdf_export import generate_multi_month_report_pdf as _gmm
                _periods_sessions = [
                    (p, _get_report_sessions(p["start"], p["end"], loc_filter, veh_filter))
                    for p in periods
                ]
                pdf_bytes = _gmm(
                    _periods_sessions, cfg, lang=lang,
                    include_signature=data.get("include_signature", False),
                    billing_config=dict(bc_row) if bc_row else None)
            else:
                pdf_bytes = generate_report_pdf(
                    all_sessions, period_info, cfg, lang=lang,
                    include_signature=data.get("include_signature", False),
                    billing_config=dict(bc_row) if bc_row else None)
        except Exception as e:
            log.warning("reports/create PDF: %s", e)

    report_id = _save_report_record(
        veh_filter, period_info, loc_filter, veh_filter, "generated",
        user["id"] if user else None, excel_bytes, pdf_bytes, summary)
    _audit("report_created", f"id={report_id} period={combined_key}", ip=request.remote_addr)
    try:
        from notification_manager import fire_event as _fe
        _fe("report_created", {"report_id": report_id, "period": combined_key,
                                "sessions": len(all_sessions)}, cfg, db_path=DB_PATH)
    except Exception: pass
    all_warnings = excel_warnings[:]
    if data.get("include_pdf", False) and not pdf_bytes and all_sessions:
        all_warnings.append("PDF konnte nicht erstellt werden")
    return jsonify({"ok": True, "report_id": report_id, "summary": summary,
                    "has_excel": excel_bytes is not None, "has_pdf": pdf_bytes is not None,
                    "warnings": all_warnings})


@reports_bp.route("/api/reports/<int:report_id>/download/<fmt>")
@require_login
def api_report_download(report_id, fmt):
    if not has_permission(_current_user(), "reports:archive"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    if fmt not in ("excel", "pdf"):
        return jsonify({"error": "Ungültiges Format"}), 400
    con = _get_db()
    row = con.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    close_db_if_owned(con)
    if not row:
        return jsonify({"error": "Report nicht gefunden"}), 404
    col  = "excel_bytes" if fmt == "excel" else "pdf_bytes"
    data = row[col]
    if not data:
        return jsonify({"error": f"Kein {fmt.upper()} für diesen Report gespeichert"}), 404
    mime = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if fmt == "excel" else "application/pdf")
    ext  = "xlsx" if fmt == "excel" else "pdf"
    label = (row["period_label"] or str(report_id)).replace(" ", "_")
    return Response(data, mimetype=mime,
                    headers={"Content-Disposition": f'attachment; filename="Report_{label}.{ext}"'})


@reports_bp.route("/api/reports/<int:report_id>/send", methods=["POST"])
@require_login
def api_report_send(report_id):
    from services.email_service import _send_email_with_attachments
    if not has_permission(_current_user(), "reports:resend"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    data = request.get_json(force=True) or {}
    recipients = data.get("recipients", [])
    con = _get_db()
    row = con.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    close_db_if_owned(con)
    if not row:
        return jsonify({"error": "Report nicht gefunden"}), 404
    cfg = load_config()
    lang = data.get("lang", "de")
    subject = f"EV Tracker — Report {row['period_label'] or report_id}"
    html = f"<p>EV Tracker Report: <b>{row['period_label']}</b></p>"
    attachments = []
    if row["excel_bytes"]:
        attachments.append(("Report.xlsx", row["excel_bytes"],
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
    if row["pdf_bytes"]:
        attachments.append(("Report.pdf", row["pdf_bytes"], "application/pdf"))
    errors = []
    for to in (recipients or json.loads(row.get("recipients") or "[]")):
        ok, err = _send_email_with_attachments(to, subject, html, attachments)
        if not ok: errors.append(f"{to}: {err}")
    if errors:
        try:
            from notification_manager import fire_event as _fe
            _fe("report_failed", {"report_id": report_id, "error": "; ".join(errors)},
                load_config(), db_path=DB_PATH)
        except Exception: pass
        return jsonify({"ok": False, "error": "; ".join(errors)})
    now_iso = datetime.utcnow().isoformat()
    _con = _get_db()
    _con.execute("UPDATE reports SET status='sent', sent_at=?, recipients=? WHERE id=?",
                 (now_iso, json.dumps(recipients), report_id))
    _con.commit(); close_db_if_owned(_con)
    _audit("report_sent", f"id={report_id}", ip=request.remote_addr)
    try:
        from notification_manager import fire_event as _fe
        _fe("report_sent", {"report_id": report_id, "recipients": recipients},
            load_config(), db_path=DB_PATH)
    except Exception: pass
    return jsonify({"ok": True})


@reports_bp.route("/api/reports/<int:report_id>/approve", methods=["POST"])
@require_login
def api_report_approve(report_id):
    if not has_permission(_current_user(), "reports:approve"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    con = _get_db()
    con.execute("UPDATE reports SET approval_status='approved', status='approved' WHERE id=?",
                (report_id,))
    con.commit(); close_db_if_owned(con)
    _audit("report_approved", f"id={report_id}", ip=request.remote_addr)
    return jsonify({"ok": True})
