from __future__ import annotations

from datetime import date


def _add_security(client, ticker: str) -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Input Co",
            "currency": "GBP",
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot(client, security_id: str, scheme_type: str = "BROKERAGE") -> str:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": scheme_type,
            "acquisition_date": date(2025, 1, 2).isoformat(),
            "quantity": "10",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_add_lot_page_shows_downstream_impact_preview(client):
    _ = _add_security(client, "WBADDL")
    page = client.get("/portfolio/add-lot")
    assert page.status_code == 200
    text = page.text
    assert "Downstream Impact Preview" in text
    assert "Estimated Position Value Contribution" in text
    assert "Impacted Surfaces" in text


def test_edit_lot_page_shows_downstream_impact_preview(client):
    security_id = _add_security(client, "WBEDIT")
    lot_id = _add_lot(client, security_id)
    page = client.get(f"/portfolio/edit-lot?lot_id={lot_id}")
    assert page.status_code == 200
    text = page.text
    assert "Downstream Impact Preview" in text
    assert "Cost Basis Total Delta" in text
    assert "True Cost Total Delta" in text


def test_transfer_lot_page_shows_downstream_surface_impact(client):
    security_id = _add_security(client, "WBXFER")
    _ = _add_lot(client, security_id, scheme_type="RSU")
    page = client.get("/portfolio/transfer-lot")
    assert page.status_code == 200
    text = page.text
    assert "Downstream Surface Impact" in text


def test_add_security_page_shows_conflict_helper_artifacts(client):
    page = client.get("/portfolio/add-security")
    assert page.status_code == 200
    text = page.text
    assert "client-conflict-helper" in text
    assert "existing-security-index-json" in text


def test_add_security_duplicate_renders_conflict_resolution_helper(client):
    _ = _add_security(client, "WBCONF")
    duplicate = client.post(
        "/portfolio/add-security",
        data={
            "ticker": "WBCONF",
            "name": "Duplicate Security",
            "currency": "GBP",
            "isin": "",
            "exchange": "",
            "units_precision": "0",
            "catalog_id": "",
            "is_manual_override": "true",
        },
    )
    assert duplicate.status_code == 422
    assert "Conflict Resolution Helper" in duplicate.text


def test_settings_page_shows_completeness_and_constrained_surfaces(client):
    page = client.get("/settings")
    assert page.status_code == 200
    text = page.text
    assert "Settings Completeness" in text
    assert "Constrained Surface" in text
