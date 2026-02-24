"""
Unit tests for sip_rules.py — SIP event-driven state machine.

Test strategy:
- Verify each event type × holding period category combination
- Verify forfeiture logic for matching shares
- Verify employer leaver simulation
- Verify no false income tax in the 5-year free scenario
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.core.tax_engine.sip_rules import (
    EmployerLeaverSIPOutcome,
    SIPEvent,
    SIPEventType,
    SIPHolding,
    SIPHoldingPeriodCategory,
    SIPShareType,
    SIPTaxResult,
    process_sip_event,
    simulate_employer_leaver,
)
from tests.test_tax_engine.conftest import assert_gbp_equal


# ── Fixtures ───────────────────────────────────────────────────────────────

ACQUISITION_DATE = date(2021, 6, 1)
MARKET_VALUE_AT_ACQUISITION = Decimal("10.00")  # £10/share


def make_partnership_holding(
    lot_id: int = 1,
    quantity: Decimal = Decimal("180"),
    acq_date: date = ACQUISITION_DATE,
    fmv_at_acq: Decimal = MARKET_VALUE_AT_ACQUISITION,
    gross_deducted: Decimal = Decimal("1800.00"),
) -> SIPHolding:
    return SIPHolding(
        lot_id=lot_id,
        share_type=SIPShareType.PARTNERSHIP,
        acquisition_date=acq_date,
        quantity=quantity,
        acquisition_market_value_gbp=fmv_at_acq,
        gross_salary_deducted_gbp=gross_deducted,
    )


def make_matching_holding(
    lot_id: int = 2,
    quantity: Decimal = Decimal("180"),
    acq_date: date = ACQUISITION_DATE,
    fmv_at_acq: Decimal = MARKET_VALUE_AT_ACQUISITION,
    linked_partnership_lot_id: int = 1,
) -> SIPHolding:
    return SIPHolding(
        lot_id=lot_id,
        share_type=SIPShareType.MATCHING,
        acquisition_date=acq_date,
        quantity=quantity,
        acquisition_market_value_gbp=fmv_at_acq,
        gross_salary_deducted_gbp=Decimal("0"),
        linked_partnership_lot_id=linked_partnership_lot_id,
    )


def make_dividend_holding(
    lot_id: int = 3,
    quantity: Decimal = Decimal("10"),
    acq_date: date = ACQUISITION_DATE,
    fmv_at_acq: Decimal = MARKET_VALUE_AT_ACQUISITION,
) -> SIPHolding:
    return SIPHolding(
        lot_id=lot_id,
        share_type=SIPShareType.DIVIDEND,
        acquisition_date=acq_date,
        quantity=quantity,
        acquisition_market_value_gbp=fmv_at_acq,
        gross_salary_deducted_gbp=Decimal("0"),
    )


# ── Holding period classification tests ───────────────────────────────────

class TestHoldingPeriodCategory:

    def test_under_3_years(self):
        h = make_partnership_holding(acq_date=date(2023, 1, 1))
        event_date = date(2024, 6, 1)  # ~17 months later
        assert h.holding_period_category(event_date) == SIPHoldingPeriodCategory.UNDER_THREE_YEARS

    def test_3_to_5_years(self):
        h = make_partnership_holding(acq_date=date(2019, 1, 1))
        event_date = date(2023, 1, 15)  # ~4 years later
        assert h.holding_period_category(event_date) == SIPHoldingPeriodCategory.THREE_TO_FIVE_YEARS

    def test_5_plus_years(self):
        h = make_partnership_holding(acq_date=date(2018, 1, 1))
        event_date = date(2024, 1, 15)  # ~6 years later
        assert h.holding_period_category(event_date) == SIPHoldingPeriodCategory.FIVE_PLUS_YEARS


# ── Acquisition event ──────────────────────────────────────────────────────

class TestAcquisitionEvent:

    def test_acquisition_no_tax(self):
        holding = make_partnership_holding()
        event = SIPEvent(
            event_type=SIPEventType.ACQUISITION,
            event_date=ACQUISITION_DATE,
            holding=holding,
            quantity=Decimal("180"),
            market_value_per_share_gbp=Decimal("10.00"),
        )
        result = process_sip_event(event)
        assert result.income_taxable_gbp == Decimal("0")
        assert result.ni_liable_gbp == Decimal("0")
        assert result.cgt_base_cost_per_share_gbp == Decimal("10.00")
        assert result.matching_lots_forfeited == []


# ── Under 3 years — full clawback ─────────────────────────────────────────

class TestWithdrawalUnder3Years:
    """
    Withdrawn < 3 years: IT + NI on full market value at withdrawal.
    This claws back the tax saving made at acquisition.
    """

    def test_partnership_withdrawal_under_3yr(self):
        """
        180 shares acquired at £10, now worth £15.
        Withdrawn after 2 years.
        Taxable = 180 × £15 = £2,700. NI liable = £2,700.
        """
        holding = make_partnership_holding(
            acq_date=date(2022, 6, 1),
            quantity=Decimal("180"),
            fmv_at_acq=Decimal("10.00"),
        )
        event = SIPEvent(
            event_type=SIPEventType.WITHDRAWAL,
            event_date=date(2024, 3, 1),  # ~21 months → under 3 years
            holding=holding,
            quantity=Decimal("180"),
            market_value_per_share_gbp=Decimal("15.00"),
        )
        result = process_sip_event(event)

        expected_taxable = Decimal("180") * Decimal("15.00")  # £2,700
        assert result.holding_period_category == SIPHoldingPeriodCategory.UNDER_THREE_YEARS
        assert_gbp_equal(result.income_taxable_gbp, expected_taxable)
        assert_gbp_equal(result.ni_liable_gbp, expected_taxable)

    def test_partnership_withdrawal_under_3yr_fallen_value(self):
        """
        Shares worth less than at acquisition (fallen market).
        Still clawed back on market value (lower amount).
        """
        holding = make_partnership_holding(
            acq_date=date(2022, 6, 1),
            quantity=Decimal("180"),
            fmv_at_acq=Decimal("10.00"),
        )
        event = SIPEvent(
            event_type=SIPEventType.WITHDRAWAL,
            event_date=date(2024, 3, 1),
            holding=holding,
            quantity=Decimal("180"),
            market_value_per_share_gbp=Decimal("7.00"),  # Fallen below acquisition
        )
        result = process_sip_event(event)
        expected_taxable = Decimal("180") * Decimal("7.00")  # £1,260
        assert_gbp_equal(result.income_taxable_gbp, expected_taxable)
        assert_gbp_equal(result.ni_liable_gbp, expected_taxable)


# ── 3–5 years — IT on lower of acquisition/current; no NI ─────────────────

class TestWithdrawal3To5Years:
    """
    3–5 years: IT on LOWER of (market value at withdrawal, acquisition value).
    NO NI in this period.
    """

    def test_partnership_withdrawal_3_to_5yr_shares_appreciated(self):
        """
        Shares have risen: acquisition value = £1,800, current = £2,700.
        IT on LOWER = £1,800. No NI.
        """
        holding = make_partnership_holding(
            acq_date=date(2020, 6, 1),  # acquired Jun 2020
            quantity=Decimal("180"),
            fmv_at_acq=Decimal("10.00"),
            gross_deducted=Decimal("1800.00"),
        )
        event = SIPEvent(
            event_type=SIPEventType.WITHDRAWAL,
            event_date=date(2024, 3, 1),  # ~3.75 years → 3-5 year bracket
            holding=holding,
            quantity=Decimal("180"),
            market_value_per_share_gbp=Decimal("15.00"),  # Current = £2,700
        )
        result = process_sip_event(event)

        assert result.holding_period_category == SIPHoldingPeriodCategory.THREE_TO_FIVE_YEARS
        assert_gbp_equal(result.income_taxable_gbp, Decimal("1800.00"))  # Lower of 1800, 2700
        assert result.ni_liable_gbp == Decimal("0")

    def test_partnership_withdrawal_3_to_5yr_shares_fallen(self):
        """
        Shares have fallen: acquisition value = £1,800, current = £1,260.
        IT on LOWER = £1,260. No NI.
        """
        holding = make_partnership_holding(
            acq_date=date(2020, 6, 1),
            quantity=Decimal("180"),
            fmv_at_acq=Decimal("10.00"),
            gross_deducted=Decimal("1800.00"),
        )
        event = SIPEvent(
            event_type=SIPEventType.WITHDRAWAL,
            event_date=date(2024, 3, 1),
            holding=holding,
            quantity=Decimal("180"),
            market_value_per_share_gbp=Decimal("7.00"),  # Current = £1,260
        )
        result = process_sip_event(event)

        assert result.holding_period_category == SIPHoldingPeriodCategory.THREE_TO_FIVE_YEARS
        assert_gbp_equal(result.income_taxable_gbp, Decimal("1260.00"))  # Lower of 1800, 1260
        assert result.ni_liable_gbp == Decimal("0")


# ── 5+ years — completely free ─────────────────────────────────────────────

class TestWithdrawal5PlusYears:
    """After 5 years: NO income tax, NO NI on removal."""

    def test_partnership_no_tax_after_5yr(self):
        holding = make_partnership_holding(
            acq_date=date(2018, 1, 1),  # Over 5 years ago
        )
        event = SIPEvent(
            event_type=SIPEventType.WITHDRAWAL,
            event_date=date(2024, 6, 1),
            holding=holding,
            quantity=Decimal("180"),
            market_value_per_share_gbp=Decimal("25.00"),  # Big appreciation — doesn't matter
        )
        result = process_sip_event(event)

        assert result.holding_period_category == SIPHoldingPeriodCategory.FIVE_PLUS_YEARS
        assert result.income_taxable_gbp == Decimal("0")
        assert result.ni_liable_gbp == Decimal("0")

    def test_matching_no_tax_after_5yr(self):
        holding = make_matching_holding(acq_date=date(2018, 1, 1))
        event = SIPEvent(
            event_type=SIPEventType.EMPLOYER_LEAVER,
            event_date=date(2024, 6, 1),
            holding=holding,
            quantity=Decimal("180"),
            market_value_per_share_gbp=Decimal("20.00"),
        )
        result = process_sip_event(event)
        assert result.income_taxable_gbp == Decimal("0")
        assert result.ni_liable_gbp == Decimal("0")


# ── Matching share forfeiture ──────────────────────────────────────────────

class TestMatchingShareForfeiture:

    def test_forfeiture_no_tax(self):
        """Forfeited matching shares: no tax, no CGT, just loss of asset."""
        holding = make_matching_holding()
        event = SIPEvent(
            event_type=SIPEventType.MATCHING_FORFEITURE,
            event_date=date(2023, 3, 1),
            holding=holding,
            quantity=Decimal("180"),
            market_value_per_share_gbp=Decimal("15.00"),
        )
        result = process_sip_event(event)
        assert result.income_taxable_gbp == Decimal("0")
        assert result.ni_liable_gbp == Decimal("0")
        assert result.cgt_gain_gbp == Decimal("0")
        assert holding.lot_id in result.matching_lots_forfeited


# ── In-plan sale ──────────────────────────────────────────────────────────

class TestInPlanSale:

    def test_in_plan_sale_with_gain_under_3yr(self):
        """
        In-plan sale after < 3 years:
        - Income tax + NI on market value (clawback)
        - CGT gain on the appreciation above acquisition price
        180 shares, acq £10, now £15. Taxable = £2,700. CGT gain = (£15-£10) × 180 = £900.
        """
        holding = make_partnership_holding(
            acq_date=date(2022, 6, 1),
            quantity=Decimal("180"),
            fmv_at_acq=Decimal("10.00"),
        )
        event = SIPEvent(
            event_type=SIPEventType.IN_PLAN_SALE,
            event_date=date(2024, 3, 1),
            holding=holding,
            quantity=Decimal("180"),
            market_value_per_share_gbp=Decimal("15.00"),
        )
        result = process_sip_event(event)
        assert_gbp_equal(result.income_taxable_gbp, Decimal("2700.00"))
        assert_gbp_equal(result.cgt_gain_gbp, Decimal("900.00"))  # (15-10) × 180
        assert result.cgt_loss_gbp == Decimal("0")

    def test_in_plan_sale_at_loss(self):
        """
        In-plan sale at a loss.
        CGT loss = (£10 - £7) × 180 = £540.
        """
        holding = make_partnership_holding(
            acq_date=date(2022, 6, 1),
            quantity=Decimal("180"),
            fmv_at_acq=Decimal("10.00"),
        )
        event = SIPEvent(
            event_type=SIPEventType.IN_PLAN_SALE,
            event_date=date(2024, 3, 1),
            holding=holding,
            quantity=Decimal("180"),
            market_value_per_share_gbp=Decimal("7.00"),
        )
        result = process_sip_event(event)
        assert_gbp_equal(result.cgt_loss_gbp, Decimal("540.00"))
        assert result.cgt_gain_gbp == Decimal("0")


# ── Post-plan sale ─────────────────────────────────────────────────────────

class TestPostPlanSale:

    def test_post_plan_sale_no_income_tax(self):
        """
        After withdrawal: income tax already settled at withdrawal date.
        This event only documents that no further income tax applies.
        """
        holding = make_partnership_holding()
        event = SIPEvent(
            event_type=SIPEventType.POST_PLAN_SALE,
            event_date=date(2024, 6, 1),
            holding=holding,
            quantity=Decimal("180"),
            market_value_per_share_gbp=Decimal("20.00"),
        )
        result = process_sip_event(event)
        assert result.income_taxable_gbp == Decimal("0")
        assert result.ni_liable_gbp == Decimal("0")


# ── Employer leaver simulation ────────────────────────────────────────────

class TestEmployerLeaverSimulation:

    def test_leaver_with_mixed_holdings(self):
        """
        Employee leaves with:
        - Partnership lot A: acquired 5 years ago (tax-free)
        - Partnership lot B: acquired 1 year ago (under 3 years)
        - Matching lot linked to B: should be forfeited
        """
        old_partnership = make_partnership_holding(
            lot_id=1,
            acq_date=date(2018, 6, 1),  # 5+ years ago
            quantity=Decimal("180"),
            fmv_at_acq=Decimal("10.00"),
        )
        recent_partnership = make_partnership_holding(
            lot_id=2,
            acq_date=date(2023, 6, 1),  # ~1 year ago
            quantity=Decimal("180"),
            fmv_at_acq=Decimal("20.00"),
            gross_deducted=Decimal("3600.00"),
        )
        matching_for_recent = make_matching_holding(
            lot_id=3,
            acq_date=date(2023, 6, 1),
            quantity=Decimal("180"),
            fmv_at_acq=Decimal("20.00"),
            linked_partnership_lot_id=2,  # Linked to recent (< 3yr) partnership
        )

        leaver_date = date(2024, 9, 1)
        market_values = {
            1: Decimal("15.00"),  # Old lot — appreciated
            2: Decimal("25.00"),  # Recent lot
            3: Decimal("25.00"),  # Matching lot
        }

        outcome = simulate_employer_leaver(
            leaver_date=leaver_date,
            partnership_holdings=[old_partnership, recent_partnership],
            matching_holdings=[matching_for_recent],
            dividend_holdings=[],
            market_values=market_values,
            plan_forfeits_matching_under_3yr=True,
        )

        # Old partnership lot (5+ years): no tax
        old_result = outcome.partnership_events[0]
        assert old_result.income_taxable_gbp == Decimal("0")

        # Recent partnership lot (< 3 years): taxed on market value
        recent_result = outcome.partnership_events[1]
        assert_gbp_equal(recent_result.income_taxable_gbp, Decimal("180") * Decimal("25.00"))

        # Matching lot: forfeited (linked to under-3yr partnership lot)
        assert 3 in outcome.lots_forfeited

        # Total: only recent partnership lot is taxable
        assert_gbp_equal(outcome.total_income_taxable_gbp, Decimal("4500.00"))  # 180 × £25
