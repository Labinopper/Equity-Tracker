from __future__ import annotations

from decimal import Decimal

from src.core.tax_engine.dividend_tax import (
    calculate_dividend_tax,
    get_dividend_tax_bands,
)


def test_dividend_allowance_schedule_across_tax_years() -> None:
    assert get_dividend_tax_bands("2022-23").dividend_allowance == Decimal("2000")
    assert get_dividend_tax_bands("2023-24").dividend_allowance == Decimal("1000")
    assert get_dividend_tax_bands("2025-26").dividend_allowance == Decimal("500")


def test_no_tax_when_no_taxable_dividends() -> None:
    result = calculate_dividend_tax(
        tax_year="2025-26",
        total_dividends=Decimal("0"),
        taxable_income_ex_dividends=Decimal("20000"),
    )
    assert result.total_dividend_tax == Decimal("0")
    assert result.taxable_dividends == Decimal("0")


def test_dividends_within_basic_rate_band() -> None:
    result = calculate_dividend_tax(
        tax_year="2025-26",
        total_dividends=Decimal("10000"),
        taxable_income_ex_dividends=Decimal("10000"),
    )
    assert result.dividend_allowance_used == Decimal("500.00")
    assert result.taxable_dividends == Decimal("9500.00")
    assert result.taxed_at_basic_rate == Decimal("9500.00")
    assert result.taxed_at_higher_rate == Decimal("0.00")
    assert result.total_dividend_tax == Decimal("831.25")


def test_dividends_split_across_basic_and_higher_rates() -> None:
    result = calculate_dividend_tax(
        tax_year="2025-26",
        total_dividends=Decimal("10000"),
        taxable_income_ex_dividends=Decimal("35000"),
    )
    assert result.dividend_allowance_used == Decimal("500.00")
    assert result.taxable_dividends == Decimal("9500.00")
    assert result.taxed_at_basic_rate == Decimal("2700.00")
    assert result.taxed_at_higher_rate == Decimal("6800.00")
    assert result.taxed_at_additional_rate == Decimal("0.00")
    assert result.total_dividend_tax == Decimal("2531.25")


def test_dividends_in_additional_rate_band() -> None:
    result = calculate_dividend_tax(
        tax_year="2025-26",
        total_dividends=Decimal("5000"),
        taxable_income_ex_dividends=Decimal("120000"),
    )
    assert result.dividend_allowance_used == Decimal("500.00")
    assert result.taxable_dividends == Decimal("4500.00")
    assert result.taxed_at_basic_rate == Decimal("0.00")
    assert result.taxed_at_higher_rate == Decimal("0.00")
    assert result.taxed_at_additional_rate == Decimal("4500.00")
    assert result.total_dividend_tax == Decimal("1770.75")
