"""
Central Excel-report builder — single source of truth.

Both the manual export (/api/export), the archived report (/api/reports/create)
and the automatic e-mail report (email_reports._send_report_email) use this so
that the *same* period/location/template/mapping always produces the *same*
XLSX.

Design notes
------------
* There is exactly ONE physical template file on disk: ``DATA_DIR/template.xlsx``
  (managed by templates_routes.py). The "active" mapping for that file lives in
  the top-level config keys (``template_column_mapping``, ``template_cell_mapping``,
  ``template_start_row`` …).
* ``export_templates`` is a list of *saved mapping presets* for that file.
* ``report_email_template_id`` selects which mapping to use for auto-reports:
    - ``None`` / ``""`` / ``"active"``      → active config-level mapping (= manual export)
    - ``"builtin:standard"`` / ``"builtin"``→ force the built-in report (ignore template.xlsx)
    - any export_templates id               → that preset's mapping (against template.xlsx)
"""
import logging

log = logging.getLogger(__name__)

_MASK_BUILTIN = ("builtin:standard", "builtin", "builtin:default")


def _template_path():
    """The single physical template file. Looked up dynamically so it matches
    whatever export_excel actually uses (and respects test monkeypatching)."""
    import export_excel
    return export_excel.TEMPLATE_PATH


def _signature_path():
    import core.db
    return core.db.DATA_DIR / "signatures" / "default_signature.png"


class ReportExcelError(Exception):
    """Fatal problem building a report XLSX. ``code`` lets callers react
    specifically (e.g. the manual route returns HTTP 409 on a hash mismatch)."""
    def __init__(self, message, code="error"):
        super().__init__(message)
        self.code = code


def _normalize_template_id(template_id):
    """Returns one of: ('active', None) | ('builtin', None) | ('preset', tid)."""
    if template_id in (None, "", "active", "default"):
        return ("active", None)
    if str(template_id).lower() in _MASK_BUILTIN:
        return ("builtin", None)
    return ("preset", template_id)


def _sig_mapping_compat(sig_mapping):
    """Backward compat: ``cell`` → ``anchor_cell`` (same as export.py)."""
    sig_mapping = sig_mapping or {}
    if sig_mapping and "cell" in sig_mapping and "anchor_cell" not in sig_mapping:
        sig_mapping = dict(sig_mapping)
        sig_mapping["anchor_cell"] = sig_mapping["cell"]
    return sig_mapping


def resolve_template_settings(cfg, template_id=None):
    """Resolve the mapping/template settings for the given template_id.

    Returns a dict::

        {
          "kind": "active"|"builtin"|"preset",
          "name": str,
          "force_builtin": bool,
          "col_override": dict|None,
          "start_row", "header_row", "footer_start_row", "sheet",
          "cell_mapping": dict,
          "signature_mapping": dict,
          "header_info": dict,
          "preset_include_signature": bool,   # only meaningful for presets
          "has_column_mapping": bool,
        }
    """
    kind, tid = _normalize_template_id(template_id)

    # header_info is identical regardless of mapping source.
    header_info = {
        "fahrer":            cfg.get("template_fahrer", ""),
        "kennzeichen":       cfg.get("template_kennzeichen", ""),
        "abteilung":         cfg.get("template_abteilung", ""),
        "kostenstelle":      cfg.get("template_kostenstelle", ""),
        "price_per_kwh":     cfg.get("price_per_kwh_home", 0.30),
        "meter_start_value": cfg.get("template_meter_start", 0.0),
    }

    if kind == "builtin":
        return {
            "kind": "builtin", "name": "Standard-Report (ohne Vorlage)",
            "force_builtin": True,
            "col_override": None, "start_row": None, "header_row": None,
            "footer_start_row": None, "sheet": None, "cell_mapping": {},
            "signature_mapping": {}, "header_info": header_info,
            "preset_include_signature": False, "has_column_mapping": False,
        }

    if kind == "preset":
        preset = next((t for t in cfg.get("export_templates", []) if t.get("id") == tid), None)
        if preset is None:
            log.warning("report template preset %s not found — falling back to active mapping", tid)
            kind = "active"  # graceful fallback
        else:
            col_map = preset.get("column_mapping") or preset.get("mapping") or {}
            col_override = {k: v for k, v in col_map.items() if v} if isinstance(col_map, dict) else None
            start_row  = preset.get("start_row")
            header_row = preset.get("header_row")
            if start_row and not header_row:
                try: header_row = int(start_row) - 1
                except (ValueError, TypeError): pass
            return {
                "kind": "preset", "name": preset.get("name", "Vorlage"),
                "force_builtin": False,
                "col_override": col_override or None,
                "start_row": start_row, "header_row": header_row,
                "footer_start_row": preset.get("footer_start_row"),
                "sheet": preset.get("sheet") or None,
                "cell_mapping": preset.get("cell_mapping") or {},
                "signature_mapping": _sig_mapping_compat(preset.get("signature_mapping")),
                "header_info": header_info,
                "preset_include_signature": bool(preset.get("include_signature", False)),
                "has_column_mapping": bool(col_override),
            }

    # kind == "active": read the live config-level mapping (identical to export.py)
    saved = cfg.get("template_column_mapping") or cfg.get("template_mapping") or {}
    col_override = {k: v for k, v in saved.items() if v} if isinstance(saved, dict) and saved else None
    start_row  = cfg.get("template_start_row")
    header_row = cfg.get("template_header_row")
    if start_row and not header_row:
        try: header_row = int(start_row) - 1
        except (ValueError, TypeError): pass
    _raw_cm = cfg.get("template_cell_mapping") or {}
    return {
        "kind": "active",
        "name": (cfg.get("active_template") or {}).get("name") or "Aktives Export-Template",
        "force_builtin": False,
        "col_override": col_override,
        "start_row": start_row, "header_row": header_row,
        "footer_start_row": cfg.get("template_footer_start_row"),
        "sheet": cfg.get("template_sheet") or None,
        "cell_mapping": _raw_cm if isinstance(_raw_cm, dict) else {},
        "signature_mapping": _sig_mapping_compat(cfg.get("signature_mapping")),
        "header_info": header_info,
        "preset_include_signature": bool(cfg.get("export_include_signature", False)),
        "has_column_mapping": bool(col_override),
    }


def validate_report_template(cfg, template_id=None, include_signature=False):
    """Validate the template/mapping *before* generating or sending.

    Returns ``(ok, fatal_error_or_None, warnings, meta)``.
    ``meta`` = {kind, name, has_column_mapping}.
    """
    settings = resolve_template_settings(cfg, template_id)
    warnings = []
    meta = {"kind": settings["kind"], "name": settings["name"],
            "has_column_mapping": settings["has_column_mapping"]}

    # Built-in never needs a template file or mapping.
    if settings["force_builtin"]:
        return True, None, warnings, meta

    template_path = _template_path()
    using_template_file = template_path.exists()
    if not using_template_file:
        # No template uploaded → export() will use the built-in report. That is a
        # valid (documented) fallback, not an error.
        warnings.append("Kein Export-Template hochgeladen — Standard-Report wird verwendet.")
        return True, None, warnings, meta

    # Hash mismatch: the saved mapping was made for a *different* template file.
    tmpl_hash = (cfg.get("active_template") or {}).get("hash")
    map_hash  = cfg.get("template_mapping_hash")
    if settings["kind"] == "active" and settings["has_column_mapping"] and tmpl_hash:
        if map_hash is None or map_hash != tmpl_hash:
            return (False,
                    "Excel-Template-Mapping ungültig. Bitte Export-Template prüfen.",
                    warnings, meta)

    # Column mapping needs a start row to know where data begins.
    if settings["has_column_mapping"] and not settings["start_row"]:
        return (False,
                "Excel-Template-Mapping ungültig: Startzeile fehlt. Bitte Export-Template prüfen.",
                warnings, meta)

    # Sheet must exist in the workbook (warning — export() falls back to active sheet).
    if settings["sheet"]:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(template_path, read_only=True)
            if settings["sheet"] not in wb.sheetnames:
                warnings.append(f"Tabellenblatt '{settings['sheet']}' nicht im Template — Standardblatt wird verwendet.")
            wb.close()
        except Exception as e:
            warnings.append(f"Template konnte nicht geprüft werden: {e}")

    # Signature requested but no position / no file.
    if include_signature:
        if not _signature_path().exists():
            warnings.append("Signatur aktiviert, aber keine Signatur gespeichert.")
        else:
            sm = settings["signature_mapping"]
            if not (sm.get("anchor_cell") or sm.get("cell")):
                warnings.append("Signatur aktiviert, aber keine Signaturposition definiert.")

    return True, None, warnings, meta


def build_report_excel_bytes(period_info, location_filter, vehicle_filter, cfg, lang,
                             include_signature=False, template_id=None):
    """Render a single month as XLSX using the active export template + mapping.

    ``period_info`` must contain ``start`` (a date/datetime). ``vehicle_filter`` is
    accepted for API symmetry but — like the existing manual export — is not applied
    at the Excel-row level (the underlying export() filters by location only).

    Returns ``(xlsx_bytes, warnings)``. Raises :class:`ReportExcelError` on a fatal
    template/mapping problem so callers never send an empty/broken attachment.
    """
    from export_excel import export as _export_func
    from core.location import normalize_location as _nl

    ok, fatal, warnings, _meta = validate_report_template(cfg, template_id, include_signature)
    if not ok:
        code = "hash_mismatch" if "Mapping ungültig" in (fatal or "") and "Startzeile" not in (fatal or "") else "invalid_mapping"
        raise ReportExcelError(fatal, code=code)

    settings = resolve_template_settings(cfg, template_id)

    start = period_info["start"]
    year, month = start.year, start.month
    xl_loc = _nl(location_filter) if location_filter not in ("all", None) else "all"

    _sp = _signature_path()
    sig_path = str(_sp) if (include_signature and _sp.exists()) else None

    xlsx_bytes, warn2 = _export_func(
        year=year, month=month, location=xl_loc,
        col_override=settings["col_override"],
        start_row=settings["start_row"],
        header_row=settings["header_row"],
        header_info=settings["header_info"],
        cell_mapping=settings["cell_mapping"],
        sheet=settings["sheet"],
        footer_start_row=settings["footer_start_row"],
        include_signature=bool(sig_path),
        signature_path=sig_path,
        signature_mapping=settings["signature_mapping"],
        lang=lang, return_warnings=True,
        force_builtin=settings["force_builtin"],
    )
    return xlsx_bytes, (warnings + (warn2 or []))


def build_multi_month_zip_bytes(periods_sessions, location_filter, vehicle_filter, cfg, lang,
                                include_signature=False, template_id=None):
    """One template XLSX per month, packed into a ZIP.

    ``periods_sessions`` is the list of ``(period_info, sessions)`` tuples used by
    the auto-report. Returns ``(zip_bytes, warnings, filename)``.
    """
    import io
    import zipfile

    warnings = []
    keys = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for period_info, _sessions in periods_sessions:
            xb, w = build_report_excel_bytes(
                period_info, location_filter, vehicle_filter, cfg, lang,
                include_signature=include_signature, template_id=template_id)
            key = period_info.get("period_key", "").replace("monthly:", "") \
                or period_info["start"].strftime("%Y-%m")
            keys.append(key)
            zf.writestr(f"EV_Report_{key}.xlsx", xb)
            warnings.extend(w or [])
    keys = [k for k in keys if k]
    fname = (f"EV_Reports_{keys[0]}_bis_{keys[-1]}.zip"
             if len(keys) >= 2 else f"EV_Reports_{keys[0] if keys else 'export'}.zip")
    return buf.getvalue(), warnings, fname
