from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.api import _state
from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.services.portfolio_service import PortfolioService
from src.settings import AppSettings


def _add_security(client, ticker: str) -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Tax Plan PLC",
            "currency": "GBP",
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot(client, security_id: str, quantity: str = "5") -> None:
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


def _add_price(security_id: str, price_date: date, close_gbp: str) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=price_date,
            close_price_original_ccy=close_gbp,
            close_price_gbp=close_gbp,
            currency="GBP",
            source="test-tax-plan-api",
        )


def test_api_tax_plan_summary_and_ui_render_on_empty_db(client):
    resp = client.get("/api/tax-plan/summary")
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["hide_values"] is False
    assert "active_tax_year" in payload
    assert "next_tax_year" in payload
    assert "summary" in payload
    assert "lots" in payload

    page = client.get("/tax-plan")
    assert page.status_code == 200
    assert "Tax Plan" in page.text
    assert "Per-Lot Projection" in page.text


def test_api_tax_plan_returns_lot_projection_rows_when_data_exists(client):
    sec_id = _add_security(client, "TPAPI")
    _add_lot(client, sec_id, quantity="4")
    _add_price(sec_id, date(2026, 2, 24), "20.00")

    resp = client.get("/api/tax-plan/summary")
    assert resp.status_code == 200
    payload = resp.json()

    rows = payload["lots"]
    assert len(rows) == 1
    assert rows[0]["ticker"] == "TPAPI"
    assert rows[0]["projection_available"] is True
    assert rows[0]["projected_gain_gbp"] == "40.00"


def test_api_tax_plan_respects_hide_values_setting(client):
    db_path = _state.get_db_path()
    assert db_path is not None

    settings = AppSettings.load(db_path)
    settings.hide_values = True
    settings.save()

    resp = client.get("/api/tax-plan/summary")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["hide_values"] is True
    assert payload["lots"] == []
    assert payload["hidden_reason"] == "Values hidden by privacy mode."

    page = client.get("/tax-plan")
    assert page.status_code == 200
    assert "Values hidden by privacy mode." in page.text


def test_tax_plan_navigation_link_present_on_home(client):
    sec_id = _add_security(client, "TPNAV")
    _add_lot(client, sec_id, quantity="1")
    _add_price(sec_id, date(2026, 2, 24), "12.00")
    PortfolioService.get_portfolio_summary()

    page = client.get("/")
    assert page.status_code == 200
    assert 'href="/tax-plan"' in page.text
