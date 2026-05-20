"""
PDF Export — Lade-Reports als PDF via reportlab.
Falls reportlab nicht installiert ist, wird ein klarer Fehler zurückgegeben.
"""
import io
import logging
from datetime import datetime

log = logging.getLogger(__name__)

_REPORTLAB_AVAILABLE = False
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm, cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, KeepTogether,
    )
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    _REPORTLAB_AVAILABLE = True
except ImportError:
    pass

_DE_MONTHS = ["Januar","Februar","März","April","Mai","Juni",
              "Juli","August","September","Oktober","November","Dezember"]
_EN_MONTHS = ["January","February","March","April","May","June",
              "July","August","September","October","November","December"]

C_ACCENT  = colors.HexColor("#6ee7b7")
C_DARK    = colors.HexColor("#1e2030")
C_HEADER  = colors.HexColor("#2d3147")
C_TEXT    = colors.HexColor("#1a1a2e")
C_MUTED   = colors.HexColor("#888888")
C_ROW_ALT = colors.HexColor("#f4f6f8")
C_WHITE   = colors.white
C_RED     = colors.HexColor("#f87171")


def _t(key: str, lang: str) -> str:
    """Translate a label key."""
    DE = {
        "title": "Ladeprotokoll",
        "period": "Zeitraum",
        "vehicle": "Fahrzeug",
        "driver": "Fahrer",
        "license": "Kennzeichen",
        "cost_center": "Kostenstelle",
        "filter": "Filter",
        "summary": "Zusammenfassung",
        "sessions": "Ladevorgänge",
        "total_kwh": "Gesamt kWh",
        "total_cost": "Gesamtkosten",
        "avg_price": "Ø Preis/kWh",
        "total_hours": "Ladezeit gesamt",
        "home": "Zuhause",
        "external": "Extern",
        "all": "Alle",
        "date": "Datum",
        "start": "Start",
        "end": "Ende",
        "kwh": "kWh",
        "cost": "Kosten",
        "location": "Standort",
        "soc_start": "SOC Start",
        "soc_end": "SOC Ende",
        "odo": "KM-Stand",
        "total": "Gesamt",
        "signature": "Unterschrift",
        "generated": "Erstellt am",
        "report_id": "Report-ID",
        "no_sessions": "Keine Ladevorgänge im gewählten Zeitraum.",
        "reimbursement": "Erstattungsbetrag",
        "reimb_rate": "Erstattungssatz",
    }
    EN = {
        "title": "Charging Log",
        "period": "Period",
        "vehicle": "Vehicle",
        "driver": "Driver",
        "license": "License Plate",
        "cost_center": "Cost Center",
        "filter": "Filter",
        "summary": "Summary",
        "sessions": "Sessions",
        "total_kwh": "Total kWh",
        "total_cost": "Total Cost",
        "avg_price": "Avg Price/kWh",
        "total_hours": "Total charge time",
        "home": "Home",
        "external": "External",
        "all": "All",
        "date": "Date",
        "start": "Start",
        "end": "End",
        "kwh": "kWh",
        "cost": "Cost",
        "location": "Location",
        "soc_start": "SOC Start",
        "soc_end": "SOC End",
        "odo": "Odometer",
        "total": "Total",
        "signature": "Signature",
        "generated": "Generated",
        "report_id": "Report ID",
        "no_sessions": "No charging sessions in the selected period.",
        "reimbursement": "Reimbursement",
        "reimb_rate": "Reimbursement rate",
    }
    table = EN if lang == "en" else DE
    return table.get(key, key)


def _fmt_date(ts_str: str, lang: str) -> str:
    if not ts_str:
        return ""
    try:
        d = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return d.strftime("%Y-%m-%d" if lang == "en" else "%d.%m.%Y")
    except Exception:
        return ts_str[:10] if ts_str else ""


def _fmt_time(ts_str: str) -> str:
    if not ts_str:
        return ""
    try:
        d = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return d.strftime("%H:%M")
    except Exception:
        return ""


def generate_report_pdf(
    sessions: list,
    period_info: dict,
    config: dict,
    lang: str = "de",
    include_signature: bool = False,
    signature_path: str = None,
    report_id: str = None,
    billing_config: dict = None,
) -> bytes:
    """
    Generate a PDF report. Returns bytes.
    Raises RuntimeError if reportlab is not installed.
    """
    if not _REPORTLAB_AVAILABLE:
        raise RuntimeError(
            "reportlab nicht installiert. Bitte 'reportlab>=4.0.0' zu requirements.txt hinzufügen und Docker-Image neu bauen."
        )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=20*mm, leftMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )

    styles = getSampleStyleSheet()
    style_h1 = ParagraphStyle("H1", parent=styles["Normal"],
                               fontSize=16, leading=20, textColor=C_TEXT,
                               fontName="Helvetica-Bold", spaceAfter=4)
    style_h2 = ParagraphStyle("H2", parent=styles["Normal"],
                               fontSize=11, leading=14, textColor=C_TEXT,
                               fontName="Helvetica-Bold", spaceAfter=2)
    style_normal = ParagraphStyle("Normal2", parent=styles["Normal"],
                                   fontSize=9, leading=12, textColor=C_TEXT)
    style_muted = ParagraphStyle("Muted", parent=styles["Normal"],
                                  fontSize=8, leading=11, textColor=C_MUTED)
    style_center = ParagraphStyle("Center", parent=style_normal,
                                   alignment=TA_CENTER)

    is_de = lang != "en"
    plabel = period_info.get("label_de" if is_de else "label_en",
                             period_info.get("period_key", ""))
    car_name = config.get("car_name", "EV")
    bc = billing_config or {}
    driver  = bc.get("driver_name") or config.get("template_fahrer", "")
    plate   = bc.get("license_plate") or config.get("template_kennzeichen", "")
    dept    = bc.get("department") or config.get("template_abteilung", "")
    cc      = bc.get("cost_center") or config.get("template_kostenstelle", "")

    # Aggregates
    total_kwh  = sum(s.get("kwh_charged") or 0 for s in sessions)
    total_cost = sum(s.get("cost_eur") or 0 for s in sessions)
    total_secs = sum(s.get("duration_sec") or 0 for s in sessions)
    total_h    = total_secs / 3600
    avg_price  = total_cost / total_kwh if total_kwh else 0
    n          = len(sessions)
    home_kwh   = sum((s.get("kwh_charged") or 0) for s in sessions if s.get("location") == "home")
    ext_kwh    = sum((s.get("kwh_charged") or 0) for s in sessions if s.get("location") == "extern")

    reimb_rate  = float(bc.get("reimbursement_price_per_kwh") or 0)
    reimb_total = total_kwh * reimb_rate if reimb_rate else 0
    loc_filter  = config.get("report_email_location_filter", "all")
    if loc_filter == "home":
        reimb_total = home_kwh * reimb_rate
    elif loc_filter in ("external", "extern"):
        reimb_total = ext_kwh * reimb_rate

    story = []

    # ── Header band ──────────────────────────────────────────────────────────
    header_data = [[
        Paragraph(f"⚡ EV Tracker — {_t('title', lang)}", style_h1),
        Paragraph(f"{_t('generated', lang)}: {datetime.now().strftime('%d.%m.%Y %H:%M')}" +
                  (f"<br/>ID: {report_id}" if report_id else ""),
                  style_muted),
    ]]
    header_tbl = Table(header_data, colWidths=[120*mm, 50*mm])
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,-1), C_DARK),
        ("TEXTCOLOR",   (0,0), (-1,-1), C_WHITE),
        ("ALIGN",       (1,0), (1,-1), "RIGHT"),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 10),
        ("RIGHTPADDING",(0,0), (-1,-1), 10),
        ("TOPPADDING",  (0,0), (-1,-1), 10),
        ("BOTTOMPADDING",(0,0),(-1,-1),10),
        ("ROWBACKGROUNDS",(0,0),(-1,-1),[C_DARK]),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 6*mm))

    # ── Meta info ────────────────────────────────────────────────────────────
    meta_rows = [
        [_t("period", lang),  plabel],
        [_t("vehicle", lang), car_name],
    ]
    if driver:  meta_rows.append([_t("driver", lang),  driver])
    if plate:   meta_rows.append([_t("license", lang), plate])
    if dept:    meta_rows.append(["Abteilung" if is_de else "Department", dept])
    if cc:      meta_rows.append([_t("cost_center", lang), cc])
    loc_labels = {"all": _t("all", lang), "home": _t("home", lang),
                  "external": _t("external", lang), "extern": _t("external", lang)}
    meta_rows.append([_t("filter", lang), loc_labels.get(loc_filter, loc_filter)])

    meta_data = [[Paragraph(f"<b>{k}</b>", style_normal),
                  Paragraph(str(v), style_normal)] for k, v in meta_rows]
    meta_tbl = Table(meta_data, colWidths=[50*mm, 120*mm])
    meta_tbl.setStyle(TableStyle([
        ("VALIGN",   (0,0), (-1,-1), "TOP"),
        ("TOPPADDING",(0,0),(-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("ROWBACKGROUNDS",(0,0),(-1,-1),[C_WHITE, C_ROW_ALT]),
    ]))
    story.append(meta_tbl)
    story.append(Spacer(1, 5*mm))
    story.append(HRFlowable(width="100%", thickness=1, color=C_HEADER))
    story.append(Spacer(1, 4*mm))

    # ── Summary ──────────────────────────────────────────────────────────────
    story.append(Paragraph(_t("summary", lang), style_h2))
    story.append(Spacer(1, 2*mm))

    sum_rows = [
        [_t("sessions", lang),   str(n)],
        [_t("total_kwh", lang),  f"{total_kwh:.2f} kWh"],
        [_t("total_cost", lang), f"{total_cost:.2f} €"],
        [_t("avg_price", lang),  f"{avg_price:.4f} €/kWh"],
        [_t("total_hours", lang),f"{total_h:.1f} h"],
    ]
    if loc_filter == "all" and (home_kwh or ext_kwh):
        sum_rows.append([f"{_t('home',lang)} / {_t('external',lang)}",
                         f"{home_kwh:.2f} / {ext_kwh:.2f} kWh"])
    if reimb_rate:
        sum_rows.append([_t("reimb_rate", lang),    f"{reimb_rate:.4f} €/kWh"])
        sum_rows.append([_t("reimbursement", lang), f"<b>{reimb_total:.2f} €</b>"])

    sum_data = [[Paragraph(f"<b>{k}</b>", style_normal),
                 Paragraph(str(v), style_normal)] for k, v in sum_rows]
    sum_tbl = Table(sum_data, colWidths=[70*mm, 100*mm])
    sum_tbl.setStyle(TableStyle([
        ("VALIGN",          (0,0), (-1,-1), "TOP"),
        ("TOPPADDING",      (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",   (0,0), (-1,-1), 3),
        ("ROWBACKGROUNDS",  (0,0), (-1,-1), [C_WHITE, C_ROW_ALT]),
        ("BACKGROUND",      (0, len(sum_data)-1), (-1, len(sum_data)-1),
         colors.HexColor("#e8faf4") if reimb_rate else C_WHITE),
    ]))
    story.append(sum_tbl)
    story.append(Spacer(1, 5*mm))

    if not sessions:
        story.append(Paragraph(_t("no_sessions", lang), style_muted))
    else:
        # ── Session table ─────────────────────────────────────────────────
        story.append(HRFlowable(width="100%", thickness=1, color=C_HEADER))
        story.append(Spacer(1, 3*mm))
        hdrs = [_t("date",lang), _t("start",lang), _t("end",lang),
                _t("soc_start",lang), _t("soc_end",lang),
                _t("kwh",lang), _t("cost",lang), _t("location",lang)]
        col_w = [22*mm, 14*mm, 14*mm, 16*mm, 16*mm, 16*mm, 18*mm, 22*mm]
        hdr_para = [Paragraph(f"<b>{h}</b>", ParagraphStyle("TH", parent=style_muted,
                    fontSize=7, textColor=C_WHITE, alignment=TA_CENTER)) for h in hdrs]
        tbl_data = [hdr_para]
        for s in sessions:
            loc_raw = s.get("location","")
            loc_lbl = (_t("home", lang) if loc_raw == "home"
                       else _t("external", lang) if loc_raw in ("extern","external")
                       else loc_raw or "—")
            row = [
                Paragraph(_fmt_date(s.get("start_ts",""), lang), ParagraphStyle("TD", parent=style_muted, fontSize=7, alignment=TA_CENTER)),
                Paragraph(_fmt_time(s.get("start_ts","")), ParagraphStyle("TD", parent=style_muted, fontSize=7, alignment=TA_CENTER)),
                Paragraph(_fmt_time(s.get("end_ts","")),   ParagraphStyle("TD", parent=style_muted, fontSize=7, alignment=TA_CENTER)),
                Paragraph(f"{s.get('soc_start') or '—'}{'%' if s.get('soc_start') else ''}", ParagraphStyle("TD", parent=style_muted, fontSize=7, alignment=TA_CENTER)),
                Paragraph(f"{s.get('soc_end') or '—'}{'%' if s.get('soc_end') else ''}", ParagraphStyle("TD", parent=style_muted, fontSize=7, alignment=TA_CENTER)),
                Paragraph(f"{s.get('kwh_charged') or 0:.2f}", ParagraphStyle("TD", parent=style_muted, fontSize=7, alignment=TA_RIGHT)),
                Paragraph(f"{s.get('cost_eur') or 0:.2f} €",  ParagraphStyle("TD", parent=style_muted, fontSize=7, alignment=TA_RIGHT)),
                Paragraph(loc_lbl, ParagraphStyle("TD", parent=style_muted, fontSize=7, alignment=TA_CENTER)),
            ]
            tbl_data.append(row)

        # Totals row
        tbl_data.append([
            Paragraph(f"<b>{_t('total', lang)}</b>", ParagraphStyle("TDF", parent=style_muted, fontSize=7, fontName="Helvetica-Bold")),
            Paragraph("", style_muted), Paragraph("", style_muted),
            Paragraph("", style_muted), Paragraph("", style_muted),
            Paragraph(f"<b>{total_kwh:.2f}</b>", ParagraphStyle("TDF", parent=style_muted, fontSize=7, fontName="Helvetica-Bold", alignment=TA_RIGHT)),
            Paragraph(f"<b>{total_cost:.2f} €</b>", ParagraphStyle("TDF", parent=style_muted, fontSize=7, fontName="Helvetica-Bold", alignment=TA_RIGHT)),
            Paragraph("", style_muted),
        ])

        n_data = len(tbl_data)
        row_bgs = ([C_HEADER] +
                   [C_WHITE if i % 2 == 0 else C_ROW_ALT for i in range(n_data-2)] +
                   [colors.HexColor("#e8faf4")])

        session_tbl = Table(tbl_data, colWidths=col_w, repeatRows=1)
        session_tbl.setStyle(TableStyle([
            ("ROWBACKGROUNDS",  (0,0), (-1,-1), row_bgs),
            ("TEXTCOLOR",       (0,0), (-1,0),  C_WHITE),
            ("BACKGROUND",      (0,0), (-1,0),  C_HEADER),
            ("GRID",            (0,0), (-1,-1), 0.25, C_HEADER),
            ("LEFTPADDING",     (0,0), (-1,-1), 3),
            ("RIGHTPADDING",    (0,0), (-1,-1), 3),
            ("TOPPADDING",      (0,0), (-1,-1), 2),
            ("BOTTOMPADDING",   (0,0), (-1,-1), 2),
            ("VALIGN",          (0,0), (-1,-1), "MIDDLE"),
        ]))
        story.append(KeepTogether([session_tbl]))

    story.append(Spacer(1, 8*mm))

    # ── Signature area ────────────────────────────────────────────────────────
    if include_signature or bc.get("requires_signature"):
        story.append(HRFlowable(width="100%", thickness=0.5, color=C_MUTED))
        story.append(Spacer(1, 3*mm))
        sig_data = [[
            Paragraph(f"<br/><br/>____________________________<br/><font size='8' color='#888'>{_t('signature', lang)}</font>", style_normal),
            Paragraph(f"<br/><br/>____________________________<br/><font size='8' color='#888'>{'Datum' if is_de else 'Date'}</font>", style_normal),
        ]]
        sig_tbl = Table(sig_data, colWidths=[85*mm, 85*mm])
        sig_tbl.setStyle(TableStyle([
            ("VALIGN",  (0,0),(-1,-1),"BOTTOM"),
            ("ALIGN",   (0,0),(-1,-1),"CENTER"),
        ]))
        story.append(sig_tbl)

    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(
        f"<font size='7' color='#888'>EV Tracker — {datetime.now().strftime('%d.%m.%Y %H:%M')}</font>",
        style_center))

    doc.build(story)
    return buf.getvalue()


def generate_multi_month_report_pdf(
    periods_sessions: list,
    config: dict,
    lang: str = "de",
    include_signature: bool = False,
    signature_path: str = None,
    report_id: str = None,
    billing_config: dict = None,
) -> bytes:
    """
    Generate a multi-month PDF report.
    periods_sessions: list of (period_info, sessions) tuples — one per month.
    Returns bytes. Raises RuntimeError if reportlab is not installed.
    """
    if not _REPORTLAB_AVAILABLE:
        raise RuntimeError(
            "reportlab nicht installiert. Bitte 'reportlab>=4.0.0' zu requirements.txt hinzufügen."
        )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=20*mm, leftMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )

    styles = getSampleStyleSheet()
    style_h1 = ParagraphStyle("H1", parent=styles["Normal"],
                               fontSize=16, leading=20, textColor=C_TEXT,
                               fontName="Helvetica-Bold", spaceAfter=4)
    style_h2 = ParagraphStyle("H2", parent=styles["Normal"],
                               fontSize=11, leading=14, textColor=C_TEXT,
                               fontName="Helvetica-Bold", spaceAfter=2)
    style_h3 = ParagraphStyle("H3", parent=styles["Normal"],
                               fontSize=10, leading=13, textColor=C_TEXT,
                               fontName="Helvetica-Bold", spaceAfter=2)
    style_normal = ParagraphStyle("Normal2", parent=styles["Normal"],
                                   fontSize=9, leading=12, textColor=C_TEXT)
    style_muted = ParagraphStyle("Muted", parent=styles["Normal"],
                                  fontSize=8, leading=11, textColor=C_MUTED)
    style_center = ParagraphStyle("Center", parent=style_normal, alignment=TA_CENTER)

    is_de = lang != "en"
    car_name = config.get("car_name", "EV")
    bc = billing_config or {}
    driver = bc.get("driver_name") or config.get("template_fahrer", "")
    plate  = bc.get("license_plate") or config.get("template_kennzeichen", "")
    dept   = bc.get("department") or config.get("template_abteilung", "")
    cc     = bc.get("cost_center") or config.get("template_kostenstelle", "")

    # Overall aggregates
    all_sessions = [s for _, slist in periods_sessions for s in slist]
    total_kwh  = sum(s.get("kwh_charged") or 0 for s in all_sessions)
    total_cost = sum(s.get("cost_eur") or 0 for s in all_sessions)
    total_secs = sum(s.get("duration_sec") or 0 for s in all_sessions)
    total_h    = total_secs / 3600
    avg_price  = total_cost / total_kwh if total_kwh else 0
    n_months   = len(periods_sessions)
    home_kwh   = sum((s.get("kwh_charged") or 0) for s in all_sessions if s.get("location") == "home")
    ext_kwh    = sum((s.get("kwh_charged") or 0) for s in all_sessions if s.get("location") == "extern")
    reimb_rate  = float(bc.get("reimbursement_price_per_kwh") or 0)
    reimb_total = total_kwh * reimb_rate if reimb_rate else 0

    # Compact period label: "Januar – Mai 2026" or individual months
    period_labels = []
    for pi, _ in periods_sessions:
        lbl = pi.get("label_de" if is_de else "label_en") or pi.get("period_key", "")
        period_labels.append(lbl)
    if n_months == 1:
        combined_label = period_labels[0]
    else:
        combined_label = ", ".join(period_labels)

    story = []

    # ── Header band ──────────────────────────────────────────────────────────
    header_data = [[
        Paragraph(f"⚡ EV Tracker — {_t('title', lang)} ({n_months} {'Monate' if is_de else 'Months'})", style_h1),
        Paragraph(f"{_t('generated', lang)}: {datetime.now().strftime('%d.%m.%Y %H:%M')}" +
                  (f"<br/>ID: {report_id}" if report_id else ""), style_muted),
    ]]
    header_tbl = Table(header_data, colWidths=[120*mm, 50*mm])
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,-1), C_DARK),
        ("TEXTCOLOR",    (0,0), (-1,-1), C_WHITE),
        ("ALIGN",        (1,0), (1,-1), "RIGHT"),
        ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING",  (0,0), (-1,-1), 10),
        ("RIGHTPADDING", (0,0), (-1,-1), 10),
        ("TOPPADDING",   (0,0), (-1,-1), 10),
        ("BOTTOMPADDING",(0,0), (-1,-1), 10),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 6*mm))

    # ── Meta info ─────────────────────────────────────────────────────────────
    meta_rows = [
        [_t("period", lang), combined_label],
        [_t("vehicle", lang), car_name],
    ]
    if driver: meta_rows.append([_t("driver", lang), driver])
    if plate:  meta_rows.append([_t("license", lang), plate])
    if dept:   meta_rows.append(["Abteilung" if is_de else "Department", dept])
    if cc:     meta_rows.append([_t("cost_center", lang), cc])
    meta_data = [[Paragraph(f"<b>{k}</b>", style_normal),
                  Paragraph(str(v), style_normal)] for k, v in meta_rows]
    meta_tbl = Table(meta_data, colWidths=[50*mm, 120*mm])
    meta_tbl.setStyle(TableStyle([
        ("VALIGN",          (0,0), (-1,-1), "TOP"),
        ("TOPPADDING",      (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",   (0,0), (-1,-1), 3),
        ("ROWBACKGROUNDS",  (0,0), (-1,-1), [C_WHITE, C_ROW_ALT]),
    ]))
    story.append(meta_tbl)
    story.append(Spacer(1, 5*mm))
    story.append(HRFlowable(width="100%", thickness=1, color=C_HEADER))
    story.append(Spacer(1, 4*mm))

    # ── Overall summary ───────────────────────────────────────────────────────
    story.append(Paragraph(_t("summary", lang), style_h2))
    story.append(Spacer(1, 2*mm))
    sum_rows = [
        ["Monate" if is_de else "Months",          str(n_months)],
        [_t("sessions", lang),                      str(len(all_sessions))],
        [_t("total_kwh", lang),                     f"{total_kwh:.2f} kWh"],
        [_t("total_cost", lang),                    f"{total_cost:.2f} €"],
        [_t("avg_price", lang),                     f"{avg_price:.4f} €/kWh"],
        [_t("total_hours", lang),                   f"{total_h:.1f} h"],
    ]
    if home_kwh or ext_kwh:
        sum_rows.append([f"{_t('home',lang)} / {_t('external',lang)}",
                         f"{home_kwh:.2f} / {ext_kwh:.2f} kWh"])
    if reimb_rate:
        sum_rows.append([_t("reimb_rate", lang),    f"{reimb_rate:.4f} €/kWh"])
        sum_rows.append([_t("reimbursement", lang), f"<b>{reimb_total:.2f} €</b>"])

    sum_data = [[Paragraph(f"<b>{k}</b>", style_normal),
                 Paragraph(str(v), style_normal)] for k, v in sum_rows]
    sum_tbl = Table(sum_data, colWidths=[70*mm, 100*mm])
    sum_tbl.setStyle(TableStyle([
        ("VALIGN",         (0,0), (-1,-1), "TOP"),
        ("TOPPADDING",     (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",  (0,0), (-1,-1), 3),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [C_WHITE, C_ROW_ALT]),
    ]))
    story.append(sum_tbl)
    story.append(Spacer(1, 5*mm))

    # ── Monthly overview table ────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=1, color=C_HEADER))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph("Monatsübersicht" if is_de else "Monthly Overview", style_h2))
    story.append(Spacer(1, 2*mm))

    ov_hdrs = ["Monat" if is_de else "Month", _t("sessions",lang),
               _t("total_kwh",lang), _t("total_cost",lang), _t("avg_price",lang)]
    ov_hdr_para = [Paragraph(f"<b>{h}</b>", ParagraphStyle("OVH", parent=style_muted,
                   fontSize=8, textColor=C_WHITE, alignment=TA_CENTER)) for h in ov_hdrs]
    ov_data = [ov_hdr_para]
    for pi, slist in periods_sessions:
        m_kwh  = sum(s.get("kwh_charged") or 0 for s in slist)
        m_cost = sum(s.get("cost_eur") or 0 for s in slist)
        m_avg  = m_cost / m_kwh if m_kwh else 0
        lbl    = pi.get("label_de" if is_de else "label_en") or pi.get("period_key", "")
        style_td = ParagraphStyle("TD", parent=style_muted, fontSize=8, alignment=TA_CENTER)
        style_tr = ParagraphStyle("TR", parent=style_muted, fontSize=8, alignment=TA_RIGHT)
        ov_data.append([
            Paragraph(lbl, style_td),
            Paragraph(str(len(slist)), style_td),
            Paragraph(f"{m_kwh:.2f}", style_tr),
            Paragraph(f"{m_cost:.2f} €", style_tr),
            Paragraph(f"{m_avg:.4f}", style_tr),
        ])

    ov_n = len(ov_data)
    ov_row_bgs = ([C_HEADER] + [C_WHITE if i%2==0 else C_ROW_ALT for i in range(ov_n-1)])
    ov_tbl = Table(ov_data, colWidths=[50*mm, 22*mm, 32*mm, 32*mm, 34*mm])
    ov_tbl.setStyle(TableStyle([
        ("ROWBACKGROUNDS",  (0,0), (-1,-1), ov_row_bgs),
        ("TEXTCOLOR",       (0,0), (-1,0),  C_WHITE),
        ("BACKGROUND",      (0,0), (-1,0),  C_HEADER),
        ("GRID",            (0,0), (-1,-1), 0.25, C_HEADER),
        ("LEFTPADDING",     (0,0), (-1,-1), 4),
        ("RIGHTPADDING",    (0,0), (-1,-1), 4),
        ("TOPPADDING",      (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",   (0,0), (-1,-1), 3),
        ("VALIGN",          (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(ov_tbl)
    story.append(Spacer(1, 6*mm))

    # ── Per-month sections ────────────────────────────────────────────────────
    for pi, slist in periods_sessions:
        lbl = pi.get("label_de" if is_de else "label_en") or pi.get("period_key", "")
        story.append(HRFlowable(width="100%", thickness=0.5, color=C_MUTED))
        story.append(Spacer(1, 3*mm))
        story.append(Paragraph(lbl, style_h3))
        story.append(Spacer(1, 1*mm))
        if not slist:
            story.append(Paragraph(_t("no_sessions", lang), style_muted))
            story.append(Spacer(1, 4*mm))
            continue

        m_kwh  = sum(s.get("kwh_charged") or 0 for s in slist)
        m_cost = sum(s.get("cost_eur") or 0 for s in slist)
        m_avg  = m_cost / m_kwh if m_kwh else 0
        m_h    = sum(s.get("duration_sec") or 0 for s in slist) / 3600

        info_rows = [
            [_t("sessions",lang), str(len(slist)),
             _t("total_kwh",lang), f"{m_kwh:.2f} kWh",
             _t("total_cost",lang), f"{m_cost:.2f} €"],
            [_t("avg_price",lang), f"{m_avg:.4f} €/kWh",
             _t("total_hours",lang), f"{m_h:.1f} h",
             "", ""],
        ]
        info_data = [[Paragraph(f"<b>{c}</b>" if i%2==0 else c, style_muted)
                      for i,c in enumerate(row)] for row in info_rows]
        info_tbl = Table(info_data, colWidths=[32*mm,30*mm,30*mm,30*mm,30*mm,18*mm])
        info_tbl.setStyle(TableStyle([
            ("VALIGN",       (0,0),(-1,-1),"TOP"),
            ("TOPPADDING",   (0,0),(-1,-1),2),
            ("BOTTOMPADDING",(0,0),(-1,-1),2),
            ("ROWBACKGROUNDS",(0,0),(-1,-1),[C_ROW_ALT, C_WHITE]),
        ]))
        story.append(info_tbl)
        story.append(Spacer(1, 2*mm))

        # Session rows for this month
        hdrs = [_t("date",lang), _t("start",lang), _t("end",lang),
                _t("soc_start",lang), _t("soc_end",lang),
                _t("kwh",lang), _t("cost",lang), _t("location",lang)]
        col_w = [22*mm, 14*mm, 14*mm, 16*mm, 16*mm, 16*mm, 18*mm, 22*mm]
        hdr_para = [Paragraph(f"<b>{h}</b>", ParagraphStyle("TH2", parent=style_muted,
                    fontSize=7, textColor=C_WHITE, alignment=TA_CENTER)) for h in hdrs]
        tbl_data2 = [hdr_para]
        for s in slist:
            loc_raw = s.get("location","")
            loc_lbl = (_t("home",lang) if loc_raw=="home"
                       else _t("external",lang) if loc_raw in ("extern","external")
                       else loc_raw or "—")
            td_s = ParagraphStyle("TD2", parent=style_muted, fontSize=7, alignment=TA_CENTER)
            td_r = ParagraphStyle("TDR2",parent=style_muted, fontSize=7, alignment=TA_RIGHT)
            tbl_data2.append([
                Paragraph(_fmt_date(s.get("start_ts",""), lang), td_s),
                Paragraph(_fmt_time(s.get("start_ts","")), td_s),
                Paragraph(_fmt_time(s.get("end_ts","")),   td_s),
                Paragraph(f"{s.get('soc_start') or '—'}{'%' if s.get('soc_start') else ''}", td_s),
                Paragraph(f"{s.get('soc_end') or '—'}{'%' if s.get('soc_end') else ''}", td_s),
                Paragraph(f"{s.get('kwh_charged') or 0:.2f}", td_r),
                Paragraph(f"{s.get('cost_eur') or 0:.2f} €", td_r),
                Paragraph(loc_lbl, td_s),
            ])
        # Month totals row
        tbl_data2.append([
            Paragraph(f"<b>{_t('total',lang)}</b>", ParagraphStyle("TDF2",parent=style_muted,fontSize=7,fontName="Helvetica-Bold")),
            Paragraph("",style_muted), Paragraph("",style_muted),
            Paragraph("",style_muted), Paragraph("",style_muted),
            Paragraph(f"<b>{m_kwh:.2f}</b>", ParagraphStyle("TDF2R",parent=style_muted,fontSize=7,fontName="Helvetica-Bold",alignment=TA_RIGHT)),
            Paragraph(f"<b>{m_cost:.2f} €</b>", ParagraphStyle("TDF2RC",parent=style_muted,fontSize=7,fontName="Helvetica-Bold",alignment=TA_RIGHT)),
            Paragraph("",style_muted),
        ])
        nd2 = len(tbl_data2)
        row_bgs2 = ([C_HEADER] +
                    [C_WHITE if i%2==0 else C_ROW_ALT for i in range(nd2-2)] +
                    [colors.HexColor("#e8faf4")])
        sess_tbl2 = Table(tbl_data2, colWidths=col_w, repeatRows=1)
        sess_tbl2.setStyle(TableStyle([
            ("ROWBACKGROUNDS", (0,0), (-1,-1), row_bgs2),
            ("TEXTCOLOR",      (0,0), (-1,0),  C_WHITE),
            ("BACKGROUND",     (0,0), (-1,0),  C_HEADER),
            ("GRID",           (0,0), (-1,-1), 0.25, C_HEADER),
            ("LEFTPADDING",    (0,0), (-1,-1), 3),
            ("RIGHTPADDING",   (0,0), (-1,-1), 3),
            ("TOPPADDING",     (0,0), (-1,-1), 2),
            ("BOTTOMPADDING",  (0,0), (-1,-1), 2),
            ("VALIGN",         (0,0), (-1,-1), "MIDDLE"),
        ]))
        story.append(KeepTogether([sess_tbl2]))
        story.append(Spacer(1, 5*mm))

    # ── Signature ─────────────────────────────────────────────────────────────
    if include_signature or bc.get("requires_signature"):
        story.append(HRFlowable(width="100%", thickness=0.5, color=C_MUTED))
        story.append(Spacer(1, 3*mm))
        sig_data = [[
            Paragraph(f"<br/><br/>____________________________<br/><font size='8' color='#888'>{_t('signature', lang)}</font>", style_normal),
            Paragraph(f"<br/><br/>____________________________<br/><font size='8' color='#888'>{'Datum' if is_de else 'Date'}</font>", style_normal),
        ]]
        sig_tbl = Table(sig_data, colWidths=[85*mm, 85*mm])
        sig_tbl.setStyle(TableStyle([
            ("VALIGN", (0,0),(-1,-1),"BOTTOM"),
            ("ALIGN",  (0,0),(-1,-1),"CENTER"),
        ]))
        story.append(sig_tbl)

    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(
        f"<font size='7' color='#888'>EV Tracker — {datetime.now().strftime('%d.%m.%Y %H:%M')}</font>",
        style_center))

    doc.build(story)
    return buf.getvalue()
