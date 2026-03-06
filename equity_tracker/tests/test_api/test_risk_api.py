from __future__ import annotations

from datetime import date

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository


def _add_security(client, ticker: str) -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Inc",
            "currency": "GBP",
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot(client, security_id: str, *, quantity: str) -> None:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2025-01-15",
            "quantity": quantity,
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert resp.status_code == 201, resp.text


def test_api_risk_summary_empty_portfolio(client):
    resp = client.get("/api/risk/summary")
    assert resp.status_code == 200
    body = resp.json()

    assert body["total_market_value_gbp"] == "0.00"
    assert body["top_holding_pct"] == "0.00"
    assert body["security_concentration"] == []
    assert body["scheme_concentration"] == []
    assert body["liquidity"]["classified_total_gbp"] == "0.00"
    assert body["top_holding_sellable_pct"] == "0.00"
    assert body["deployable"]["deployable_capital_gbp"] == "0.00"
    assert body["employer_dependence"]["ratio_pct"] == "0.00"
    assert body["wrapper_allocation"]["isa_pct_of_total"] == "0.00"
    assert len(body["optionality_timeline"]) == 5
    assert "score" in body["optionality_index"]
    assert len(body["stress_points"]) == 6


def test_api_risk_summary_with_priced_holdings(client):
    sec_id = _add_security(client, "RISKAPI")
    _add_lot(client, sec_id, quantity="10")

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="20.00",
            close_price_gbp="20.00",
            currency="GBP",
            source="test-risk-api",
        )

    resp = client.get("/api/risk/summary")
    assert resp.status_code == 200
    body = resp.json()

    assert body["total_market_value_gbp"] == "200.00"
    assert body["top_holding_pct"] == "100.00"
    assert body["security_concentration"][0]["label"] == "RISKAPI"
    assert body["security_concentration"][0]["value_gbp"] == "200.00"
    assert body["liquidity"]["sellable_gbp"] == "200.00"
    assert body["top_holding_sellable_pct"] == "100.00"
    assert body["deployable"]["deployable_capital_gbp"] == "200.00"
    assert body["deployable"]["employer_share_of_deployable_pct"] == "0.00"
    assert body["wrapper_allocation"]["taxable_pct_of_total"] == "100.00"
    assert body["optionality_timeline"][0]["label"] == "Now"
    assert body["stress_points"][0]["shock_label"] == "-30%"
    assert body["stress_points"][-1]["shock_label"] == "+20%"


def test_risk_ui_page_renders(client):
    resp = client.get("/risk")
    assert resp.status_code == 200
    assert "Risk" in resp.text
    assert "Optionality Index" in resp.text
    assert "Future Optionality Timeline" in resp.text
    assert "Top Holdings Concentration" in resp.text
    assert "Stress Test" in resp.text


def test_api_risk_summary_accepts_optionality_weight_overrides(client):
    resp = client.get(
        "/api/risk/summary?weight_sellability=60&weight_forfeiture=10&weight_concentration=10&weight_isa_ratio=10&weight_config=10"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["optionality_index"]["weights_pct"]["sellability"] == "60.00"
    assert body["optionality_index"]["weights_pct"]["forfeiture"] == "10.00"
