from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.services.exposure_service import ExposureService
from src.services.portfolio_service import PortfolioService


def _set_price(security_id: str, close_gbp: str) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=date.today(),
            close_price_original_ccy=close_gbp,
            close_price_gbp=close_gbp,
            currency="GBP",
            source="test-exposure-service",
        )


def test_exposure_snapshot_keeps_locked_forfeitable_and_concentration_splits(app_context):
    ibm = PortfolioService.add_security(
        ticker="IBM",
        name="IBM Corp",
        currency="GBP",
        is_manual_override=True,
    )
    other = PortfolioService.add_security(
        ticker="OTHER",
        name="Other Corp",
        currency="GBP",
        is_manual_override=True,
    )

    PortfolioService.add_lot(
        security_id=ibm.id,
        scheme_type="BROKERAGE",
        acquisition_date=date.today() - timedelta(days=120),
        quantity=Decimal("10"),
        acquisition_price_gbp=Decimal("10.00"),
        true_cost_per_share_gbp=Decimal("10.00"),
    )
    employee = PortfolioService.add_lot(
        security_id=ibm.id,
        scheme_type="ESPP_PLUS",
        acquisition_date=date.today() - timedelta(days=30),
        quantity=Decimal("4"),
        acquisition_price_gbp=Decimal("8.00"),
        true_cost_per_share_gbp=Decimal("8.00"),
        fmv_at_acquisition_gbp=Decimal("8.00"),
    )
    PortfolioService.add_lot(
        security_id=ibm.id,
        scheme_type="ESPP_PLUS",
        acquisition_date=date.today() - timedelta(days=30),
        quantity=Decimal("1"),
        acquisition_price_gbp=Decimal("0.00"),
        true_cost_per_share_gbp=Decimal("0.00"),
        fmv_at_acquisition_gbp=Decimal("8.00"),
        matching_lot_id=employee.id,
        forfeiture_period_end=date.today() + timedelta(days=15),
    )
    PortfolioService.add_lot(
        security_id=other.id,
        scheme_type="RSU",
        acquisition_date=date.today() + timedelta(days=20),
        quantity=Decimal("5"),
        acquisition_price_gbp=Decimal("20.00"),
        true_cost_per_share_gbp=Decimal("20.00"),
    )

    _set_price(ibm.id, "30.00")
    _set_price(other.id, "40.00")

    summary = PortfolioService.get_portfolio_summary(use_live_true_cost=False)
    snapshot = ExposureService.get_snapshot(summary=summary)

    assert snapshot["locked_capital_gbp"] == Decimal("200.00")
    assert snapshot["forfeitable_capital_gbp"] == Decimal("30.00")
    assert snapshot["total_sellable_market_value_gbp"] == Decimal("420.00")
    assert snapshot["top_holding_ticker_gross"] == "IBM"
    assert snapshot["top_holding_pct_gross"] == Decimal("69.23")
    assert snapshot["top_holding_ticker_sellable"] == "IBM"
    assert snapshot["top_holding_pct_sellable"] == Decimal("100.00")
