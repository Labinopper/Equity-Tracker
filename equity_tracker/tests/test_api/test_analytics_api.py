from __future__ import annotations

from datetime import date, timedelta
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
            "name": f"{ticker} Analytics PLC",
            "currency": "GBP",
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot(client, security_id: str, quantity: str = "4") -> None:
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
            source="test-analytics-api",
        )


def _commit_disposal(
    security_id: str,
    *,
    quantity: str,
    price_per_share_gbp: str,
    transaction_date: date,
) -> None:
    PortfolioService.commit_disposal(
        security_id=security_id,
        quantity=Decimal(quantity),
        price_per_share_gbp=Decimal(price_per_share_gbp),
        transaction_date=transaction_date,
    )


def test_api_analytics_summary_and_portfolio_time_empty_db(client):
    summary_resp = client.get("/api/analytics/summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()

    assert "generated_at_utc" in summary
    assert summary["hide_values"] is False
    assert "widgets" in summary
    assert "portfolio_value_time" in summary["widgets"]
    assert "scheme_concentration" in summary["widgets"]
    assert "security_concentration" in summary["widgets"]
    assert "liquidity_breakdown" in summary["widgets"]
    assert "unrealised_pnl" in summary["widgets"]
    assert "cgt_year_position" in summary["widgets"]
    assert "gain_loss_history" in summary["widgets"]
    assert "economic_vs_tax" in summary["widgets"]
    assert "stress_test" in summary["widgets"]
    assert "fx_attribution" in summary["widgets"]
    assert "forfeiture_at_risk" in summary["widgets"]
    assert "events_timeline" in summary["widgets"]
    assert "widget_order" in summary
    assert summary["widgets"]["liquidity_breakdown"]["decision_criticality"] == "Critical"
    assert summary["widgets"]["liquidity_breakdown"]["decision_context_label"] == "Liquidity Clarity"

    series_resp = client.get("/api/analytics/portfolio-over-time")
    assert series_resp.status_code == 200
    series = series_resp.json()

    assert series["has_data"] is False
    assert series["reason"] == "No active lots available."
    assert series["points"] == []

    tax_resp = client.get("/api/analytics/tax-position")
    assert tax_resp.status_code == 200
    tax_payload = tax_resp.json()
    assert "active_tax_year" in tax_payload
    assert "widgets" in tax_payload
    assert "cgt_year_position" in tax_payload["widgets"]


def test_api_analytics_endpoints_return_data_when_prices_exist(client):
    sec_id = _add_security(client, "ANAPI")
    _add_lot(client, sec_id, quantity="3")
    _add_price(sec_id, date(2026, 2, 24), "20.00")

    summary_resp = client.get("/api/analytics/summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()

    scheme_rows = summary["widgets"]["scheme_concentration"]["rows"]
    assert len(scheme_rows) == 1
    assert scheme_rows[0]["label"] == "Brokerage"

    unrealised_rows = summary["widgets"]["unrealised_pnl"]["rows"]
    assert len(unrealised_rows) == 1
    assert unrealised_rows[0]["ticker"] == "ANAPI"
    assert unrealised_rows[0]["market_value_gbp"] == "60.00"
    assert len(summary["widgets"]["stress_test"]["rows"]) == 6
    assert "rows" in summary["widgets"]["fx_attribution"]
    assert summary["widgets"]["events_timeline"]["has_data"] is True

    series_resp = client.get("/api/analytics/portfolio-over-time")
    assert series_resp.status_code == 200
    series = series_resp.json()

    assert series["has_data"] is True
    assert series["labels"] == ["2026-02-24"]
    assert series["values_gbp"] == ["60.00"]


def test_api_analytics_group_c_and_d_widgets_with_risk_and_calendar_inputs(client):
    sec_rsu = _add_security(client, "ANAPIRSU")
    sec_plus = _add_security(client, "ANAPIPLUS")

    PortfolioService.add_lot(
        security_id=sec_rsu,
        scheme_type="RSU",
        acquisition_date=date.today() + timedelta(days=14),
        quantity=Decimal("6"),
        acquisition_price_gbp=Decimal("10.00"),
        true_cost_per_share_gbp=Decimal("10.00"),
    )

    employee = PortfolioService.add_lot(
        security_id=sec_plus,
        scheme_type="ESPP_PLUS",
        acquisition_date=date.today() - timedelta(days=30),
        quantity=Decimal("8"),
        acquisition_price_gbp=Decimal("9.00"),
        true_cost_per_share_gbp=Decimal("8.00"),
        fmv_at_acquisition_gbp=Decimal("9.00"),
    )
    PortfolioService.add_lot(
        security_id=sec_plus,
        scheme_type="ESPP_PLUS",
        acquisition_date=date.today() - timedelta(days=30),
        quantity=Decimal("2"),
        acquisition_price_gbp=Decimal("0.00"),
        true_cost_per_share_gbp=Decimal("0.00"),
        fmv_at_acquisition_gbp=Decimal("9.00"),
        matching_lot_id=employee.id,
        forfeiture_period_end=date.today() + timedelta(days=21),
    )

    _add_price(sec_rsu, date.today(), "15.00")
    _add_price(sec_plus, date.today(), "12.00")

    resp = client.get("/api/analytics/summary")
    assert resp.status_code == 200
    summary = resp.json()

    forfeiture_widget = summary["widgets"]["forfeiture_at_risk"]
    assert forfeiture_widget["has_data"] is True
    assert forfeiture_widget["total_lot_count"] == 1
    assert forfeiture_widget["rows"][0]["ticker"] == "ANAPIPLUS"
    assert forfeiture_widget["rows"][0]["value_at_risk_gbp"] == "24.00"

    timeline_widget = summary["widgets"]["events_timeline"]
    assert timeline_widget["has_data"] is True
    event_types = {row["event_type"] for row in timeline_widget["rows"]}
    assert "VEST_DATE" in event_types
    assert "FORFEITURE_END" in event_types


def test_analytics_ui_page_renders_widget_controls_and_table_toggle(client):
    resp = client.get("/analytics")
    assert resp.status_code == 200
    assert "Analytics" in resp.text
    assert "analytics-widget-toggle" in resp.text
    assert "analytics-focus-btn" in resp.text
    assert "Show table" in resp.text
    assert "analytics.widget_visibility.v1" in resp.text
    assert "cgt-year-position" in resp.text
    assert "gain-loss-history" in resp.text
    assert "economic-vs-tax" in resp.text
    assert "stress-test" in resp.text
    assert "fx-attribution" in resp.text
    assert "forfeiture-at-risk" in resp.text
    assert "events-timeline" in resp.text
    assert "analytics-widget-visibility-table" in resp.text
    assert "applyPriorityOrder" in resp.text
    assert "analytics-critical-warning" in resp.text
    assert "applyCriticalFloor" in resp.text
    assert "decision_criticality" in resp.text


def test_api_analytics_respects_hide_values_setting(client):
    db_path = _state.get_db_path()
    assert db_path is not None

    settings = AppSettings.load(db_path)
    settings.hide_values = True
    settings.save()

    summary_resp = client.get("/api/analytics/summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert summary["hide_values"] is True
    assert summary["widgets"]["portfolio_value_time"]["hidden"] is True
    assert summary["widgets"]["scheme_concentration"]["reason"] == "Values hidden by privacy mode."
    assert summary["widgets"]["cgt_year_position"]["hidden"] is True
    assert summary["widgets"]["stress_test"]["hidden"] is True
    assert summary["widgets"]["fx_attribution"]["hidden"] is True
    assert summary["widgets"]["forfeiture_at_risk"]["hidden"] is True
    assert summary["widgets"]["events_timeline"]["hidden"] is True

    series_resp = client.get("/api/analytics/portfolio-over-time")
    assert series_resp.status_code == 200
    series = series_resp.json()
    assert series["hidden"] is True
    assert series["has_data"] is False
    assert series["reason"] == "Values hidden by privacy mode."

    tax_resp = client.get("/api/analytics/tax-position")
    assert tax_resp.status_code == 200
    tax_payload = tax_resp.json()
    assert tax_payload["widgets"]["cgt_year_position"]["hidden"] is True

    page_resp = client.get("/analytics")
    assert page_resp.status_code == 200
    assert "Values hidden by privacy mode." in page_resp.text


def test_api_analytics_tax_position_returns_history_when_disposals_exist(client):
    sec_id = _add_security(client, "ANTAXAPI")
    _add_lot(client, sec_id, quantity="5")
    _commit_disposal(
        sec_id,
        quantity="2",
        price_per_share_gbp="12.00",
        transaction_date=date(2025, 7, 8),
    )

    tax_resp = client.get("/api/analytics/tax-position")
    assert tax_resp.status_code == 200
    payload = tax_resp.json()

    assert payload["widgets"]["cgt_year_position"]["has_data"] is True
    assert payload["widgets"]["gain_loss_history"]["has_data"] is True
    assert payload["widgets"]["economic_vs_tax"]["has_data"] is True
