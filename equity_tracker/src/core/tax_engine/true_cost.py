"""
True Economic Cost modelling.

The "true economic cost" is what you actually gave up in after-tax terms to hold
a position. This is distinct from the nominal cost basis used for CGT purposes.

The true cost is critical for:
1. Understanding real return on equity compensation (not just nominal gain).
2. Deciding when to sell (true breakeven price differs from CGT cost basis).
3. Comparing returns across different scheme types on a like-for-like basis.

True cost by scheme type:

RSU (Restricted Stock Units):
    Taxed as employment income at vest. You receive the FMV in shares and pay
    IT + NI + SL on that FMV via payroll. Your CGT cost basis = FMV at vest.
    True economic cost = FMV at vest (same as cost basis — you've already paid tax).
    Real "effective price paid" = FMV (there is no discount; tax is just compulsory).

SIP Partnership Shares:
    Purchased from GROSS salary (pre-tax). You never receive the gross salary;
    it goes directly to buy shares.
    True cost = gross_deducted × (1 - combined_marginal_rate)
    This represents the after-tax money you effectively gave up.
    Example: £1,800 gross at 51% combined rate → true cost = £1,800 × 0.49 = £882.

SIP Matching Shares:
    Free from employer. True economic cost = £0 (subject to forfeiture risk).
    If forfeited, there is no cost and no gain.

SIP Dividend Shares:
    Acquired from dividends that would otherwise be paid in cash.
    Dividends within a SIP are income-tax free (the dividend is not taxed when
    reinvested as SIP dividend shares).
    True cost = £0 (the dividend would have been subject to dividend tax outside
    the plan, so these shares represent a tax-saving on the dividend).

ESPP (Employee Share Purchase Plan, non-HMRC-approved):
    You contribute from NET salary (post-tax payroll deductions).
    At purchase, the discount is taxable as employment income (IT + NI).
    True cost = after-tax contribution + IT paid on discount + NI paid on discount
              = purchase_price_paid + income_tax_on_discount + ni_on_discount
    This equals approximately the FMV at purchase (which is the CGT cost basis).
    But the EFFECTIVE cost is: what you gave up in pre-tax salary terms vs. the
    value you received. The scheme is beneficial because the lookback means the
    purchase price is lower than current FMV.
    True benefit = (FMV_at_purchase - true_cost) per share.

ESPP+ Matching Shares:
    Employer-funded. True cost = £0 (subject to vesting/forfeiture conditions).

Standard Brokerage:
    Purchased from net (post-tax) income. True cost = purchase price paid (in GBP).
    No tax adjustment — the money was already taxed as employment income.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .marginal_rates import MarginalRates


@dataclass(frozen=True)
class TrueCostResult:
    """
    True economic cost calculation result for a single acquisition event.

    All values in GBP.

    Attributes:
        scheme_type:             Scheme identifier string.
        gross_cost_gbp:          Nominal/gross cost (FMV at acquisition × quantity).
        tax_saved_gbp:           Tax saving from the scheme (e.g. SIP pre-tax benefit).
        tax_paid_additional_gbp: Any additional tax paid at acquisition (e.g. IT on RSU vest).
        true_net_cost_gbp:       The actual economic cost in after-tax money.
        cgt_cost_basis_gbp:      Cost basis used for CGT purposes (may differ from true cost).
        true_cost_per_share_gbp: True net cost divided by quantity.
        notes:                   Human-readable explanation of calculation.
    """

    scheme_type: str
    quantity: Decimal
    gross_cost_gbp: Decimal
    tax_saved_gbp: Decimal
    tax_paid_additional_gbp: Decimal
    true_net_cost_gbp: Decimal
    cgt_cost_basis_gbp: Decimal
    true_cost_per_share_gbp: Decimal
    notes: list[str] = field(default_factory=list)

    @property
    def economic_gain_vs_cgt_basis(self) -> Decimal:
        """
        Difference between true economic cost and CGT cost basis.
        A positive value means the CGT basis overstates the true cost
        (common for RSUs where FMV = CGT basis but tax was compulsory).
        """
        return self.cgt_cost_basis_gbp - self.true_net_cost_gbp


# ─────────────────────────────────────────────────────────────────────────────
# RSU
# ─────────────────────────────────────────────────────────────────────────────

def rsu_true_cost(
    fmv_at_vest_gbp: Decimal,
    quantity: Decimal,
    marginal_rates: MarginalRates,
    shares_withheld_for_tax: Decimal = Decimal("0"),
) -> TrueCostResult:
    """
    Compute the true economic cost of RSU shares received at vest.

    RSU shares vest as employment income. The FMV at vest is subject to
    IT + NI + SL via payroll. Typically the broker withholds shares to cover
    the tax liability ("sell-to-cover" or "share withholding").

    The CGT cost basis = FMV at vest (because that's what was taxed as income).
    The true economic cost = FMV at vest (the tax is compulsory, there is no
    "savings" or "discount" — you just receive shares worth the FMV and pay tax).

    However, the true RETURN perspective is:
        You effectively "bought" the shares at FMV using money that was:
        (a) the shares themselves (which you then hold), and
        (b) cash from withheld shares (which you surrendered to cover tax).

    So true cost = gross FMV received × (1 - marginal_rate) is NOT correct here.
    The true cost IS the FMV because you needed the full FMV to satisfy the tax
    and receive the shares. The return is driven by subsequent price appreciation.

    Args:
        fmv_at_vest_gbp:          Per-share FMV at vest date.
        quantity:                  Shares actually RECEIVED (net of withholding).
        marginal_rates:            Marginal rates at time of vest.
        shares_withheld_for_tax:   Additional withheld shares for tax reference.

    Returns:
        TrueCostResult with CGT basis = FMV and true cost = FMV.
    """
    notes: list[str] = []
    gross_total = fmv_at_vest_gbp * (quantity + shares_withheld_for_tax)
    notes.append(
        f"RSU vest: total gross value = £{gross_total:,.2f} "
        f"({quantity + shares_withheld_for_tax} shares × £{fmv_at_vest_gbp:,.4f})."
    )

    # Tax paid via payroll on the gross value
    tax_paid = (gross_total * marginal_rates.combined).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    notes.append(
        f"Tax at vest (IT {marginal_rates.income_tax * 100:.0f}% + "
        f"NI {marginal_rates.national_insurance * 100:.0f}% + "
        f"SL {marginal_rates.student_loan * 100:.0f}%): £{tax_paid:,.2f}."
    )

    # CGT cost basis = FMV at vest for net shares received
    cgt_basis_total = fmv_at_vest_gbp * quantity
    notes.append(
        f"CGT cost basis: £{cgt_basis_total:,.2f} "
        f"(£{fmv_at_vest_gbp:,.4f} × {quantity} net shares)."
    )
    notes.append(
        "True economic cost = CGT cost basis (FMV at vest). "
        "RSU scheme provides no pre-tax purchasing advantage; "
        "return is driven purely by post-vest price appreciation."
    )

    cost_per_share = cgt_basis_total / quantity if quantity > 0 else Decimal("0")

    return TrueCostResult(
        scheme_type="RSU",
        quantity=quantity,
        gross_cost_gbp=cgt_basis_total,
        tax_saved_gbp=Decimal("0"),
        tax_paid_additional_gbp=tax_paid,
        true_net_cost_gbp=cgt_basis_total,
        cgt_cost_basis_gbp=cgt_basis_total,
        true_cost_per_share_gbp=cost_per_share,
        notes=notes,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SIP Partnership Shares
# ─────────────────────────────────────────────────────────────────────────────

def sip_partnership_true_cost(
    gross_salary_deducted_gbp: Decimal,
    quantity: Decimal,
    fmv_at_purchase_gbp: Decimal,
    marginal_rates: MarginalRates,
) -> TrueCostResult:
    """
    Compute the true economic cost of SIP partnership shares.

    Partnership shares are purchased from GROSS salary (pre-tax, pre-NI, pre-SL).
    The tax saving is: gross_deducted × combined_marginal_rate.
    The true net cost is the after-tax equivalent of the gross deduction.

    This is the key economic benefit of SIP partnership shares:
    a higher-rate + SL taxpayer paying £1,800 gross effectively pays only ~£882 net.

    CGT cost basis is a separate concept:
    - For CGT purposes, cost basis = FMV at purchase (= gross deduction / quantity
      since shares bought at market value). This equals the gross_salary_deducted.

    Args:
        gross_salary_deducted_gbp: Total gross salary used to buy the shares.
        quantity:                  Shares purchased.
        fmv_at_purchase_gbp:       Per-share FMV at purchase (= purchase price for SIP).
        marginal_rates:            Combined marginal rates at time of purchase.

    Returns:
        TrueCostResult with true_net_cost < gross_cost (the scheme benefit).
    """
    notes: list[str] = []

    # Tax saving: the gross salary that went to shares was never taxed
    tax_saved = (gross_salary_deducted_gbp * marginal_rates.combined).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    notes.append(
        f"SIP partnership purchase: £{gross_salary_deducted_gbp:,.2f} gross salary deducted."
    )
    notes.append(
        f"Tax saving: £{gross_salary_deducted_gbp:,.2f} × {marginal_rates.combined * 100:.1f}% "
        f"(IT {marginal_rates.income_tax * 100:.0f}% + "
        f"NI {marginal_rates.national_insurance * 100:.0f}% + "
        f"SL {marginal_rates.student_loan * 100:.0f}%) = £{tax_saved:,.2f}."
    )

    true_net_cost = gross_salary_deducted_gbp - tax_saved
    notes.append(
        f"True net cost (after-tax equivalent): "
        f"£{gross_salary_deducted_gbp:,.2f} - £{tax_saved:,.2f} = £{true_net_cost:,.2f}."
    )

    # CGT cost basis = gross salary deducted (= FMV at purchase for SIP)
    cgt_basis = gross_salary_deducted_gbp
    notes.append(
        f"CGT cost basis: £{cgt_basis:,.2f} "
        f"(= gross salary deducted, which equals FMV at purchase for SIP partnership shares)."
    )

    if marginal_rates.taper_zone:
        notes.append(
            "TAPER ZONE: This purchase occurs while income is in the 60% effective IT zone. "
            "The tax saving is maximised here. Consider increasing SIP contributions to "
            "reduce ANI below £100,000."
        )

    cost_per_share = true_net_cost / quantity if quantity > 0 else Decimal("0")

    return TrueCostResult(
        scheme_type="SIP_PARTNERSHIP",
        quantity=quantity,
        gross_cost_gbp=gross_salary_deducted_gbp,
        tax_saved_gbp=tax_saved,
        tax_paid_additional_gbp=Decimal("0"),
        true_net_cost_gbp=true_net_cost,
        cgt_cost_basis_gbp=cgt_basis,
        true_cost_per_share_gbp=cost_per_share,
        notes=notes,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SIP Matching & Dividend Shares
# ─────────────────────────────────────────────────────────────────────────────

def sip_matching_true_cost(
    quantity: Decimal,
    fmv_at_award_gbp: Decimal,
    has_forfeiture_risk: bool = True,
) -> TrueCostResult:
    """
    Compute true economic cost of SIP matching shares.

    Matching shares are employer-funded. True cost = £0.
    CGT cost basis = FMV at award (used if shares are later sold post-plan).

    Args:
        quantity:               Shares awarded.
        fmv_at_award_gbp:       Per-share FMV at award date.
        has_forfeiture_risk:    Whether these shares are subject to forfeiture
                                if partnership shares are withdrawn early.
    """
    notes: list[str] = [
        "SIP matching shares: employer-funded. True economic cost = £0.",
        f"CGT cost basis: £{fmv_at_award_gbp * quantity:,.2f} "
        f"(£{fmv_at_award_gbp:,.4f} × {quantity} shares, FMV at award).",
    ]
    if has_forfeiture_risk:
        notes.append(
            "Forfeiture risk: these shares will be forfeited if linked partnership "
            "shares are withdrawn or sold within 3 years."
        )

    cgt_basis = fmv_at_award_gbp * quantity

    return TrueCostResult(
        scheme_type="SIP_MATCHING",
        quantity=quantity,
        gross_cost_gbp=Decimal("0"),
        tax_saved_gbp=Decimal("0"),
        tax_paid_additional_gbp=Decimal("0"),
        true_net_cost_gbp=Decimal("0"),
        cgt_cost_basis_gbp=cgt_basis,
        true_cost_per_share_gbp=Decimal("0"),
        notes=notes,
    )


def sip_dividend_true_cost(
    quantity: Decimal,
    fmv_at_reinvestment_gbp: Decimal,
) -> TrueCostResult:
    """
    True economic cost of SIP dividend shares.

    Dividend shares are acquired from dividends reinvested within the plan.
    Within a SIP, dividends are income-tax free when reinvested as shares.
    Outside the plan, dividends above the £500 allowance (2024-25) would be
    taxed. True cost = £0 (and there's a tax saving on the dividend).

    CGT cost basis = FMV at reinvestment date.
    """
    cgt_basis = fmv_at_reinvestment_gbp * quantity
    notes = [
        "SIP dividend shares: acquired from reinvested dividends (income-tax free in plan).",
        "True economic cost = £0.",
        f"CGT cost basis: £{cgt_basis:,.2f} (FMV at dividend reinvestment date).",
    ]

    return TrueCostResult(
        scheme_type="SIP_DIVIDEND",
        quantity=quantity,
        gross_cost_gbp=Decimal("0"),
        tax_saved_gbp=Decimal("0"),
        tax_paid_additional_gbp=Decimal("0"),
        true_net_cost_gbp=Decimal("0"),
        cgt_cost_basis_gbp=cgt_basis,
        true_cost_per_share_gbp=Decimal("0"),
        notes=notes,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ESPP (non-HMRC-approved, standard US company plan)
# ─────────────────────────────────────────────────────────────────────────────

def espp_true_cost(
    purchase_price_gbp: Decimal,       # Price actually paid per share (post-discount)
    fmv_at_purchase_gbp: Decimal,      # FMV per share on purchase date
    quantity: Decimal,
    marginal_rates: MarginalRates,
    offer_date_fmv_gbp: Decimal | None = None,  # For lookback plans
    discount_rate: Decimal | None = None,
) -> TrueCostResult:
    """
    Compute true economic cost of ESPP shares (non-HMRC-approved plan).

    For non-approved plans (most US company ESPPs for UK employees):
    - Contributions come from NET (post-tax) salary.
    - At purchase, the discount = FMV_at_purchase - purchase_price is treated
      as employment income → IT + NI + SL are due.
    - CGT cost basis = FMV at purchase (income-tax step-up).

    With lookback pricing:
    - Effective purchase price = min(offer_date_fmv, purchase_date_fmv) × (1 - discount%)
    - The taxable discount = FMV_at_purchase - actual_purchase_price
      (This can be much larger than the nominal discount rate if shares appreciated.)

    True economic cost:
        = (purchase_price_paid × quantity) + IT_on_discount + NI_on_discount + SL_on_discount
        ≈ FMV_at_purchase × quantity   [because the income tax "step up" covers the discount]

    The TRUE BENEFIT of ESPP is that you receive shares at a discount,
    and even after paying tax on the discount, you still come out ahead
    compared to buying at market price from net salary.

    Args:
        purchase_price_gbp:   Actual price paid per share (post-discount, in GBP).
        fmv_at_purchase_gbp:  FMV per share on the purchase date.
        quantity:             Shares purchased.
        marginal_rates:       Combined marginal rates at purchase.
        offer_date_fmv_gbp:   FMV at offer date start (for lookback documentation).
        discount_rate:        Nominal discount rate (e.g. Decimal('0.15') for 15%).
    """
    notes: list[str] = []

    total_purchase_cost = purchase_price_gbp * quantity
    total_fmv = fmv_at_purchase_gbp * quantity
    taxable_discount_total = total_fmv - total_purchase_cost

    if offer_date_fmv_gbp is not None:
        notes.append(
            f"ESPP lookback: offer date FMV = £{offer_date_fmv_gbp:,.4f}/share, "
            f"purchase date FMV = £{fmv_at_purchase_gbp:,.4f}/share."
        )
        if offer_date_fmv_gbp < fmv_at_purchase_gbp:
            notes.append(
                f"Lookback applied: purchase price based on lower offer-date FMV "
                f"(£{offer_date_fmv_gbp:,.4f} × (1 - {discount_rate or 0:.0%}))."
            )

    notes.append(
        f"ESPP purchase: {quantity} shares at £{purchase_price_gbp:,.4f}/share "
        f"(FMV: £{fmv_at_purchase_gbp:,.4f}). "
        f"Total purchase cost: £{total_purchase_cost:,.2f}."
    )
    notes.append(
        f"Taxable discount (employment income): "
        f"£{total_fmv:,.2f} - £{total_purchase_cost:,.2f} = £{taxable_discount_total:,.2f}."
    )

    # Tax on the discount
    tax_on_discount = (taxable_discount_total * marginal_rates.combined).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    notes.append(
        f"Tax on discount: £{taxable_discount_total:,.2f} × "
        f"{marginal_rates.combined * 100:.1f}% = £{tax_on_discount:,.2f}."
    )

    # True economic cost
    # Note: purchase_cost came from NET salary (already taxed), so it's an after-tax cost.
    true_net_cost = total_purchase_cost + tax_on_discount
    notes.append(
        f"True net cost: £{total_purchase_cost:,.2f} (purchase) + "
        f"£{tax_on_discount:,.2f} (tax on discount) = £{true_net_cost:,.2f}."
    )

    # CGT cost basis = FMV at purchase (income tax step-up)
    cgt_basis = total_fmv
    notes.append(
        f"CGT cost basis: £{cgt_basis:,.2f} (FMV at purchase date — income tax step-up)."
    )

    economic_benefit = cgt_basis - true_net_cost
    notes.append(
        f"Immediate economic benefit (CGT basis - true cost): £{economic_benefit:,.2f}. "
        f"This represents the net value of the ESPP discount after all taxes."
    )

    cost_per_share = true_net_cost / quantity if quantity > 0 else Decimal("0")

    return TrueCostResult(
        scheme_type="ESPP",
        quantity=quantity,
        gross_cost_gbp=total_purchase_cost,
        tax_saved_gbp=Decimal("0"),
        tax_paid_additional_gbp=tax_on_discount,
        true_net_cost_gbp=true_net_cost,
        cgt_cost_basis_gbp=cgt_basis,
        true_cost_per_share_gbp=cost_per_share,
        notes=notes,
    )


def espp_plus_matching_true_cost(
    quantity: Decimal,
    fmv_at_award_gbp: Decimal,
    vesting_period_months: int,
) -> TrueCostResult:
    """
    True economic cost of ESPP+ matching shares (employer-funded).

    ESPP+ matching shares are employer-awarded, typically vesting over a set period.
    True economic cost = £0 (employer-funded).
    CGT cost basis = FMV at award date.

    Args:
        quantity:               Matching shares awarded.
        fmv_at_award_gbp:       Per-share FMV at award date.
        vesting_period_months:  Months until matching shares are fully vested.
    """
    cgt_basis = fmv_at_award_gbp * quantity
    notes = [
        "ESPP+ matching shares: employer-funded. True economic cost = £0.",
        f"CGT cost basis: £{cgt_basis:,.2f} (FMV at award date).",
        f"Vesting period: {vesting_period_months} months. "
        "Shares may be forfeited if employment ends before vesting.",
    ]

    return TrueCostResult(
        scheme_type="ESPP_PLUS_MATCHING",
        quantity=quantity,
        gross_cost_gbp=Decimal("0"),
        tax_saved_gbp=Decimal("0"),
        tax_paid_additional_gbp=Decimal("0"),
        true_net_cost_gbp=Decimal("0"),
        cgt_cost_basis_gbp=cgt_basis,
        true_cost_per_share_gbp=Decimal("0"),
        notes=notes,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Standard brokerage
# ─────────────────────────────────────────────────────────────────────────────

def brokerage_true_cost(
    purchase_price_gbp: Decimal,
    quantity: Decimal,
    broker_fees_gbp: Decimal = Decimal("0"),
) -> TrueCostResult:
    """
    True economic cost of a standard brokerage purchase (stocks/ETFs).

    Purchased from net (post-tax) income. No scheme adjustments.
    True cost = purchase price + fees. Same as CGT cost basis.

    Args:
        purchase_price_gbp: Per-share price paid (in GBP).
        quantity:           Shares purchased.
        broker_fees_gbp:    Transaction fees (add to CGT cost basis — HMRC allows this).
    """
    total_cost = purchase_price_gbp * quantity + broker_fees_gbp
    notes = [
        f"Brokerage purchase: {quantity} shares × £{purchase_price_gbp:,.4f} "
        f"+ £{broker_fees_gbp:,.2f} fees = £{total_cost:,.2f}.",
        "True cost = purchase price (from net income, no scheme adjustment).",
        f"CGT cost basis: £{total_cost:,.2f} (includes broker fees per HMRC guidance).",
    ]

    cost_per_share = total_cost / quantity if quantity > 0 else Decimal("0")

    return TrueCostResult(
        scheme_type="BROKERAGE",
        quantity=quantity,
        gross_cost_gbp=total_cost,
        tax_saved_gbp=Decimal("0"),
        tax_paid_additional_gbp=Decimal("0"),
        true_net_cost_gbp=total_cost,
        cgt_cost_basis_gbp=total_cost,
        true_cost_per_share_gbp=cost_per_share,
        notes=notes,
    )
