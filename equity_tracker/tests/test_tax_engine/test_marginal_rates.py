"""
Unit tests for marginal_rates.py — the combined marginal rate calculator.

This module tests the key outputs used throughout the true economic cost model.
The test cases cover the distinct income bands that the user may experience.

Key financial scenarios tested:
    1. Basic rate taxpayer — typical employee below £50k
    2. Higher rate taxpayer — typical tech employee £50k–£100k
    3. Taper zone — bonus pushes ANI above £100k (the '60% tax trap')
    4. Pension sacrifice escaping the taper zone
    5. Additional rate — above £125,140
    6. Manual rate override
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.tax_engine.context import TaxContext
from src.core.tax_engine.marginal_rates import MarginalRates, get_marginal_rates
from tests.test_tax_engine.conftest import assert_gbp_equal, pct


class TestMarginalRatesBasicRate:
    """Basic rate taxpayer — income in basic rate band, Plan 2 above threshold."""

    def test_basic_rate_above_sl_threshold(self):
        """
        Income £40,000: basic rate band.
        IT=20%, NI=8%, SL=9% → Combined=37%.
        """
        ctx = TaxContext(
            tax_year="2024-25",
            gross_employment_income=Decimal("40000"),
            student_loan_plan=2,
        )
        rates = get_marginal_rates(ctx)
        assert rates.income_tax == pct("20")
        assert rates.national_insurance == pct("8")
        assert rates.student_loan == pct("9")
        assert rates.combined == pct("37")
        assert not rates.taper_zone

    def test_basic_rate_below_sl_threshold(self):
        """
        Income £25,000: basic rate, below SL Plan 2 threshold (£27,295).
        IT=20%, NI=8%, SL=0% → Combined=28%.
        """
        ctx = TaxContext(
            tax_year="2024-25",
            gross_employment_income=Decimal("25000"),
            student_loan_plan=2,
        )
        rates = get_marginal_rates(ctx)
        assert rates.income_tax == pct("20")
        assert rates.national_insurance == pct("8")
        assert rates.student_loan == pct("0")
        assert rates.combined == pct("28")

    def test_no_student_loan(self):
        """
        Income £40,000, no student loan.
        IT=20%, NI=8%, SL=0% → Combined=28%.
        """
        ctx = TaxContext(
            tax_year="2024-25",
            gross_employment_income=Decimal("40000"),
            student_loan_plan=None,
        )
        rates = get_marginal_rates(ctx)
        assert rates.combined == pct("28")
        assert rates.student_loan == pct("0")


class TestMarginalRatesHigherRate:
    """Higher rate taxpayer — income £50k–£100k."""

    def test_higher_rate_with_sl(self):
        """
        Income £80,000: higher rate (above UEL for NI).
        IT=40%, NI=2%, SL=9% → Combined=51%.
        """
        ctx = TaxContext(
            tax_year="2024-25",
            gross_employment_income=Decimal("80000"),
            student_loan_plan=2,
        )
        rates = get_marginal_rates(ctx)
        assert rates.income_tax == pct("40")
        assert rates.national_insurance == pct("2")
        assert rates.student_loan == pct("9")
        assert rates.combined == pct("51")
        assert not rates.taper_zone

    def test_higher_rate_no_sl(self):
        """
        Income £80,000, no student loan.
        IT=40%, NI=2%, SL=0% → Combined=42%.
        """
        ctx = TaxContext(
            tax_year="2024-25",
            gross_employment_income=Decimal("80000"),
            student_loan_plan=None,
        )
        rates = get_marginal_rates(ctx)
        assert rates.combined == pct("42")

    def test_pension_sacrifice_no_effect_on_it_rate_at_higher(self):
        """
        Gross £80k, pension sacrifice £10k → ANI £70k.
        Still higher rate (ANI below taper start £100k).
        NI-relevant income: £70k (above UEL) → NI=2%.
        """
        ctx = TaxContext(
            tax_year="2024-25",
            gross_employment_income=Decimal("80000"),
            pension_sacrifice=Decimal("10000"),
            student_loan_plan=2,
        )
        rates = get_marginal_rates(ctx)
        assert rates.income_tax == pct("40")
        assert rates.national_insurance == pct("2")
        assert rates.combined == pct("51")


class TestMarginalRatesTaperZone:
    """The PA taper zone (ANI £100,001–£125,140) — the 60% tax trap."""

    def test_60pct_effective_it_in_taper_zone(self):
        """
        Income £110,000 (no pension sacrifice): in taper zone.
        IT=60%, NI=2%, SL=9% → Combined=71%.
        This is the maximum combined marginal rate.
        """
        ctx = TaxContext(
            tax_year="2024-25",
            gross_employment_income=Decimal("110000"),
            student_loan_plan=2,
        )
        rates = get_marginal_rates(ctx)
        assert rates.income_tax == pct("60")
        assert rates.national_insurance == pct("2")
        assert rates.student_loan == pct("9")
        assert rates.combined == pct("71")
        assert rates.taper_zone is True

    def test_taper_zone_retention(self):
        """In the taper zone with SL: you keep only 29p per £1 earned."""
        ctx = TaxContext(
            tax_year="2024-25",
            gross_employment_income=Decimal("110000"),
            student_loan_plan=2,
        )
        rates = get_marginal_rates(ctx)
        assert rates.pence_kept_per_pound == pct("29")

    def test_pension_sacrifice_exits_taper_zone(self):
        """
        Gross £120k, pension sacrifice £25k → ANI = £95k (below taper start).
        Exits taper zone → IT=40%, NI=2%, SL=9% → Combined=51%.
        NI-relevant income: 120,000 - 25,000 = 95,000 (above UEL) → NI=2%.
        """
        ctx = TaxContext(
            tax_year="2024-25",
            gross_employment_income=Decimal("120000"),
            pension_sacrifice=Decimal("25000"),
            student_loan_plan=2,
        )
        rates = get_marginal_rates(ctx)
        assert rates.income_tax == pct("40"), (
            "Pension sacrifice should have pulled ANI below £100k, exiting taper zone"
        )
        assert rates.taper_zone is False
        assert rates.combined == pct("51")

    def test_partial_pension_sacrifice_still_in_taper(self):
        """
        Gross £115k, pension sacrifice £10k → ANI = £105k (still in taper zone).
        IT=60%, still in taper zone.
        """
        ctx = TaxContext(
            tax_year="2024-25",
            gross_employment_income=Decimal("115000"),
            pension_sacrifice=Decimal("10000"),
            student_loan_plan=2,
        )
        rates = get_marginal_rates(ctx)
        assert rates.income_tax == pct("60")
        assert rates.taper_zone is True

    def test_at_taper_zone_boundary(self):
        """
        ANI exactly at £100,000 — NOT in taper zone (strictly > £100k triggers it).
        IT=40%, Higher rate.
        """
        ctx = TaxContext(
            tax_year="2024-25",
            gross_employment_income=Decimal("100000"),
            student_loan_plan=2,
        )
        rates = get_marginal_rates(ctx)
        assert rates.income_tax == pct("40")
        assert rates.taper_zone is False


class TestMarginalRatesAdditionalRate:
    """Additional rate taxpayer — above £125,140."""

    def test_additional_rate_with_sl(self):
        """
        Income £150,000: additional rate, PA fully tapered.
        IT=45%, NI=2%, SL=9% → Combined=56%.
        """
        ctx = TaxContext(
            tax_year="2024-25",
            gross_employment_income=Decimal("150000"),
            student_loan_plan=2,
        )
        rates = get_marginal_rates(ctx)
        assert rates.income_tax == pct("45")
        assert rates.national_insurance == pct("2")
        assert rates.student_loan == pct("9")
        assert rates.combined == pct("56")
        assert not rates.taper_zone  # Taper zone ends at £125,140


class TestMarginalRatesManualOverride:
    """Tests for manual marginal rate overrides."""

    def test_manual_it_rate_override(self):
        """
        Manual override takes precedence over computed rate.
        Useful for mid-year NI rate changes.
        """
        ctx = TaxContext(
            tax_year="2023-24",
            gross_employment_income=Decimal("80000"),
            student_loan_plan=2,
            manual_marginal_it_rate=Decimal("0.45"),  # Override to 45%
        )
        rates = get_marginal_rates(ctx)
        assert rates.income_tax == pct("45")

    def test_manual_ni_rate_override(self):
        """
        Manual NI override for mid-year rate change (e.g. NI cut Jan 2024).
        """
        ctx = TaxContext(
            tax_year="2023-24",
            gross_employment_income=Decimal("40000"),
            student_loan_plan=None,
            manual_marginal_ni_rate=Decimal("0.12"),  # Pre-cut rate
        )
        rates = get_marginal_rates(ctx)
        assert rates.national_insurance == pct("12")


class TestMarginalRatesTaxContextHelpers:
    """Tests for TaxContext helper methods used in combination with marginal rates."""

    def test_at_income_position(self):
        """
        at_income_position() returns a context for YTD income.
        Useful for per-transaction marginal rate accuracy.
        """
        ctx = TaxContext(
            tax_year="2024-25",
            gross_employment_income=Decimal("150000"),  # Full year
            student_loan_plan=2,
        )
        # In April (early year), income YTD is £30k — basic rate
        ctx_april = ctx.at_income_position(
            income_ytd=Decimal("30000"),
            pension_sacrifice_ytd=Decimal("0"),
        )
        rates_april = get_marginal_rates(ctx_april)
        assert rates_april.income_tax == pct("20")

        # After December bonus pushing to £110k — taper zone
        ctx_dec = ctx.at_income_position(
            income_ytd=Decimal("110000"),
            pension_sacrifice_ytd=Decimal("0"),
        )
        rates_dec = get_marginal_rates(ctx_dec)
        assert rates_dec.income_tax == pct("60")
        assert rates_dec.taper_zone is True
