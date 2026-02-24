"""
Unit tests for capital_gains.py

Test strategy:
- Verify AEA is applied correctly
- Verify correct rate split between basic/higher rate bands
- Verify prior year losses reduce taxable gain
- Verify net loss scenario (no CGT due)
- Verify the correct CGT rates for 2024-25 (10%/20% for shares)
- Verify AEA history across years (£12,300 → £6,000 → £3,000)
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.tax_engine.bands import get_bands
from src.core.tax_engine.capital_gains import (
    CgtResult,
    calculate_cgt,
    marginal_cgt_rate,
)
from tests.test_tax_engine.conftest import assert_gbp_equal, pct


class TestCGTBasicScenarios:

    def test_no_cgt_within_aea(self, bands_2024):
        """Gain of £2,000 — below AEA of £3,000. No CGT."""
        result = calculate_cgt(
            bands=bands_2024,
            realised_gains=[Decimal("2000")],
            realised_losses=[],
            taxable_income_ex_gains=Decimal("50000"),
        )
        assert result.total_cgt == Decimal("0")
        assert_gbp_equal(result.taxable_gain, Decimal("0"))

    def test_gain_exactly_at_aea(self, bands_2024):
        """Gain of £3,000 — exactly equal to AEA. No CGT."""
        result = calculate_cgt(
            bands=bands_2024,
            realised_gains=[Decimal("3000")],
            realised_losses=[],
            taxable_income_ex_gains=Decimal("50000"),
        )
        assert result.total_cgt == Decimal("0")

    def test_net_loss(self, bands_2024):
        """Losses exceed gains — net loss, no CGT."""
        result = calculate_cgt(
            bands=bands_2024,
            realised_gains=[Decimal("5000")],
            realised_losses=[Decimal("8000")],
            taxable_income_ex_gains=Decimal("50000"),
        )
        assert result.total_cgt == Decimal("0")
        assert result.net_gain == Decimal("-3000")


class TestCGTHigherRateOnly:

    def test_higher_rate_taxpayer_all_at_20pct(self, bands_2024):
        """
        Higher rate taxpayer. Taxable income fills the basic rate band.
        All gain taxed at 20%.
        Gain £10,000, AEA £3,000, taxable gain £7,000.
        Tax = £7,000 × 20% = £1,400.
        """
        result = calculate_cgt(
            bands=bands_2024,
            realised_gains=[Decimal("10000")],
            realised_losses=[],
            taxable_income_ex_gains=Decimal("40000"),  # Fills basic rate band (37,700)
        )
        assert_gbp_equal(result.taxable_gain, Decimal("7000"))
        assert_gbp_equal(result.tax_at_higher_rate, Decimal("1400"))
        assert result.tax_at_basic_rate == Decimal("0")
        assert_gbp_equal(result.total_cgt, Decimal("1400"))


class TestCGTMixedRates:

    def test_gain_straddles_basic_higher_boundary(self, bands_2024):
        """
        Taxable income (ex gains) = £30,000.
        Basic rate band remaining = £37,700 - £30,000 = £7,700.
        Gain = £20,000. AEA = £3,000. Taxable gain = £17,000.
        First £7,700 at 10% = £770.
        Remaining £9,300 at 20% = £1,860.
        Total = £2,630.
        """
        result = calculate_cgt(
            bands=bands_2024,
            realised_gains=[Decimal("20000")],
            realised_losses=[],
            taxable_income_ex_gains=Decimal("30000"),
        )
        assert_gbp_equal(result.taxable_gain, Decimal("17000"))
        assert_gbp_equal(result.tax_at_basic_rate, Decimal("770.00"))
        assert_gbp_equal(result.tax_at_higher_rate, Decimal("1860.00"))
        assert_gbp_equal(result.total_cgt, Decimal("2630.00"))

    def test_all_in_basic_rate_band(self, bands_2024):
        """
        Taxable income £10,000. Basic rate band remaining = £27,700.
        Gain £5,000. AEA = £3,000. Taxable = £2,000.
        All £2,000 in basic rate band → 10%.
        Tax = £200.
        """
        result = calculate_cgt(
            bands=bands_2024,
            realised_gains=[Decimal("5000")],
            realised_losses=[],
            taxable_income_ex_gains=Decimal("10000"),
        )
        assert_gbp_equal(result.taxable_gain, Decimal("2000"))
        assert_gbp_equal(result.tax_at_basic_rate, Decimal("200"))
        assert result.tax_at_higher_rate == Decimal("0")


class TestCGTWithLosses:

    def test_losses_reduce_gain(self, bands_2024):
        """
        Gains £15,000, losses £4,000. Net = £11,000.
        AEA = £3,000. Taxable = £8,000.
        Income fills basic rate band → all at 20%.
        Tax = £8,000 × 20% = £1,600.
        """
        result = calculate_cgt(
            bands=bands_2024,
            realised_gains=[Decimal("15000")],
            realised_losses=[Decimal("4000")],
            taxable_income_ex_gains=Decimal("40000"),
        )
        assert_gbp_equal(result.net_gain, Decimal("11000"))
        assert_gbp_equal(result.taxable_gain, Decimal("8000"))
        assert_gbp_equal(result.total_cgt, Decimal("1600"))

    def test_prior_year_losses(self, bands_2024):
        """
        Gain £10,000. AEA £3,000. Prior year losses £4,000.
        Taxable = £10,000 - £3,000 - £4,000 = £3,000.
        Income fills band → 20%.
        Tax = £3,000 × 20% = £600.
        """
        result = calculate_cgt(
            bands=bands_2024,
            realised_gains=[Decimal("10000")],
            realised_losses=[],
            taxable_income_ex_gains=Decimal("40000"),
            prior_year_losses=Decimal("4000"),
        )
        assert_gbp_equal(result.taxable_gain, Decimal("3000"))
        assert_gbp_equal(result.total_cgt, Decimal("600"))


class TestCGTAnnualExemptAmountHistory:
    """Verify the AEA has correctly declined across tax years."""

    def test_aea_2022_23(self):
        """2022-23: AEA was £12,300."""
        bands = get_bands("2022-23")
        assert bands.cgt_annual_exempt_amount == Decimal("12300")

    def test_aea_2023_24(self):
        """2023-24: AEA reduced to £6,000."""
        bands = get_bands("2023-24")
        assert bands.cgt_annual_exempt_amount == Decimal("6000")

    def test_aea_2024_25(self):
        """2024-25: AEA further reduced to £3,000."""
        bands = get_bands("2024-25")
        assert bands.cgt_annual_exempt_amount == Decimal("3000")

    def test_same_gain_different_aea_gives_different_tax(self):
        """
        A gain of £10,000 attracts more CGT in 2024-25 than 2022-23
        due to the reduced AEA.
        """
        gain = [Decimal("10000")]
        income = Decimal("40000")  # Full basic rate band

        # 2022-23: taxable gain = 10,000 - 12,300 → max 0 (fully covered by AEA!)
        bands_old = get_bands("2022-23")
        result_old = calculate_cgt(bands_old, gain, [], income)
        assert result_old.total_cgt == Decimal("0")

        # 2024-25: taxable gain = 10,000 - 3,000 = £7,000 → taxed at 20%
        bands_new = get_bands("2024-25")
        result_new = calculate_cgt(bands_new, gain, [], income)
        assert result_new.total_cgt > Decimal("0")

        assert result_new.total_cgt > result_old.total_cgt


class TestMarginalCGTRate:

    def test_basic_rate_band_available(self, bands_2024):
        """Taxable income £20k — £17,700 of basic rate band remaining → 10%."""
        rate = marginal_cgt_rate(bands_2024, Decimal("20000"))
        assert rate == pct("10")

    def test_basic_rate_band_exhausted(self, bands_2024):
        """Taxable income £40k — basic rate band exhausted → 20%."""
        rate = marginal_cgt_rate(bands_2024, Decimal("40000"))
        assert rate == pct("20")

    def test_at_boundary(self, bands_2024):
        """Taxable income = exactly basic rate band (£37,700) → 20%."""
        rate = marginal_cgt_rate(bands_2024, Decimal("37700"))
        assert rate == pct("20")
