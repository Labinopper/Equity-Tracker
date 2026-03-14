from __future__ import annotations

from datetime import date, timedelta
from src.app_context import AppContext
from src.db.repository.prices import PriceRepository


def _add_security(client, ticker: str = "HISTDIV", currency: str = "GBP") -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Inc",
            "currency": currency,
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot(
    client,
    *,
    security_id: str,
    scheme_type: str = "BROKERAGE",
    quantity: str = "10",
    price: str = "10.00",
    acquisition_date: str = "2025-01-15",
) -> None:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": scheme_type,
            "acquisition_date": acquisition_date,
            "quantity": quantity,
            "acquisition_price_gbp": price,
            "true_cost_per_share_gbp": price,
        },
    )
    assert resp.status_code == 201, resp.text


def _upsert_daily_price(security_id: str, *, price_date: date, close_gbp: str) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=price_date,
            close_price_original_ccy=close_gbp,
            close_price_gbp=close_gbp,
            currency="GBP",
            source="yfinance_history",
        )


def test_api_history_portfolio_includes_dividend_adjusted_gain_fields(client):
    security_id = _add_security(client, ticker="HPORTDIV")
    _add_lot(client, security_id=security_id)

    yesterday = date.today() - timedelta(days=1)
    today = date.today()
    _upsert_daily_price(security_id, price_date=yesterday, close_gbp="12.00")
    _upsert_daily_price(security_id, price_date=today, close_gbp="12.00")

    entry = client.post(
        "/api/dividends/entries",
        json={
            "security_id": security_id,
            "dividend_date": today.isoformat(),
            "amount_gbp": "50.00",
            "tax_treatment": "TAXABLE",
            "source": "test",
        },
    )
    assert entry.status_code == 201, entry.text

    payload = client.get("/api/history/portfolio").json()
    assert payload["has_data"] is True
    latest = payload["total_series"][-1]
    assert latest["sellable_gain_gbp"] == "20.00"
    assert latest["cumulative_net_dividends_gbp"] == "50.00"
    assert latest["sellable_gain_plus_net_dividends_gbp"] == "70.00"
    assert latest["decomp_price_gbp"] is not None
    assert latest["decomp_quantity_gbp"] is not None
    assert latest["decomp_fx_gbp"] is not None
    assert latest["decomp_dividends_gbp"] is not None
    assert payload["major_shift_rows"]

    stats = payload["summary_stats"]
    assert stats["estimated_net_dividends_gbp"] == "50.00"
    assert stats["gain_if_sold_plus_net_dividends_gbp"] == "70.00"
    assert stats["capital_at_risk_after_dividends_gbp"] == "50.00"


def test_api_history_security_includes_dividend_adjusted_gain_fields(client):
    security_id = _add_security(client, ticker="HSECDIV")
    _add_lot(client, security_id=security_id)

    yesterday = date.today() - timedelta(days=1)
    today = date.today()
    _upsert_daily_price(security_id, price_date=yesterday, close_gbp="12.00")
    _upsert_daily_price(security_id, price_date=today, close_gbp="12.00")

    entry = client.post(
        "/api/dividends/entries",
        json={
            "security_id": security_id,
            "dividend_date": today.isoformat(),
            "amount_gbp": "50.00",
            "tax_treatment": "TAXABLE",
            "source": "test",
        },
    )
    assert entry.status_code == 201, entry.text

    payload = client.get(f"/api/history/{security_id}").json()
    assert payload["has_data"] is True
    latest = payload["price_series"][-1]
    assert latest["sellable_gain_gbp"] == "20.00"
    assert latest["cumulative_net_dividends_gbp"] == "50.00"
    assert latest["sellable_gain_plus_net_dividends_gbp"] == "70.00"
    assert latest["decomp_price_gbp"] is not None
    assert latest["decomp_quantity_gbp"] is not None
    assert latest["decomp_fx_gbp"] is not None
    assert latest["decomp_dividends_gbp"] is not None
    assert payload["major_shift_rows"]

    stats = payload["summary_stats"]
    assert stats["estimated_net_dividends_gbp"] == "50.00"
    assert stats["gain_if_sold_plus_net_dividends_gbp"] == "70.00"
    assert stats["capital_at_risk_after_dividends_gbp"] == "50.00"


def test_api_history_uses_actual_net_dividends_when_withholding_is_recorded(client):
    security_id = _add_security(client, ticker="HISTWHT")
    _add_lot(client, security_id=security_id)

    yesterday = date.today() - timedelta(days=1)
    today = date.today()
    _upsert_daily_price(security_id, price_date=yesterday, close_gbp="12.00")
    _upsert_daily_price(security_id, price_date=today, close_gbp="12.00")

    entry = client.post(
        "/api/dividends/entries",
        json={
            "security_id": security_id,
            "dividend_date": today.isoformat(),
            "amount_original_ccy": "2.50",
            "original_currency": "GBP",
            "tax_withheld_original_ccy": "0.37",
            "tax_treatment": "TAXABLE",
            "source": "test",
        },
    )
    assert entry.status_code == 201, entry.text

    payload = client.get("/api/history/portfolio").json()
    latest = payload["total_series"][-1]
    assert latest["cumulative_net_dividends_gbp"] == "2.13"
    assert latest["sellable_gain_plus_net_dividends_gbp"] == "22.13"

    stats = payload["summary_stats"]
    assert stats["estimated_net_dividends_gbp"] == "2.13"
    assert stats["gain_if_sold_plus_net_dividends_gbp"] == "22.13"
    assert stats["capital_at_risk_after_dividends_gbp"] == "97.87"


def test_history_ui_pages_show_dividend_adjusted_toggle_and_labels(client):
    security_id = _add_security(client, ticker="HUITOG")
    _add_lot(client, security_id=security_id)

    today = date.today()
    _upsert_daily_price(security_id, price_date=today, close_gbp="12.00")

    overview = client.get("/history")
    assert overview.status_code == 200
    assert "Gain + Net Dividends" in overview.text
    assert "data-gain-overlay=\"plus_dividends\"" in overview.text
    assert "Shift Decomposition" in overview.text

    detail = client.get(f"/history/{security_id}")
    assert detail.status_code == 200
    assert "Gain + Net Dividends" in detail.text
    assert "data-gain-overlay=\"plus_dividends\"" in detail.text
    assert "Shift Decomposition" in detail.text
