from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.services.portfolio_service import PortfolioService
from src.services.tax_plan_service import TaxPlanService
from src.settings import AppSettings


def _add_security(ticker: str):
    return PortfolioService.add_security(
        ticker=ticker,
        name=f"{ticker} Tax Planner Co",
        currency="GBP",
        is_manual_override=True,
    )


def _add_price(security_id: str, price_date: date, close_gbp: str) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=price_date,
            close_price_original_ccy=close_gbp,
            close_price_gbp=close_gbp,
            currency="GBP",
            source="test-tax-plan-service",
        )


def test_tax_plan_summary_empty_portfolio_returns_explicit_scope(app_context):
    payload = TaxPlanService.get_summary(as_of=date(2026, 2, 24))

    assert payload["hide_values"] is False
    assert payload["active_tax_year"] == "2025-26"
    assert payload["next_tax_year"] == "2026-27"
    assert payload["lots"] == []
    assert (
        payload["summary"]["cross_year_comparison"]["additional_realisation_scope"][
            "sellable_projected_lot_count"
        ]
        == 0
    )


def test_tax_plan_summary_projects_per_lot_and_cross_year_difference(app_context):
    sec = _add_security("TPLAN")
    _add_price(sec.id, date(2026, 2, 24), "20.00")

    PortfolioService.add_lot(
        security_id=sec.id,
        scheme_type="BROKERAGE",
        acquisition_date=date(2025, 1, 15),
        quantity=Decimal("300"),
        acquisition_price_gbp=Decimal("10.00"),
        true_cost_per_share_gbp=Decimal("10.00"),
    )
    PortfolioService.commit_disposal(
        security_id=sec.id,
        quantity=Decimal("300"),
        price_per_share_gbp=Decimal("20.00"),
        transaction_date=date(2025, 8, 1),
    )
    PortfolioService.add_lot(
        security_id=sec.id,
        scheme_type="BROKERAGE",
        acquisition_date=date(2025, 9, 1),
        quantity=Decimal("500"),
        acquisition_price_gbp=Decimal("10.00"),
        true_cost_per_share_gbp=Decimal("10.00"),
    )

    payload = TaxPlanService.get_summary(as_of=date(2026, 2, 24))

    assert len(payload["lots"]) == 1
    row = payload["lots"][0]
    assert row["projection_available"] is True
    assert row["projected_gain_gbp"] == "5000.00"
    assert row["if_sold_current_year_incremental_cgt_gbp"] == "900.00"
    assert row["if_sold_next_year_incremental_cgt_gbp"] == "360.00"
    assert row["incremental_cgt_difference_wait_gbp"] == "540.00"

    comparison = payload["summary"]["cross_year_comparison"]
    assert (
        comparison["sell_before_tax_year_end"]["projected_incremental_cgt_gbp"]
        == "900.00"
    )
    assert (
        comparison["sell_after_tax_year_rollover"]["projected_incremental_cgt_gbp"]
        == "360.00"
    )
    assert comparison["incremental_cgt_difference_if_wait_gbp"] == "540.00"


def test_tax_plan_summary_marks_locked_and_isa_rows_as_unavailable(app_context):
    as_of = date(2026, 2, 24)

    sec_rsu = _add_security("TPRSU")
    sec_isa = _add_security("TPISA")
    _add_price(sec_rsu.id, as_of, "15.00")
    _add_price(sec_isa.id, as_of, "10.00")

    PortfolioService.add_lot(
        security_id=sec_rsu.id,
        scheme_type="RSU",
        acquisition_date=date(2026, 5, 1),
        quantity=Decimal("10"),
        acquisition_price_gbp=Decimal("12.00"),
        true_cost_per_share_gbp=Decimal("12.00"),
    )
    PortfolioService.add_lot(
        security_id=sec_isa.id,
        scheme_type="ISA",
        acquisition_date=date(2025, 7, 1),
        quantity=Decimal("10"),
        acquisition_price_gbp=Decimal("8.00"),
        true_cost_per_share_gbp=Decimal("8.00"),
    )

    payload = TaxPlanService.get_summary(as_of=as_of)
    rows = {row["ticker"]: row for row in payload["lots"]}

    assert rows["TPRSU"]["projection_available"] is False
    assert "locked" in rows["TPRSU"]["projection_unavailable_reason"].lower()

    assert rows["TPISA"]["projection_available"] is False
    assert "tax-sheltered" in rows["TPISA"]["projection_unavailable_reason"].lower()


def test_tax_plan_compensation_rows_include_sell_hold_and_pension_variants(app_context):
    sec = _add_security("TPCOMP")
    _add_price(sec.id, date(2026, 2, 24), "20.00")
    PortfolioService.add_lot(
        security_id=sec.id,
        scheme_type="BROKERAGE",
        acquisition_date=date(2025, 1, 15),
        quantity=Decimal("300"),
        acquisition_price_gbp=Decimal("10.00"),
        true_cost_per_share_gbp=Decimal("10.00"),
    )
    PortfolioService.commit_disposal(
        security_id=sec.id,
        quantity=Decimal("300"),
        price_per_share_gbp=Decimal("20.00"),
        transaction_date=date(2025, 8, 1),
    )
    PortfolioService.add_lot(
        security_id=sec.id,
        scheme_type="BROKERAGE",
        acquisition_date=date(2025, 9, 1),
        quantity=Decimal("1000"),
        acquisition_price_gbp=Decimal("10.00"),
        true_cost_per_share_gbp=Decimal("10.00"),
    )

    settings = AppSettings()
    settings.default_student_loan_plan = 2

    payload = TaxPlanService.get_summary(
        settings=settings,
        as_of=date(2026, 2, 24),
        compensation_gross_income_gbp=Decimal("99000"),
        compensation_bonus_gbp=Decimal("0"),
        compensation_sell_amount_gbp=Decimal("5000"),
        compensation_additional_pension_sacrifice_gbp=Decimal("1000"),
    )

    comp = payload["compensation_plan"]
    rows = {row["scenario_id"]: row for row in comp["rows"]}
    assert set(rows) == {
        "hold_baseline",
        "sell_baseline",
        "sell_with_extra_pension",
        "sell_next_tax_year",
        "sell_next_tax_year_with_extra_pension",
    }
    assert comp["sale_assumption"]["method"] == "portfolio-weighted-cost-ratio"
    assert comp["sale_assumption"]["estimated_gain_ratio_pct"] == "50.00"
    assert rows["sell_baseline"]["planning_tax_year"] == payload["active_tax_year"]
    assert rows["sell_next_tax_year"]["planning_tax_year"] == payload["next_tax_year"]

    hold_net = Decimal(rows["hold_baseline"]["net_decision_cash_gbp"])
    sell_net = Decimal(rows["sell_baseline"]["net_decision_cash_gbp"])
    assert sell_net > hold_net

    sell_plus_pension = rows["sell_with_extra_pension"]
    assert (
        Decimal(sell_plus_pension["pension_tax_saving_breakdown_gbp"]["total_gbp"])
        > Decimal("0")
    )
    assert (
        Decimal(comp["comparison"]["ani_reduction_from_extra_pension_gbp"])
        == Decimal("1000.00")
    )
    assert "sell_next_vs_sell_delta_gbp" in comp["comparison"]
    assert "sell_next_with_pension_vs_sell_with_pension_delta_gbp" in comp["comparison"]

    timing = comp["timing_comparison"]
    assert timing["sell_this_tax_year"] == payload["active_tax_year"]
    assert timing["sell_next_tax_year"] == payload["next_tax_year"]
    assert Decimal(
        timing["baseline_pension"]["combined_tax_drag_delta_wait_vs_sell_now_gbp"]
    ) <= Decimal("0")


def test_tax_plan_compensation_highlights_101k_taper_and_pension_escape(app_context):
    sec = _add_security("TPANI")
    _add_price(sec.id, date(2026, 2, 24), "18.00")
    PortfolioService.add_lot(
        security_id=sec.id,
        scheme_type="BROKERAGE",
        acquisition_date=date(2025, 2, 1),
        quantity=Decimal("800"),
        acquisition_price_gbp=Decimal("10.00"),
        true_cost_per_share_gbp=Decimal("10.00"),
    )

    settings = AppSettings()

    payload = TaxPlanService.get_summary(
        settings=settings,
        as_of=date(2026, 2, 24),
        compensation_gross_income_gbp=Decimal("101000"),
        compensation_bonus_gbp=Decimal("0"),
        compensation_sell_amount_gbp=Decimal("5000"),
        compensation_additional_pension_sacrifice_gbp=Decimal("3000"),
    )

    rows = {
        row["scenario_id"]: row
        for row in payload["compensation_plan"]["rows"]
    }
    sell = rows["sell_baseline"]
    sell_with_pension = rows["sell_with_extra_pension"]

    assert sell["in_pa_taper_zone_after_bonus"] is True
    assert sell["marginal_rates_pct"]["income_tax"] == "60.00"
    assert sell_with_pension["in_pa_taper_zone_after_bonus"] is False
    assert sell_with_pension["marginal_rates_pct"]["income_tax"] == "40.00"
    assert (
        Decimal(sell_with_pension["personal_allowance_after_bonus_gbp"])
        > Decimal(sell["personal_allowance_after_bonus_gbp"])
    )
    assert (
        Decimal(payload["compensation_plan"]["comparison"]["ani_reduction_from_extra_pension_gbp"])
        == Decimal("3000.00")
    )
