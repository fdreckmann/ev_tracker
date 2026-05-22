"""
Email report configuration and sending routes.
"""
import json
import calendar as _calendar
from datetime import datetime, timedelta
from threading import Timer

from flask import Blueprint, jsonify, request

from core.db import _get_db, close_db_if_owned, DATA_DIR
from core.config import load_config, save_config, DEFAULT_CONFIG
from core.security import require_login, has_permission, _current_user, _audit
from version import APP_VERSION

email_reports_bp = Blueprint("email_reports", __name__)

# ── Module-level state ────────────────────────────────────────────────────────
_report_timer = None

_DE_MONTHS_FULL = ["Januar", "Februar", "März", "April", "Mai", "Juni",
                   "Juli", "August", "September", "Oktober", "November", "Dezember"]
_EN_MONTHS_FULL = ["January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November", "December"]


def _month_period(ym_str):
    """Build a period dict for a single YYYY-MM string."""
    from datetime import date
    try:
        year, month = int(ym_str[:4]), int(ym_str[5:7])
        start = date(year, month, 1)
        last = _calendar.monthrange(year, month)[1]
        end = date(year, month, last)
        return {"start": start, "end": end,
                "label_de": f"{_DE_MONTHS_FULL[month-1]} {year}",
                "label_en": f"{_EN_MONTHS_FULL[month-1]} {year}",
                "period_key": f"monthly:{year}-{month:02d}"}
    except Exception:
        return None


def calculate_report_period(schedule_type, period_mode, now, config):
    """Return dict with start, end (date objects), label_de, label_en, period_key."""
    from datetime import date, timedelta
    today = now.date() if hasattr(now, 'date') else now

    if period_mode == "single_month":
        ym = config.get("report_email_single_month", "")
        p = _month_period(ym)
        if p:
            return p
        return calculate_report_period(schedule_type, "current_period", now, config)

    if period_mode == "multiple_months":
        months = config.get("report_email_months", [])
        valid = sorted(set(m for m in months if len(m) == 7 and m[4] == "-"))
        if valid:
            p = _month_period(valid[0])
            if p:
                combined_key = "months:" + ",".join(valid)
                p = dict(p); p["period_key"] = combined_key
                return p
        return calculate_report_period(schedule_type, "current_period", now, config)

    if period_mode == "custom_range":
        s = config.get("report_email_custom_start_date", "")
        e = config.get("report_email_custom_end_date", "")
        try:
            start = date.fromisoformat(s); end = date.fromisoformat(e)
        except Exception:
            start = end = today
        return {"start": start, "end": end,
                "label_de": f"{start.strftime('%d.%m.%Y')} – {end.strftime('%d.%m.%Y')}",
                "label_en": f"{start.isoformat()} – {end.isoformat()}",
                "period_key": f"custom:{start}:{end}"}

    if schedule_type == "daily":
        d = (today - timedelta(days=1)) if period_mode == "previous_period" else today
        return {"start": d, "end": d, "label_de": d.strftime("%d.%m.%Y"),
                "label_en": d.isoformat(), "period_key": f"daily:{d}"}

    if schedule_type == "weekly":
        if period_mode == "previous_period":
            this_mon = today - timedelta(days=today.weekday())
            start = this_mon - timedelta(weeks=1)
        else:
            start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        year, week, _ = start.isocalendar()
        return {"start": start, "end": end,
                "label_de": f"KW {week:02d} / {year}", "label_en": f"Week {week:02d} / {year}",
                "period_key": f"weekly:{year}-W{week:02d}"}

    if schedule_type == "monthly":
        if period_mode == "previous_period":
            first_this = today.replace(day=1)
            end = first_this - timedelta(days=1); start = end.replace(day=1)
        else:
            start = today.replace(day=1)
            last = _calendar.monthrange(today.year, today.month)[1]
            end = today.replace(day=last)
        return {"start": start, "end": end,
                "label_de": f"{_DE_MONTHS_FULL[start.month-1]} {start.year}",
                "label_en": f"{_EN_MONTHS_FULL[start.month-1]} {start.year}",
                "period_key": f"monthly:{start.year}-{start.month:02d}"}

    if schedule_type == "quarterly":
        q = (today.month - 1) // 3 + 1; year = today.year
        if period_mode == "previous_period":
            q -= 1
            if q == 0: q = 4; year -= 1
        sm = (q - 1) * 3 + 1; em = sm + 2
        start = date(year, sm, 1)
        end   = date(year, em, _calendar.monthrange(year, em)[1])
        return {"start": start, "end": end,
                "label_de": f"Q{q} {year}", "label_en": f"Q{q} {year}",
                "period_key": f"quarterly:{year}-Q{q}"}

    if schedule_type == "yearly":
        year = (today.year - 1) if period_mode == "previous_period" else today.year
        return {"start": date(year, 1, 1), "end": date(year, 12, 31),
                "label_de": str(year), "label_en": str(year),
                "period_key": f"yearly:{year}"}

    if schedule_type == "custom_days":
        x = int(config.get("report_email_custom_days", 14))
        end = today; start = today - timedelta(days=x)
        return {"start": start, "end": end,
                "label_de": f"Letzte {x} Tage", "label_en": f"Last {x} days",
                "period_key": f"custom_days:{x}:{today}"}

    return calculate_report_period("monthly", period_mode, now, config)


def calculate_report_periods(schedule_type, period_mode, now, config):
    """Return list of period dicts. Usually one, multiple for multiple_months."""
    if period_mode == "multiple_months":
        months = config.get("report_email_months", [])
        valid = sorted(set(m for m in months if len(m) == 7 and m[4] == "-"))[:24]
        periods = [p for m in valid for p in [_month_period(m)] if p]
        if periods:
            return periods
    return [calculate_report_period(schedule_type, period_mode, now, config)]


def _get_report_sessions(start_date, end_date, location_filter="all", vehicle_filter="all"):
    import sqlite3
    from datetime import timedelta
    from core.db import DB_PATH
    where  = ["end_ts IS NOT NULL", "start_ts >= ?", "start_ts < ?"]
    params = [start_date.isoformat(), (end_date + timedelta(days=1)).isoformat()]
    if location_filter == "home":
        where.append("location = 'home'")
    elif location_filter == "external":
        where.append("location = 'extern'")
    if vehicle_filter and vehicle_filter != "all":
        where.append("vehicle_id = ?"); params.append(vehicle_filter)
    sql = f"SELECT * FROM sessions WHERE {' AND '.join(where)} ORDER BY start_ts ASC"
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(sql, params).fetchall(); close_db_if_owned(con)
    return [dict(r) for r in rows]


def _report_filter_labels(cfg, is_de):
    loc = cfg.get("report_email_location_filter", "all")
    veh = cfg.get("report_email_vehicle_filter", "all")
    if is_de:
        loc_lbl = {"all": "Alle Ladevorgänge", "home": "Nur Zuhause / Intern",
                   "external": "Nur Extern"}.get(loc, loc)
        veh_lbl = "Alle Fahrzeuge" if veh == "all" else veh
    else:
        loc_lbl = {"all": "All charging sessions", "home": "Home only",
                   "external": "External only"}.get(loc, loc)
        veh_lbl = "All vehicles" if veh == "all" else veh
    return loc_lbl, veh_lbl


def _build_report_html(sessions, period_info, cfg, lang="de"):
    is_de      = lang != "en"
    plabel     = period_info.get("label_de" if is_de else "label_en", "")
    loc_filter = cfg.get("report_email_location_filter", "all")
    loc_lbl, veh_lbl = _report_filter_labels(cfg, is_de)
    total_kwh  = sum(s.get("kwh_charged") or 0 for s in sessions)
    total_cost = sum(s.get("cost_eur")    or 0 for s in sessions)
    total_secs = sum(s.get("duration_sec") or 0 for s in sessions)
    home_kwh   = sum((s.get("kwh_charged") or 0) for s in sessions if s.get("location") == "home")
    ext_kwh    = sum((s.get("kwh_charged") or 0) for s in sessions if s.get("location") == "extern")
    total_h    = total_secs / 3600
    avg_price  = total_cost / total_kwh if total_kwh else 0
    avg_power  = total_kwh / total_h if total_h else 0
    n          = len(sessions)
    if is_de:
        title = "EV Tracker — Lade-Report"
        rows  = [("Zeitraum", plabel), ("Filter", loc_lbl), ("Fahrzeug", veh_lbl),
                 ("Ladevorgänge", str(n)),
                 ("Geladene kWh", f"{total_kwh:.2f} kWh"),
                 ("Gesamtkosten", f"{total_cost:.2f} €"),
                 ("Ø Preis/kWh", f"{avg_price:.4f} €"),
                 ("Ladezeit gesamt", f"{total_h:.1f} h"),
                 ("Ø Ladeleistung", f"{avg_power:.1f} kW")]
        if loc_filter == "all" and (home_kwh or ext_kwh):
            rows.append(("Zuhause / Extern", f"{home_kwh:.1f} / {ext_kwh:.1f} kWh"))
        empty_txt = "Keine Ladevorgänge im gewählten Zeitraum."
    else:
        title = "EV Tracker — Charging Report"
        rows  = [("Period", plabel), ("Sessions", str(n)),
                 ("Energy charged", f"{total_kwh:.2f} kWh"),
                 ("Total cost", f"{total_cost:.2f} €"),
                 ("Avg price/kWh", f"{avg_price:.4f} €"),
                 ("Total charge time", f"{total_h:.1f} h"),
                 ("Avg charge power", f"{avg_power:.1f} kW")]
        if home_kwh or ext_kwh:
            rows.append(("Home / External", f"{home_kwh:.1f} / {ext_kwh:.1f} kWh"))
        empty_txt = "No charging sessions in the selected period."
    trows = "".join(
        f'<tr><td style="padding:6px 14px;color:#888;white-space:nowrap">{k}</td>'
        f'<td style="padding:6px 14px;font-weight:600;color:#fff">{v}</td></tr>'
        for k, v in rows)
    empty = f'<p style="color:#f59e0b;margin:16px 0">{empty_txt}</p>' if not sessions else ""
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
            f'<body style="background:#0f1117;color:#e8e8f0;font-family:sans-serif;margin:0;padding:24px">'
            f'<div style="max-width:560px;margin:0 auto"><div style="background:#1e2030;border-radius:12px;padding:28px">'
            f'<h1 style="color:#6ee7b7;font-size:1.3rem;margin:0 0 6px">⚡ {title}</h1>'
            f'<hr style="border:none;border-top:1px solid #2d3147;margin:16px 0">'
            f'{empty}<table style="width:100%;border-collapse:collapse">{trows}</table>'
            f'<hr style="border:none;border-top:1px solid #2d3147;margin:16px 0">'
            f'<p style="color:#555;font-size:.75rem;margin:0">EV Tracker v{APP_VERSION}</p>'
            f'</div></div></body></html>')


def _build_multi_month_html(periods_sessions, cfg, lang="de"):
    """Build email HTML for multiple months. periods_sessions: list of (period_info, sessions)."""
    is_de   = lang != "en"
    title   = "EV Tracker — Lade-Report" if is_de else "EV Tracker — Charging Report"
    n_months = len(periods_sessions)
    subj_lbl = (f"Bericht {n_months} Monate" if is_de else f"Report {n_months} months") if n_months != 1 else (
        periods_sessions[0][0].get("label_de" if is_de else "label_en", ""))
    if is_de:
        hdr_cells = ["Monat", "Ladevorgänge", "kWh", "Kosten", "Ø Preis/kWh"]
    else:
        hdr_cells = ["Month", "Sessions", "kWh", "Cost", "Avg Price/kWh"]
    hdr_row = "".join(
        f'<th style="padding:6px 10px;color:#888;text-align:left;white-space:nowrap">{h}</th>'
        for h in hdr_cells)
    data_rows = ""
    total_kwh = total_cost = total_n = 0
    for period_info, sessions in periods_sessions:
        plabel = period_info.get("label_de" if is_de else "label_en", "")
        kwh    = sum(s.get("kwh_charged") or 0 for s in sessions)
        cost   = sum(s.get("cost_eur") or 0 for s in sessions)
        n      = len(sessions)
        avg_p  = cost / kwh if kwh else 0
        total_kwh += kwh; total_cost += cost; total_n += n
        data_rows += (
            f'<tr style="border-bottom:1px solid rgba(255,255,255,.04)">'
            f'<td style="padding:5px 10px;color:#e8e8f0">{plabel}</td>'
            f'<td style="padding:5px 10px;color:#e8e8f0;text-align:right">{n}</td>'
            f'<td style="padding:5px 10px;color:#e8e8f0;text-align:right">{kwh:.2f}</td>'
            f'<td style="padding:5px 10px;color:#e8e8f0;text-align:right">{cost:.2f} €</td>'
            f'<td style="padding:5px 10px;color:#e8e8f0;text-align:right">{avg_p:.4f} €</td>'
            f'</tr>')
    avg_total_p = total_cost / total_kwh if total_kwh else 0
    data_rows += (
        f'<tr style="border-top:1px solid #2d3147;font-weight:700">'
        f'<td style="padding:5px 10px;color:#6ee7b7">{"Gesamt" if is_de else "Total"}</td>'
        f'<td style="padding:5px 10px;color:#6ee7b7;text-align:right">{total_n}</td>'
        f'<td style="padding:5px 10px;color:#6ee7b7;text-align:right">{total_kwh:.2f}</td>'
        f'<td style="padding:5px 10px;color:#6ee7b7;text-align:right">{total_cost:.2f} €</td>'
        f'<td style="padding:5px 10px;color:#6ee7b7;text-align:right">{avg_total_p:.4f} €</td>'
        f'</tr>')
    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
        f'<body style="background:#0f1117;color:#e8e8f0;font-family:sans-serif;margin:0;padding:24px">'
        f'<div style="max-width:640px;margin:0 auto"><div style="background:#1e2030;border-radius:12px;padding:28px">'
        f'<h1 style="color:#6ee7b7;font-size:1.3rem;margin:0 0 6px">⚡ {title}</h1>'
        f'<p style="color:#888;margin:0 0 16px;font-size:.85rem">{subj_lbl}</p>'
        f'<hr style="border:none;border-top:1px solid #2d3147;margin:16px 0">'
        f'<table style="width:100%;border-collapse:collapse">'
        f'<thead><tr style="border-bottom:1px solid #2d3147">{hdr_row}</tr></thead>'
        f'<tbody>{data_rows}</tbody></table>'
        f'<hr style="border:none;border-top:1px solid #2d3147;margin:16px 0">'
        f'<p style="color:#555;font-size:.75rem;margin:0">EV Tracker v{APP_VERSION}</p>'
        f'</div></div></body></html>')


def _send_email_with_attachments(to_addr, subject, body_html, attachments=None):
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders as _enc
    import server as _srv
    cfg  = load_config()
    name = cfg.get("smtp_from_name", "EV Tracker")
    srv, frm, err = _srv._smtp_open(cfg)
    if err:
        return False, err
    if not frm:
        srv.quit()
        return False, "Keine Absenderadresse konfiguriert"
    try:
        msg = MIMEMultipart(); msg["From"] = f"{name} <{frm}>"; msg["To"] = to_addr
        msg["Subject"] = subject; msg.attach(MIMEText(body_html, "html", "utf-8"))
        for fname, data, mime_type in (attachments or []):
            part = MIMEBase(*mime_type.split("/", 1)); part.set_payload(data)
            _enc.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=fname)
            msg.attach(part)
        srv.sendmail(frm, to_addr, msg.as_string()); srv.quit()
        return True, None
    except Exception as e:
        try: srv.quit()
        except Exception: pass
        return False, str(e)


def _log_report_history(period_info, cfg, status, error, triggered_by):
    import logging
    log = logging.getLogger(__name__)
    try:
        period_label = period_info.get("label_de", period_info.get("period_key", ""))
        con = _get_db()
        con.execute("""INSERT INTO email_report_history
            (sent_at,schedule_type,period_start,period_end,period_key,
             location_filter,vehicle_filter,recipients,status,error,triggered_by,
             period_label,period_mode)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (datetime.utcnow().isoformat(),
             cfg.get("report_email_schedule_type", "monthly"),
             period_info["start"].isoformat(), period_info["end"].isoformat(),
             period_info["period_key"],
             cfg.get("report_email_location_filter", "all"),
             cfg.get("report_email_vehicle_filter", "all"),
             json.dumps(cfg.get("report_email_recipients", [])),
             status, error, triggered_by,
             period_label,
             cfg.get("report_email_period_mode", "previous_period")))
        con.commit(); close_db_if_owned(con)
    except Exception as e:
        log.warning("Report-History-Log fehlgeschlagen: %s", e)


def _send_report_email(cfg=None, triggered_by="auto"):
    import logging
    log = logging.getLogger(__name__)
    if cfg is None: cfg = load_config()
    if not cfg.get("report_email_enabled"):
        return False, "Reports nicht aktiviert"
    if not cfg.get("smtp_host", "") or not cfg.get("smtp_from_email", ""):
        return False, "SMTP nicht konfiguriert"
    recipients = cfg.get("report_email_recipients", [])
    if not recipients:
        return False, "Keine Empfänger konfiguriert"
    stype       = cfg.get("report_email_schedule_type", "monthly")
    period_mode = cfg.get("report_email_period_mode", "previous_period")
    loc_filter  = cfg.get("report_email_location_filter", "all")
    veh_filter  = cfg.get("report_email_vehicle_filter", "all")
    lang        = cfg.get("report_email_language", "auto")
    if lang == "auto": lang = "de"
    is_de       = lang != "en"

    SIGNATURE_PATH = DATA_DIR / "signatures" / "default_signature.png"

    periods = calculate_report_periods(stype, period_mode, datetime.now(), cfg)
    if period_mode == "multiple_months" and len(periods) > 1:
        combined_key = "months:" + ",".join(
            p["period_key"].replace("monthly:", "") for p in periods
        )
    else:
        combined_key = periods[0]["period_key"] if periods else "unknown"

    if triggered_by == "auto" and cfg.get("report_email_last_sent_key", "") == combined_key:
        log.info("Report bereits gesendet für %s — übersprungen", combined_key)
        _log_report_history(periods[0], cfg, "skipped", None, triggered_by)
        return True, None

    attachments = []

    if period_mode == "multiple_months" and len(periods) > 1:
        # Multi-month: per-month sessions + combined HTML + multi-sheet Excel
        periods_sessions = []
        for p in periods:
            s = _get_report_sessions(p["start"], p["end"], loc_filter, veh_filter)
            periods_sessions.append((p, s))
        all_sessions = [s for _, ss in periods_sessions for s in ss]
        if cfg.get("report_email_include_summary", True):
            html = _build_multi_month_html(periods_sessions, cfg, lang)
        else:
            n_months = len(periods)
            html = f"<p>EV Tracker Report — {n_months} {'Monate' if is_de else 'months'}</p>"
        n_months = len(periods)
        if is_de:
            subject = f"EV Tracker — Bericht {n_months} Monate"
        else:
            subject = f"EV Tracker — Report {n_months} months"
        if cfg.get("report_email_include_excel") and all_sessions:
            try:
                from export_excel import export_multi_month_bytes as _emm
                sig_path = str(SIGNATURE_PATH) if SIGNATURE_PATH.exists() and cfg.get("report_email_include_signature") else None
                sig_map  = cfg.get("signature_mapping", {}) if sig_path else {}
                xl_bytes, _ = _emm(
                    periods_sessions=periods_sessions,
                    loc_filter=loc_filter, config=cfg, lang=lang,
                    include_signature=bool(sig_path),
                    signature_path=sig_path, signature_mapping=sig_map)
                attachments.append(("Ladeprotokoll.xlsx", xl_bytes,
                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
            except Exception as e:
                log.warning("Multi-Monats-Excel-Anhang fehlgeschlagen: %s", e)
        # use first period for history logging
        log_period = periods[0]
        log_period = dict(log_period); log_period["period_key"] = combined_key
    else:
        # Single period (includes single_month)
        period_info = periods[0]
        sessions    = _get_report_sessions(period_info["start"], period_info["end"], loc_filter, veh_filter)
        if cfg.get("report_email_include_summary", True):
            html = _build_report_html(sessions, period_info, cfg, lang)
        else:
            html = f"<p>EV Tracker Report — {period_info.get('label_de', '')}</p>"
        plabel  = period_info.get("label_de" if is_de else "label_en", combined_key)
        subject = (f"EV Tracker — {('Monatsbericht' if is_de else 'Monthly Report')} {plabel}"
                   if period_mode in ("single_month", "previous_period", "current_period") and stype == "monthly"
                   else f"EV Tracker — Report {plabel}")
        if cfg.get("report_email_include_excel") and sessions:
            try:
                from export_excel import export as _export_func
                sig_path = str(SIGNATURE_PATH) if SIGNATURE_PATH.exists() and cfg.get("report_email_include_signature") else None
                sig_map  = cfg.get("signature_mapping", {}) if sig_path else {}
                xl_loc   = "extern" if loc_filter == "external" else loc_filter
                xl_bytes, _ = _export_func(
                    year=period_info["start"].year, month=period_info["start"].month,
                    location=xl_loc, config=cfg, lang=lang,
                    include_signature=bool(sig_path),
                    signature_path=sig_path, signature_mapping=sig_map, return_warnings=True)
                attachments.append(("Ladeprotokoll.xlsx", xl_bytes,
                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
            except Exception as e:
                log.warning("Report-Excel-Anhang fehlgeschlagen: %s", e)
        log_period = period_info

    errors = []
    for to in recipients:
        ok, err = _send_email_with_attachments(to, subject, html, attachments)
        if not ok: errors.append(f"{to}: {err}")
    if errors:
        _log_report_history(log_period, cfg, "error", "; ".join(errors), triggered_by)
        return False, "; ".join(errors)
    cfg["report_email_last_sent_key"] = combined_key
    save_config(cfg)
    _log_report_history(log_period, cfg, "sent", None, triggered_by)
    log.info("Report gesendet: %s → %s", combined_key, recipients)
    return True, None


def _next_report_seconds(cfg, now=None):
    if not cfg.get("report_email_enabled"): return None
    stype  = cfg.get("report_email_schedule_type", "monthly")
    t_str  = cfg.get("report_email_time", "08:00")
    if now is None: now = datetime.now()
    try: t_hour, t_min = [int(x) for x in t_str.split(":")]
    except Exception: t_hour, t_min = 8, 0
    today = now.date()
    from datetime import date, timedelta

    if stype == "daily":
        fire = datetime.combine(today, datetime.min.time()).replace(hour=t_hour, minute=t_min)
        if fire <= now: fire += timedelta(days=1)
        return (fire - now).total_seconds()

    if stype == "weekly":
        wd = int(cfg.get("report_email_weekday", 1)) - 1
        da = (wd - today.weekday()) % 7 or 7
        fire = datetime.combine(today + timedelta(days=da), datetime.min.time()).replace(hour=t_hour, minute=t_min)
        if fire <= now: fire += timedelta(weeks=1)
        return (fire - now).total_seconds()

    if stype == "monthly":
        dom = int(cfg.get("report_email_day_of_month", 1))
        try:
            fire = datetime.combine(today.replace(day=dom), datetime.min.time()).replace(hour=t_hour, minute=t_min)
            if fire > now: return (fire - now).total_seconds()
        except ValueError: pass
        nm = today.month % 12 + 1; ny = today.year + (1 if today.month == 12 else 0)
        try: fire = datetime(ny, nm, dom, t_hour, t_min)
        except ValueError: fire = datetime(ny, nm, _calendar.monthrange(ny, nm)[1], t_hour, t_min)
        return (fire - now).total_seconds()

    if stype == "quarterly":
        dom = int(cfg.get("report_email_day_of_month", 1))
        for yo in range(2):
            for qm in [1, 4, 7, 10]:
                try:
                    fire = datetime(today.year + yo, qm, dom, t_hour, t_min)
                    if fire > now: return (fire - now).total_seconds()
                except ValueError: continue
        return 90 * 86400

    if stype == "yearly":
        mo  = int(cfg.get("report_email_month", 1))
        dom = int(cfg.get("report_email_day_of_month", 1))
        for year in [today.year, today.year + 1]:
            try:
                fire = datetime(year, mo, dom, t_hour, t_min)
                if fire > now: return (fire - now).total_seconds()
            except ValueError: continue
        return 365 * 86400

    if stype == "custom_days":
        x    = int(cfg.get("report_email_custom_days", 14))
        fire = datetime.combine(today, datetime.min.time()).replace(hour=t_hour, minute=t_min)
        if fire <= now: fire += timedelta(days=x)
        return (fire - now).total_seconds()

    if stype == "custom_cron":
        cron = cfg.get("report_email_cron", "").strip()
        if not cron: return None
        try:
            parts = cron.split()
            if len(parts) >= 2:
                c_min, c_hour = int(parts[0]), int(parts[1])
                fire = datetime.combine(today, datetime.min.time()).replace(hour=c_hour, minute=c_min)
                if fire <= now: fire += timedelta(days=1)
                return (fire - now).total_seconds()
        except Exception: return None

    return None


def schedule_report():
    import logging
    log = logging.getLogger(__name__)
    global _report_timer
    cfg  = load_config()
    secs = _next_report_seconds(cfg)
    if not secs or secs <= 0: return

    def run():
        try:
            ok, err = _send_report_email(triggered_by="auto")
            if not ok: log.warning("Auto-Report Fehler: %s", err)
        except Exception as e:
            log.warning("Auto-Report Exception: %s", e)
        schedule_report()

    _report_timer = Timer(secs, run); _report_timer.daemon = True; _report_timer.start()
    log.info("Nächster Auto-Report: %s", (datetime.now() + timedelta(seconds=secs)).strftime("%d.%m.%Y %H:%M"))


# ── Routes ────────────────────────────────────────────────────────────────────

@email_reports_bp.route("/api/report/config", methods=["GET"])
@require_login
def api_report_config_get():
    if not has_permission(_current_user(), "reports:view"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    cfg  = load_config()
    keys = [k for k in DEFAULT_CONFIG if k.startswith("report_email_")]
    return jsonify({k: cfg.get(k, DEFAULT_CONFIG[k]) for k in keys})


@email_reports_bp.route("/api/report/config", methods=["POST"])
@require_login
def api_report_config_save():
    if not has_permission(_current_user(), "reports:configure"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    data = request.get_json(force=True) or {}
    cfg  = load_config()
    allowed = [k for k in DEFAULT_CONFIG if k.startswith("report_email_")]
    for k in allowed:
        if k in data:
            cfg[k] = data[k]
    save_config(cfg)
    schedule_report()
    _audit("report_config_saved", ip=request.remote_addr)
    return jsonify({"ok": True})


@email_reports_bp.route("/api/report/send-now", methods=["POST"])
@require_login
def api_report_send_now():
    if not has_permission(_current_user(), "reports:send"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    data = request.get_json(force=True) or {}
    cfg  = load_config()
    # Allow overriding config for this send
    for k in ["report_email_location_filter", "report_email_vehicle_filter",
              "report_email_period_mode", "report_email_schedule_type",
              "report_email_recipients", "report_email_language",
              "report_email_single_month", "report_email_months"]:
        if k in data: cfg[k] = data[k]
    cfg["report_email_enabled"] = True
    ok, err = _send_report_email(cfg=cfg, triggered_by="manual")
    _audit("report_send_now", f"ok={ok} err={err}", ip=request.remote_addr)
    return jsonify({"ok": ok, "error": err})


@email_reports_bp.route("/api/report/history")
@require_login
def api_report_history():
    if not has_permission(_current_user(), "reports:history"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    try: limit = int(request.args.get("limit", 50))
    except (ValueError, TypeError): limit = 50
    con = _get_db()
    rows = con.execute(
        "SELECT * FROM email_report_history ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    close_db_if_owned(con)
    return jsonify([dict(r) for r in rows])
