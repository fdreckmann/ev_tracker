import os, sys, sqlite3, shutil, json
from datetime import datetime
from pathlib import Path
import openpyxl
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

DATA_DIR      = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH       = DATA_DIR / "sessions.db"
EXPORT_DIR    = DATA_DIR / "exports"
TEMPLATE_PATH = DATA_DIR / "template.xlsx"
PRICE_PER_KWH = float(os.environ.get("PRICE_PER_KWH", "0.30"))

# ── Column keyword → field name mapping ───────────────────────────────────────
COLUMN_KEYWORDS = {
    "datum":       "date",
    "date":        "date",
    "start":       "start_time",
    "beginn":      "start_time",
    "ende":        "end_time",
    "end":         "end_time",
    "km start":    "odo_start",
    "km-start":    "odo_start",
    "start km":    "odo_start",
    "odometer":    "odo_start",
    "km ende":     "odo_end",
    "km-ende":     "odo_end",
    "end km":      "odo_end",
    "kwh":         "kwh_charged",
    "geladen":     "kwh_charged",
    "energie":     "kwh_charged",
    "energy":      "kwh_charged",
    "kosten":      "cost_eur",
    "cost":        "cost_eur",
    "betrag":      "cost_eur",
    "soc start":   "soc_start",
    "soc anfang":  "soc_start",
    "soc ende":    "soc_end",
    "soc end":     "soc_end",
    "standort":    "location",
    "ort":         "location",
    "location":    "location",
    "nr":          "row_num",
    "#":           "row_num",
    "lfd":         "row_num",
}

FIELD_LABELS = {
    "date":        "Datum",
    "start_time":  "Start Uhrzeit",
    "end_time":    "Ende Uhrzeit",
    "odo_start":   "KM-Stand Start",
    "odo_end":     "KM-Stand Ende",
    "soc_start":   "SOC Start (%)",
    "soc_end":     "SOC Ende (%)",
    "kwh_charged": "Geladene kWh",
    "cost_eur":    "Kosten (€)",
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
    "row_num":     "0",
}

LOCATION_LABELS = {"home": "🏠 Zuhause", "extern": "⚡ Extern", "unknown": "—"}

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
    return {
        "row_num":     idx,
        "date":        dt_s.date(),
        "start_time":  dt_s.time(),
        "end_time":    dt_e.time() if dt_e else None,
        "odo_start":   s.get("odo_start"),
        "odo_end":     s.get("odo_end"),
        "soc_start":   s.get("soc_start"),
        "soc_end":     s.get("soc_end"),
        "kwh_charged": s.get("kwh_charged"),
        "cost_eur":    s.get("cost_eur"),
        "location":    LOCATION_LABELS.get(s.get("location", "unknown"), s.get("location", "—")),
    }

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

# ── Template-based export ─────────────────────────────────────────────────────
def export_with_template(year, month, sessions, location, col_override=None, start_row=None):
    ml     = datetime(year, month, 1).strftime("%B %Y")
    suffix = f"_{location}" if location != "all" else ""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = EXPORT_DIR / f"EV_Ladeprotokoll_{year:04d}-{month:02d}{suffix}.xlsx"
    shutil.copy(TEMPLATE_PATH, out)
    wb = openpyxl.load_workbook(out, keep_vba=False)
    ws = wb.active

    # build column map from explicit mapping (col_override already contains saved mapping)
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
                break

    # determine data start row
    if start_row:
        ds = int(start_row)
    else:
        # auto-detect: first row with >= 2 filled cells + 1
        detected = None
        for row in ws.iter_rows():
            filled = [c for c in row if c.value is not None and str(c.value).strip()]
            if len(filled) >= 2:
                detected = row[0].row; break
        ds = (detected + 1) if detected else (ws.max_row or 1)

    if not col_map:
        col_map = {1:"row_num",2:"date",3:"start_time",4:"end_time",
                   5:"odo_start",6:"odo_end",7:"kwh_charged",8:"cost_eur",9:"location"}

    max_row  = ws.max_row or 0

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

    def safe_set(cell, value):
        try: cell.value = value
        except (AttributeError, TypeError): pass

    # clear old data rows
    for r in range(ds, max_row + 1):
        for cell in ws[r]:
            safe_set(cell, None)

    # write data
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

    # update title cell
    if header_row > 1:
        for row in ws.iter_rows(max_row=header_row - 1):
            for cell in row:
                if cell.value and any(w in str(cell.value).lower()
                        for w in ("monat","month","bericht","protokoll","ladeprotokoll")):
                    safe_set(cell, f"EV Ladeprotokoll – {ml}"); break

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
def export(year, month, location="all", col_override=None, start_row=None):
    sessions = fetch_sessions(year, month, location)
    if TEMPLATE_PATH.exists():
        return export_with_template(year, month, sessions, location, col_override, start_row)
    return export_builtin(year, month, sessions, location)

if __name__ == "__main__":
    y, m = (map(int, sys.argv[1].split("-")) if len(sys.argv) > 1
            else (datetime.now().year, datetime.now().month))
    loc = sys.argv[2] if len(sys.argv) > 2 else "all"
    export(y, m, loc)
