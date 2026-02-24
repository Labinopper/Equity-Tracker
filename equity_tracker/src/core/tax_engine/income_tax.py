"""
UK Income Tax calculation module.

All functions are pure (no I/O, no side effects). All monetary values use Decimal.

Key facts (2024-25):
  Personal Allowance: £12,570 (reduced in taper zone above £100,000 ANI)
  Basic rate (20%):   £12,571 – £50,270 of gross income
  Higher rate (40%):  £50,271 – £125,140 of gross income
  Additional rate (45%): above £125,140

PA taper zone (ANI £100,000 – £125,140):
  The PA reduces by £1 for every £2 of ANI above £100,000.
  This creates an effective marginal IT rate of 60%:
    - £1 of income → 40p higher rate tax
    - £0.50 of previously-PA income becomes taxable (in higher rate band) → 20p
    - Total: 60p per £1

Rounding:
  Intermediate calculations use full Decimal precision.
  Final monetary results are rounded to 2 decimal places using ROUND_HALF_UP,
  consistent with HMRC's published approach.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .bands import TaxYearBands


# ─────────────────────────────────────────────────────────────────────────────
# Personal Allowance
# ─────────────────────────────────────────────────────────────────────────────

def personal_allowance(bands: TaxYearBands, adjusted_net_income: Decimal) -> Decimal:
    """
    Calculate personal allowance after applying the income taper.

    Args:
        bands:                Tax year band data.
        adjusted_net_income:  ANI = gross employment income - pension sacrifice + other income.
                              This is the figure HMRC uses for PA taper (NOT gross income).

    Returns:
        Personal allowance in GBP (always >= 0).

    Examples:
        ANI £80,000  → full PA £12,570
        ANI £110,000 → £12,570 - (10,000 / 2) = £7,570
        ANI £125,140 → £12,570 - (25,140 / 2) = £0
        ANI £150,000 → £0 (capped at zero)
    """
    if adjusted_net_income <= bands.pa_taper_start:
        return bands.personal_allowance

    excess = adjusted_net_income - bands.pa_taper_start
    reduction = (excess / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return max(Decimal("0"), bands.personal_allowance - reduction)


# ─────────────────────────────────────────────────────────────────────────────
# Income Tax Liability
# ─────────────────────────────────────────────────────────────────────────────

def income_tax_liability(
    bands: TaxYearBands,
    gross_income: Decimal,
    pa: Decimal,
) -> Decimal:
    """
    Calculate total income tax liability for the given gross income and personal allowance.

    Args:
        bands:        Tax year band data.
        gross_income: Total gross employment income (before PA deduction).
        pa:           Personal allowance (use personal_allowance() to compute).
                      Passed separately so callers can inject a custom PA
                      (e.g. when computing tax on a specific income component).

    Returns:
        Total income tax in GBP (>= 0), rounded to 2 decimal places.

    Band structure:
        taxable_income = gross_income - pa
        20% on first £37,700 of taxable income (basic rate band)
        40% on taxable income £37,701 – (higher_rate_threshold - pa)
        45% on taxable income above that
    """
    if gross_income <= pa:
        return Decimal("0")

    taxable = gross_income - pa
    tax = Decimal("0")

    # Basic rate band: 0 to basic_rate_band_width of taxable income
    basic_band = bands.basic_rate_band_width  # e.g. £37,700
    basic_taxable = min(taxable, basic_band)
    tax += basic_taxable * bands.basic_rate

    # Higher rate band: above basic band up to (higher_rate_threshold - pa)
    # We express the higher rate limit in taxable income terms.
    higher_rate_taxable_limit = bands.higher_rate_threshold - pa
    if taxable > basic_band:
        higher_taxable = min(taxable, higher_rate_taxable_limit) - basic_band
        if higher_taxable > 0:
            tax += higher_taxable * bands.higher_rate

    # Additional rate band: above higher_rate_threshold in taxable income terms
    if taxable > higher_rate_taxable_limit:
        additional_taxable = taxable - higher_rate_taxable_limit
        tax += additional_taxable * bands.additional_rate

    return tax.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def income_tax_on_context(
    bands: TaxYearBands,
    gross_income: Decimal,
    adjusted_net_income: Decimal,
) -> Decimal:
    """
    Compute income tax where ANI may differ from gross income (e.g. pension sacrifice).

    This is the standard entry point for full annual tax liability calculations.
    The ANI determines the PA (via taper), while gross_income determines the total
    income being taxed.

    Args:
        bands:                  Tax year band data.
        gross_income:           Total employment income (not reduced by pension sacrifice).
        adjusted_net_income:    ANI after pension sacrifice and other adjustments.
    """
    pa = personal_allowance(bands, adjusted_net_income)
    return income_tax_liability(bands, gross_income, pa)


# ─────────────────────────────────────────────────────────────────────────────
# Marginal Rate
# ─────────────────────────────────────────────────────────────────────────────

def marginal_income_tax_rate(
    bands: TaxYearBands,
    gross_income: Decimal,
    adjusted_net_income: Decimal | None = None,
) -> Decimal:
    """
    Return the analytical marginal income tax rate at the given income level.

    This uses the analytical (closed-form) formula rather than numerical
    differentiation to ensure exact precision for financial calculations.

    Args:
        bands:                Tax year band data.
        gross_income:         The income level at which to compute the marginal rate.
                              This is the income position BEFORE the next pound arrives.
        adjusted_net_income:  ANI (defaults to gross_income if not provided, i.e.
                              assumes no pension sacrifice and no other income).
                              Must be provided if pension sacrifice applies, because
                              pension sacrifice reduces ANI but the gross_income used
                              for band positioning remains gross.

    Returns:
        Decimal marginal rate (e.g. Decimal('0.40') for 40%).

    Rate schedule:
        0%  — income at or below personal allowance
        20% — basic rate band (PA to £50,270)
        40% — higher rate band (£50,270 to £100,000 ANI)
        60% — PA taper zone (ANI £100,000 to £125,140): 40% + 20% taper effect
        45% — additional rate (above £125,140)

    Critical note on the 60% zone:
        The taper zone has a HIGHER effective marginal rate (60%) than the
        additional rate band (45%) immediately above it. This is intentional
        HMRC policy, not an error. The zone is sometimes called the "60% tax trap".
    """
    ani = adjusted_net_income if adjusted_net_income is not None else gross_income

    # Income at or below the effective personal allowance (may be zero if fully tapered)
    pa = personal_allowance(bands, ani)
    if gross_income <= pa:
        return Decimal("0")

    # Basic rate band: above PA, up to basic_rate_threshold
    if gross_income <= bands.basic_rate_threshold:
        return bands.basic_rate  # 20%

    # Higher rate band (non-taper): above basic rate threshold, ANI below taper start
    if ani <= bands.pa_taper_start:
        return bands.higher_rate  # 40%

    # PA taper zone: ANI between pa_taper_start and pa_taper_end
    # Gross income at this point is >= basic_rate_threshold (always true in practice)
    if ani <= bands.pa_taper_end:
        # 60% effective rate: higher_rate × 1.5
        return bands.taper_zone_effective_it_rate  # 60%

    # Additional rate: ANI above pa_taper_end (£125,140), PA fully withdrawn
    return bands.additional_rate  # 45%


def income_tax_on_additional_income(
    bands: TaxYearBands,
    current_gross_income: Decimal,
    additional_income: Decimal,
    current_ani: Decimal | None = None,
) -> Decimal:
    """
    Calculate the income tax cost of receiving `additional_income` on top of an
    existing income level. This handles band boundaries correctly — if the
    additional income spans multiple bands, each slice is taxed at the right rate.

    Args:
        bands:                 Tax year band data.
        current_gross_income:  Income already received (the baseline).
        additional_income:     The extra income to tax (e.g. RSU vest value).
        current_ani:           Current ANI (defaults to current_gross_income).

    Returns:
        Income tax payable on the additional income, in GBP.

    This is more accurate than simply multiplying by the marginal rate when the
    additional income is large enough to cross band boundaries.
    """
    if additional_income <= Decimal("0"):
        return Decimal("0")

    ani_base = current_ani if current_ani is not None else current_gross_income
    # ANI increases proportionally with gross income (simplified: assumes the
    # additional income has the same pension/sacrifice impact as existing income)
    ani_top = ani_base + additional_income

    pa_base = personal_allowance(bands, ani_base)
    tax_base = income_tax_liability(bands, current_gross_income, pa_base)

    pa_top = personal_allowance(bands, ani_top)
    tax_top = income_tax_liability(bands, current_gross_income + additional_income, pa_top)

    return (tax_top - tax_base).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
