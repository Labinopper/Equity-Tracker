"""
TransactionRepository — read access for the transactions table.

Transactions are immutable event records after creation; corrections are
handled by writing a reversal Transaction with is_reversal=True.
DisposalRepository owns the write path for DISPOSAL transactions.
This repository provides read-only queries for the UI (transaction history,
report aggregations, etc.).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Transaction


class TransactionRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    # ── Read ────────────────────────────────────────────────────────────────

    def get_by_id(self, transaction_id: str) -> Transaction | None:
        """Return a Transaction by primary key, or None if not found."""
        return self._s.get(Transaction, transaction_id)

    def require_by_id(self, transaction_id: str) -> Transaction:
        """Like get_by_id but raises KeyError if not found."""
        obj = self.get_by_id(transaction_id)
        if obj is None:
            raise KeyError(f"Transaction not found: {transaction_id!r}")
        return obj

    def list_for_security(
        self,
        security_id: str,
        transaction_type: str | None = None,
    ) -> list[Transaction]:
        """
        Return all transactions for a security, newest first.

        Args:
            security_id      : Filter by security.
            transaction_type : Optional filter (e.g. "DISPOSAL", "DIVIDEND").
                               Must be one of the values in the CHECK constraint:
                               DISPOSAL, DIVIDEND, CORPORATE_ACTION, ADJUSTMENT.

        Returns transactions ordered: transaction_date DESC, id ASC.
        """
        stmt = (
            select(Transaction)
            .where(Transaction.security_id == security_id)
            .order_by(Transaction.transaction_date.desc(), Transaction.id.asc())
        )
        if transaction_type is not None:
            stmt = stmt.where(Transaction.transaction_type == transaction_type)

        return list(self._s.execute(stmt).scalars())

    def get_by_external_id(self, external_id: str) -> Transaction | None:
        """Return a Transaction by its idempotency key, or None if not found."""
        stmt = select(Transaction).where(Transaction.external_id == external_id)
        return self._s.execute(stmt).scalar_one_or_none()
