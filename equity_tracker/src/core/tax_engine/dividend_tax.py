"""
UK dividend tax helpers.

This module is additive and does not alter existing CGT/income tax logic.
All monetary values are Decimal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .bands import TaxYearBands, get_bands

_PENNY = Decimal("0.01")


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_PENNY, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class DividendTaxBands:
    tax_year: str
    dividend_allowance: Decimal
    basic_rate: Decimal
    higher_rate: Decimal
    additional_rate: Decimal


@dataclass(frozen=True)
class DividendTaxResult:
    tax_year: str
    total_dividends: Decimal
    dividend_allowance_used: Decimal
    taxable_dividends: Decimal
    taxed_at_basic_rate: Decimal
    taxed_at_higher_rate: Decimal
    taxed_at_additional_rate: Decimal
    tax_at_basic_rate: Decimal
    tax_at_higher_rate: Decimal
    tax_at_additional_rate: Decimal
    total_dividend_tax: Decimal
    effective_rate: Decimal
    notes: list[str]


def get_dividend_tax_bands(tax_year: str) -> DividendTaxBands:
    """
    Return dividend tax rates/allowance for a UK tax year.

    Assumptions encoded here:
    - allowance: 2000 up to 2022-23, 1000 in 2023-24, 500 from 2024-25 onward.
    - rates: current UK dividend rates (8.75%, 33.75%, 39.35%).
    """
    start_year = int(tax_year.split("-", 1)[0])
    if start_year >= 2024:
        allowance = Decimal("500")
    elif start_year == 2023:
        allowance = Decimal("1000")
    else:
        allowance = Decimal("2000")

    return DividendTaxBands(
        tax_year=tax_year,
        dividend_allowance=allowance,
        basic_rate=Decimal("0.0875"),
        higher_rate=Decimal("0.3375"),
        additional_rate=Decimal("0.3935"),
    )


def _dividend_band_room(
    *,
    bands: TaxYearBands,
    taxable_income_ex_dividends: Decimal,
) -> tuple[Decimal, Decimal]:
    """
    Return (basic_room, higher_room) in taxable-income space.

    Additional-rate room is unbounded and is implied by remaining dividends.
    """
    basic_width = bands.basic_rate_band_width
    higher_limit = max(
        Decimal("0"),
        bands.higher_rate_threshold - bands.personal_allowance,
    )

    basic_room = max(Decimal("0"), basic_width - taxable_income_ex_dividends)
    higher_room = max(
        Decimal("0"),
        higher_limit - max(taxable_income_ex_dividends, basic_width),
    )
    return basic_room, higher_room


def calculate_dividend_tax(
    *,
    tax_year: str,
    total_dividends: Decimal,
    taxable_income_ex_dividends: Decimal,
) -> DividendTaxResult:
    """
    Estimate dividend tax for a tax year.

    Args:
        tax_year: UK tax year label, e.g. "2025-26".
        total_dividends: Taxable dividend amount (already excluding ISA-exempt flow).
        taxable_income_ex_dividends: Taxable income excluding dividends.
    """
    notes: list[str] = []
    if total_dividends <= Decimal("0"):
        notes.append("No taxable dividends for the selected period.")
        return DividendTaxResult(
            tax_year=tax_year,
            total_dividends=Decimal("0"),
            dividend_allowance_used=Decimal("0"),
            taxable_dividends=Decimal("0"),
            taxed_at_basic_rate=Decimal("0"),
            taxed_at_higher_rate=Decimal("0"),
            taxed_at_additional_rate=Decimal("0"),
            tax_at_basic_rate=Decimal("0"),
            tax_at_higher_rate=Decimal("0"),
            tax_at_additional_rate=Decimal("0"),
            total_dividend_tax=Decimal("0"),
            effective_rate=Decimal("0"),
            notes=notes,
        )

    income_bands = get_bands(tax_year)
    dividend_bands = get_dividend_tax_bands(tax_year)
    allowance_used = min(total_dividends, dividend_bands.dividend_allowance)
    taxable_dividends = total_dividends - allowance_used

    basic_room, higher_room = _dividend_band_room(
        bands=income_bands,
        taxable_income_ex_dividends=taxable_income_ex_dividends,
    )
    basic_taxed = min(taxable_dividends, basic_room)
    remaining = taxable_dividends - basic_taxed
    higher_taxed = min(remaining, higher_room)
    additional_taxed = remaining - higher_taxed

    tax_basic = _q_money(basic_taxed * dividend_bands.basic_rate)
    tax_higher = _q_money(higher_taxed * dividend_bands.higher_rate)
    tax_additional = _q_money(additional_taxed * dividend_bands.additional_rate)
    total_tax = tax_basic + tax_higher + tax_additional
    effective_rate = (
        (total_tax / taxable_dividends).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        if taxable_dividends > Decimal("0")
        else Decimal("0")
    )

    notes.append(
        f"Dividend allowance used: {allowance_used:.2f} "
        f"(full allowance {dividend_bands.dividend_allowance:.2f})."
    )
    notes.append(
        f"Taxable income ex-dividends: {taxable_income_ex_dividends:.2f}; "
        f"basic room: {basic_room:.2f}, higher room: {higher_room:.2f}."
    )

    return DividendTaxResult(
        tax_year=tax_year,
        total_dividends=_q_money(total_dividends),
        dividend_allowance_used=_q_money(allowance_used),
        taxable_dividends=_q_money(taxable_dividends),
        taxed_at_basic_rate=_q_money(basic_taxed),
        taxed_at_higher_rate=_q_money(higher_taxed),
        taxed_at_additional_rate=_q_money(additional_taxed),
        tax_at_basic_rate=tax_basic,
        tax_at_higher_rate=tax_higher,
        tax_at_additional_rate=tax_additional,
        total_dividend_tax=_q_money(total_tax),
        effective_rate=effective_rate,
        notes=notes,
    )
