"""
DividendEntryRepository - CRUD for manual dividend records.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DividendEntry, _new_uuid


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
