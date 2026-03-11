from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.services.liquidation_tax_service import LiquidationTaxService
from src.services.portfolio_service import PortfolioService
from src.settings import AppSettings


def test_project_sell_now_applies_aea_before_cgt(app_context):
    sec = PortfolioService.add_security("LQTY", "Liquidation Plc", "USD", is_manual_override=True)
    PortfolioService.add_lot(
        security_id=sec.id,
        scheme_type="BROKERAGE",
        acquisition_date=date(2026, 1, 10),
        quantity=Decimal("10"),
        acquisition_price_gbp=Decimal("10.00"),
        true_cost_per_share_gbp=Decimal("10.00"),
    )

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec.id,
            price_date=date(2026, 3, 11),
            close_price_original_ccy="20.00",
            close_price_gbp="15.00",
            currency="USD",
            source="test-liquidation-tax-service",
        )

    settings = AppSettings()
    settings.default_tax_year = "2025-26"
    settings.default_gross_income = Decimal("65000")
    settings.default_other_income = Decimal("10000")

    summary = PortfolioService.get_portfolio_summary(settings=settings, as_of=date(2026, 3, 11))
    projection = LiquidationTaxService.project_sell_now(
        summary=summary,
        settings=settings,
        as_of=date(2026, 3, 11),
    )

    assert projection["hypothetical_gains_gbp"] == Decimal("49.25")
    assert projection["aea_used_gbp"] == Decimal("49.25")
    assert projection["taxable_gain_gbp"] == Decimal("0.00")
    assert projection["incremental_cgt_gbp"] == Decimal("0.00")
    assert projection["estimated_fees_total_gbp"] == Decimal("0.75")


def test_project_tax_year_incremental_applies_fees_as_allowable_costs(app_context):
    settings = AppSettings()
    settings.default_tax_year = "2025-26"
    settings.default_gross_income = Decimal("65000")
    settings.default_other_income = Decimal("10000")

    projection = LiquidationTaxService.project_tax_year_incremental(
        tax_year="2025-26",
        settings=settings,
        additional_gains=[Decimal("3005.00")],
        additional_losses=[],
    )

    assert projection["taxable_gain_gbp"] == Decimal("5.00")
    assert projection["incremental_cgt_gbp"] == Decimal("1.20")
    assert projection["tax_at_higher_rate_gbp"] == Decimal("1.20")
