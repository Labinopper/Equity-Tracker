"""
Unit tests for national_insurance.py

Test strategy:
- Verify zero NI below primary threshold
- Verify correct rate between PT and UEL
- Verify 2% above UEL
- Verify that salary sacrifice reduces NI
- Verify marginal rate at each boundary
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.tax_engine.national_insurance import (
    marginal_ni_rate,
    ni_liability,
    ni_on_additional_income,
    ni_saving_from_salary_sacrifice,
)
from tests.test_tax_engine.conftest import assert_gbp_equal, pct


class TestNILiability:
    """Tests for ni_liability() — 2024-25 rates."""

    def test_no_ni_below_threshold(self, bands_2024):
        """Income below PT (£12,570) — no NI."""
        ni = ni_liability(bands_2024, Decimal("10000"))
        assert ni == Decimal("0")

    def test_no_ni_at_primary_threshold(self, bands_2024):
        """Income exactly at PT — no NI (threshold is exclusive lower bound)."""
        ni = ni_liability(bands_2024, Decimal("12570"))
        assert ni == Decimal("0")

    def test_ni_above_threshold(self, bands_2024):
        """
        Income £30,000.
        NI = (30,000 - 12,570) × 8% = 17,430 × 8% = £1,394.40.
        """
        ni = ni_liability(bands_2024, Decimal("30000"))
        assert_gbp_equal(ni, Decimal("1394.40"))

    def test_ni_at_uel_boundary(self, bands_2024):
        """
        Income exactly at UEL (£50,270).
        NI = (50,270 - 12,570) × 8% = 37,700 × 8% = £3,016.00.
        """
        ni = ni_liability(bands_2024, Decimal("50270"))
        assert_gbp_equal(ni, Decimal("3016.00"))

    def test_ni_above_uel(self, bands_2024):
        """
        Income £80,000.
        NI = (50,270 - 12,570) × 8% + (80,000 - 50,270) × 2%
           = 37,700 × 8% + 29,730 × 2%
           = £3,016.00 + £594.60 = £3,610.60.
        """
        ni = ni_liability(bands_2024, Decimal("80000"))
        assert_gbp_equal(ni, Decimal("3610.60"))

    def test_ni_at_200k_income(self, bands_2024):
        """
        Income £200,000.
        NI = (50,270 - 12,570) × 8% + (200,000 - 50,270) × 2%
           = £3,016.00 + 149,730 × 2% = £3,016.00 + £2,994.60 = £6,010.60.
        """
        ni = ni_liability(bands_2024, Decimal("200000"))
        assert_gbp_equal(ni, Decimal("6010.60"))


class TestMarginalNIRate:
    """Tests for marginal_ni_rate()."""

    def test_zero_below_threshold(self, bands_2024):
        rate = marginal_ni_rate(bands_2024, Decimal("12000"))
        assert rate == Decimal("0")

    def test_zero_at_threshold(self, bands_2024):
        """At the threshold, the next pound is still in the 0% band."""
        rate = marginal_ni_rate(bands_2024, Decimal("12570"))
        # At exactly PT, the next pound crosses into 8% territory.
        # Our implementation: if ni_relevant_income < PT → 0%, if < UEL → 8%.
        # At PT exactly: ni_relevant_income is NOT < PT, so → 8%.
        # This is correct: the first pound ABOVE PT is at 8%.
        assert rate == pct("8")

    def test_8pct_between_pt_and_uel(self, bands_2024):
        rate = marginal_ni_rate(bands_2024, Decimal("30000"))
        assert rate == pct("8")

    def test_2pct_above_uel(self, bands_2024):
        rate = marginal_ni_rate(bands_2024, Decimal("60000"))
        assert rate == pct("2")

    def test_2pct_at_uel(self, bands_2024):
        """At the UEL, rate for the next pound is 2%."""
        rate = marginal_ni_rate(bands_2024, Decimal("50270"))
        assert rate == pct("2")


class TestNIOnAdditionalIncome:
    """Tests for ni_on_additional_income() — handles UEL boundary crossings."""

    def test_simple_below_uel(self, bands_2024):
        """
        Employee at £30k receives £10k bonus — stays below UEL.
        NI = 10,000 × 8% = £800.
        """
        ni = ni_on_additional_income(bands_2024, Decimal("30000"), Decimal("10000"))
        assert_gbp_equal(ni, Decimal("800.00"))

    def test_crossing_uel_boundary(self, bands_2024):
        """
        Employee at £45k receives £15k — crosses UEL at £50,270.
        First £5,270 at 8%: £421.60.
        Next £9,730 at 2%: £194.60.
        Total: £616.20.
        """
        ni = ni_on_additional_income(bands_2024, Decimal("45000"), Decimal("15000"))
        assert_gbp_equal(ni, Decimal("616.20"))

    def test_all_above_uel(self, bands_2024):
        """
        Employee already at £60k (above UEL) receives £20k.
        NI = 20,000 × 2% = £400.
        """
        ni = ni_on_additional_income(bands_2024, Decimal("60000"), Decimal("20000"))
        assert_gbp_equal(ni, Decimal("400.00"))


class TestNISavingFromSalaryScrifice:
    """Tests for ni_saving_from_salary_sacrifice()."""

    def test_sacrifice_below_uel(self, bands_2024):
        """
        Employee at £40k sacrifices £5k.
        Saving = 5,000 × 8% = £400.
        """
        saving = ni_saving_from_salary_sacrifice(
            bands_2024, Decimal("40000"), Decimal("5000")
        )
        assert_gbp_equal(saving, Decimal("400.00"))

    def test_sacrifice_crossing_uel(self, bands_2024):
        """
        Employee at £52k sacrifices £5k — crosses UEL downward.
        From £52k to £50,270: £1,730 × 2% = £34.60 saved.
        From £50,270 to £47k: £3,270 × 8% = £261.60 saved.
        Total: £296.20.
        """
        saving = ni_saving_from_salary_sacrifice(
            bands_2024, Decimal("52000"), Decimal("5000")
        )
        assert_gbp_equal(saving, Decimal("296.20"))

    def test_sacrifice_above_uel(self, bands_2024):
        """
        Employee at £80k sacrifices £10k — stays above UEL.
        Saving = 10,000 × 2% = £200.
        """
        saving = ni_saving_from_salary_sacrifice(
            bands_2024, Decimal("80000"), Decimal("10000")
        )
        assert_gbp_equal(saving, Decimal("200.00"))

    def test_zero_sacrifice(self, bands_2024):
        saving = ni_saving_from_salary_sacrifice(
            bands_2024, Decimal("60000"), Decimal("0")
        )
        assert saving == Decimal("0")
