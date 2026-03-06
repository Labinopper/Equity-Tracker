from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.services.portfolio_service import PortfolioService
from src.services.risk_service import RiskService


def _add_security(ticker: str, *, currency: str = "GBP"):
    return PortfolioService.add_security(
        ticker=ticker,
        name=f"{ticker} Corp",
        currency=currency,
        is_manual_override=True,
    )


def _add_price(security_id: str, price_gbp: str) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy=price_gbp,
            close_price_gbp=price_gbp,
            currency="GBP",
            source="test-risk",
        )


def test_risk_summary_empty_portfolio_returns_zeroed_metrics(app_context):
    summary = RiskService.get_risk_summary()

    assert summary.total_market_value_gbp == Decimal("0.00")
    assert summary.top_holding_pct == Decimal("0.00")
    assert summary.security_concentration == []
    assert summary.scheme_concentration == []
    assert summary.liquidity is not None
    assert summary.liquidity.classified_total_gbp == Decimal("0.00")
    assert summary.wrapper_allocation is not None
    assert summary.wrapper_allocation.isa_pct_of_total == Decimal("0.00")
    assert len(summary.stress_points) == 6
    assert len(summary.optionality_timeline) == 5
    assert summary.optionality_index is not None
    assert summary.stress_points[0].shock_label == "-30%"
    assert summary.stress_points[-1].shock_label == "+20%"
    assert any("No priced holdings available" in n for n in summary.notes)


def test_risk_summary_calculates_concentration_liquidity_and_stress(app_context):
    sec_a = _add_security("RISK_A")
    sec_b = _add_security("RISK_B")

    # Sellable BROKERAGE lot.
    PortfolioService.add_lot(
        security_id=sec_a.id,
        scheme_type="BROKERAGE",
        acquisition_date=date(2025, 1, 10),
        quantity=Decimal("10"),
        acquisition_price_gbp=Decimal("10.00"),
        true_cost_per_share_gbp=Decimal("10.00"),
    )
    # Locked RSU lot (acquisition_date doubles as vest date in current model).
    PortfolioService.add_lot(
        security_id=sec_a.id,
        scheme_type="RSU",
        acquisition_date=date.today() + timedelta(days=30),
        quantity=Decimal("5"),
        acquisition_price_gbp=Decimal("12.00"),
        true_cost_per_share_gbp=Decimal("12.00"),
    )

    # ESPP+ employee lot with linked in-window matched lot -> employee marked AT_RISK.
    employee = PortfolioService.add_lot(
        security_id=sec_b.id,
        scheme_type="ESPP_PLUS",
        acquisition_date=date(2025, 8, 1),
        quantity=Decimal("8"),
        acquisition_price_gbp=Decimal("9.00"),
        true_cost_per_share_gbp=Decimal("7.00"),
        fmv_at_acquisition_gbp=Decimal("9.00"),
    )
    PortfolioService.add_lot(
        security_id=sec_b.id,
        scheme_type="ESPP_PLUS",
        acquisition_date=date(2025, 8, 1),
        quantity=Decimal("2"),
        acquisition_price_gbp=Decimal("0.00"),
        true_cost_per_share_gbp=Decimal("0.00"),
        fmv_at_acquisition_gbp=Decimal("9.00"),
        matching_lot_id=employee.id,
        forfeiture_period_end=date.today() + timedelta(days=20),
    )

    _add_price(sec_a.id, "20.00")
    _add_price(sec_b.id, "15.00")

    summary = RiskService.get_risk_summary()
    assert summary.total_market_value_gbp == Decimal("450.00")
    assert summary.top_holding_pct == Decimal("66.67")

    assert [row.label for row in summary.security_concentration] == ["RISK_A", "RISK_B"]
    assert summary.security_concentration[0].value_gbp == Decimal("300.00")
    assert summary.security_concentration[1].value_gbp == Decimal("150.00")

    assert summary.liquidity is not None
    assert summary.liquidity.sellable_gbp == Decimal("200.00")
    assert summary.liquidity.locked_gbp == Decimal("130.00")
    assert summary.liquidity.at_risk_gbp == Decimal("120.00")
    assert summary.liquidity.classified_total_gbp == Decimal("450.00")
    assert summary.liquidity.sellable_pct == Decimal("44.44")
    assert summary.liquidity.locked_pct == Decimal("28.89")
    assert summary.liquidity.at_risk_pct == Decimal("26.67")
    assert summary.optionality_index is not None
    assert summary.optionality_timeline[0].label == "Now"
    assert summary.optionality_timeline[0].forfeitable_pct == Decimal("6.67")

    shock_map = {p.shock_label: p.stressed_market_value_gbp for p in summary.stress_points}
    assert shock_map["-30%"] == Decimal("315.00")
    assert shock_map["+20%"] == Decimal("540.00")


def test_risk_summary_flags_unpriced_active_lots(app_context):
    sec = _add_security("NOPRISK")
    PortfolioService.add_lot(
        security_id=sec.id,
        scheme_type="BROKERAGE",
        acquisition_date=date(2025, 1, 10),
        quantity=Decimal("4"),
        acquisition_price_gbp=Decimal("10.00"),
        true_cost_per_share_gbp=Decimal("10.00"),
    )

    summary = RiskService.get_risk_summary()
    assert summary.liquidity is not None
    assert summary.liquidity.unpriced_lot_count == 1
    assert any("excluded due to missing live prices" in n for n in summary.notes)
