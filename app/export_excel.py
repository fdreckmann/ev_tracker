import os, sys, sqlite3, shutil, json
from datetime import datetime
from pathlib import Path
import openpyxl
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

try:
    from template_fields import TABLE_FIELDS, HEADER_FIELDS
except ImportError:
    TABLE_FIELDS = {}
    HEADER_FIELDS = {}

# ── Localization ──────────────────────────────────────────────────────────────
MONTH_NAMES = {
    "de": {1:"Januar",2:"Februar",3:"März",4:"April",5:"Mai",6:"Juni",
           7:"Juli",8:"August",9:"September",10:"Oktober",11:"November",12:"Dezember"},
    "en": {1:"January",2:"February",3:"March",4:"April",5:"May",6:"June",
           7:"July",8:"August",9:"September",10:"October",11:"November",12:"December"},
}

DATE_FORMATS = {
    "de": "%d.%m.%Y",
    "en": "%Y-%m-%d",
}

EXPORT_LABELS = {
    "de": {
        "driver": "Fahrer", "license_plate": "Kennzeichen", "cost_center": "Kostenstelle",
        "department": "Abteilung", "month": "Monat", "export_date": "Exportdatum",
        "total_cost": "Gesamtkosten", "total_kwh": "Gesamt kWh",
        "avg_charge_power": "Ø Ladeleistung (kW)", "total_charging_time": "Ladezeit gesamt",
        "signature": "Unterschrift", "charging_log": "Ladeprotokoll",
        "meter_old": "Zählerstand Alt", "meter_new": "Zählerstand Neu",
        "location": "Ladeort", "charger_type": "Ladeart",
        "start": "Start", "end": "Ende", "duration": "Dauer", "cost": "Kosten",
        "period": "Zeitraum",
    },
    "en": {
        "driver": "Driver", "license_plate": "License plate", "cost_center": "Cost center",
        "department": "Department", "month": "Month", "export_date": "Export date",
        "total_cost": "Total cost", "total_kwh": "Total kWh",
        "avg_charge_power": "Average charging power (kW)", "total_charging_time": "Total charging time",
        "signature": "Signature", "charging_log": "Charging log",
        "meter_old": "Meter reading old", "meter_new": "Meter reading new",
        "location": "Charging location", "charger_type": "Charger type",
        "start": "Start", "end": "End", "duration": "Duration", "cost": "Cost",
        "period": "Period",
    },
}

def t_export(key: str, lang: str = "de") -> str:
    """Translate an export label key to the given language."""
    lang = lang if lang in EXPORT_LABELS else "de"
    return EXPORT_LABELS[lang].get(key, EXPORT_LABELS["de"].get(key, key))

DATA_DIR      = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH       = DATA_DIR / "sessions.db"
EXPORT_DIR    = DATA_DIR / "exports"
TEMPLATE_PATH = DATA_DIR / "template.xlsx"
PRICE_PER_KWH = float(os.environ.get("PRICE_PER_KWH", "0.30"))

# ── Column keyword → field name mapping ───────────────────────────────────────
COLUMN_KEYWORDS = {
    "datum":            "date",
    "date":             "date",
    "start":            "start_time",
    "beginn":           "start_time",
    "ende":             "end_time",
    "end":              "end_time",
    "km start":         "odo_start",
    "km-start":         "odo_start",
    "start km":         "odo_start",
    "odometer":         "odo_start",
    "km ende":          "odo_end",
    "km-ende":          "odo_end",
    "end km":           "odo_end",
    "kilometerstand":   "odo_end",
    "kilome":           "odo_end",
    "kwh":              "kwh_charged",
    "geladen":          "kwh_charged",
    "energie":          "kwh_charged",
    "energy":           "kwh_charged",
    "lademenge":        "kwh_charged",
    "ladedauer":        "duration",
    "ladezeit":         "duration",
    "dauer":            "duration",
    "kosten":           "cost_eur",
    "cost":             "cost_eur",
    "betrag":           "cost_eur",
    "ladekosten":       "cost_eur",
    "soc start":        "soc_start",
    "soc anfang":       "soc_start",
    "soc ende":         "soc_end",
    "soc end":          "soc_end",
    "standort":         "location",
    "ort":              "location",
    "location":         "location",
    "nr":               "row_num",
    "#":                "row_num",
    "lfd":              "row_num",
    "zählerstand alt":  "meter_old",
    "zählerstand neu":  "meter_new",
    "zählerstand":      "meter_old",
}

FIELD_LABELS = {
    "date":        "Datum",
    "start_time":  "Start Uhrzeit",
    "end_time":    "Ende Uhrzeit",
    "odo_start":   "KM-Stand Start",
    "odo_end":     "KM-Stand Ende / Kilometerstand",
    "soc_start":   "SOC Start (%)",
    "soc_end":     "SOC Ende (%)",
    "kwh_charged": "Geladene kWh / Lademenge",
    "cost_eur":    "Kosten (€) / Ladekosten",
    "duration":    "Ladedauer",
    "meter_old":   "Zählerstand Alt (kWh)",
    "meter_new":   "Zählerstand Neu (kWh)",
    "location":    "Standort",
    "row_num":     "Nr.",
    None:          "— nicht zugewiesen —",
}

NUM_FMT = {
    "date":        "DD.MM.YYYY",
    "start_time":  "HH:MM",
    "end_time":    "HH:MM",
    "odo_start":   '#,##0 "km"',
    "odo_end":     '#,##0 "km"',
    "soc_start":   '0"%"',
    "soc_end":     '0"%"',
    "kwh_charged": '0.00 "kWh"',
    "cost_eur":    '€#,##0.00',
    "duration":         '[h]:MM',
    "duration_hours":   '0.00 "h"',
    "charge_power_kw":  '0.00 "kW"',
    "meter_old":   '0.000',
    "meter_new":   '0.000',
    "row_num":     "0",
}

LOCATION_LABELS = {"home": "🏠 Zuhause", "extern": "⚡ Extern", "unknown": "—"}

# ── Header label → data key mapping for auto-fill ────────────────────────────
HEADER_KEYWORDS = {
    "abrechnungsmonat": "month_year",
    "gesamtkosten":     "total_cost",
    "kennzeichen":      "kennzeichen",
    "fahrer":           "fahrer",
    "abteilung":        "abteilung",
    "kostenstelle":     "kostenstelle",
    "kosten pro kw":    "price_per_kwh",
    "kosten pro kwh":   "price_per_kwh",
    "preis pro kwh":    "price_per_kwh",
    "monat":            "month_year",
}

def match_column(txt):
    if not txt: return None
    h = str(txt).lower().strip()
    for kw, field in COLUMN_KEYWORDS.items():
        if kw in h: return field
    return None

def fetch_sessions(year, month, location="all"):
    if not DB_PATH.exists(): return []
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    where  = ["end_ts IS NOT NULL", f"start_ts LIKE '{year:04d}-{month:02d}%'"]
    if location and location != "all":
        where.append(f"location = '{location}'")
    rows = con.execute(
        f"SELECT * FROM sessions WHERE {' AND '.join(where)} ORDER BY start_ts"
    ).fetchall()
    con.close(); return [dict(r) for r in rows]

def to_row(s, idx):
    dt_s = datetime.fromisoformat(s["start_ts"])
    dt_e = datetime.fromisoformat(s["end_ts"]) if s.get("end_ts") else None
    duration = None
    if dt_e:
        duration = (dt_e - dt_s).total_seconds() / 86400  # fraction of day for [h]:MM format
    duration_hours = round(duration * 24, 4) if duration is not None else None
    row = {
        "row_num":      idx,
        "date":         dt_s.date(),
        "start_time":   dt_s.time(),
        "end_time":     dt_e.time() if dt_e else None,
        "duration":     duration,
        "duration_hours": duration_hours,
        "charge_power_kw": None,  # calculated below
        "odo_start":    s.get("odo_start"),
        "odo_end":      s.get("odo_end"),
        "soc_start":    s.get("soc_start"),
        "soc_end":      s.get("soc_end"),
        "kwh_charged":  s.get("kwh_charged"),
        "cost_eur":     s.get("cost_eur"),
        "price_per_kwh": s.get("price_per_kwh"),
        "meter_old":    s.get("meter_old"),
        "meter_new":    s.get("meter_new"),
        "location":     LOCATION_LABELS.get(s.get("location", "unknown"), s.get("location", "—")),
        "charger_type": s.get("charger_type"),
    }
    # Calculate charge_power_kw = kwh / duration_h
    kwh = s.get("kwh_charged")
    dur_h = row.get("duration_hours")
    if kwh is not None and dur_h and dur_h > 0:
        row["charge_power_kw"] = round(kwh / dur_h, 2)
    return row

# ── Built-in style helpers ────────────────────────────────────────────────────
C_HDR  = "1F4E79"; C_FG  = "FFFFFF"
C_SUM  = "D6E4F0"; C_ALT = "F2F7FB"
C_HOME = "E8F5E9"; C_EXT = "FFF8E1"
T = Side(style="thin", color="A9C4D8")
BRD = Border(left=T, right=T, top=T, bottom=T)

def cs(ws, r, c, v, bold=False, bg=None, fg="000000", nf=None, al="left"):
    cell = ws.cell(row=r, column=c, value=v)
    cell.font      = Font(name="Arial", bold=bold, color=fg, size=10)
    cell.alignment = Alignment(horizontal=al, vertical="center")
    cell.border    = BRD
    if bg: cell.fill = PatternFill("solid", start_color=bg.lstrip("#"))
    if nf: cell.number_format = nf
    return cell

def row_bg(s):
    loc = s.get("location", "unknown")
    if loc == "home":   return C_HOME
    if loc == "extern": return C_EXT
    return C_ALT

def safe_set(cell, value):
    try: cell.value = value
    except (AttributeError, TypeError): pass

# ── Header section auto-fill ──────────────────────────────────────────────────
def fill_header_section(ws, data_start_row, header_data):
    """Scan rows before data_start_row for label-value pairs and auto-fill."""
    for row_idx in range(1, data_start_row):
        for cell in ws[row_idx]:
            if not cell.value or isinstance(cell, MergedCell):
                continue
            key = str(cell.value).lower().strip().rstrip(':').strip()

            # Match against known header labels
            matched_field = None
            for kw, field in HEADER_KEYWORDS.items():
                if kw in key:
                    matched_field = field
                    break

            if matched_field and matched_field in header_data and header_data[matched_field] is not None:
                # Fill the next writable cell to the right
                target = ws.cell(row=cell.row, column=cell.column + 1)
                if not isinstance(target, MergedCell):
                    safe_set(target, header_data[matched_field])
                    if matched_field == "total_cost":
                        try: target.number_format = '€#,##0.00'
                        except Exception: pass
                    elif matched_field == "price_per_kwh":
                        try: target.number_format = '€0.00'
                        except Exception: pass

            # Special: cell ending with ", den" → fill date in next cell
            if key.endswith(", den") or key.endswith(",den"):
                target = ws.cell(row=cell.row, column=cell.column + 1)
                if not isinstance(target, MergedCell) and not target.value:
                    safe_set(target, datetime.today().date())
                    try: target.number_format = 'DD.MM.YYYY'
                    except Exception: pass

# ── Header values computation ─────────────────────────────────────────────────
def compute_header_values(sessions, year, month, header_info=None, lang="de"):
    """Compute all HEADER_FIELDS values from sessions + config."""
    import calendar as _cal
    if header_info is None:
        header_info = {}
    home_s     = [s for s in sessions if s.get("location") == "home"]
    ext_s      = [s for s in sessions if s.get("location") == "extern"]
    home_kwh   = sum(s.get("kwh_charged") or 0 for s in home_s)
    ext_kwh    = sum(s.get("kwh_charged") or 0 for s in ext_s)
    home_cost  = sum(s.get("cost_eur")    or 0 for s in home_s)
    ext_cost   = sum(s.get("cost_eur")    or 0 for s in ext_s)
    total_kwh  = home_kwh + ext_kwh
    n = len(sessions)
    total_km = 0
    if n:
        odo_end   = sessions[-1].get("odo_end")   or 0
        odo_start = sessions[0].get("odo_start")  or 0
        total_km  = odo_end - odo_start
    avg_cons = None
    if total_km and total_km > 0:
        avg_cons = round(total_kwh / total_km * 100, 2)

    # Total charging hours
    total_charging_hours = 0.0
    for s in sessions:
        if s.get("start_ts") and s.get("end_ts"):
            try:
                dt_s = datetime.fromisoformat(s["start_ts"])
                dt_e = datetime.fromisoformat(s["end_ts"])
                total_charging_hours += (dt_e - dt_s).total_seconds() / 3600
            except Exception:
                pass
    total_charging_hours = round(total_charging_hours, 2)
    avg_charge_power_kw = None
    if total_charging_hours > 0 and total_kwh > 0:
        avg_charge_power_kw = round(total_kwh / total_charging_hours, 2)

    # Localized month name and period
    month_name = MONTH_NAMES.get(lang, MONTH_NAMES["de"])[month]
    month_year = f"{month_name} {year}"
    export_date_str = datetime.today().strftime(DATE_FORMATS.get(lang, "%d.%m.%Y"))
    last_day = _cal.monthrange(year, month)[1]
    if lang == "en":
        export_period = f"{year}-{month:02d}-01 – {year}-{month:02d}-{last_day:02d}"
    else:
        export_period = f"01.{month:02d}.{year} – {last_day:02d}.{month:02d}.{year}"

    return {
        "fahrer":                   header_info.get("fahrer") or None,
        "kennzeichen":              header_info.get("kennzeichen") or None,
        "abteilung":                header_info.get("abteilung") or None,
        "kostenstelle":             header_info.get("kostenstelle") or None,
        "month_year":               month_year,
        "month_name":               month_name,
        "export_date":              datetime.today().date(),
        "export_period":            export_period,
        "total_sessions":           n,
        "total_kwh":                total_kwh,
        "total_cost":               home_cost + ext_cost,
        "total_home_kwh":           home_kwh,
        "total_external_kwh":       ext_kwh,
        "total_home_cost":          home_cost,
        "total_external_cost":      ext_cost,
        "total_km":                 total_km,
        "avg_consumption_kwh_100km": avg_cons,
        "meter_start_value":        sessions[0].get("meter_old") if n else None,
        "meter_end_value":          sessions[-1].get("meter_new") if n else None,
        "price_per_kwh":            header_info.get("price_per_kwh") or None,
        "total_charging_hours":     total_charging_hours,
        "avg_charge_power_kw":      avg_charge_power_kw,
    }


# ── Template-based export ─────────────────────────────────────────────────────
def export_with_template(year, month, sessions, location, col_override=None, start_row=None, header_row=None, header_info=None,
                          cell_mapping=None, sheet=None,
                          include_signature=False, signature_path=None, signature_mapping=None,
                          lang="de"):
    if header_info is None:
        header_info = {}
    if cell_mapping is None:
        cell_mapping = {}
    ml     = datetime(year, month, 1).strftime("%B %Y")
    suffix = f"_{location}" if location != "all" else ""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = EXPORT_DIR / f"EV_Ladeprotokoll_{year:04d}-{month:02d}{suffix}.xlsx"
    shutil.copy(TEMPLATE_PATH, out)
    wb = openpyxl.load_workbook(out, keep_vba=False)
    # Select sheet
    if sheet and sheet in wb.sheetnames:
        ws = wb[sheet]
    else:
        ws = wb.active

    # build column map from explicit mapping
    col_map = {}
    if col_override:
        for col_str, field in col_override.items():
            try: col_idx = int(col_str)
            except (ValueError, TypeError): continue
            if field:
                col_map[col_idx] = field

    # fallback: auto-detect from header keywords if no mapping provided
    if not col_map:
        for row in ws.iter_rows():
            filled = [c for c in row if c.value is not None and str(c.value).strip()]
            if len(filled) >= 2:
                for cell in row:
                    f = match_column(cell.value)
                    if f: col_map[cell.column] = f
                if col_map:
                    break

    # determine data start row
    if start_row:
        ds = int(start_row)
    else:
        detected = None
        for row in ws.iter_rows():
            filled = [c for c in row if c.value is not None and str(c.value).strip()]
            if len(filled) >= 2:
                detected = row[0].row; break
        ds = (detected + 1) if detected else (ws.max_row or 1)

    if not col_map:
        col_map = {1:"row_num",2:"date",3:"start_time",4:"end_time",
                   5:"odo_start",6:"odo_end",7:"kwh_charged",8:"cost_eur",9:"location"}

    max_row = ws.max_row or 0

    # capture template row styles from first data row
    tstyles = {}
    if max_row >= ds:
        for ci in col_map:
            c = ws.cell(row=ds, column=ci)
            try:
                tstyles[ci] = {
                    "font":      c.font.copy()      if c.font      else None,
                    "fill":      c.fill.copy()      if c.fill      else None,
                    "border":    c.border.copy()    if c.border    else None,
                    "alignment": c.alignment.copy() if c.alignment else None,
                }
            except Exception:
                pass

    # meter readings: use stored values if available, else calculate from meter_start_value
    has_stored = any(s.get("meter_old") is not None for s in sessions)
    if not has_stored:
        meter_start = float(header_info.get("meter_start_value") or 0)
        cumulative = meter_start
        enriched = []
        for s in sessions:
            s = dict(s)
            s["meter_old"] = round(cumulative, 3)
            cumulative += float(s.get("kwh_charged") or 0)
            s["meter_new"] = round(cumulative, 3)
            enriched.append(s)
        sessions = enriched

    # clear old data rows
    for r in range(ds, max_row + 1):
        for cell in ws[r]:
            safe_set(cell, None)

    # write data rows
    for i, s in enumerate(sessions):
        rd = to_row(s, i + 1); tr = ds + i
        for ci, field in col_map.items():
            cell = ws.cell(row=tr, column=ci)
            safe_set(cell, rd.get(field))
            if ci in tstyles:
                st = tstyles[ci]
                try:
                    if st["font"]:      cell.font      = st["font"]
                    if st["fill"]:      cell.fill      = st["fill"]
                    if st["border"]:    cell.border    = st["border"]
                    if st["alignment"]: cell.alignment = st["alignment"]
                except Exception:
                    pass
            try:
                if field in NUM_FMT: cell.number_format = NUM_FMT[field]
            except Exception:
                pass

    # Compute full header values
    hv = compute_header_values(sessions, year, month, header_info, lang=lang)

    # auto-fill header section (Abrechnungsmonat, Kennzeichen, Fahrer, etc.)
    # Use hv merged with legacy header_data keys for backward compat
    header_data = dict(hv)
    fill_header_section(ws, ds, header_data)

    # Replace {{placeholder}} in any cell text
    import re as _re
    ph_pattern = _re.compile(r'\{\{(\w+)\}\}')
    for ws_sheet in wb.worksheets:
        for row in ws_sheet.iter_rows():
            for cell in row:
                if isinstance(cell, MergedCell):
                    continue
                val = cell.value
                if isinstance(val, str) and '{{' in val:
                    # Handle {{signature}} placeholder specially
                    if '{{signature}}' in val:
                        if not include_signature:
                            # Clear placeholder when signature not included
                            try:
                                cell.value = val.replace('{{signature}}', '')
                            except Exception:
                                pass
                        # If include_signature=True, leave for signature insertion code below
                        continue
                    def _replace_ph(m, _hv=hv):
                        field = m.group(1)
                        v = _hv.get(field)
                        if v is None:
                            return m.group(0)
                        return str(v)
                    new_val = ph_pattern.sub(_replace_ph, val)
                    if new_val != val:
                        try:
                            cell.value = new_val
                        except Exception:
                            pass

    # Write cell_mapping: { "B4": "kennzeichen" or {"field": "kennzeichen", ...} }
    if cell_mapping:
        col_letter_re = _re.compile(r'^([A-Za-z]+)(\d+)$')
        for addr, field_info in cell_mapping.items():
            field = field_info if isinstance(field_info, str) else field_info.get("field")
            if not field or field not in hv:
                continue
            value = hv.get(field)
            if value is None:
                continue
            m = col_letter_re.match(str(addr))
            if not m:
                continue
            try:
                col_letters = m.group(1).upper()
                row_num = int(m.group(2))
                # Convert column letters to index
                col_idx = 0
                for ch in col_letters:
                    col_idx = col_idx * 26 + (ord(ch) - 64)
                cell = ws.cell(row=row_num, column=col_idx)
                if isinstance(cell, MergedCell):
                    # Find merge top-left
                    for mr in ws.merged_cells.ranges:
                        if mr.min_row <= row_num <= mr.max_row and mr.min_col <= col_idx <= mr.max_col:
                            cell = ws.cell(row=mr.min_row, column=mr.min_col)
                            break
                # Skip formula cells
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    continue
                safe_set(cell, value)
            except Exception:
                pass

    # ── Signature insertion ──────────────────────────────────────────────────
    if include_signature and signature_path and signature_mapping:
        try:
            from openpyxl.drawing.image import Image as XLImage
            # Support both old "cell" key and new "anchor_cell" key
            sig_cell = signature_mapping.get("anchor_cell") or signature_mapping.get("cell")
            # Check for {{signature}} placeholder — find cell with that text
            if not sig_cell:
                for row in ws.iter_rows():
                    for cell in row:
                        if cell.value and "{{signature}}" in str(cell.value):
                            from openpyxl.utils import get_column_letter as _gcl
                            sig_cell = f"{_gcl(cell.column)}{cell.row}"
                            cell.value = None  # clear placeholder
                            break
                    if sig_cell:
                        break
            if sig_cell:
                img = XLImage(signature_path)
                sig_w = int(signature_mapping.get("width", 220))
                sig_h = int(signature_mapping.get("height", 80))
                # keep aspect ratio if requested
                if signature_mapping.get("keep_aspect_ratio", True):
                    try:
                        from PIL import Image as _PILImage
                        with _PILImage.open(signature_path) as _pimg:
                            iw, ih = _pimg.size
                        if iw > 0 and ih > 0:
                            ratio = min(sig_w / iw, sig_h / ih)
                            sig_w = int(iw * ratio)
                            sig_h = int(ih * ratio)
                    except Exception:
                        pass
                img.width  = sig_w
                img.height = sig_h
                # Try to apply offset using OneCellAnchor if offset values are provided
                offset_x = int(signature_mapping.get("offset_x", 0))
                offset_y = int(signature_mapping.get("offset_y", 0))
                placed = False
                if offset_x or offset_y:
                    try:
                        import re as _re_sig
                        from openpyxl.utils import column_index_from_string as _col_idx
                        from openpyxl.drawing.anchor import OneCellAnchor, AnchorMarker
                        try:
                            from openpyxl.utils.units import pixels_to_EMU
                            emu_x = pixels_to_EMU(offset_x)
                            emu_y = pixels_to_EMU(offset_y)
                        except (ImportError, Exception):
                            emu_x = offset_x * 9525
                            emu_y = offset_y * 9525
                        m2 = _re_sig.match(r'^([A-Za-z]+)(\d+)$', sig_cell)
                        if m2:
                            col_str = m2.group(1).upper()
                            row_num2 = int(m2.group(2)) - 1  # 0-based
                            col_num2 = _col_idx(col_str) - 1  # 0-based
                            marker = AnchorMarker(col=col_num2, colOff=emu_x, row=row_num2, rowOff=emu_y)
                            anchor = OneCellAnchor(_from=marker, ext=None)
                            anchor.ext.cx = img.width * 9525
                            anchor.ext.cy = img.height * 9525
                            img.anchor = anchor
                            ws.add_image(img)
                            placed = True
                    except Exception:
                        placed = False
                if not placed:
                    ws.add_image(img, sig_cell)
        except Exception as e:
            print(f"Signatur-Einfügung fehlgeschlagen: {e}")
    # ── End signature ────────────────────────────────────────────────────────

    wb.save(out)
    print(f"✅ Template-Export: {out} ({len(sessions)} Sessions)")
    return str(out)

# ── Built-in export ───────────────────────────────────────────────────────────
def export_builtin(year, month, sessions, location):
    ml        = datetime(year, month, 1).strftime("%B %Y")
    loc_label = {"all":"Alle Standorte","home":"🏠 Zuhause","extern":"⚡ Extern"}.get(location,"Alle")
    suffix    = f"_{location}" if location != "all" else ""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    ws = wb.active; ws.title = "Ladevorgänge"; ws.freeze_panes = "A3"

    # Title
    ws.merge_cells("A1:K1")
    t = ws["A1"]
    t.value     = f"EV Ladeprotokoll – {ml}  |  {loc_label}"
    t.font      = Font(name="Arial", bold=True, size=14, color=C_FG)
    t.fill      = PatternFill("solid", start_color=C_HDR)
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    hdrs = ["#","Datum","Start","Ende","KM Start","KM Ende","SOC Start","SOC Ende","kWh","Kosten","Standort"]
    for col, h in enumerate(hdrs, 1):
        cs(ws, 2, col, h, bold=True, bg="#"+C_HDR, fg=C_FG, al="center")

    ds = 3
    for i, s in enumerate(sessions):
        r  = ds + i
        rd = to_row(s, i + 1)
        bg = row_bg(s)
        cs(ws,r,1, rd["row_num"],    bg=bg, al="center")
        cs(ws,r,2, rd["date"],       bg=bg, nf="DD.MM.YYYY", al="center")
        cs(ws,r,3, rd["start_time"], bg=bg, nf="HH:MM", al="center")
        cs(ws,r,4, rd["end_time"],   bg=bg, nf="HH:MM", al="center")
        cs(ws,r,5, rd["odo_start"],  bg=bg, nf='#,##0 "km"', al="right")
        cs(ws,r,6, rd["odo_end"],    bg=bg, nf='#,##0 "km"', al="right")
        cs(ws,r,7, rd["soc_start"],  bg=bg, nf='0"%"', al="right")
        cs(ws,r,8, rd["soc_end"],    bg=bg, nf='0"%"', al="right")
        cs(ws,r,9, rd["kwh_charged"],bg=bg, nf='0.00 "kWh"', al="right")
        cs(ws,r,10,rd["cost_eur"],   bg=bg, nf='€#,##0.00', al="right")
        cs(ws,r,11,rd["location"],   bg=bg, al="center")

    n = len(sessions)
    if n:
        tr = ds + n
        for col in range(1, 12):
            v  = ("Σ" if col==1
                  else f"=SUM(I{ds}:I{ds+n-1})" if col==9
                  else f"=SUM(J{ds}:J{ds+n-1})" if col==10
                  else "")
            nf = ('0.00 "kWh"' if col==9 else '€#,##0.00' if col==10 else None)
            cs(ws, tr, col, v, bold=True, bg="#"+C_SUM, nf=nf,
               al="center" if col==1 else "right")

    # column widths
    for col, w in enumerate([5,13,8,8,13,13,10,10,13,12,14],1):
        ws.column_dimensions[get_column_letter(col)].width = w

    # Legende
    leg = ds + n + 2
    ws.merge_cells(f"A{leg}:C{leg}")
    c = ws[f"A{leg}"]; c.value="🏠 Grün = Zuhause"; c.font=Font(name="Arial",size=9,color="2E7D32")
    ws.merge_cells(f"D{leg}:F{leg}")
    c = ws[f"D{leg}"]; c.value="⚡ Gelb = Extern"; c.font=Font(name="Arial",size=9,color="F57F17")

    # ── Summary sheet ─────────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Zusammenfassung")
    ws2.merge_cells("A1:B1")
    h = ws2["A1"]
    h.value     = f"Zusammenfassung – {ml} – {loc_label}"
    h.font      = Font(name="Arial", bold=True, size=13, color=C_FG)
    h.fill      = PatternFill("solid", start_color=C_HDR)
    h.alignment = Alignment(horizontal="center", vertical="center")
    ws2.row_dimensions[1].height = 24

    home_s    = [s for s in sessions if s.get("location")=="home"]
    ext_s     = [s for s in sessions if s.get("location")=="extern"]
    home_kwh  = sum(s.get("kwh_charged") or 0 for s in home_s)
    ext_kwh   = sum(s.get("kwh_charged") or 0 for s in ext_s)
    home_cost = sum(s.get("cost_eur")    or 0 for s in home_s)
    ext_cost  = sum(s.get("cost_eur")    or 0 for s in ext_s)
    total_km  = ((sessions[-1].get("odo_end") or 0) - (sessions[0].get("odo_start") or 0)) if n else 0
    total_kwh = home_kwh + ext_kwh
    verbrauch = round(total_kwh / total_km * 100, 1) if total_km > 0 else None

    rows2 = [
        ("Ladevorgänge gesamt",   n,              None),
        ("  🏠 Zuhause",          len(home_s),    None),
        ("  ⚡ Extern",           len(ext_s),     None),
        ("Gesamt kWh",            total_kwh,      '0.00 "kWh"'),
        ("  🏠 Zuhause kWh",      home_kwh,       '0.00 "kWh"'),
        ("  ⚡ Extern kWh",       ext_kwh,        '0.00 "kWh"'),
        ("Gesamtkosten",          home_cost+ext_cost, "€#,##0.00"),
        ("  🏠 Zuhause Kosten",   home_cost,      "€#,##0.00"),
        ("KM Monatsanfang",       sessions[0].get("odo_start") if n else "-", '#,##0 "km"'),
        ("KM Monatsende",         sessions[-1].get("odo_end")  if n else "-", '#,##0 "km"'),
        ("Gefahrene KM",          total_km,       '#,##0 "km"'),
        ("Verbrauch",             verbrauch,      '0.0 "kWh/100km"'),
    ]
    for ri, (lbl, val, fmt_) in enumerate(rows2, 3):
        bg = "#"+C_ALT if ri % 2 == 0 else None
        cs(ws2, ri, 1, lbl, bold=not lbl.startswith("  "), bg=bg, al="left")
        cs(ws2, ri, 2, val, bg=bg, nf=fmt_, al="right")
    ws2.column_dimensions["A"].width = 26
    ws2.column_dimensions["B"].width = 20

    out = EXPORT_DIR / f"EV_Ladeprotokoll_{year:04d}-{month:02d}{suffix}.xlsx"
    wb.save(out)
    print(f"✅ Export: {out} ({n} Sessions)")
    return str(out)

# ── Entry point ───────────────────────────────────────────────────────────────
def export(year, month, location="all", col_override=None, start_row=None, header_info=None,
           cell_mapping=None, sheet=None,
           include_signature=False, signature_path=None, signature_mapping=None):
    sessions = fetch_sessions(year, month, location)
    if TEMPLATE_PATH.exists():
        return export_with_template(year, month, sessions, location, col_override, start_row, header_info,
                                    cell_mapping, sheet,
                                    include_signature=include_signature,
                                    signature_path=signature_path,
                                    signature_mapping=signature_mapping)
    return export_builtin(year, month, sessions, location)

if __name__ == "__main__":
    y, m = (map(int, sys.argv[1].split("-")) if len(sys.argv) > 1
            else (datetime.now().year, datetime.now().month))
    loc = sys.argv[2] if len(sys.argv) > 2 else "all"
    export(y, m, loc)
