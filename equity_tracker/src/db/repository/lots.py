"""
LotRepository — acquisition lot persistence and queries.

Ordering rule (FIFO):
  Active lots for a security are returned ordered by acquisition_date ASC,
  then lot id ASC (for same-day acquisitions). This is the correct FIFO
  order mandated by HMRC for share identification (Section 104 pool aside —
  for scheme shares we use specific identification with FIFO order).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Lot, _new_uuid
from .audit import AuditRepository


class LotRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    # ── Write ──────────────────────────────────────────────────────────────

    def add(self, lot: Lot, audit: AuditRepository | None = None) -> Lot:
        """
        Persist a new Lot. quantity_remaining is set to equal quantity on creation.

        If audit is provided, an INSERT audit entry is written.
        Caller must commit the session.
        """
        # SQLAlchemy's default=callable fires on INSERT (flush), not on __init__.
        # Pre-assign the UUID so audit entries can reference lot.id immediately.
        if not lot.id:
            lot.id = _new_uuid()
        self._s.add(lot)
        if audit is not None:
            audit.log_insert(
                table_name="lots",
                record_id=lot.id,
                new_values={
                    "security_id": lot.security_id,
                    "scheme_type": lot.scheme_type,
                    "acquisition_date": str(lot.acquisition_date),
                    "quantity": lot.quantity,
                    "acquisition_price_gbp": lot.acquisition_price_gbp,
                    "true_cost_per_share_gbp": lot.true_cost_per_share_gbp,
                },
            )
        return lot

    def update_quantity_remaining(
        self,
        lot: Lot,
        new_quantity_remaining: Decimal,
        audit: AuditRepository | None = None,
    ) -> None:
        """
        Reduce quantity_remaining on a Lot (the only permitted mutation).

        Raises ValueError if new_quantity_remaining is negative or exceeds
        the original quantity.

        If audit is provided, an UPDATE audit entry is written with old/new values.
        Caller must commit the session.
        """
        old_qty_str = lot.quantity_remaining
        new_qty_str = str(new_quantity_remaining)

        if new_quantity_remaining < Decimal("0"):
            raise ValueError(
                f"Lot {lot.id}: quantity_remaining cannot be negative "
                f"(attempted: {new_quantity_remaining})"
            )
        if new_quantity_remaining > Decimal(lot.quantity):
            raise ValueError(
                f"Lot {lot.id}: quantity_remaining ({new_quantity_remaining}) "
                f"cannot exceed original quantity ({lot.quantity})"
            )

        lot.quantity_remaining = new_qty_str

        if audit is not None:
            audit.log_update(
                table_name="lots",
                record_id=lot.id,
                old_values={"quantity_remaining": old_qty_str},
                new_values={"quantity_remaining": new_qty_str},
                notes="FIFO disposal allocation",
            )

    # ── Read ───────────────────────────────────────────────────────────────

    def get_by_id(self, lot_id: str) -> Lot | None:
        return self._s.get(Lot, lot_id)

    def require_by_id(self, lot_id: str) -> Lot:
        obj = self.get_by_id(lot_id)
        if obj is None:
            raise KeyError(f"Lot not found: {lot_id!r}")
        return obj

    def get_active_lots_for_security(
        self,
        security_id: str,
        scheme_type: str | None = None,
        as_of_date: date | None = None,
    ) -> list[Lot]:
        """
        Return lots with quantity_remaining > '0', ordered FIFO.

        Args:
            security_id  : Filter by security.
            scheme_type  : Optional filter (e.g. "SIP_PARTNERSHIP" only).
            as_of_date   : If provided, only include lots acquired on or before
                           this date (useful for tax-year-end queries).

        Returns lots ordered: acquisition_date ASC, id ASC.
        The caller passes this list directly to allocate_fifo().
        """
        stmt = (
            select(Lot)
            .where(
                Lot.security_id == security_id,
                # SQLite TEXT comparison for Decimal: "0" < "1" etc. works for
                # simple non-negative values without leading zeros.
                Lot.quantity_remaining != "0",
            )
            .order_by(Lot.acquisition_date.asc(), Lot.id.asc())
        )

        if scheme_type is not None:
            stmt = stmt.where(Lot.scheme_type == scheme_type)

        if as_of_date is not None:
            stmt = stmt.where(Lot.acquisition_date <= as_of_date)

        rows = list(self._s.execute(stmt).scalars())
        # Additional Python-side filter to exclude truly-zero remainders stored as
        # e.g. "0.00" which wouldn't match the != "0" SQL predicate.
        return [lot for lot in rows if Decimal(lot.quantity_remaining) > Decimal("0")]

    def get_all_lots_for_security(self, security_id: str) -> list[Lot]:
        """Return all lots (including fully disposed) for a security."""
        stmt = (
            select(Lot)
            .where(Lot.security_id == security_id)
            .order_by(Lot.acquisition_date.asc(), Lot.id.asc())
        )
        return list(self._s.execute(stmt).scalars())

    def get_by_external_id(self, external_id: str) -> Lot | None:
        """
        Return a Lot by its idempotency key, or None if not found.

        Used by the Add Lot form to detect duplicate imports before saving.
        The external_id column carries a UNIQUE constraint in the schema.
        """
        stmt = select(Lot).where(Lot.external_id == external_id)
        return self._s.execute(stmt).scalar_one_or_none()
