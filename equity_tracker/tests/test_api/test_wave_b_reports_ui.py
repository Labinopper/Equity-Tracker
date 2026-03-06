from __future__ import annotations

from datetime import date, timedelta

from src.core.tax_engine import tax_year_for_date


def _add_security(client, ticker: str) -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Wave B Plc",
            "currency": "GBP",
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot(client, security_id: str, *, acquisition_date: date) -> None:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": acquisition_date.isoformat(),
            "quantity": "10",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "8.00",
        },
    )
    assert resp.status_code == 201, resp.text


def test_cgt_page_shows_assumption_basis_card(client):
    resp = client.get("/cgt")
    assert resp.status_code == 200
    text = resp.text
    assert "CGT Assumption Basis" in text
    assert "Realised-only mode" in text


def test_economic_gain_page_shows_cgt_delta_columns(client):
    security_id = _add_security(client, "WBECOD")
    tx_date = date(2025, 6, 1)
    _add_lot(client, security_id, acquisition_date=date(2025, 1, 2))
    commit = client.post(
        "/portfolio/disposals/commit",
        json={
            "security_id": security_id,
            "quantity": "5",
            "price_per_share_gbp": "12.00",
            "transaction_date": tx_date.isoformat(),
        },
    )
    assert commit.status_code == 201, commit.text

    tax_year = tax_year_for_date(tx_date)
    page = client.get(f"/economic-gain?tax_year={tax_year}")
    assert page.status_code == 200
    text = page.text
    assert "Net Delta vs CGT Basis" in text
    assert "CGT Gain / Loss" in text
    assert "Delta (Economic - CGT)" in text


def test_dividends_page_shows_actual_vs_forecast_split(client):
    security_id = _add_security(client, "WBDIVS")
    today = date.today()
    past_date = today - timedelta(days=10)
    future_date = today + timedelta(days=10)

    for div_date in (past_date, future_date):
        resp = client.post(
            "/api/dividends/entries",
            json={
                "security_id": security_id,
                "dividend_date": div_date.isoformat(),
                "amount_gbp": "50.00",
                "tax_treatment": "TAXABLE",
                "source": "test-wave-b",
            },
        )
        assert resp.status_code == 201, resp.text

    page = client.get("/dividends")
    assert page.status_code == 200
    text = page.text
    assert "Actual vs Forecast Split" in text
    assert "Actual Entries Through" in text
    assert "Forecast Entries After" in text
