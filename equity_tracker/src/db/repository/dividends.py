"""
Dividend repositories.

- DividendEntryRepository: manual / realised dividend records.
- DividendReferenceEventRepository: provider-sourced reference dividend events.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DividendEntry, DividendReferenceEvent, _new_uuid


class DividendEntryRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    # Write
    def add(
        self,
        *,
        security_id: str,
        dividend_date: date,
        amount_gbp: Decimal,
        amount_original_ccy: Decimal | None = None,
        original_currency: str | None = None,
        fx_rate_to_gbp: Decimal | None = None,
        fx_rate_source: str | None = None,
        tax_treatment: str = "TAXABLE",
        source: str | None = None,
        notes: str | None = None,
    ) -> DividendEntry:
        row = DividendEntry(
            security_id=security_id,
            dividend_date=dividend_date,
            amount_gbp=str(amount_gbp),
            amount_original_ccy=(
                str(amount_original_ccy) if amount_original_ccy is not None else None
            ),
            original_currency=(original_currency or None),
            fx_rate_to_gbp=(str(fx_rate_to_gbp) if fx_rate_to_gbp is not None else None),
            fx_rate_source=(fx_rate_source or None),
            tax_treatment=tax_treatment,
            source=source,
            notes=notes,
        )
        row.id = _new_uuid()
        self._s.add(row)
        return row

    # Read
    def get_by_id(self, entry_id: str) -> DividendEntry | None:
        return self._s.get(DividendEntry, entry_id)

    def list_all(self) -> list[DividendEntry]:
        stmt = (
            select(DividendEntry)
            .order_by(DividendEntry.dividend_date.desc(), DividendEntry.id.asc())
        )
        return list(self._s.scalars(stmt).all())

    def list_for_security(self, security_id: str) -> list[DividendEntry]:
        stmt = (
            select(DividendEntry)
            .where(DividendEntry.security_id == security_id)
            .order_by(DividendEntry.dividend_date.desc(), DividendEntry.id.asc())
        )
        return list(self._s.scalars(stmt).all())

    def delete(self, entry_id: str) -> bool:
        row = self.get_by_id(entry_id)
        if row is None:
            return False
        self._s.delete(row)
        return True


class DividendReferenceEventRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def upsert(
        self,
        *,
        security_id: str,
        ex_dividend_date: date,
        payment_date: date | None,
        amount_original_ccy: Decimal,
        original_currency: str,
        source: str,
        provider_event_key: str,
    ) -> tuple[DividendReferenceEvent, bool]:
        stmt = select(DividendReferenceEvent).where(
            DividendReferenceEvent.provider_event_key == provider_event_key
        )
        row = self._s.scalars(stmt).first()
        created = row is None
        if row is None:
            row = DividendReferenceEvent(
                id=_new_uuid(),
                security_id=security_id,
                provider_event_key=provider_event_key,
            )
            self._s.add(row)

        row.security_id = security_id
        row.ex_dividend_date = ex_dividend_date
        row.payment_date = payment_date
        row.amount_original_ccy = str(amount_original_ccy)
        row.original_currency = original_currency
        row.source = source
        return row, created

    def list_all(self) -> list[DividendReferenceEvent]:
        stmt = (
            select(DividendReferenceEvent)
            .order_by(
                DividendReferenceEvent.ex_dividend_date.desc(),
                DividendReferenceEvent.id.asc(),
            )
        )
        return list(self._s.scalars(stmt).all())

    def list_for_security_ids(self, security_ids: list[str]) -> list[DividendReferenceEvent]:
        if not security_ids:
            return []
        stmt = (
            select(DividendReferenceEvent)
            .where(DividendReferenceEvent.security_id.in_(security_ids))
            .order_by(
                DividendReferenceEvent.ex_dividend_date.desc(),
                DividendReferenceEvent.id.asc(),
            )
        )
        return list(self._s.scalars(stmt).all())
