"""
Tariff/pricing configuration routes.
"""
from flask import Blueprint, jsonify, request

import logging

from core.db import _get_db, close_db_if_owned
from core.config import load_config, save_config, DEFAULT_CONFIG
from core.security import require_login, has_permission, _current_user, _audit

log = logging.getLogger(__name__)

tariff_bp = Blueprint("tariff", __name__)


@tariff_bp.route("/api/tariff/config", methods=["GET"])
@require_login
def api_tariff_config_get():
    if not has_permission(_current_user(), "tariffs:view"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    cfg  = load_config()
    keys = [k for k in DEFAULT_CONFIG if k.startswith("tariff_") or k in
            ("octopus_api_key","octopus_account_id","octopus_product_code","octopus_tariff_code","octopus_gbp_eur_factor",
             "tibber_token","generic_tariff_url","generic_tariff_headers","generic_tariff_json_path",
             "generic_tariff_unit","generic_tariff_factor","price_per_kwh_home","price_per_kwh_ac","price_per_kwh_dc",
             "tariff_ha_url","tariff_ha_token","tariff_ha_entity","tariff_evcc_url")]
    result = {k: cfg.get(k, DEFAULT_CONFIG.get(k)) for k in keys}
    for mask_key in ("octopus_api_key", "tibber_token", "tariff_ha_token"):
        if result.get(mask_key):
            result[mask_key] = "********"
    return jsonify(result)


@tariff_bp.route("/api/tariff/config", methods=["POST"])
@require_login
def api_tariff_config_save():
    if not has_permission(_current_user(), "tariffs:configure"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    data = request.get_json(force=True) or {}
    cfg  = load_config()
    allowed = [k for k in DEFAULT_CONFIG if k.startswith("tariff_") or k in
               ("octopus_api_key","octopus_account_id","octopus_product_code","octopus_tariff_code","octopus_gbp_eur_factor",
                "tibber_token","generic_tariff_url","generic_tariff_headers","generic_tariff_json_path",
                "generic_tariff_unit","generic_tariff_factor","price_per_kwh_home","price_per_kwh_ac","price_per_kwh_dc")]
    for k in allowed:
        if k in data and data[k] != "********":
            cfg[k] = data[k]
    save_config(cfg)
    _audit("tariff_config_saved", ip=request.remote_addr)
    return jsonify({"ok": True})


@tariff_bp.route("/api/tariff/test", methods=["POST"])
@require_login
def api_tariff_test():
    if not has_permission(_current_user(), "tariffs:test"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    try:
        from tariff_providers import get_tariff_provider
    except ImportError:
        return jsonify({"error": "tariff_providers Modul nicht gefunden"}), 503
    data = request.get_json(force=True) or {}
    cfg  = load_config()
    cfg.update({k: v for k, v in data.items() if v != "********"})
    try:
        provider = get_tariff_provider(cfg)
        result   = provider.test_connection()
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})


@tariff_bp.route("/api/tariff/prices")
@require_login
def api_tariff_prices():
    if not has_permission(_current_user(), "tariffs:view"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    try:
        from tariff_providers import get_tariff_provider
    except ImportError:
        return jsonify({"error": "tariff_providers Modul nicht gefunden"}), 503
    cfg = load_config()
    try:
        provider = get_tariff_provider(cfg)
        from datetime import date, datetime
        today = datetime.combine(date.today(), datetime.min.time())
        tomorrow = today.replace(hour=23, minute=59)
        prices = provider.get_prices_for_range(today, tomorrow)
        return jsonify({"ok": True, "prices": prices, "provider": cfg.get("tariff_provider","fixed")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "prices": []})


@tariff_bp.route("/api/tariffs/recalculate", methods=["POST"])
@require_login
def api_tariff_recalculate():
    if not has_permission(_current_user(), "tariffs:configure"):
        return jsonify({"ok": False, "error": "Keine Berechtigung: tariffs:configure"}), 403
    data = request.get_json(force=True) or {}
    vehicle_id      = data.get("vehicle_id", "v0")
    month           = data.get("month")           # "2026-05"
    location_filter = data.get("location_filter", "home")
    cfg = load_config()
    try:
        from tariff_providers import get_tariff_provider
        provider = get_tariff_provider(cfg)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Tarifprovider-Fehler: {e}"}), 500
    from datetime import datetime
    con = _get_db()
    try:
        q = "SELECT id, start_ts, end_ts, kwh_charged, cost_manual FROM sessions WHERE end_ts IS NOT NULL AND cost_manual=0"
        params = []
        if vehicle_id and vehicle_id != "all":
            q += " AND vehicle_id=?"; params.append(vehicle_id)
        if month:
            q += " AND start_ts LIKE ?"; params.append(f"{month}%")
        if location_filter and location_filter != "all":
            q += " AND location=?"; params.append(location_filter)
        rows = con.execute(q, params).fetchall()
        fallback_price = float(cfg.get("tariff_fallback_price", cfg.get("price_per_kwh_home", 0.30)))
        provider_id    = cfg.get("tariff_provider", "fixed")
        updated = errors = 0
        for row in rows:
            try:
                start = datetime.fromisoformat(row["start_ts"])
                end   = datetime.fromisoformat(row["end_ts"])
                kwh   = row["kwh_charged"]
                if kwh is None:
                    continue
                price = provider.get_average_price(start, end)
                price_source = provider_id if price is not None else "fallback"
                if price is None:
                    price = fallback_price
                cost = round(float(kwh) * price, 2)
                con.execute(
                    "UPDATE sessions SET price_per_kwh=?, cost_eur=?, tariff_provider=?, tariff_price_source=? WHERE id=?",
                    (round(price, 5), cost, provider_id, price_source, row["id"]))
                updated += 1
            except Exception as _re:
                log.warning("Tariff recalculate session #%s: %s", row["id"], _re)
                errors += 1
        con.commit()
        _audit("tariff_recalculated", ip=request.remote_addr)
        return jsonify({"ok": True, "updated": updated, "errors": errors, "total": len(rows)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        close_db_if_owned(con)
