"""
Student Loan repayment calculation module.

All functions are pure. All monetary values use Decimal.

Supported plans:
    Plan 1: For students who started before 1 September 2012 (England/Wales),
            or for students from Northern Ireland.
    Plan 2: For students who started on or after 1 September 2012 (England/Wales).
            Most relevant for tech workers in their 30s/40s.
    Plan 4: Scottish students — NOT YET SUPPORTED (v1).
    Plan 5: New plan from Aug 2023 — NOT YET SUPPORTED (v1).
    Postgraduate: NOT YET SUPPORTED (v1).

Repayment basis:
    Plan 1 & 2: 9% of gross income above the relevant threshold.
    Gross income for SL purposes = total employment income + other income.
    Salary sacrifice pension DOES reduce SL income (unlike income tax which
    uses ANI, SL uses the income AFTER pension sacrifice for assessment).

    Note: This is a nuanced point — HMRC guidance confirms that salary sacrifice
    reduces the income used for student loan assessment. Source: SLC guidance 2023.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .bands import TaxYearBands


def student_loan_repayment(
    bands: TaxYearBands,
    sl_relevant_income: Decimal,
    plan: int | None,
) -> Decimal:
    """
    Calculate annual student loan repayment.

    Args:
        bands:               Tax year band data.
        sl_relevant_income:  Income for student loan purposes.
                             = gross_employment_income - pension_sacrifice + other_income
                             (salary sacrifice reduces SL income).
        plan:                Plan number (1 or 2). None means no student loan.

    Returns:
        Annual student loan repayment in GBP, rounded to 2 decimal places.
        Returns Decimal('0') if plan is None or income is below threshold.
    """
    if plan is None:
        return Decimal("0")

    if plan == 1:
        threshold = bands.student_loan_plan1_threshold
        rate = bands.student_loan_plan1_rate
    elif plan == 2:
        threshold = bands.student_loan_plan2_threshold
        rate = bands.student_loan_plan2_rate
    else:
        raise ValueError(
            f"Student loan plan {plan} is not supported. "
            "Supported plans: 1, 2. Plans 4 and 5 will be added in a future release."
        )

    if sl_relevant_income <= threshold:
        return Decimal("0")

    repayment = (sl_relevant_income - threshold) * rate
    return repayment.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def marginal_student_loan_rate(
    bands: TaxYearBands,
    sl_relevant_income: Decimal,
    plan: int | None,
) -> Decimal:
    """
    Return the marginal student loan rate at the given income level.

    Returns:
        9%  — if income is above the plan threshold (plan 1 or 2)
        0%  — if income is at or below the threshold, or plan is None
    """
    if plan is None:
        return Decimal("0")

    if plan == 1:
        threshold = bands.student_loan_plan1_threshold
        rate = bands.student_loan_plan1_rate
    elif plan == 2:
        threshold = bands.student_loan_plan2_threshold
        rate = bands.student_loan_plan2_rate
    else:
        raise ValueError(f"Student loan plan {plan} is not supported.")

    return rate if sl_relevant_income > threshold else Decimal("0")


def sl_on_additional_income(
    bands: TaxYearBands,
    current_sl_income: Decimal,
    additional_income: Decimal,
    plan: int | None,
) -> Decimal:
    """
    Calculate the student loan cost of receiving `additional_income` on top
    of existing SL-relevant income.

    Correctly handles the threshold boundary crossing.

    Args:
        bands:               Tax year band data.
        current_sl_income:   Existing SL-relevant income (baseline).
        additional_income:   Extra income to assess.
        plan:                Plan number (1 or 2, or None).

    Returns:
        Student loan repayment on the additional income, in GBP.
    """
    if additional_income <= Decimal("0") or plan is None:
        return Decimal("0")

    sl_base = student_loan_repayment(bands, current_sl_income, plan)
    sl_top = student_loan_repayment(bands, current_sl_income + additional_income, plan)
    return (sl_top - sl_base).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
