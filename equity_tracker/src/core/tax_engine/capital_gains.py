"""
UK Capital Gains Tax (CGT) calculation module.

All functions are pure. All monetary values use Decimal.

Key facts (2024-25):
    Annual Exempt Amount (AEA): £3,000  (was £12,300 until 2022-23, £6,000 in 2023-24)
    Rate on shares (basic rate taxpayer):  10%
    Rate on shares (higher/additional rate): 20%
    Rates on residential property: 18% / 24% — NOT MODELLED HERE (v1 focuses on shares)

CGT rate depends on the taxpayer's INCOME band in the tax year of disposal:
    - If total taxable income + gains falls within the basic rate band → 10%
    - Gains above the basic rate band → 20%
    - Gains may straddle the basic rate boundary, requiring two rates.

CGT is computed on REALISED gains only (sale proceeds minus cost basis).
The cost basis for each lot is tracked separately (see lot_engine/cost_basis.py).

SIP shares disposed from within the plan:
    - Gains on SIP shares sold in-plan may be exempt from CGT depending on
      holding period. This is handled in sip_rules.py; this module provides
      the standard CGT calculation for non-SIP disposals and post-plan SIP disposals.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .bands import TaxYearBands


@dataclass(frozen=True)
class CgtResult:
    """
    Result of a CGT calculation for a disposal or set of disposals in a tax year.

    Attributes:
        total_gain:          Sum of all realised gains (before AEA).
        total_loss:          Sum of all realised losses (positive number).
        net_gain:            total_gain - total_loss (may be negative).
        annual_exempt_amount: AEA applied (capped at net_gain if smaller).
        taxable_gain:        Net gain after AEA (0 if net_gain <= AEA).
        tax_at_basic_rate:   CGT at 10% on gains within basic rate band.
        tax_at_higher_rate:  CGT at 20% on gains above basic rate band.
        total_cgt:           Total CGT payable.
        effective_rate:      Overall effective CGT rate on taxable gain.
        notes:               Explanatory notes for audit trail.
    """

    total_gain: Decimal
    total_loss: Decimal
    net_gain: Decimal
    annual_exempt_amount: Decimal
    taxable_gain: Decimal
    tax_at_basic_rate: Decimal
    tax_at_higher_rate: Decimal
    total_cgt: Decimal
    effective_rate: Decimal
    notes: list[str]


def calculate_cgt(
    bands: TaxYearBands,
    realised_gains: list[Decimal],
    realised_losses: list[Decimal],
    taxable_income_ex_gains: Decimal,
    prior_year_losses: Decimal = Decimal("0"),
) -> CgtResult:
    """
    Calculate CGT for a set of disposals in a tax year.

    The marginal CGT rate is determined by the taxpayer's income band:
    gains that fall within the remaining basic rate band are taxed at 10%,
    gains above are taxed at 20%.

    Args:
        bands:                    Tax year band data.
        realised_gains:           List of individual gains from disposals (>= 0 each).
        realised_losses:          List of individual losses (>= 0 each, as positive amounts).
        taxable_income_ex_gains:  Taxable income for the year EXCLUDING capital gains.
                                  = gross_income - personal_allowance (before any CGT).
                                  Used to determine which rate band gains fall into.
        prior_year_losses:        Unused capital losses brought forward from prior years.
                                  These are deducted AFTER the AEA is applied, from
                                  taxable gains only (cannot take gains below zero).

    Returns:
        CgtResult with full breakdown.
    """
    notes: list[str] = []

    total_gain = sum(realised_gains, Decimal("0"))
    total_loss = sum(realised_losses, Decimal("0"))
    net_gain = total_gain - total_loss

    if net_gain <= Decimal("0"):
        # Net loss — no CGT payable; loss can be carried forward
        notes.append(
            f"Net position is a loss of £{abs(net_gain):,.2f}. "
            "This can be reported to HMRC and carried forward to future years."
        )
        return CgtResult(
            total_gain=total_gain,
            total_loss=total_loss,
            net_gain=net_gain,
            annual_exempt_amount=Decimal("0"),
            taxable_gain=Decimal("0"),
            tax_at_basic_rate=Decimal("0"),
            tax_at_higher_rate=Decimal("0"),
            total_cgt=Decimal("0"),
            effective_rate=Decimal("0"),
            notes=notes,
        )

    # Apply AEA — reduces net gains, cannot take below zero
    aea_used = min(net_gain, bands.cgt_annual_exempt_amount)
    gain_after_aea = net_gain - aea_used
    notes.append(
        f"Annual exempt amount: £{aea_used:,.2f} applied "
        f"(full AEA: £{bands.cgt_annual_exempt_amount:,.2f})."
    )

    # Apply prior year losses — only against taxable gains (above AEA)
    if prior_year_losses > Decimal("0"):
        losses_used = min(gain_after_aea, prior_year_losses)
        gain_after_aea -= losses_used
        notes.append(f"Prior year losses of £{losses_used:,.2f} applied.")

    taxable_gain = gain_after_aea

    if taxable_gain <= Decimal("0"):
        notes.append("No CGT payable after AEA and loss relief.")
        return CgtResult(
            total_gain=total_gain,
            total_loss=total_loss,
            net_gain=net_gain,
            annual_exempt_amount=aea_used,
            taxable_gain=Decimal("0"),
            tax_at_basic_rate=Decimal("0"),
            tax_at_higher_rate=Decimal("0"),
            total_cgt=Decimal("0"),
            effective_rate=Decimal("0"),
            notes=notes,
        )

    # Determine how much basic rate band remains after income
    # The basic rate band is £37,700 of taxable income (standard).
    basic_rate_band_total = bands.basic_rate_band_width  # £37,700
    basic_rate_remaining = max(Decimal("0"), basic_rate_band_total - taxable_income_ex_gains)

    notes.append(
        f"Taxable income (ex gains): £{taxable_income_ex_gains:,.2f}. "
        f"Basic rate band remaining: £{basic_rate_remaining:,.2f}."
    )

    # Split taxable gain between basic and higher rate
    gain_in_basic_band = min(taxable_gain, basic_rate_remaining)
    gain_in_higher_band = taxable_gain - gain_in_basic_band

    tax_basic = (gain_in_basic_band * bands.cgt_basic_rate).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    tax_higher = (gain_in_higher_band * bands.cgt_higher_rate).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    total_cgt = tax_basic + tax_higher

    effective_rate = (
        (total_cgt / taxable_gain).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        if taxable_gain > Decimal("0")
        else Decimal("0")
    )

    if gain_in_basic_band > Decimal("0"):
        notes.append(
            f"£{gain_in_basic_band:,.2f} taxed at basic rate "
            f"({bands.cgt_basic_rate * 100:.0f}%) = £{tax_basic:,.2f}."
        )
    if gain_in_higher_band > Decimal("0"):
        notes.append(
            f"£{gain_in_higher_band:,.2f} taxed at higher rate "
            f"({bands.cgt_higher_rate * 100:.0f}%) = £{tax_higher:,.2f}."
        )

    return CgtResult(
        total_gain=total_gain,
        total_loss=total_loss,
        net_gain=net_gain,
        annual_exempt_amount=aea_used,
        taxable_gain=taxable_gain,
        tax_at_basic_rate=tax_basic,
        tax_at_higher_rate=tax_higher,
        total_cgt=total_cgt,
        effective_rate=effective_rate,
        notes=notes,
    )


def marginal_cgt_rate(
    bands: TaxYearBands,
    taxable_income_ex_gains: Decimal,
) -> Decimal:
    """
    Return the marginal CGT rate for the next pound of gain, given the
    taxpayer's taxable income (excluding any capital gains).

    Returns 10% if there is basic rate band remaining, otherwise 20%.
    Note: if taxable_income_ex_gains is itself negative (large losses), the
    effective marginal rate is 10% since all gains would fall in the basic band.

    Args:
        bands:                    Tax year band data.
        taxable_income_ex_gains:  Taxable income = gross income - personal allowance.
    """
    basic_band_remaining = bands.basic_rate_band_width - taxable_income_ex_gains
    if basic_band_remaining > Decimal("0"):
        return bands.cgt_basic_rate   # 10%
    return bands.cgt_higher_rate      # 20%
