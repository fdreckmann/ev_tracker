"""
Billing configuration and summary routes.
"""
import json
from datetime import date, datetime

from flask import Blueprint, jsonify, request

from core.db import _get_db, close_db_if_owned
from core.config import load_config
from core.security import require_login, has_permission, _current_user, _audit

billing_bp = Blueprint("billing", __name__)


@billing_bp.route("/api/billing/config/<vehicle_id>", methods=["GET"])
@require_login
def api_billing_config_get(vehicle_id):
    if not has_permission(_current_user(), "billing:view"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    con = _get_db()
    row = con.execute("SELECT * FROM billing_config WHERE vehicle_id=?", (vehicle_id,)).fetchone()
    close_db_if_owned(con)
    if row:
        d = dict(row)
        d["recipients"] = json.loads(d.get("recipients") or "[]")
        return jsonify(d)
    return jsonify({"vehicle_id": vehicle_id, "enabled": False,
                    "reimbursement_mode": "fixed_price", "reimbursement_price_per_kwh": 0.30,
                    "location_filter": "all", "recipients": []})


@billing_bp.route("/api/billing/config/<vehicle_id>", methods=["POST"])
@require_login
def api_billing_config_save(vehicle_id):
    if not has_permission(_current_user(), "billing:configure"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    data    = request.get_json(force=True) or {}
    now_iso = datetime.utcnow().isoformat()
    con     = _get_db()
    existing = con.execute("SELECT id FROM billing_config WHERE vehicle_id=?", (vehicle_id,)).fetchone()
    recipients = json.dumps(data.get("recipients", []))
    fields = {
        "vehicle_id":                  vehicle_id,
        "enabled":                     int(bool(data.get("enabled"))),
        "location_filter":             data.get("location_filter", "all"),
        "reimbursement_mode":          data.get("reimbursement_mode", "fixed_price"),
        "reimbursement_price_per_kwh": float(data.get("reimbursement_price_per_kwh") or 0.30),
        "requires_approval":           int(bool(data.get("requires_approval"))),
        "report_template_id":          data.get("report_template_id"),
        "auto_send":                   int(bool(data.get("auto_send"))),
        "recipients":                  recipients,
        "driver_name":                 data.get("driver_name", ""),
        "license_plate":               data.get("license_plate", ""),
        "cost_center":                 data.get("cost_center", ""),
        "employee_id":                 data.get("employee_id", ""),
        "department":                  data.get("department", ""),
        "employer_email":              data.get("employer_email", ""),
        "requires_signature":          int(bool(data.get("requires_signature"))),
        "updated_at":                  now_iso,
    }
    if existing:
        sets = ", ".join(f"{k}=?" for k in fields if k != "vehicle_id")
        vals = [fields[k] for k in fields if k != "vehicle_id"] + [vehicle_id]
        con.execute(f"UPDATE billing_config SET {sets} WHERE vehicle_id=?", vals)
    else:
        fields["created_at"] = now_iso
        keys = list(fields.keys())
        vals = [fields[k] for k in keys]
        con.execute(
            f"INSERT INTO billing_config ({','.join(keys)}) VALUES ({','.join(['?']*len(keys))})",
            vals)
    con.commit()
    close_db_if_owned(con)
    _audit("billing_config_saved", f"vehicle={vehicle_id}", ip=request.remote_addr)
    return jsonify({"ok": True})


@billing_bp.route("/api/billing/summary")
@require_login
def api_billing_summary():
    if not has_permission(_current_user(), "billing:view"):
        return jsonify({"error": "Keine Berechtigung"}), 403
    today = date.today()
    first = today.replace(day=1)
    con   = _get_db()
    sessions = con.execute(
        "SELECT * FROM sessions WHERE end_ts IS NOT NULL AND start_ts >= ? ORDER BY start_ts DESC",
        (first.isoformat(),)).fetchall()
    rows = [dict(r) for r in sessions]
    bc_row = con.execute("SELECT * FROM billing_config WHERE enabled=1 LIMIT 1").fetchone()
    close_db_if_owned(con)
    total_kwh  = sum(r.get("kwh_charged") or 0 for r in rows)
    total_cost = sum(r.get("cost_eur") or 0 for r in rows)
    bc         = dict(bc_row) if bc_row else {}
    reimb_rate = float(bc.get("reimbursement_price_per_kwh") or 0)
    lf = bc.get("location_filter", "all")
    if lf == "home":
        kwh_for_reimb = sum((r.get("kwh_charged") or 0) for r in rows if r.get("location") == "home")
    elif lf in ("extern", "external"):
        kwh_for_reimb = sum((r.get("kwh_charged") or 0) for r in rows if r.get("location") in ("extern", "external"))
    else:
        kwh_for_reimb = total_kwh
    return jsonify({
        "month":               first.isoformat(),
        "sessions":            len(rows),
        "total_kwh":           round(total_kwh, 3),
        "total_cost":          round(total_cost, 2),
        "reimbursable_kwh":    round(kwh_for_reimb, 3),
        "reimbursement_total": round(kwh_for_reimb * reimb_rate, 2),
        "reimbursement_rate":  reimb_rate,
    })
