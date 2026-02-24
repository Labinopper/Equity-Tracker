"""
Unit tests for true_cost.py — true economic cost modelling.

Test strategy:
- Verify that each scheme produces the correct true_net_cost_gbp
- Verify that CGT cost basis is correctly separated from true cost
- Test the SIP partnership benefit at different marginal rate scenarios
  (the key financial insight of the whole system)
- Verify zero true cost for employer-funded shares
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.tax_engine.context import TaxContext
from src.core.tax_engine.marginal_rates import MarginalRates, get_marginal_rates
from src.core.tax_engine.true_cost import (
    brokerage_true_cost,
    espp_plus_matching_true_cost,
    espp_true_cost,
    rsu_true_cost,
    sip_dividend_true_cost,
    sip_matching_true_cost,
    sip_partnership_true_cost,
)
from tests.test_tax_engine.conftest import assert_gbp_equal, pct


# ── Fixtures ───────────────────────────────────────────────────────────────

def make_higher_rate_rates() -> MarginalRates:
    """Higher rate taxpayer: IT=40%, NI=2%, SL=9%, Combined=51%."""
    ctx = TaxContext(
        tax_year="2024-25",
        gross_employment_income=Decimal("80000"),
        student_loan_plan=2,
    )
    return get_marginal_rates(ctx)


def make_taper_zone_rates() -> MarginalRates:
    """Taper zone: IT=60%, NI=2%, SL=9%, Combined=71%."""
    ctx = TaxContext(
        tax_year="2024-25",
        gross_employment_income=Decimal("110000"),
        student_loan_plan=2,
    )
    return get_marginal_rates(ctx)


def make_basic_rate_rates() -> MarginalRates:
    """Basic rate: IT=20%, NI=8%, SL=9%, Combined=37%."""
    ctx = TaxContext(
        tax_year="2024-25",
        gross_employment_income=Decimal("40000"),
        student_loan_plan=2,
    )
    return get_marginal_rates(ctx)


# ── RSU ────────────────────────────────────────────────────────────────────

class TestRSUTrueCost:

    def test_rsu_true_cost_equals_fmv_at_vest(self):
        """
        RSU vest: 100 shares at £50/share FMV (= £5,000 gross).
        CGT basis = FMV = £5,000. True cost = £5,000.
        RSU provides no pre-tax purchase advantage; cost basis = FMV.
        """
        result = rsu_true_cost(
            fmv_at_vest_gbp=Decimal("50.00"),
            quantity=Decimal("100"),
            marginal_rates=make_higher_rate_rates(),
        )
        assert result.scheme_type == "RSU"
        assert_gbp_equal(result.cgt_cost_basis_gbp, Decimal("5000.00"))
        assert_gbp_equal(result.true_net_cost_gbp, Decimal("5000.00"))
        # economic_gain_vs_cgt_basis should be 0 for RSU
        assert_gbp_equal(result.economic_gain_vs_cgt_basis, Decimal("0.00"))

    def test_rsu_tax_paid_at_vest_is_documented(self):
        """
        RSU vest: tax paid at vest is documented for audit purposes.
        100 shares at £50, higher rate: tax = 5,000 × 51% = £2,550.
        """
        rates = make_higher_rate_rates()
        result = rsu_true_cost(
            fmv_at_vest_gbp=Decimal("50.00"),
            quantity=Decimal("100"),
            marginal_rates=rates,
        )
        expected_tax = Decimal("5000.00") * rates.combined
        assert_gbp_equal(result.tax_paid_additional_gbp, expected_tax)

    def test_rsu_with_withholding(self):
        """
        RSU vest: 150 gross shares, 50 withheld for tax, 100 received.
        CGT basis = 100 × FMV (received shares only).
        """
        result = rsu_true_cost(
            fmv_at_vest_gbp=Decimal("50.00"),
            quantity=Decimal("100"),  # Net shares received
            marginal_rates=make_higher_rate_rates(),
            shares_withheld_for_tax=Decimal("50"),
        )
        assert_gbp_equal(result.cgt_cost_basis_gbp, Decimal("5000.00"))  # 100 × £50
        assert result.quantity == Decimal("100")


# ── SIP Partnership ────────────────────────────────────────────────────────

class TestSIPPartnershipTrueCost:
    """
    The SIP partnership true cost is the most important calculation.
    True cost = gross × (1 - combined_marginal_rate).
    This demonstrates the core economic benefit of SIP partnership shares.
    """

    def test_higher_rate_sip_true_cost(self):
        """
        £1,800 gross salary deducted for SIP partnership shares.
        Higher rate taxpayer: combined 51%.
        True cost = £1,800 × (1 - 0.51) = £1,800 × 0.49 = £882.
        """
        result = sip_partnership_true_cost(
            gross_salary_deducted_gbp=Decimal("1800.00"),
            quantity=Decimal("180"),
            fmv_at_purchase_gbp=Decimal("10.00"),
            marginal_rates=make_higher_rate_rates(),
        )
        assert_gbp_equal(result.true_net_cost_gbp, Decimal("882.00"))
        assert_gbp_equal(result.tax_saved_gbp, Decimal("918.00"))  # 1800 × 51%
        assert_gbp_equal(result.cgt_cost_basis_gbp, Decimal("1800.00"))  # gross deduction

    def test_taper_zone_sip_true_cost(self):
        """
        £1,800 gross in the 60%+NI+SL taper zone (71% combined).
        True cost = £1,800 × (1 - 0.71) = £1,800 × 0.29 = £522.
        This is the maximum benefit of SIP — keep 29p per £1.
        """
        result = sip_partnership_true_cost(
            gross_salary_deducted_gbp=Decimal("1800.00"),
            quantity=Decimal("180"),
            fmv_at_purchase_gbp=Decimal("10.00"),
            marginal_rates=make_taper_zone_rates(),
        )
        assert_gbp_equal(result.true_net_cost_gbp, Decimal("522.00"))  # 1800 × 0.29
        assert_gbp_equal(result.tax_saved_gbp, Decimal("1278.00"))    # 1800 × 0.71

    def test_basic_rate_sip_true_cost(self):
        """
        £1,800 gross, basic rate taxpayer (37% combined).
        True cost = £1,800 × 0.63 = £1,134.
        """
        result = sip_partnership_true_cost(
            gross_salary_deducted_gbp=Decimal("1800.00"),
            quantity=Decimal("180"),
            fmv_at_purchase_gbp=Decimal("10.00"),
            marginal_rates=make_basic_rate_rates(),
        )
        assert_gbp_equal(result.true_net_cost_gbp, Decimal("1134.00"))

    def test_cgt_basis_equals_gross_deduction(self):
        """
        For SIP: CGT cost basis = gross salary deducted (= FMV at purchase).
        This is independent of the true cost (which is lower).
        """
        result = sip_partnership_true_cost(
            gross_salary_deducted_gbp=Decimal("1800.00"),
            quantity=Decimal("180"),
            fmv_at_purchase_gbp=Decimal("10.00"),
            marginal_rates=make_higher_rate_rates(),
        )
        assert_gbp_equal(result.cgt_cost_basis_gbp, Decimal("1800.00"))

    def test_true_cost_per_share(self):
        """True cost per share = true_net_cost / quantity."""
        result = sip_partnership_true_cost(
            gross_salary_deducted_gbp=Decimal("1800.00"),
            quantity=Decimal("180"),
            fmv_at_purchase_gbp=Decimal("10.00"),
            marginal_rates=make_higher_rate_rates(),
        )
        # True cost = £882, quantity = 180 → £4.90/share
        assert_gbp_equal(result.true_cost_per_share_gbp, Decimal("4.90"))

    def test_sip_vs_brokerage_comparison(self):
        """
        Key insight test: buying £1,800 of shares via SIP vs. brokerage.
        Higher rate taxpayer with SL:
          SIP:       true cost = £882 (for 180 shares at £10 = £1,800 market value)
          Brokerage: true cost = £882 (net salary to buy £882 of shares at market)
        BUT with SIP you get £1,800 of shares for effectively £882 net.
        With brokerage at £882 net, you can only buy £882 of shares.
        Net advantage = £1,800 - £882 = £918 extra market value for the same net outlay.
        """
        rates = make_higher_rate_rates()
        sip_result = sip_partnership_true_cost(
            gross_salary_deducted_gbp=Decimal("1800.00"),
            quantity=Decimal("180"),
            fmv_at_purchase_gbp=Decimal("10.00"),
            marginal_rates=rates,
        )
        brokerage_result = brokerage_true_cost(
            purchase_price_gbp=Decimal("4.90"),   # Same after-tax cost per share as SIP
            quantity=Decimal("180"),
        )
        # SIP gives 180 shares for same net cost as 180 × £4.90 brokerage
        # The economic advantage of SIP: same net outlay, £1,800 vs ~£882 of market exposure
        market_value_sip = Decimal("180") * Decimal("10.00")
        market_value_brokerage = brokerage_result.true_net_cost_gbp
        advantage = market_value_sip - market_value_brokerage
        assert advantage > Decimal("0"), "SIP should provide more market value for the same net cost"


# ── SIP Matching ───────────────────────────────────────────────────────────

class TestSIPMatchingTrueCost:

    def test_matching_true_cost_is_zero(self):
        """Employer-funded matching shares: true cost = £0."""
        result = sip_matching_true_cost(
            quantity=Decimal("180"),
            fmv_at_award_gbp=Decimal("10.00"),
        )
        assert result.true_net_cost_gbp == Decimal("0")
        assert result.tax_saved_gbp == Decimal("0")

    def test_matching_cgt_basis_is_fmv(self):
        """CGT basis = FMV at award date."""
        result = sip_matching_true_cost(
            quantity=Decimal("180"),
            fmv_at_award_gbp=Decimal("10.00"),
        )
        assert_gbp_equal(result.cgt_cost_basis_gbp, Decimal("1800.00"))


# ── SIP Dividend ───────────────────────────────────────────────────────────

class TestSIPDividendTrueCost:

    def test_dividend_true_cost_is_zero(self):
        """Dividend shares reinvested within plan: true cost = £0."""
        result = sip_dividend_true_cost(
            quantity=Decimal("10"),
            fmv_at_reinvestment_gbp=Decimal("12.00"),
        )
        assert result.true_net_cost_gbp == Decimal("0")
        assert_gbp_equal(result.cgt_cost_basis_gbp, Decimal("120.00"))


# ── ESPP ───────────────────────────────────────────────────────────────────

class TestESPPTrueCost:

    def test_espp_standard_discount(self):
        """
        ESPP purchase: 100 shares at £8.50 (15% discount from £10 FMV).
        Taxable discount = (£10 - £8.50) × 100 = £150.
        Tax on discount at 51% = £76.50.
        True cost = £850 + £76.50 = £926.50.
        CGT basis = £1,000 (FMV at purchase).
        """
        rates = make_higher_rate_rates()
        result = espp_true_cost(
            purchase_price_gbp=Decimal("8.50"),
            fmv_at_purchase_gbp=Decimal("10.00"),
            quantity=Decimal("100"),
            marginal_rates=rates,
            discount_rate=Decimal("0.15"),
        )
        assert_gbp_equal(result.true_net_cost_gbp, Decimal("926.50"))
        assert_gbp_equal(result.cgt_cost_basis_gbp, Decimal("1000.00"))  # FMV at purchase
        assert_gbp_equal(result.tax_paid_additional_gbp, Decimal("76.50"))

    def test_espp_with_lookback_large_discount(self):
        """
        ESPP with lookback: offer-date FMV was £7, purchase-date FMV is £10.
        Purchase price = min(£7, £10) × 0.85 = £5.95.
        Taxable discount = £10 - £5.95 = £4.05 per share. × 100 shares = £405.
        Tax = £405 × 51% = £206.55.
        True cost = £595 + £206.55 = £801.55.
        This shows how valuable lookback is — large effective discount.
        """
        rates = make_higher_rate_rates()
        result = espp_true_cost(
            purchase_price_gbp=Decimal("5.95"),  # min(7, 10) × 0.85
            fmv_at_purchase_gbp=Decimal("10.00"),
            quantity=Decimal("100"),
            marginal_rates=rates,
            offer_date_fmv_gbp=Decimal("7.00"),
            discount_rate=Decimal("0.15"),
        )
        assert_gbp_equal(result.true_net_cost_gbp, Decimal("801.55"))
        assert_gbp_equal(result.cgt_cost_basis_gbp, Decimal("1000.00"))

    def test_espp_immediate_economic_benefit(self):
        """
        CGT basis - true cost = immediate economic benefit of the scheme.
        For standard 15% discount (no lookback): £1,000 - £926.50 = £73.50.
        """
        rates = make_higher_rate_rates()
        result = espp_true_cost(
            purchase_price_gbp=Decimal("8.50"),
            fmv_at_purchase_gbp=Decimal("10.00"),
            quantity=Decimal("100"),
            marginal_rates=rates,
        )
        assert_gbp_equal(result.economic_gain_vs_cgt_basis, Decimal("73.50"))


# ── ESPP+ Matching ─────────────────────────────────────────────────────────

class TestESPPPlusMatchingTrueCost:

    def test_espp_plus_matching_zero_true_cost(self):
        """Employer-funded matching shares: true cost = £0."""
        result = espp_plus_matching_true_cost(
            quantity=Decimal("50"),
            fmv_at_award_gbp=Decimal("10.00"),
            vesting_period_months=12,
        )
        assert result.true_net_cost_gbp == Decimal("0")
        assert_gbp_equal(result.cgt_cost_basis_gbp, Decimal("500.00"))


# ── Brokerage ──────────────────────────────────────────────────────────────

class TestBrokerageTrueCost:

    def test_brokerage_true_cost_equals_purchase(self):
        """Brokerage: true cost = purchase price. No adjustment."""
        result = brokerage_true_cost(
            purchase_price_gbp=Decimal("50.00"),
            quantity=Decimal("100"),
        )
        assert_gbp_equal(result.true_net_cost_gbp, Decimal("5000.00"))
        assert_gbp_equal(result.cgt_cost_basis_gbp, Decimal("5000.00"))

    def test_brokerage_fees_added_to_cost_basis(self):
        """
        Broker fees are added to CGT cost basis (HMRC allows this).
        100 × £50 + £10 fees = £5,010 cost basis.
        """
        result = brokerage_true_cost(
            purchase_price_gbp=Decimal("50.00"),
            quantity=Decimal("100"),
            broker_fees_gbp=Decimal("10.00"),
        )
        assert_gbp_equal(result.cgt_cost_basis_gbp, Decimal("5010.00"))
        assert_gbp_equal(result.true_net_cost_gbp, Decimal("5010.00"))

    def test_brokerage_no_tax_saving(self):
        """Brokerage: no tax saving (purchased from net income)."""
        result = brokerage_true_cost(
            purchase_price_gbp=Decimal("50.00"),
            quantity=Decimal("100"),
        )
        assert result.tax_saved_gbp == Decimal("0")
