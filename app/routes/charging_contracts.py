"""CRUD API for charging contracts (public charging price agreements)."""
from __future__ import annotations
from datetime import datetime

from flask import Blueprint, jsonify, request

from core.db import _get_db, close_db_if_owned
from core.security import require_login, _current_user, has_permission
from services.public_charging_price_service import BUILTIN_CONTRACTS

charging_contracts_bp = Blueprint("charging_contracts", __name__)


def _check_perm(write: bool = False):
    user = _current_user()
    perm = "public_charging:manage_contracts" if write else "public_charging:view"
    if not has_permission(user, perm):
        return jsonify({"error": f"Keine Berechtigung: {perm}"}), 403
    return None


@charging_contracts_bp.route("/api/charging-contracts/builtin")
@require_login
def api_builtin_contracts():
    err = _check_perm()
    if err:
        return err
    return jsonify(list(BUILTIN_CONTRACTS.values()))


@charging_contracts_bp.route("/api/charging-contracts")
@require_login
def api_list_contracts():
    err = _check_perm()
    if err:
        return err
    con = _get_db()
    try:
        rows = con.execute(
            "SELECT * FROM charging_contracts ORDER BY is_default DESC, name ASC"
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        close_db_if_owned(con)


@charging_contracts_bp.route("/api/charging-contracts", methods=["POST"])
@require_login
def api_create_contract():
    err = _check_perm(write=True)
    if err:
        return err
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name erforderlich"}), 400
    now = datetime.utcnow().isoformat()
    con = _get_db()
    try:
        price_ac = _parse_price(data.get("price_ac_kwh"))
        price_dc = _parse_price(data.get("price_dc_kwh"))
        price_kwh = _parse_price(data.get("price_kwh"))
        monthly = _parse_price(data.get("monthly_fee_eur")) or 0.0
        cur = con.execute(
            """INSERT INTO charging_contracts
               (name, cpo, price_ac_kwh, price_dc_kwh, price_kwh,
                monthly_fee_eur, active, is_default, notes, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                name,
                (data.get("cpo") or "").strip(),
                price_ac,
                price_dc,
                price_kwh,
                monthly,
                1 if data.get("active", True) else 0,
                1 if data.get("is_default", False) else 0,
                (data.get("notes") or "").strip(),
                now,
                now,
            ),
        )
        con.commit()
        return jsonify({"id": cur.lastrowid}), 201
    finally:
        close_db_if_owned(con)


@charging_contracts_bp.route("/api/charging-contracts/<int:cid>", methods=["PUT"])
@require_login
def api_update_contract(cid: int):
    err = _check_perm(write=True)
    if err:
        return err
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name erforderlich"}), 400
    now = datetime.utcnow().isoformat()
    con = _get_db()
    try:
        if not con.execute("SELECT id FROM charging_contracts WHERE id=?", (cid,)).fetchone():
            return jsonify({"error": "Nicht gefunden"}), 404
        con.execute(
            """UPDATE charging_contracts SET
               name=?, cpo=?, price_ac_kwh=?, price_dc_kwh=?, price_kwh=?,
               monthly_fee_eur=?, active=?, is_default=?, notes=?, updated_at=?
               WHERE id=?""",
            (
                name,
                (data.get("cpo") or "").strip(),
                _parse_price(data.get("price_ac_kwh")),
                _parse_price(data.get("price_dc_kwh")),
                _parse_price(data.get("price_kwh")),
                _parse_price(data.get("monthly_fee_eur")) or 0.0,
                1 if data.get("active", True) else 0,
                1 if data.get("is_default", False) else 0,
                (data.get("notes") or "").strip(),
                now,
                cid,
            ),
        )
        con.commit()
        return jsonify({"ok": True})
    finally:
        close_db_if_owned(con)


@charging_contracts_bp.route("/api/charging-contracts/<int:cid>", methods=["DELETE"])
@require_login
def api_delete_contract(cid: int):
    err = _check_perm(write=True)
    if err:
        return err
    con = _get_db()
    try:
        if not con.execute("SELECT id FROM charging_contracts WHERE id=?", (cid,)).fetchone():
            return jsonify({"error": "Nicht gefunden"}), 404
        con.execute("DELETE FROM charging_contracts WHERE id=?", (cid,))
        con.commit()
        return jsonify({"ok": True})
    finally:
        close_db_if_owned(con)


@charging_contracts_bp.route("/api/charging-contracts/<int:cid>/set-default", methods=["POST"])
@require_login
def api_set_default_contract(cid: int):
    err = _check_perm(write=True)
    if err:
        return err
    con = _get_db()
    try:
        if not con.execute("SELECT id FROM charging_contracts WHERE id=?", (cid,)).fetchone():
            return jsonify({"error": "Nicht gefunden"}), 404
        con.execute("UPDATE charging_contracts SET is_default=0")
        con.execute("UPDATE charging_contracts SET is_default=1 WHERE id=?", (cid,))
        con.commit()
        return jsonify({"ok": True})
    finally:
        close_db_if_owned(con)


def _parse_price(val) -> float | None:
    if val is None or val == "":
        return None
    try:
        return round(float(val), 4)
    except (TypeError, ValueError):
        return None
