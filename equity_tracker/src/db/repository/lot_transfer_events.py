"""
LotTransferEventRepository - append-only transfer history persistence.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import LotTransferEvent, _new_uuid


class LotTransferEventRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def add(
        self,
        *,
        security_id: str,
        source_lot_id: str,
        destination_lot_id: str | None,
        source_scheme: str,
        destination_scheme: str,
        transfer_date: date,
        quantity: Decimal,
        source: str | None = None,
        external_id: str | None = None,
        notes: str | None = None,
    ) -> LotTransferEvent:
        row = LotTransferEvent(
            id=_new_uuid(),
            security_id=security_id,
            source_lot_id=source_lot_id,
            destination_lot_id=destination_lot_id,
            source_scheme=source_scheme,
            destination_scheme=destination_scheme,
            transfer_date=transfer_date,
            quantity=str(quantity),
            source=source,
            external_id=external_id,
            notes=notes,
        )
        self._s.add(row)
        return row

    def get_by_external_id(self, external_id: str) -> LotTransferEvent | None:
        stmt = select(LotTransferEvent).where(LotTransferEvent.external_id == external_id)
        return self._s.execute(stmt).scalar_one_or_none()

    def list_for_security(self, security_id: str) -> list[LotTransferEvent]:
        stmt = (
            select(LotTransferEvent)
            .where(LotTransferEvent.security_id == security_id)
            .order_by(
                LotTransferEvent.transfer_date.asc(),
                LotTransferEvent.created_at.asc(),
                LotTransferEvent.id.asc(),
            )
        )
        return list(self._s.scalars(stmt).all())
