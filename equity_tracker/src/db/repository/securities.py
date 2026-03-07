"""
SecurityRepository — CRUD for the securities table.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Security, _utcnow
from .audit import AuditRepository

_UNSET = object()


class SecurityRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    # ── Write ──────────────────────────────────────────────────────────────

    def add(self, security: Security) -> Security:
        """Persist a new Security. Caller must commit the session."""
        self._s.add(security)
        return security

    # ── Read ───────────────────────────────────────────────────────────────

    def get_by_id(self, security_id: str) -> Security | None:
        return self._s.get(Security, security_id)

    def get_by_ticker(self, ticker: str) -> Security | None:
        return self._s.execute(
            select(Security).where(Security.ticker == ticker)
        ).scalar_one_or_none()

    def list_all(self) -> list[Security]:
        return list(self._s.execute(select(Security)).scalars())

    def require_by_id(self, security_id: str) -> Security:
        """Like get_by_id but raises KeyError if not found."""
        obj = self.get_by_id(security_id)
        if obj is None:
            raise KeyError(f"Security not found: {security_id!r}")
        return obj

    def update(
        self,
        security: Security,
        *,
        name: str | None = None,
        isin: str | None = None,
        exchange: str | None = None,
        units_precision: int | None = None,
        dividend_reminder_date: date | None | object = _UNSET,
        audit: AuditRepository | None = None,
    ) -> Security:
        """
        Update mutable metadata on an existing Security.

        Only the fields explicitly passed (non-None) are changed.  Ticker and
        currency are intentionally excluded — they are identity fields that must
        not change once lots exist (would invalidate CGT records).

        If audit is provided, a single UPDATE entry is written summarising all
        changed fields.  If no fields actually changed, the call is a no-op
        (no audit entry is written, updated_at is unchanged).

        Caller must commit the session.
        """
        old_values: dict[str, str] = {}
        new_values: dict[str, str] = {}

        if name is not None and name != security.name:
            old_values["name"] = security.name
            new_values["name"] = name
            security.name = name

        if isin is not None and isin != security.isin:
            old_values["isin"] = str(security.isin)
            new_values["isin"] = isin
            security.isin = isin

        if exchange is not None and exchange != security.exchange:
            old_values["exchange"] = str(security.exchange)
            new_values["exchange"] = exchange
            security.exchange = exchange

        if units_precision is not None and units_precision != security.units_precision:
            old_values["units_precision"] = str(security.units_precision)
            new_values["units_precision"] = str(units_precision)
            security.units_precision = units_precision

        if (
            dividend_reminder_date is not _UNSET
            and dividend_reminder_date != security.dividend_reminder_date
        ):
            old_values["dividend_reminder_date"] = str(security.dividend_reminder_date)
            new_values["dividend_reminder_date"] = str(dividend_reminder_date)
            security.dividend_reminder_date = dividend_reminder_date

        if new_values:
            security.updated_at = _utcnow()
            if audit is not None:
                audit.log_update(
                    table_name="securities",
                    record_id=security.id,
                    old_values=old_values,
                    new_values=new_values,
                )

        return security
