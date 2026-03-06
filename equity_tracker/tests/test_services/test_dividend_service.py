from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.services.dividend_service import DividendService
from src.services.portfolio_service import PortfolioService
from src.settings import AppSettings


def _add_security(ticker: str):
    return PortfolioService.add_security(
        ticker=ticker,
        name=f"{ticker} Dividend Co",
        currency="GBP",
        is_manual_override=True,
    )


def test_dividend_summary_empty_portfolio_returns_zeroed_state(app_context):
    payload = DividendService.get_summary(as_of=date(2026, 2, 24))

    assert payload["hide_values"] is False
    assert payload["entries"] == []
    assert payload["summary"]["all_time_total_gbp"] == "0.00"
    assert payload["summary"]["estimated_tax_gbp"] == "0.00"


def test_dividend_summary_trailing_forecast_and_tax_year_totals(app_context):
    sec = _add_security("DIVSVC")
    as_of = date(2026, 2, 24)

    DividendService.add_dividend_entry(
        security_id=sec.id,
        dividend_date=date(2025, 6, 1),
        amount_gbp=Decimal("1000"),
        tax_treatment="TAXABLE",
    )
    DividendService.add_dividend_entry(
        security_id=sec.id,
        dividend_date=date(2025, 7, 1),
        amount_gbp=Decimal("50"),
        tax_treatment="ISA_EXEMPT",
    )
    DividendService.add_dividend_entry(
        security_id=sec.id,
        dividend_date=date(2026, 6, 1),
        amount_gbp=Decimal("200"),
        tax_treatment="TAXABLE",
    )

    payload = DividendService.get_summary(as_of=as_of)
    summary = payload["summary"]

    assert summary["trailing_12m_total_gbp"] == "1050.00"
    assert summary["forecast_12m_total_gbp"] == "200.00"
    assert summary["all_time_total_gbp"] == "1250.00"
    assert summary["all_time_taxable_dividends_gbp"] == "1200.00"
    assert summary["all_time_isa_exempt_dividends_gbp"] == "50.00"
    assert summary["estimated_tax_gbp"] == "43.75"
    assert summary["estimated_net_dividends_gbp"] == "1206.25"
    assert summary["tax_drag_pct"] == "3.65"

    years = {row["tax_year"]: row for row in payload["tax_years"]}
    assert years["2025-26"]["taxable_dividends_gbp"] == "1000.00"
    assert years["2025-26"]["estimated_dividend_tax_gbp"] == "43.75"
    assert years["2026-27"]["taxable_dividends_gbp"] == "200.00"
    assert years["2026-27"]["estimated_dividend_tax_gbp"] == "0.00"


def test_dividend_summary_respects_hide_values_mode(app_context):
    settings = AppSettings()
    settings.hide_values = True

    payload = DividendService.get_summary(settings=settings, as_of=date(2026, 2, 24))
    assert payload["hide_values"] is True
    assert payload["entries"] == []
    assert payload["tax_years"] == []


def test_dividend_entry_supports_native_currency_with_fx_provenance(app_context):
    sec = _add_security("DIVUSD")

    created = DividendService.add_dividend_entry(
        security_id=sec.id,
        dividend_date=date(2026, 2, 10),
        amount_original_ccy=Decimal("100.00"),
        original_currency="USD",
        fx_rate_to_gbp=Decimal("0.8000"),
        fx_rate_source="manual_test",
        tax_treatment="TAXABLE",
    )
    assert created["amount_gbp"] == "80.00"
    assert created["amount_original_ccy"] == "100.00"
    assert created["original_currency"] == "USD"
    assert created["fx_rate_to_gbp"] == "0.800000"

    payload = DividendService.get_summary(as_of=date(2026, 2, 24))
    entry = payload["entries"][0]
    assert entry["amount_gbp"] == "80.00"
    assert entry["amount_original_ccy"] == "100.00"
    assert entry["original_currency"] == "USD"
    assert entry["fx_rate_to_gbp"] == "0.800000"
    assert payload["allocation"]["mode"] == "SECURITY_LEVEL"
    assert payload["allocation"]["rows"][0]["ticker"] == "DIVUSD"
