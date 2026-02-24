"""
Unit tests for income_tax.py

Test strategy:
- Verify exact band boundaries (below PA, at PA, basic/higher/additional transitions)
- Verify PA taper zone calculations with the 60% effective rate
- Verify that pension sacrifice correctly reduces ANI and avoids taper
- All expected values derived from HMRC published examples and manual calculation
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.tax_engine.bands import get_bands
from src.core.tax_engine.income_tax import (
    income_tax_liability,
    income_tax_on_additional_income,
    marginal_income_tax_rate,
    personal_allowance,
)
from tests.test_tax_engine.conftest import assert_gbp_equal, pct


class TestPersonalAllowance:
    """Tests for personal_allowance() — PA taper logic."""

    def test_full_pa_below_taper_start(self, bands_2024):
        """ANI well below £100k — full PA."""
        pa = personal_allowance(bands_2024, Decimal("80000"))
        assert pa == Decimal("12570")

    def test_full_pa_at_taper_start(self, bands_2024):
        """ANI exactly at £100k — still full PA (taper starts ABOVE £100k)."""
        pa = personal_allowance(bands_2024, Decimal("100000"))
        assert pa == Decimal("12570")

    def test_pa_reduced_in_taper_zone(self, bands_2024):
        """ANI = £110k → PA = 12,570 - (10,000 / 2) = 7,570."""
        pa = personal_allowance(bands_2024, Decimal("110000"))
        assert pa == Decimal("7570")

    def test_pa_reduced_at_midpoint(self, bands_2024):
        """ANI = £112,570 → PA = 12,570 - (12,570 / 2) = 6,285."""
        pa = personal_allowance(bands_2024, Decimal("112570"))
        assert pa == Decimal("6285")

    def test_pa_zero_at_taper_end(self, bands_2024):
        """ANI = £125,140 → PA = 0 (fully tapered out)."""
        pa = personal_allowance(bands_2024, Decimal("125140"))
        assert pa == Decimal("0")

    def test_pa_zero_above_taper_end(self, bands_2024):
        """ANI = £200k → PA = 0 (clamped at zero)."""
        pa = personal_allowance(bands_2024, Decimal("200000"))
        assert pa == Decimal("0")

    def test_pa_taper_end_formula(self, bands_2024):
        """Taper end should equal pa_taper_start + 2 × personal_allowance."""
        expected_end = Decimal("100000") + Decimal("12570") * 2  # = 125,140
        assert bands_2024.pa_taper_end == expected_end


class TestIncomeTaxLiability:
    """Tests for income_tax_liability() — full annual liability."""

    def test_no_tax_below_pa(self, bands_2024):
        """Income below PA — no tax."""
        pa = personal_allowance(bands_2024, Decimal("10000"))
        tax = income_tax_liability(bands_2024, Decimal("10000"), pa)
        assert tax == Decimal("0")

    def test_no_tax_at_pa_exactly(self, bands_2024):
        """Income exactly at PA = £12,570 — no tax."""
        pa = personal_allowance(bands_2024, Decimal("12570"))
        tax = income_tax_liability(bands_2024, Decimal("12570"), pa)
        assert tax == Decimal("0")

    def test_basic_rate_simple(self, bands_2024):
        """
        Income £30,000.
        Taxable = 30,000 - 12,570 = 17,430.
        Tax = 17,430 × 20% = £3,486.
        """
        pa = personal_allowance(bands_2024, Decimal("30000"))
        tax = income_tax_liability(bands_2024, Decimal("30000"), pa)
        assert_gbp_equal(tax, Decimal("3486.00"))

    def test_basic_rate_at_upper_bound(self, bands_2024):
        """
        Income = £50,270 (top of basic rate band).
        Taxable = 50,270 - 12,570 = 37,700.
        Tax = 37,700 × 20% = £7,540.
        """
        pa = personal_allowance(bands_2024, Decimal("50270"))
        tax = income_tax_liability(bands_2024, Decimal("50270"), pa)
        assert_gbp_equal(tax, Decimal("7540.00"))

    def test_higher_rate(self, bands_2024):
        """
        Income = £80,000.
        Taxable = 80,000 - 12,570 = 67,430.
        Basic rate: 37,700 × 20% = £7,540.
        Higher rate: (67,430 - 37,700) × 40% = 29,730 × 40% = £11,892.
        Total: £19,432.
        """
        pa = personal_allowance(bands_2024, Decimal("80000"))
        tax = income_tax_liability(bands_2024, Decimal("80000"), pa)
        assert_gbp_equal(tax, Decimal("19432.00"))

    def test_taper_zone_income(self, bands_2024):
        """
        Income = £110,000. ANI = £110,000 (no pension sacrifice).
        PA = 12,570 - (10,000 / 2) = 7,570.
        Taxable = 110,000 - 7,570 = 102,430.
        Basic rate: 37,700 × 20% = £7,540.
        Higher rate: (102,430 - 37,700) × 40% = 64,730 × 40% = £25,892.
        Total: £33,432.
        """
        pa = personal_allowance(bands_2024, Decimal("110000"))
        tax = income_tax_liability(bands_2024, Decimal("110000"), pa)
        assert_gbp_equal(tax, Decimal("33432.00"))

    def test_above_taper_zone(self, bands_2024):
        """
        Income = £130,000. PA = 0 (above taper end).
        Taxable = £130,000.
        Basic: 37,700 × 20% = £7,540.
        Higher: (125,140 - 37,700) × 40% = 87,440 × 40% = £34,976.
        Additional: (130,000 - 125,140) × 45% = 4,860 × 45% = £2,187.
        Total: £44,703.
        """
        pa = personal_allowance(bands_2024, Decimal("130000"))
        tax = income_tax_liability(bands_2024, Decimal("130000"), pa)
        assert_gbp_equal(tax, Decimal("44703.00"))

    def test_pension_sacrifice_reduces_pa_taper(self, bands_2024):
        """
        Gross income = £120,000, pension sacrifice = £25,000.
        ANI = £95,000 → below taper → full PA = £12,570.
        Taxable = 120,000 - 12,570 = 107,430.
        Basic: 37,700 × 20% = £7,540.
        Higher: (107,430 - 37,700) × 40% = 69,730 × 40% = £27,892.
        Total: £35,432.
        (Much less than without pension sacrifice.)
        """
        pa = personal_allowance(bands_2024, Decimal("95000"))  # ANI
        tax = income_tax_liability(bands_2024, Decimal("120000"), pa)
        assert_gbp_equal(tax, Decimal("35432.00"))


class TestMarginalIncomeTaxRate:
    """
    Tests for marginal_income_tax_rate().

    The key test is the 60% taper zone — this is the most financially significant
    calculation in the entire system and must be correct.
    """

    def test_zero_rate_below_pa(self, bands_2024):
        rate = marginal_income_tax_rate(bands_2024, Decimal("10000"))
        assert rate == Decimal("0")

    def test_zero_rate_at_pa(self, bands_2024):
        rate = marginal_income_tax_rate(bands_2024, Decimal("12570"))
        assert rate == Decimal("0")

    def test_basic_rate(self, bands_2024):
        rate = marginal_income_tax_rate(bands_2024, Decimal("30000"))
        assert rate == pct("20")

    def test_higher_rate_above_basic_threshold(self, bands_2024):
        rate = marginal_income_tax_rate(bands_2024, Decimal("60000"))
        assert rate == pct("40")

    def test_higher_rate_just_below_taper(self, bands_2024):
        """At £99,999 ANI — still higher rate (taper starts strictly above £100k)."""
        rate = marginal_income_tax_rate(bands_2024, Decimal("99999"))
        assert rate == pct("40")

    def test_60pct_effective_rate_in_taper_zone(self, bands_2024):
        """
        THE CRITICAL TEST: 60% effective marginal rate in the PA taper zone.

        Analytically verified: for income in £100,001–£125,140 (ANI same as gross),
        each £1 costs 60p in tax:
        - 40p from higher rate tax on the £1
        - 20p from lost PA (PA reduces by 50p → 50p × 40% higher rate = 20p)

        This is numerically verified below using income_tax_liability.
        """
        # Analytical check
        rate = marginal_income_tax_rate(bands_2024, Decimal("110000"))
        assert rate == pct("60")

        # Numerical verification: tax on £110,001 vs £110,000
        pa_at_110000 = personal_allowance(bands_2024, Decimal("110000"))
        pa_at_110001 = personal_allowance(bands_2024, Decimal("110001"))
        tax_at_110000 = income_tax_liability(bands_2024, Decimal("110000"), pa_at_110000)
        tax_at_110001 = income_tax_liability(bands_2024, Decimal("110001"), pa_at_110001)
        numerical_marginal = (tax_at_110001 - tax_at_110000) / Decimal("1")
        assert_gbp_equal(numerical_marginal, Decimal("0.60"), tolerance=Decimal("0.005"))

    def test_60pct_at_start_of_taper_zone(self, bands_2024):
        """Rate immediately above £100,000 should be 60%."""
        rate = marginal_income_tax_rate(bands_2024, Decimal("100001"))
        assert rate == pct("60")

    def test_60pct_at_end_of_taper_zone(self, bands_2024):
        """Rate just below taper end (£125,139) should be 60%."""
        rate = marginal_income_tax_rate(bands_2024, Decimal("125139"))
        assert rate == pct("60")

    def test_additional_rate_above_taper_zone(self, bands_2024):
        """Above £125,140 — drops back to 45% additional rate."""
        rate = marginal_income_tax_rate(bands_2024, Decimal("125141"))
        assert rate == pct("45")

    def test_additional_rate_at_200k(self, bands_2024):
        rate = marginal_income_tax_rate(bands_2024, Decimal("200000"))
        assert rate == pct("45")

    def test_pension_sacrifice_prevents_taper(self, bands_2024):
        """
        Gross £110k, pension sacrifice £15k → ANI = £95k (below taper start).
        Marginal IT rate should be 40% (higher rate), not 60%.
        """
        rate = marginal_income_tax_rate(
            bands_2024,
            gross_income=Decimal("110000"),
            adjusted_net_income=Decimal("95000"),  # after £15k sacrifice
        )
        assert rate == pct("40")

    def test_taper_zone_effective_rate_property(self, bands_2024):
        """The TaxYearBands.taper_zone_effective_it_rate property should equal 60%."""
        assert bands_2024.taper_zone_effective_it_rate == pct("60")


class TestIncomeTaxOnAdditionalIncome:
    """Tests for income_tax_on_additional_income() — marginal tax on a specific amount."""

    def test_rsu_vest_in_higher_rate_band(self, bands_2024):
        """
        Employee at £60k receives £20k RSU vest.
        All £20k at 40% higher rate = £8,000.
        """
        tax = income_tax_on_additional_income(
            bands_2024,
            current_gross_income=Decimal("60000"),
            additional_income=Decimal("20000"),
            current_ani=Decimal("60000"),
        )
        assert_gbp_equal(tax, Decimal("8000.00"))

    def test_vest_crossing_basic_to_higher_boundary(self, bands_2024):
        """
        Employee at £45k receives £15k vest — crosses basic rate boundary (£50,270).
        First £5,270 at 20%: £1,054.
        Next £9,730 at 40%: £3,892.
        Total: £4,946.
        """
        tax = income_tax_on_additional_income(
            bands_2024,
            current_gross_income=Decimal("45000"),
            additional_income=Decimal("15000"),
            current_ani=Decimal("45000"),
        )
        assert_gbp_equal(tax, Decimal("4946.00"))

    def test_vest_crossing_into_taper_zone(self, bands_2024):
        """
        Employee at £95k receives £20k vest — crosses into taper zone at £100k.
        First £5k at 40%: £2,000.
        Next £15k at 60% (taper zone): £9,000.
        Total: £11,000.
        """
        tax = income_tax_on_additional_income(
            bands_2024,
            current_gross_income=Decimal("95000"),
            additional_income=Decimal("20000"),
            current_ani=Decimal("95000"),
        )
        assert_gbp_equal(tax, Decimal("11000.00"))
