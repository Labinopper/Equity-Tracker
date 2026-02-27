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


def test_api_tax_plan_compensation_query_params_and_ui_sections(client):
    sec_id = _add_security(client, "TPCMP")
    _add_lot(client, sec_id, quantity="1000")
    _add_price(sec_id, date(2026, 2, 24), "20.00")

    db_path = _state.get_db_path()
    assert db_path is not None
    settings = AppSettings.load(db_path)
    settings.default_student_loan_plan = 2
    settings.save()

    params = {
        "gross_income_gbp": "101000",
        "bonus_gbp": "0",
        "sell_amount_gbp": "5000",
        "additional_pension_sacrifice_gbp": "3000",
    }
    resp = client.get("/api/tax-plan/summary", params=params)
    assert resp.status_code == 200
    payload = resp.json()
    comp = payload["compensation_plan"]

    assert comp["inputs"]["gross_income_gbp"] == "101000.00"
    assert comp["inputs"]["sell_amount_gbp"] == "5000.00"
    assert len(comp["rows"]) == 5

    rows = {row["scenario_id"]: row for row in comp["rows"]}
    assert rows["sell_baseline"]["in_pa_taper_zone_after_bonus"] is True
    assert rows["sell_baseline"]["marginal_rates_pct"]["income_tax"] == "60.00"
    assert rows["sell_with_extra_pension"]["in_pa_taper_zone_after_bonus"] is False
    assert rows["sell_with_extra_pension"]["marginal_rates_pct"]["income_tax"] == "40.00"
    assert rows["sell_next_tax_year"]["planning_tax_year"] == payload["next_tax_year"]
    assert rows["sell_next_tax_year_with_extra_pension"]["planning_tax_year"] == payload["next_tax_year"]
    assert Decimal(comp["comparison"]["ani_reduction_from_extra_pension_gbp"]) == Decimal("3000.00")
    assert "sell_next_vs_sell_delta_gbp" in comp["comparison"]
    assert "timing_comparison" in comp
    assert (
        comp["timing_comparison"]["baseline_pension"]["income_tax_delta_wait_vs_sell_now_gbp"]
        is not None
    )

    page = client.get("/tax-plan", params=params)
    assert page.status_code == 200
    assert "Compensation What-If (IT / NI / SL + CGT)" in page.text
    assert "Sell Timing Comparison (Wait vs Sell This Year)" in page.text
    assert "Sale Gain Assumption" in page.text
    assert "Sell next tax year + increase pension first" in page.text


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
