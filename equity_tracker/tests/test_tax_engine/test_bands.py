from __future__ import annotations

from decimal import Decimal

from src.core.tax_engine.bands import available_tax_years, get_bands


def test_available_tax_years_include_forward_window() -> None:
    years = available_tax_years()
    assert "2026-27" in years
    assert "2035-36" in years


def test_2026_27_uses_published_it_ni_and_student_loan_values() -> None:
    bands = get_bands("2026-27")
    assert bands.personal_allowance == Decimal("12570")
    assert bands.basic_rate_threshold == Decimal("50270")
    assert bands.higher_rate_threshold == Decimal("125140")

    assert bands.ni_primary_threshold == Decimal("12570")
    assert bands.ni_upper_earnings_limit == Decimal("50270")
    assert bands.ni_rate_below_uel == Decimal("0.08")
    assert bands.ni_rate_above_uel == Decimal("0.02")

    assert bands.student_loan_plan1_threshold == Decimal("26900")
    assert bands.student_loan_plan2_threshold == Decimal("29385")
    assert bands.student_loan_plan1_rate == Decimal("0.09")
    assert bands.student_loan_plan2_rate == Decimal("0.09")


def test_future_years_carry_forward_latest_published_values() -> None:
    previous = get_bands("2034-35")
    future = get_bands("2035-36")
    assert future.tax_year == "2035-36"

    assert future.personal_allowance == previous.personal_allowance
    assert future.basic_rate_threshold == previous.basic_rate_threshold
    assert future.higher_rate_threshold == previous.higher_rate_threshold

    assert future.ni_primary_threshold == previous.ni_primary_threshold
    assert future.ni_upper_earnings_limit == previous.ni_upper_earnings_limit
    assert future.ni_rate_below_uel == previous.ni_rate_below_uel
    assert future.ni_rate_above_uel == previous.ni_rate_above_uel

    assert future.student_loan_plan1_threshold == previous.student_loan_plan1_threshold
    assert future.student_loan_plan2_threshold == previous.student_loan_plan2_threshold
    assert future.student_loan_plan1_rate == previous.student_loan_plan1_rate
    assert future.student_loan_plan2_rate == previous.student_loan_plan2_rate
