"""
EmploymentTaxEventRepository - structured employment-tax event persistence.

This repository stores and reads non-disposal employment-tax events that are
relevant to planning/reporting (for example ESPP+ transfer-time tax eligibility).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import EmploymentTaxEvent, _new_uuid


class EmploymentTaxEventRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    # Write
    def add(
        self,
        *,
        lot_id: str,
        security_id: str,
        event_type: str,
        event_date: date,
        estimated_tax_gbp: Decimal | None = None,
        estimation_notes: str | None = None,
        source: str | None = None,
    ) -> EmploymentTaxEvent:
        """
        Append a structured employment-tax event row.
        """
        row = EmploymentTaxEvent(
            lot_id=lot_id,
            security_id=security_id,
            event_type=event_type,
            event_date=event_date,
            estimated_tax_gbp=(
                str(estimated_tax_gbp) if estimated_tax_gbp is not None else None
            ),
            estimation_notes=estimation_notes,
            source=source,
        )
        row.id = _new_uuid()
        self._s.add(row)
        return row

    # Read
    def list_for_lot(self, lot_id: str) -> list[EmploymentTaxEvent]:
        """
        Return employment-tax events for a lot, newest first.
        """
        stmt = (
            select(EmploymentTaxEvent)
            .where(EmploymentTaxEvent.lot_id == lot_id)
            .order_by(
                EmploymentTaxEvent.event_date.desc(),
                EmploymentTaxEvent.created_at.desc(),
                EmploymentTaxEvent.id.asc(),
            )
        )
        return list(self._s.scalars(stmt).all())

    def list_for_security(self, security_id: str) -> list[EmploymentTaxEvent]:
        """
        Return employment-tax events for a security, newest first.
        """
        stmt = (
            select(EmploymentTaxEvent)
            .where(EmploymentTaxEvent.security_id == security_id)
            .order_by(
                EmploymentTaxEvent.event_date.desc(),
                EmploymentTaxEvent.created_at.desc(),
                EmploymentTaxEvent.id.asc(),
            )
        )
        return list(self._s.scalars(stmt).all())

