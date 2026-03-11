"""
UK share-matching allocation engine.

Current scope:
  - Same-day rule.
  - Section 104 pooled holding for pre-existing acquisitions.

Intentional limitation:
  - The 30-day "bed and breakfast" rule is not applied in this allocator yet.
    That rule can match disposals against later acquisitions, which requires a
    tax-only matching layer separate from the persisted lot-depletion model.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .fifo import FIFOAllocation, FIFOResult, LotForFIFO

_TWO_DP = Decimal("0.01")


def _round(value: Decimal) -> Decimal:
    return value.quantize(_TWO_DP, rounding=ROUND_HALF_UP)


def allocate_uk_share_matching(
    lots: list[LotForFIFO],
    quantity_to_sell: Decimal,
    disposal_price_gbp: Decimal,
    *,
    disposal_date: date,
) -> FIFOResult:
    if not lots:
        raise ValueError("No lots provided for UK share matching allocation.")
    if quantity_to_sell <= Decimal("0"):
        raise ValueError(
            f"quantity_to_sell must be positive; got {quantity_to_sell!r}"
        )
    if disposal_price_gbp < Decimal("0"):
        raise ValueError(
            f"disposal_price_gbp must be non-negative; got {disposal_price_gbp!r}"
        )

    same_day_lots = sorted(
        [
            lot
            for lot in lots
            if lot.quantity_remaining > Decimal("0")
            and lot.acquisition_date == disposal_date
        ],
        key=lambda lot: (lot.acquisition_date, lot.lot_id),
    )
    pool_lots = sorted(
        [
            lot
            for lot in lots
            if lot.quantity_remaining > Decimal("0")
            and lot.acquisition_date < disposal_date
        ],
        key=lambda lot: (lot.acquisition_date, lot.lot_id),
    )

    remaining = quantity_to_sell
    allocations: list[FIFOAllocation] = []

    for lot in same_day_lots:
        if remaining <= Decimal("0"):
            break
        allocated = min(lot.quantity_remaining, remaining)
        if allocated <= Decimal("0"):
            continue
        remaining -= allocated
        cost_basis = _round(allocated * lot.acquisition_price_gbp)
        true_cost = _round(allocated * lot.true_cost_per_share_gbp)
        proceeds = _round(allocated * disposal_price_gbp)
        allocations.append(
            FIFOAllocation(
                lot_id=lot.lot_id,
                acquisition_date=lot.acquisition_date,
                quantity_allocated=allocated,
                cost_basis_gbp=cost_basis,
                true_cost_gbp=true_cost,
                proceeds_gbp=proceeds,
                realised_gain_gbp=proceeds - cost_basis,
                realised_gain_economic_gbp=proceeds - true_cost,
            )
        )

    if remaining > Decimal("0") and pool_lots:
        pool_quantity = sum((lot.quantity_remaining for lot in pool_lots), Decimal("0"))
        if pool_quantity > Decimal("0"):
            average_cost_basis = sum(
                (lot.quantity_remaining * lot.acquisition_price_gbp for lot in pool_lots),
                Decimal("0"),
            ) / pool_quantity
            average_true_cost = sum(
                (lot.quantity_remaining * lot.true_cost_per_share_gbp for lot in pool_lots),
                Decimal("0"),
            ) / pool_quantity

            for lot in pool_lots:
                if remaining <= Decimal("0"):
                    break
                allocated = min(lot.quantity_remaining, remaining)
                if allocated <= Decimal("0"):
                    continue
                remaining -= allocated
                cost_basis = _round(allocated * average_cost_basis)
                true_cost = _round(allocated * average_true_cost)
                proceeds = _round(allocated * disposal_price_gbp)
                allocations.append(
                    FIFOAllocation(
                        lot_id=lot.lot_id,
                        acquisition_date=lot.acquisition_date,
                        quantity_allocated=allocated,
                        cost_basis_gbp=cost_basis,
                        true_cost_gbp=true_cost,
                        proceeds_gbp=proceeds,
                        realised_gain_gbp=proceeds - cost_basis,
                        realised_gain_economic_gbp=proceeds - true_cost,
                    )
                )

    quantity_sold = sum((a.quantity_allocated for a in allocations), Decimal("0"))
    return FIFOResult(
        allocations=tuple(allocations),
        quantity_requested=quantity_to_sell,
        quantity_sold=quantity_sold,
        shortfall=quantity_to_sell - quantity_sold,
        disposal_price_gbp=disposal_price_gbp,
        total_proceeds_gbp=sum((a.proceeds_gbp for a in allocations), Decimal("0")),
        total_cost_basis_gbp=sum((a.cost_basis_gbp for a in allocations), Decimal("0")),
        total_true_cost_gbp=sum((a.true_cost_gbp for a in allocations), Decimal("0")),
        total_realised_gain_gbp=sum(
            (a.realised_gain_gbp for a in allocations), Decimal("0")
        ),
        total_realised_gain_economic_gbp=sum(
            (a.realised_gain_economic_gbp for a in allocations), Decimal("0")
        ),
    )
