"""
Unit tests for student_loan.py

Test strategy:
- Verify zero repayment below threshold
- Verify 9% above threshold for Plan 2
- Verify threshold boundary handling
- Verify pension sacrifice reduces SL income
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.tax_engine.student_loan import (
    marginal_student_loan_rate,
    sl_on_additional_income,
    student_loan_repayment,
)
from tests.test_tax_engine.conftest import assert_gbp_equal, pct


class TestStudentLoanRepayment:
    """Tests for student_loan_repayment() — Plan 2, 2024-25."""

    def test_no_repayment_below_threshold(self, bands_2024):
        """Income £20,000 — below Plan 2 threshold (£27,295)."""
        repayment = student_loan_repayment(bands_2024, Decimal("20000"), plan=2)
        assert repayment == Decimal("0")

    def test_no_repayment_at_threshold(self, bands_2024):
        """Income exactly at £27,295 — no repayment (strictly above threshold)."""
        repayment = student_loan_repayment(bands_2024, Decimal("27295"), plan=2)
        assert repayment == Decimal("0")

    def test_repayment_just_above_threshold(self, bands_2024):
        """
        Income £27,296 — 1p above threshold.
        Repayment = £1 × 9% = £0.09 → rounds to £0.09.
        """
        repayment = student_loan_repayment(bands_2024, Decimal("27296"), plan=2)
        assert repayment == Decimal("0.09")

    def test_repayment_at_40k(self, bands_2024):
        """
        Income £40,000.
        Repayment = (40,000 - 27,295) × 9% = 12,705 × 9% = £1,143.45.
        """
        repayment = student_loan_repayment(bands_2024, Decimal("40000"), plan=2)
        assert_gbp_equal(repayment, Decimal("1143.45"))

    def test_repayment_at_80k(self, bands_2024):
        """
        Income £80,000.
        Repayment = (80,000 - 27,295) × 9% = 52,705 × 9% = £4,743.45.
        """
        repayment = student_loan_repayment(bands_2024, Decimal("80000"), plan=2)
        assert_gbp_equal(repayment, Decimal("4743.45"))

    def test_none_plan_returns_zero(self, bands_2024):
        """No student loan."""
        repayment = student_loan_repayment(bands_2024, Decimal("100000"), plan=None)
        assert repayment == Decimal("0")

    def test_unsupported_plan_raises(self, bands_2024):
        """Plan 4 (Scottish) raises ValueError in v1."""
        with pytest.raises(ValueError, match="not supported"):
            student_loan_repayment(bands_2024, Decimal("50000"), plan=4)

    def test_plan1_threshold_different(self, bands_2024):
        """Plan 1 threshold (£24,990 in 2024-25) is lower than Plan 2."""
        # Below Plan 2 threshold but above Plan 1 threshold
        r1 = student_loan_repayment(bands_2024, Decimal("26000"), plan=1)
        r2 = student_loan_repayment(bands_2024, Decimal("26000"), plan=2)
        # Plan 1: (26,000 - 24,990) × 9% = 1,010 × 9% = £90.90
        assert_gbp_equal(r1, Decimal("90.90"))
        # Plan 2: below threshold → £0
        assert r2 == Decimal("0")


class TestMarginalStudentLoanRate:
    """Tests for marginal_student_loan_rate()."""

    def test_zero_below_threshold(self, bands_2024):
        rate = marginal_student_loan_rate(bands_2024, Decimal("20000"), plan=2)
        assert rate == Decimal("0")

    def test_9pct_above_threshold(self, bands_2024):
        rate = marginal_student_loan_rate(bands_2024, Decimal("40000"), plan=2)
        assert rate == pct("9")

    def test_none_plan_always_zero(self, bands_2024):
        rate = marginal_student_loan_rate(bands_2024, Decimal("100000"), plan=None)
        assert rate == Decimal("0")


class TestSLOnAdditionalIncome:
    """Tests for sl_on_additional_income() — handles threshold crossings."""

    def test_all_above_threshold(self, bands_2024):
        """
        Employee at £40k receives £10k bonus.
        SL = 10,000 × 9% = £900.
        """
        sl = sl_on_additional_income(bands_2024, Decimal("40000"), Decimal("10000"), plan=2)
        assert_gbp_equal(sl, Decimal("900.00"))

    def test_crossing_threshold_boundary(self, bands_2024):
        """
        Employee at £25,000 receives £5k bonus — crosses Plan 2 threshold at £27,295.
        Only the portion above £27,295 is subject to SL.
        Above threshold: (25,000 + 5,000 - 27,295) = 2,705.
        SL = 2,705 × 9% = £243.45.
        """
        sl = sl_on_additional_income(bands_2024, Decimal("25000"), Decimal("5000"), plan=2)
        assert_gbp_equal(sl, Decimal("243.45"))

    def test_all_below_threshold(self, bands_2024):
        """Income and bonus both below threshold — no SL."""
        sl = sl_on_additional_income(bands_2024, Decimal("20000"), Decimal("5000"), plan=2)
        assert sl == Decimal("0")

    def test_none_plan_returns_zero(self, bands_2024):
        sl = sl_on_additional_income(bands_2024, Decimal("50000"), Decimal("10000"), plan=None)
        assert sl == Decimal("0")
