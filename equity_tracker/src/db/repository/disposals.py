"""
DisposalRepository — atomic persistence of a disposal event.

record_disposal() is the critical write path:
  1. Adds the Transaction to the session.
  2. For each (Lot, FIFOAllocation) pair:
     a. Creates a LotDisposal record.
     b. Calls LotRepository.update_quantity_remaining() to reduce the lot.
     c. Writes audit entries for both.
  3. Writes a top-level audit INSERT for the Transaction.

All of this happens within the caller's session. The caller commits (or the
DatabaseEngine.session() context manager auto-commits on clean exit).

Raises ValueError (before any DB writes) if FIFOResult.shortfall > 0 —
an incomplete allocation must not be persisted.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.lot_engine.fifo import FIFOAllocation, FIFOResult
from ..models import Lot, LotDisposal, Transaction, _new_uuid
from .audit import AuditRepository
from .lots import LotRepository


class DisposalRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def record_disposal(
        self,
        transaction: Transaction,
        lot_allocations: list[tuple[Lot, FIFOAllocation]],
        audit: AuditRepository | None = None,
    ) -> list[LotDisposal]:
        """
        Atomically persist a disposal across one or more lots.

        Args:
            transaction    : The disposal Transaction ORM object (not yet in session).
            lot_allocations: List of (Lot ORM object, FIFOAllocation) pairs.
                             The Lot objects must already be in the session (fetched
                             by LotRepository.get_active_lots_for_security()).
            audit          : Optional AuditRepository for writing audit entries.

        Returns:
            List of created LotDisposal ORM objects.

        Raises:
            ValueError: if lot_allocations is empty.
        """
        if not lot_allocations:
            raise ValueError("lot_allocations must not be empty.")

        lot_repo = LotRepository(self._s)

        # 1. Persist the Transaction (pre-assign ID so audit can reference it)
        if not transaction.id:
            transaction.id = _new_uuid()
        self._s.add(transaction)
        if audit:
            audit.log_insert(
                table_name="transactions",
                record_id=transaction.id,
                new_values={
                    "security_id": transaction.security_id,
                    "transaction_type": transaction.transaction_type,
                    "transaction_date": str(transaction.transaction_date),
                    "quantity": transaction.quantity,
                    "price_per_share_gbp": transaction.price_per_share_gbp,
                    "total_proceeds_gbp": transaction.total_proceeds_gbp,
                },
            )

        # 2. Create LotDisposal records and update lot quantities
        created_disposals: list[LotDisposal] = []
        for lot, alloc in lot_allocations:
            disposal_id = _new_uuid()   # pre-assign so audit entry references it
            disposal = LotDisposal(
                id=disposal_id,
                transaction_id=transaction.id,
                lot_id=lot.id,
                quantity_allocated=str(alloc.quantity_allocated),
                cost_basis_gbp=str(alloc.cost_basis_gbp),
                true_cost_gbp=str(alloc.true_cost_gbp),
                proceeds_gbp=str(alloc.proceeds_gbp),
                realised_gain_gbp=str(alloc.realised_gain_gbp),
                realised_gain_economic_gbp=str(alloc.realised_gain_economic_gbp),
            )
            self._s.add(disposal)
            created_disposals.append(disposal)

            if audit:
                audit.log_insert(
                    table_name="lot_disposals",
                    record_id=disposal_id,
                    new_values={
                        "lot_id": lot.id,
                        "quantity_allocated": str(alloc.quantity_allocated),
                        "realised_gain_gbp": str(alloc.realised_gain_gbp),
                        "realised_gain_economic_gbp": str(alloc.realised_gain_economic_gbp),
                    },
                )

            # Reduce lot quantity
            new_remaining = Decimal(lot.quantity_remaining) - alloc.quantity_allocated
            lot_repo.update_quantity_remaining(lot, new_remaining, audit=audit)

        return created_disposals

    def record_disposal_from_fifo(
        self,
        transaction: Transaction,
        fifo_result: FIFOResult,
        lots_by_id: dict[str, Lot],
        audit: AuditRepository | None = None,
    ) -> list[LotDisposal]:
        """
        Convenience method: build lot_allocations from a FIFOResult.

        Args:
            transaction  : Disposal Transaction ORM object.
            fifo_result  : Output of allocate_fifo(). Must be fully allocated
                           (shortfall == 0). Raises ValueError otherwise.
            lots_by_id   : Dict mapping lot_id → Lot ORM object (pre-fetched).
            audit        : Optional AuditRepository.

        Returns list of created LotDisposal objects.
        """
        if not fifo_result.is_fully_allocated:
            raise ValueError(
                f"FIFO allocation has a shortfall of {fifo_result.shortfall}. "
                "Cannot persist an incomplete disposal. Check that sufficient "
                "active lots exist for this security."
            )

        lot_allocations = [
            (lots_by_id[alloc.lot_id], alloc)
            for alloc in fifo_result.allocations
        ]
        return self.record_disposal(transaction, lot_allocations, audit=audit)

    # ── Read ────────────────────────────────────────────────────────────────

    def list_for_transaction(self, transaction_id: str) -> list[LotDisposal]:
        """
        Return all LotDisposal records for a specific disposal transaction.

        Used by ReportService to aggregate gains per transaction when building
        CGT and economic gain reports.

        Returns records in insertion order (lot_disposal.id ASC).
        """
        stmt = (
            select(LotDisposal)
            .where(LotDisposal.transaction_id == transaction_id)
            .order_by(LotDisposal.id.asc())
        )
        return list(self._s.execute(stmt).scalars())

    def list_for_security(self, security_id: str) -> list[LotDisposal]:
        """
        Return all LotDisposal records for a security, newest-transaction first.

        Joins LotDisposal → Transaction to filter by security_id.  Used by the
        Lot Explorer detail panel and the CGT summary report.

        Returns list ordered: transaction_date DESC, lot_disposal.id ASC.
        """
        stmt = (
            select(LotDisposal)
            .join(Transaction, LotDisposal.transaction_id == Transaction.id)
            .where(Transaction.security_id == security_id)
            .order_by(Transaction.transaction_date.desc(), LotDisposal.id.asc())
        )
        return list(self._s.execute(stmt).scalars())
