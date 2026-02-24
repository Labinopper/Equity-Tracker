"""
Shared fixtures and helpers for tax engine tests.

Tolerance policy:
    All monetary comparisons use assert_gbp_equal() which allows a maximum
    rounding difference of £0.01. This matches HMRC's own rounding rules.
    For rate comparisons, exact Decimal equality is required.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.tax_engine.bands import TaxYearBands, get_bands
from src.core.tax_engine.context import TaxContext


# ── Standard tax contexts ──────────────────────────────────────────────────

@pytest.fixture
def bands_2024() -> TaxYearBands:
    return get_bands("2024-25")


@pytest.fixture
def bands_2023() -> TaxYearBands:
    return get_bands("2023-24")


@pytest.fixture
def ctx_basic_rate() -> TaxContext:
    """Higher-40k basic rate employee, no student loan, no pension sacrifice."""
    return TaxContext(
        tax_year="2024-25",
        gross_employment_income=Decimal("40000"),
        pension_sacrifice=Decimal("0"),
        student_loan_plan=None,
    )


@pytest.fixture
def ctx_higher_rate() -> TaxContext:
    """Higher rate employee at £80k, Plan 2 student loan."""
    return TaxContext(
        tax_year="2024-25",
        gross_employment_income=Decimal("80000"),
        pension_sacrifice=Decimal("0"),
        student_loan_plan=2,
    )


@pytest.fixture
def ctx_taper_zone() -> TaxContext:
    """Employee at £110k — in PA taper zone, Plan 2."""
    return TaxContext(
        tax_year="2024-25",
        gross_employment_income=Decimal("110000"),
        pension_sacrifice=Decimal("0"),
        student_loan_plan=2,
    )


@pytest.fixture
def ctx_taper_zone_with_pension() -> TaxContext:
    """
    Employee with £120k gross and £25k pension sacrifice.
    ANI = £120,000 - £25,000 = £95,000 → below taper zone.
    Demonstrates how pension sacrifice rescues someone from the taper zone.
    """
    return TaxContext(
        tax_year="2024-25",
        gross_employment_income=Decimal("120000"),
        pension_sacrifice=Decimal("25000"),
        student_loan_plan=2,
    )


@pytest.fixture
def ctx_additional_rate() -> TaxContext:
    """Additional rate taxpayer above £125,140, Plan 2."""
    return TaxContext(
        tax_year="2024-25",
        gross_employment_income=Decimal("150000"),
        pension_sacrifice=Decimal("0"),
        student_loan_plan=2,
    )


# ── Assertion helpers ──────────────────────────────────────────────────────

def assert_gbp_equal(actual: Decimal, expected: Decimal, tolerance: Decimal = Decimal("0.01")) -> None:
    """
    Assert two GBP amounts are equal within £0.01 tolerance.
    Raises AssertionError with a clear message if they differ.
    """
    diff = abs(actual - expected)
    assert diff <= tolerance, (
        f"Expected £{expected:,.2f}, got £{actual:,.2f} (difference: £{diff:,.4f})"
    )


def pct(value: str) -> Decimal:
    """Shorthand: pct('40') → Decimal('0.40')"""
    return Decimal(value) / Decimal("100")
