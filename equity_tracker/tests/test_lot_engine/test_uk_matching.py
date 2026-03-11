from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.core.lot_engine.fifo import LotForFIFO
from src.core.lot_engine.uk_matching import allocate_uk_share_matching


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


def test_same_day_rule_consumes_same_day_lot_before_section_104_pool():
    lots = [
        _lot("older", date(2024, 1, 1), "10", "10.00", "10.00"),
        _lot("same-day", date(2024, 6, 1), "5", "20.00", "20.00"),
    ]

    result = allocate_uk_share_matching(
        lots,
        Decimal("5"),
        Decimal("30.00"),
        disposal_date=date(2024, 6, 1),
    )

    assert result.is_fully_allocated
    assert len(result.allocations) == 1
    assert result.allocations[0].lot_id == "same-day"
    assert result.total_cost_basis_gbp == Decimal("100.00")


def test_section_104_pool_uses_weighted_average_cost_basis():
    lots = [
        _lot("lot-1", date(2024, 1, 1), "10", "10.00", "10.00"),
        _lot("lot-2", date(2024, 2, 1), "10", "20.00", "20.00"),
    ]

    result = allocate_uk_share_matching(
        lots,
        Decimal("10"),
        Decimal("30.00"),
        disposal_date=date(2024, 6, 1),
    )

    assert result.is_fully_allocated
    assert result.total_cost_basis_gbp == Decimal("150.00")
    assert result.total_realised_gain_gbp == Decimal("150.00")
