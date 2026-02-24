"""
UK National Insurance (Employee, Class 1) calculation module.

All functions are pure. All monetary values use Decimal.

Key facts (2024-25):
  Primary Threshold (PT):      £12,570/year — below this, no NI
  Upper Earnings Limit (UEL):  £50,270/year — above this, lower rate
  Rate PT→UEL:                 8% (reduced from 12% in 2022-23, cut to 10% Jan 2024, 8% Apr 2024)
  Rate above UEL:              2%

NI and salary sacrifice:
  Salary sacrifice (pension) reduces NI-liable pay.
  SIP partnership share deductions ALSO reduce NI-liable pay (handled per-transaction).
  Standard (relief at source) pension contributions do NOT reduce NI.

NI is calculated on EMPLOYMENT income only — dividends, rental etc. are not subject
to employee Class 1 NI. This module computes Class 1 employee NI only.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .bands import TaxYearBands


def ni_liability(bands: TaxYearBands, ni_relevant_income: Decimal) -> Decimal:
    """
    Calculate annual employee Class 1 NI liability.

    Args:
        bands:               Tax year band data.
        ni_relevant_income:  Employment income NET of salary sacrifice pension
                             (and any other approved NI-reducing deductions).
                             This is NOT the same as ANI for income tax purposes.

    Returns:
        Total Class 1 employee NI in GBP, rounded to 2 decimal places.

    Examples (2024-25):
        £12,570 → £0        (at PT, nothing to pay)
        £30,000 → (30,000 - 12,570) × 8% = £1,394.40
        £60,000 → (50,270 - 12,570) × 8% + (60,000 - 50,270) × 2%
                = £3,016.00 + £194.60 = £3,210.60
    """
    if ni_relevant_income <= bands.ni_primary_threshold:
        return Decimal("0")

    ni = Decimal("0")

    # Between PT and UEL
    below_uel = min(ni_relevant_income, bands.ni_upper_earnings_limit)
    ni += (below_uel - bands.ni_primary_threshold) * bands.ni_rate_below_uel

    # Above UEL
    if ni_relevant_income > bands.ni_upper_earnings_limit:
        above_uel = ni_relevant_income - bands.ni_upper_earnings_limit
        ni += above_uel * bands.ni_rate_above_uel

    return ni.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def marginal_ni_rate(bands: TaxYearBands, ni_relevant_income: Decimal) -> Decimal:
    """
    Return the marginal NI rate at the given NI-relevant income level.

    Args:
        bands:               Tax year band data.
        ni_relevant_income:  Current NI-relevant income position.

    Returns:
        Decimal marginal NI rate:
            0%  — at or below primary threshold
            8%  — between PT and UEL (2024-25)
            2%  — above UEL

    Note: The rate is evaluated AT the given income (i.e. for the next pound of income).
    """
    if ni_relevant_income < bands.ni_primary_threshold:
        return Decimal("0")
    elif ni_relevant_income < bands.ni_upper_earnings_limit:
        return bands.ni_rate_below_uel
    else:
        return bands.ni_rate_above_uel


def ni_on_additional_income(
    bands: TaxYearBands,
    current_ni_income: Decimal,
    additional_income: Decimal,
) -> Decimal:
    """
    Calculate NI cost of receiving `additional_income` on top of existing NI income.

    Correctly handles boundary crossings (e.g. if additional income crosses the UEL).

    Args:
        bands:               Tax year band data.
        current_ni_income:   Existing NI-relevant income (the baseline).
        additional_income:   Extra income to assess for NI.

    Returns:
        NI payable on the additional income, in GBP.
    """
    if additional_income <= Decimal("0"):
        return Decimal("0")

    ni_base = ni_liability(bands, current_ni_income)
    ni_top = ni_liability(bands, current_ni_income + additional_income)
    return (ni_top - ni_base).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def ni_saving_from_salary_sacrifice(
    bands: TaxYearBands,
    current_ni_income: Decimal,
    sacrifice_amount: Decimal,
) -> Decimal:
    """
    Calculate the employee NI saving from a salary sacrifice arrangement.

    For pension sacrifice or SIP partnership share purchases, the gross salary
    deducted reduces NI-liable pay. This function returns the NI amount saved.

    This is the EMPLOYEE saving only. Employer NI savings are not modelled here
    (they are employer benefit, not part of the employee's true economic cost).

    Args:
        bands:               Tax year band data.
        current_ni_income:   NI income BEFORE the sacrifice (pre-sacrifice position).
        sacrifice_amount:    Amount being sacrificed from gross salary.

    Returns:
        NI saving in GBP (positive number = saving).
    """
    if sacrifice_amount <= Decimal("0"):
        return Decimal("0")

    ni_before = ni_liability(bands, current_ni_income)
    ni_after = ni_liability(bands, max(Decimal("0"), current_ni_income - sacrifice_amount))
    return (ni_before - ni_after).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
