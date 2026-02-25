from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.services.analytics_service import AnalyticsService
from src.services.portfolio_service import PortfolioService
from src.settings import AppSettings


def _add_security(ticker: str):
    return PortfolioService.add_security(
        ticker=ticker,
        name=f"{ticker} Analytics Co",
        currency="GBP",
        is_manual_override=True,
    )


def _add_lot(
    security_id: str,
    quantity: str,
    *,
    acquisition_date: date = date(2025, 1, 15),
) -> None:
    PortfolioService.add_lot(
        security_id=security_id,
        scheme_type="BROKERAGE",
        acquisition_date=acquisition_date,
        quantity=Decimal(quantity),
        acquisition_price_gbp=Decimal("10.00"),
        true_cost_per_share_gbp=Decimal("10.00"),
    )


def _add_price(security_id: str, price_date: date, close_gbp: str) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=price_date,
            close_price_original_ccy=close_gbp,
            close_price_gbp=close_gbp,
            currency="GBP",
            source="test-analytics",
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


def test_portfolio_over_time_empty_portfolio_returns_explicit_reason(app_context):
    payload = AnalyticsService.get_portfolio_over_time()

    assert payload["has_data"] is False
    assert payload["reason"] == "No active lots available."
    assert payload["points"] == []


def test_portfolio_over_time_with_no_price_history_returns_empty_state(app_context):
    sec = _add_security("ANONOHIST")
    _add_lot(sec.id, "5")

    payload = AnalyticsService.get_portfolio_over_time()

    assert payload["has_data"] is False
    assert "No GBP price history" in payload["reason"]
    assert payload["points"] == []


def test_portfolio_over_time_handles_partial_price_history_with_coverage_notes(app_context):
    sec_a = _add_security("ANPARTA")
    sec_b = _add_security("ANPARTB")
    _add_lot(sec_a.id, "5")
    _add_lot(sec_b.id, "2")

    _add_price(sec_a.id, date(2026, 2, 20), "10.00")
    _add_price(sec_a.id, date(2026, 2, 21), "11.00")
    _add_price(sec_b.id, date(2026, 2, 21), "20.00")

    payload = AnalyticsService.get_portfolio_over_time()

    assert payload["has_data"] is True
    assert payload["labels"] == ["2026-02-20", "2026-02-21"]
    assert payload["points"][0]["priced_security_count"] == 1
    assert payload["points"][0]["total_security_count"] == 2
    assert payload["points"][0]["total_value_gbp"] == "50.00"
    assert payload["points"][1]["total_value_gbp"] == "95.00"
    assert any("partial" in note.lower() for note in payload["notes"])


def test_summary_payload_contains_group_a_widgets_and_unrealised_rows(app_context):
    sec = _add_security("ANSUM")
    _add_lot(sec.id, "3")
    _add_price(sec.id, date(2026, 2, 24), "15.00")

    summary = AnalyticsService.get_summary()

    assert summary["hide_values"] is False
    assert "portfolio_value_time" in summary["widgets"]
    assert "scheme_concentration" in summary["widgets"]
    assert "security_concentration" in summary["widgets"]
    assert "liquidity_breakdown" in summary["widgets"]
    assert "unrealised_pnl" in summary["widgets"]
    assert "stress_test" in summary["widgets"]
    assert "forfeiture_at_risk" in summary["widgets"]
    assert "events_timeline" in summary["widgets"]

    unrealised_rows = summary["widgets"]["unrealised_pnl"]["rows"]
    assert len(unrealised_rows) == 1
    assert unrealised_rows[0]["ticker"] == "ANSUM"
    assert unrealised_rows[0]["market_value_gbp"] == "45.00"


def test_summary_payload_includes_group_c_and_d_rows(app_context):
    sec_rsu = _add_security("ANGCRSU")
    sec_plus = _add_security("ANGCPLUS")

    PortfolioService.add_lot(
        security_id=sec_rsu.id,
        scheme_type="RSU",
        acquisition_date=date.today() + timedelta(days=14),
        quantity=Decimal("6"),
        acquisition_price_gbp=Decimal("10.00"),
        true_cost_per_share_gbp=Decimal("10.00"),
    )

    employee = PortfolioService.add_lot(
        security_id=sec_plus.id,
        scheme_type="ESPP_PLUS",
        acquisition_date=date.today() - timedelta(days=30),
        quantity=Decimal("8"),
        acquisition_price_gbp=Decimal("9.00"),
        true_cost_per_share_gbp=Decimal("8.00"),
        fmv_at_acquisition_gbp=Decimal("9.00"),
    )
    PortfolioService.add_lot(
        security_id=sec_plus.id,
        scheme_type="ESPP_PLUS",
        acquisition_date=date.today() - timedelta(days=30),
        quantity=Decimal("2"),
        acquisition_price_gbp=Decimal("0.00"),
        true_cost_per_share_gbp=Decimal("0.00"),
        fmv_at_acquisition_gbp=Decimal("9.00"),
        matching_lot_id=employee.id,
        forfeiture_period_end=date.today() + timedelta(days=21),
    )

    _add_price(sec_rsu.id, date.today(), "15.00")
    _add_price(sec_plus.id, date.today(), "12.00")

    summary = AnalyticsService.get_summary()

    stress_widget = summary["widgets"]["stress_test"]
    assert stress_widget["has_data"] is True
    assert len(stress_widget["rows"]) == 6

    forfeiture_widget = summary["widgets"]["forfeiture_at_risk"]
    assert forfeiture_widget["has_data"] is True
    assert forfeiture_widget["total_lot_count"] == 1
    assert forfeiture_widget["rows"][0]["ticker"] == "ANGCPLUS"
    assert forfeiture_widget["rows"][0]["value_at_risk_gbp"] == "24.00"

    timeline_widget = summary["widgets"]["events_timeline"]
    assert timeline_widget["has_data"] is True
    event_types = {row["event_type"] for row in timeline_widget["rows"]}
    assert "VEST_DATE" in event_types
    assert "FORFEITURE_END" in event_types


def test_summary_respects_hide_values_mode(app_context):
    settings = AppSettings()
    settings.hide_values = True

    summary = AnalyticsService.get_summary(settings=settings)
    portfolio_time = AnalyticsService.get_portfolio_over_time(settings=settings)

    assert summary["hide_values"] is True
    assert summary["widgets"]["scheme_concentration"]["hidden"] is True
    assert summary["widgets"]["stress_test"]["hidden"] is True
    assert summary["widgets"]["forfeiture_at_risk"]["hidden"] is True
    assert summary["widgets"]["events_timeline"]["hidden"] is True
    assert portfolio_time["hidden"] is True
    assert portfolio_time["has_data"] is False
    assert portfolio_time["reason"] == "Values hidden by privacy mode."


def test_tax_position_payload_contains_group_b_rows_across_tax_years(app_context):
    sec = _add_security("ANTAX")
    _add_lot(sec.id, "5", acquisition_date=date(2024, 1, 15))

    _commit_disposal(
        sec.id,
        quantity="2",
        price_per_share_gbp="12.00",
        transaction_date=date(2024, 7, 10),
    )
    _commit_disposal(
        sec.id,
        quantity="1",
        price_per_share_gbp="11.00",
        transaction_date=date(2025, 7, 10),
    )

    settings = AppSettings()
    settings.default_tax_year = "2025-26"

    payload = AnalyticsService.get_tax_position(settings=settings)

    assert payload["active_tax_year"] == "2025-26"
    assert payload["widgets"]["cgt_year_position"]["has_data"] is True
    assert payload["widgets"]["gain_loss_history"]["has_data"] is True
    assert payload["widgets"]["economic_vs_tax"]["has_data"] is True

    years = [row["tax_year"] for row in payload["widgets"]["gain_loss_history"]["rows"]]
    assert years == ["2024-25", "2025-26"]


def test_tax_position_respects_hide_values_mode(app_context):
    settings = AppSettings()
    settings.hide_values = True

    payload = AnalyticsService.get_tax_position(settings=settings)

    assert payload["widgets"]["cgt_year_position"]["hidden"] is True
    assert payload["widgets"]["gain_loss_history"]["hidden"] is True
    assert payload["widgets"]["economic_vs_tax"]["hidden"] is True
