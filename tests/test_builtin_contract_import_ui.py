"""
Regression tests for the builtin charging-contract import flow.

Bug: the import button was built as
  onclick="importBuiltinContract(${JSON.stringify(JSON.stringify(c))})"
which embeds raw double quotes inside a double-quoted HTML attribute — the
browser truncates the attribute and the click does nothing.

Fix: builtins are stored in window._builtinContractsById and the button only
carries a safe id (data-builtin-id) → importBuiltinContractById(id).
"""
import json
import os

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def _template():
    with open(os.path.join(ROOT, "app", "templates", "index.html")) as fh:
        return fh.read()


def test_no_json_in_onclick_attribute():
    html = _template()
    assert "JSON.stringify(JSON.stringify" not in html, \
        "JSON darf nicht doppelt-stringified in ein onclick-Attribut geschrieben werden"


def test_import_uses_id_map():
    html = _template()
    assert "window._builtinContractsById" in html
    assert "importBuiltinContractById" in html
    idx = html.find("async function importBuiltinContractById")
    assert idx >= 0
    body = html[idx:idx + 1600]
    # Errors must be surfaced, not swallowed
    assert "toast(" in body
    assert "console." in body


def test_inline_escapehtml_escapes_quotes():
    """The inline escapeHtml overrides api.js' version — it must escape quotes
    so data-name="..."-style attributes can't be broken or injected."""
    html = _template()
    idx = html.find("function escapeHtml(s)")
    assert idx >= 0
    body = html[idx:idx + 300]
    assert "&quot;" in body, "escapeHtml im Template muss doppelte Anführungszeichen escapen"
    assert "&#39;" in body, "escapeHtml im Template muss einfache Anführungszeichen escapen"


def test_builtin_endpoint_returns_ids(authed_client):
    """Every builtin template must carry an id usable as the JS map key."""
    rv = authed_client.get("/api/charging-contracts/builtin")
    assert rv.status_code == 200
    builtins = json.loads(rv.data)
    assert len(builtins) > 0
    for c in builtins:
        assert c.get("id"), f"Builtin ohne id: {c.get('name')}"
        assert c.get("name")


def test_import_builtin_roundtrip(authed_client):
    """POSTing a builtin's fields creates the contract; duplicate gives 409 with error text."""
    rv = authed_client.get("/api/charging-contracts/builtin")
    c = json.loads(rv.data)[0]
    payload = {
        "name": c["name"], "cpo": c.get("cpo") or "",
        "price_ac_kwh": c.get("price_ac_kwh"), "price_dc_kwh": c.get("price_dc_kwh"),
        "monthly_fee_eur": c.get("monthly_fee_eur") or 0,
        "notes": c.get("description") or "",
    }
    r1 = authed_client.post("/api/charging-contracts",
                            data=json.dumps(payload), content_type="application/json")
    assert r1.status_code == 201
    assert json.loads(r1.data).get("id")

    # Imported contract appears in the list
    rows = json.loads(authed_client.get("/api/charging-contracts").data)
    assert any(r["name"] == c["name"] for r in rows)

    # Duplicate import → 409 with visible error message
    r2 = authed_client.post("/api/charging-contracts",
                            data=json.dumps(payload), content_type="application/json")
    assert r2.status_code == 409
    d2 = json.loads(r2.data)
    assert d2.get("error")
    assert d2.get("duplicate") is True
