"""
Scheme-driven employment-tax engine for ESPP and ESPP_PLUS.

This module intentionally bypasses legacy SIP rules for ESPP/ESPP_PLUS disposal
employment-tax estimation.

MIGRATION NOTES:
- Some historical ESPP_PLUS rows may not have fmv_at_acquisition_gbp populated.
- Fallback order for award FMV per share is:
  1) lot.fmv_at_acquisition_gbp
  2) linked employee lot fmv_at_acquisition_gbp (for matched lots)
  3) linked employee lot acquisition_price_gbp (for matched lots)
  4) lot.acquisition_price_gbp
- This preserves deterministic behavior while approximating missing award FMV.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .marginal_rates import MarginalRates


_Q2 = Decimal("0.01")


@dataclass(frozen=True)
class EmploymentTaxContext:
    """Additional lot context used for legacy-data fallbacks."""

    lots_by_id: dict[str, object]


@dataclass(frozen=True)
class EmploymentTaxEstimate:
    """Employment-tax bases and estimated tax amounts for one lot allocation."""

    holding_period_category: str
    income_taxable_base_gbp: Decimal
    ni_base_gbp: Decimal
    student_loan_base_gbp: Decimal
    est_it_gbp: Decimal
    est_ni_gbp: Decimal
    est_sl_gbp: Decimal
    est_total_gbp: Decimal


def estimate_employment_tax_for_lot(
    *,
    lot: object,
    quantity: Decimal,
    event_date: date,
    disposal_price_per_share_gbp: Decimal,
    rates: MarginalRates,
    context: EmploymentTaxContext | None = None,
) -> EmploymentTaxEstimate:
    """
    Estimate employment tax for one lot allocation using scheme-driven rules.

    Supported schemes:
    - ESPP: zero disposal employment tax.
    - ESPP_PLUS: holding-window rules with exact calendar-year thresholds.
    """
    if quantity <= Decimal("0"):
        return _zero_estimate("NO_QUANTITY")

    scheme_type = str(getattr(lot, "scheme_type"))

    if scheme_type == "ESPP":
        return _zero_estimate("ESPP_ZERO")
    if scheme_type != "ESPP_PLUS":
        return _zero_estimate("UNSUPPORTED_SCHEME")

    removal_value_total = _q2(quantity * disposal_price_per_share_gbp)
    award_fmv_per_share = _resolve_award_fmv_per_share(lot, context)
    award_value_total = _q2(quantity * award_fmv_per_share)

    acquisition_date = getattr(lot, "acquisition_date")
    three_year_date = _add_years(acquisition_date, 3)
    five_year_date = _add_years(acquisition_date, 5)
    forfeiture_end = getattr(lot, "forfeiture_period_end", None) or (
        acquisition_date + timedelta(days=183)
    )
    is_matched = getattr(lot, "matching_lot_id", None) is not None

    # Matched shares are forfeited in-window and must not be taxed on disposal.
    if is_matched and event_date < forfeiture_end:
        return _zero_estimate("FORFEITED_MATCHED_UNDER_183D")

    if event_date >= five_year_date:
        return _zero_estimate("FIVE_PLUS_YEARS")

    if event_date >= three_year_date:
        income_base = min(removal_value_total, award_value_total)
        ni_base = Decimal("0")
        sl_base = Decimal("0")
        return _estimate_with_bases(
            "THREE_TO_FIVE_YEARS",
            income_base=income_base,
            ni_base=ni_base,
            sl_base=sl_base,
            rates=rates,
        )

    # < 3 years (includes both <183d and 183d-3y for employee-paid legs)
    return _estimate_with_bases(
        "UNDER_THREE_YEARS",
        income_base=removal_value_total,
        ni_base=removal_value_total,
        sl_base=removal_value_total,
        rates=rates,
    )


def _estimate_with_bases(
    category: str,
    *,
    income_base: Decimal,
    ni_base: Decimal,
    sl_base: Decimal,
    rates: MarginalRates,
) -> EmploymentTaxEstimate:
    est_it = _q2(income_base * rates.income_tax)
    est_ni = _q2(ni_base * rates.national_insurance)
    est_sl = _q2(sl_base * rates.student_loan)
    return EmploymentTaxEstimate(
        holding_period_category=category,
        income_taxable_base_gbp=income_base,
        ni_base_gbp=ni_base,
        student_loan_base_gbp=sl_base,
        est_it_gbp=est_it,
        est_ni_gbp=est_ni,
        est_sl_gbp=est_sl,
        est_total_gbp=est_it + est_ni + est_sl,
    )


def _zero_estimate(category: str) -> EmploymentTaxEstimate:
    z = Decimal("0")
    return EmploymentTaxEstimate(
        holding_period_category=category,
        income_taxable_base_gbp=z,
        ni_base_gbp=z,
        student_loan_base_gbp=z,
        est_it_gbp=z,
        est_ni_gbp=z,
        est_sl_gbp=z,
        est_total_gbp=z,
    )


def _resolve_award_fmv_per_share(
    lot: object,
    context: EmploymentTaxContext | None,
) -> Decimal:
    direct = _optional_decimal(getattr(lot, "fmv_at_acquisition_gbp", None))
    if direct is not None:
        return direct

    matching_lot_id = getattr(lot, "matching_lot_id", None)
    if matching_lot_id and context is not None:
        linked = context.lots_by_id.get(str(matching_lot_id))
        if linked is not None:
            linked_fmv = _optional_decimal(getattr(linked, "fmv_at_acquisition_gbp", None))
            if linked_fmv is not None:
                return linked_fmv
            linked_price = _optional_decimal(getattr(linked, "acquisition_price_gbp", None))
            if linked_price is not None:
                return linked_price

    own_price = _optional_decimal(getattr(lot, "acquisition_price_gbp", None))
    if own_price is not None:
        return own_price
    return Decimal("0")


def _optional_decimal(raw: object) -> Decimal | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    return Decimal(text)


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def _q2(value: Decimal) -> Decimal:
    return value.quantize(_Q2, rounding=ROUND_HALF_UP)
