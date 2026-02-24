"""
Unit tests for src/core/lot_engine/fifo.py

Test strategy:
  - Each test exercises allocate_fifo() with controlled LotForFIFO inputs.
  - No database calls — this is a pure-function module.
  - Tests cover: single lot, multi-lot FIFO order, partial consumption,
    shortfall, loss scenario, SIP economic vs CGT gain, zero-remaining skips,
    rounding at lot boundaries, and guard clause errors.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.core.lot_engine.fifo import (
    FIFOAllocation,
    FIFOResult,
    LotForFIFO,
    allocate_fifo,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lot(
    lot_id: str,
    acq_date: date,
    qty_remaining: str,
    price_gbp: str,
    true_cost_gbp: str,
) -> LotForFIFO:
    return LotForFIFO(
        lot_id=lot_id,
        acquisition_date=acq_date,
        quantity_remaining=Decimal(qty_remaining),
        acquisition_price_gbp=Decimal(price_gbp),
        true_cost_per_share_gbp=Decimal(true_cost_gbp),
    )


def _d(s: str) -> Decimal:
    return Decimal(s)


# ---------------------------------------------------------------------------
# Guard clauses
# ---------------------------------------------------------------------------

class TestFIFOGuards:

    def test_empty_lots_raises(self):
        with pytest.raises(ValueError, match="No lots"):
            allocate_fifo([], _d("100"), _d("15.00"))

    def test_zero_quantity_raises(self):
        lots = [_lot("l1", date(2024, 1, 1), "100", "10", "10")]
        with pytest.raises(ValueError, match="positive"):
            allocate_fifo(lots, _d("0"), _d("15.00"))

    def test_negative_quantity_raises(self):
        lots = [_lot("l1", date(2024, 1, 1), "100", "10", "10")]
        with pytest.raises(ValueError, match="positive"):
            allocate_fifo(lots, _d("-1"), _d("15.00"))

    def test_negative_disposal_price_raises(self):
        lots = [_lot("l1", date(2024, 1, 1), "100", "10", "10")]
        with pytest.raises(ValueError, match="non-negative"):
            allocate_fifo(lots, _d("100"), _d("-1.00"))


# ---------------------------------------------------------------------------
# Single-lot scenarios
# ---------------------------------------------------------------------------

class TestFIFOSingleLot:

    def test_full_sale_of_single_lot(self):
        """100 shares at £10 CGT basis, true cost £4.90, sell all 100 at £15."""
        lots = [_lot("lot-1", date(2024, 1, 15), "100", "10.00", "4.90")]
        result = allocate_fifo(lots, _d("100"), _d("15.00"))

        assert result.is_fully_allocated
        assert result.quantity_sold == _d("100")
        assert result.shortfall == _d("0")
        assert len(result.allocations) == 1

        alloc = result.allocations[0]
        assert alloc.lot_id == "lot-1"
        assert alloc.quantity_allocated == _d("100")
        assert alloc.proceeds_gbp == _d("1500.00")
        assert alloc.cost_basis_gbp == _d("1000.00")
        assert alloc.true_cost_gbp == _d("490.00")
        assert alloc.realised_gain_gbp == _d("500.00")
        assert alloc.realised_gain_economic_gbp == _d("1010.00")

    def test_partial_sale_of_single_lot(self):
        """Sell 30 of 100 shares. Lot partially consumed."""
        lots = [_lot("lot-1", date(2024, 1, 1), "100", "10.00", "10.00")]
        result = allocate_fifo(lots, _d("30"), _d("15.00"))

        assert result.is_fully_allocated
        assert result.quantity_sold == _d("30")
        assert result.allocations[0].quantity_allocated == _d("30")
        assert result.total_proceeds_gbp == _d("450.00")
        assert result.total_cost_basis_gbp == _d("300.00")
        assert result.total_realised_gain_gbp == _d("150.00")

    def test_shortfall_when_lot_insufficient(self):
        """Sell 100 but lot only has 50. Shortfall = 50."""
        lots = [_lot("lot-1", date(2024, 1, 1), "50", "10.00", "10.00")]
        result = allocate_fifo(lots, _d("100"), _d("15.00"))

        assert not result.is_fully_allocated
        assert result.quantity_sold == _d("50")
        assert result.shortfall == _d("50")

    def test_realised_loss(self):
        """Disposal price below cost basis — negative gain (a loss)."""
        lots = [_lot("lot-1", date(2024, 1, 1), "100", "15.00", "15.00")]
        result = allocate_fifo(lots, _d("100"), _d("10.00"))

        assert result.total_realised_gain_gbp == _d("-500.00")
        assert result.total_realised_gain_economic_gbp == _d("-500.00")

    def test_zero_gain_at_break_even(self):
        """Sell exactly at acquisition price."""
        lots = [_lot("lot-1", date(2024, 1, 1), "100", "10.00", "10.00")]
        result = allocate_fifo(lots, _d("100"), _d("10.00"))
        assert result.total_realised_gain_gbp == _d("0.00")


# ---------------------------------------------------------------------------
# Multi-lot FIFO ordering
# ---------------------------------------------------------------------------

class TestFIFOMultiLot:

    def test_older_lot_consumed_first(self):
        """FIFO: oldest acquisition date is consumed first."""
        lots = [
            _lot("lot-1", date(2024, 1, 1), "50", "10.00", "10.00"),   # older
            _lot("lot-2", date(2024, 6, 1), "50", "12.00", "12.00"),   # newer
        ]
        result = allocate_fifo(lots, _d("70"), _d("15.00"))

        assert result.is_fully_allocated
        assert result.quantity_sold == _d("70")
        assert len(result.allocations) == 2

        # Lot-1 fully consumed first
        assert result.allocations[0].lot_id == "lot-1"
        assert result.allocations[0].quantity_allocated == _d("50")

        # Lot-2 partially consumed for remaining 20
        assert result.allocations[1].lot_id == "lot-2"
        assert result.allocations[1].quantity_allocated == _d("20")

    def test_all_lots_consumed(self):
        """Sell exactly the sum of two lots."""
        lots = [
            _lot("lot-1", date(2024, 1, 1), "100", "10.00", "10.00"),
            _lot("lot-2", date(2024, 6, 1), "50", "12.00", "12.00"),
        ]
        result = allocate_fifo(lots, _d("150"), _d("15.00"))

        assert result.is_fully_allocated
        assert result.quantity_sold == _d("150")
        assert len(result.allocations) == 2

    def test_first_lot_only_if_sufficient(self):
        """If first lot covers the sale, second lot is untouched."""
        lots = [
            _lot("lot-1", date(2024, 1, 1), "200", "10.00", "10.00"),
            _lot("lot-2", date(2024, 6, 1), "100", "12.00", "12.00"),
        ]
        result = allocate_fifo(lots, _d("100"), _d("15.00"))

        assert len(result.allocations) == 1
        assert result.allocations[0].lot_id == "lot-1"

    def test_exhausted_lot_skipped(self):
        """Lots with quantity_remaining=0 are silently skipped."""
        lots = [
            _lot("lot-1", date(2024, 1, 1), "0", "10.00", "10.00"),    # exhausted
            _lot("lot-2", date(2024, 6, 1), "50",  "12.00", "12.00"),  # active
        ]
        result = allocate_fifo(lots, _d("50"), _d("15.00"))

        assert result.is_fully_allocated
        assert len(result.allocations) == 1
        assert result.allocations[0].lot_id == "lot-2"

    def test_aggregated_totals_are_sum_of_allocations(self):
        """FIFOResult totals are consistent with sum of individual allocations."""
        lots = [
            _lot("lot-1", date(2024, 1, 1), "60", "10.00", "8.00"),
            _lot("lot-2", date(2024, 4, 1), "40", "11.00", "11.00"),
        ]
        result = allocate_fifo(lots, _d("100"), _d("15.00"))

        expected_proceeds = sum(a.proceeds_gbp for a in result.allocations)
        expected_cost = sum(a.cost_basis_gbp for a in result.allocations)
        expected_gain = sum(a.realised_gain_gbp for a in result.allocations)
        expected_eco_gain = sum(a.realised_gain_economic_gbp for a in result.allocations)

        assert result.total_proceeds_gbp == expected_proceeds
        assert result.total_cost_basis_gbp == expected_cost
        assert result.total_realised_gain_gbp == expected_gain
        assert result.total_realised_gain_economic_gbp == expected_eco_gain


# ---------------------------------------------------------------------------
# SIP economic vs CGT gain distinction
# ---------------------------------------------------------------------------

class TestFIFOSIPEconomicGain:
    """
    SIP Partnership shares: true_cost_per_share < acquisition_price_gbp.
    Economic gain > CGT gain because the true cost was lower (tax was saved
    on the gross salary deducted, reducing the real cash outflow).

    Higher rate taxpayer (51% combined):
      Gross salary deducted = £10/share → CGT cost basis = £10/share
      True net cost = £10 × (1 - 0.51) = £4.90/share
    """

    def test_sip_economic_gain_exceeds_cgt_gain(self):
        lots = [
            _lot(
                lot_id="sip-lot",
                acq_date=date(2024, 1, 15),
                qty_remaining="100",
                price_gbp="10.00",        # CGT cost basis (gross salary deducted)
                true_cost_gbp="4.90",     # economic net cost (51% tax saved)
            )
        ]
        result = allocate_fifo(lots, _d("100"), _d("15.00"))

        # CGT gain  = 1500 - 1000 = £500
        assert result.total_realised_gain_gbp == _d("500.00")
        # Economic gain = 1500 - 490 = £1,010
        assert result.total_realised_gain_economic_gbp == _d("1010.00")
        # Economic gain is substantially larger
        assert result.total_realised_gain_economic_gbp > result.total_realised_gain_gbp

    def test_rsu_economic_gain_equals_cgt_gain(self):
        """RSU: true cost == FMV at vest == CGT basis → economic gain = CGT gain."""
        lots = [
            _lot(
                lot_id="rsu-lot",
                acq_date=date(2024, 3, 20),
                qty_remaining="50",
                price_gbp="12.00",
                true_cost_gbp="12.00",    # RSU: no pre-tax discount
            )
        ]
        result = allocate_fifo(lots, _d("50"), _d("15.00"))
        assert result.total_realised_gain_gbp == result.total_realised_gain_economic_gbp


# ---------------------------------------------------------------------------
# Rounding
# ---------------------------------------------------------------------------

class TestFIFORounding:

    def test_rounding_on_fractional_price(self):
        """Per-lot monetary totals are rounded to 2 d.p. (ROUND_HALF_UP)."""
        # 3 shares × £3.333... = £9.999... → rounds to £10.00
        lots = [_lot("lot-1", date(2024, 1, 1), "3", "10.00", "10.00")]
        # disposal price 10/3 = 3.333...
        result = allocate_fifo(lots, _d("3"), Decimal("10") / Decimal("3"))

        # proceeds = 3 × 3.3333... = 10.0000 → £10.00
        assert result.total_proceeds_gbp == _d("10.00")

    def test_result_is_immutable(self):
        """FIFOResult and FIFOAllocation are frozen dataclasses."""
        lots = [_lot("l1", date(2024, 1, 1), "10", "10", "10")]
        result = allocate_fifo(lots, _d("10"), _d("15"))
        with pytest.raises((AttributeError, TypeError)):
            result.quantity_sold = _d("99")  # type: ignore[misc]
