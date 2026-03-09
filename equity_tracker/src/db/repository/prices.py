"""
PriceRepository: price_history and ticker-snapshot persistence.

Price history stores end-of-day closes keyed by (security_id, price_date, source).
Ticker snapshots store per-refresh displayed GBP prices for freshness/staleness UI.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import PriceHistory, PriceTickerSnapshot, _new_uuid


class PriceRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    # Write
    def upsert(
        self,
        security_id: str,
        price_date: date,
        close_price_original_ccy: str,
        currency: str,
        source: str,
        *,
        close_price_gbp: str | None = None,
    ) -> PriceHistory:
        """
        Insert a PriceHistory row, or update the existing one for
        (security_id, price_date, source).
        """
        stmt = select(PriceHistory).where(
            PriceHistory.security_id == security_id,
            PriceHistory.price_date == price_date,
            PriceHistory.source == source,
        )
        existing = self._s.scalars(stmt).first()

        if existing is not None:
            existing.close_price_original_ccy = close_price_original_ccy
            existing.close_price_gbp = close_price_gbp
            existing.currency = currency
            existing.fetched_at = datetime.now(tz=timezone.utc)
            return existing

        row = PriceHistory(
            security_id=security_id,
            price_date=price_date,
            close_price_original_ccy=close_price_original_ccy,
            close_price_gbp=close_price_gbp,
            currency=currency,
            source=source,
            fetched_at=datetime.now(tz=timezone.utc),
        )
        row.id = _new_uuid()
        self._s.add(row)
        return row

    def add_ticker_snapshot(
        self,
        *,
        security_id: str,
        price_date: date,
        price_native: str | None = None,
        currency: str | None = None,
        price_gbp: str,
        source: str | None = None,
        direction: str | None = None,
        percent_change: str | None = None,
        observed_at: datetime | None = None,
    ) -> PriceTickerSnapshot:
        """
        Append a per-refresh ticker snapshot for UI freshness/staleness tracking.

        To keep the intraday series meaningful, do not add a new row when the
        latest stored snapshot for the same security/date/source has the same
        native and GBP values. In that case the latest row remains the live
        representation until the price actually moves.
        """
        snapshot_at = observed_at or datetime.now(tz=timezone.utc)

        existing = self._s.scalars(
            select(PriceTickerSnapshot)
            .where(
                PriceTickerSnapshot.security_id == security_id,
                PriceTickerSnapshot.price_date == price_date,
                PriceTickerSnapshot.source == source,
                PriceTickerSnapshot.observed_at == snapshot_at,
            )
            .limit(1)
        ).first()
        if existing is not None:
            existing.price_native = price_native
            existing.currency = currency
            existing.price_gbp = price_gbp
            existing.direction = direction
            existing.percent_change = percent_change
            return existing

        latest_same_series = self._s.scalars(
            select(PriceTickerSnapshot)
            .where(
                PriceTickerSnapshot.security_id == security_id,
                PriceTickerSnapshot.price_date == price_date,
                PriceTickerSnapshot.source == source,
            )
            .order_by(PriceTickerSnapshot.observed_at.desc())
            .limit(1)
        ).first()
        if (
            latest_same_series is not None
            and latest_same_series.price_native == price_native
            and latest_same_series.currency == currency
            and latest_same_series.price_gbp == price_gbp
        ):
            latest_same_series.direction = direction
            latest_same_series.percent_change = percent_change
            return latest_same_series

        row = PriceTickerSnapshot(
            security_id=security_id,
            price_date=price_date,
            price_native=price_native,
            currency=currency,
            price_gbp=price_gbp,
            source=source,
            direction=direction,
            percent_change=percent_change,
            observed_at=snapshot_at,
        )
        row.id = _new_uuid()
        self._s.add(row)
        return row

    # Read
    def get_latest(self, security_id: str) -> PriceHistory | None:
        """
        Return the most recent price-history row for a security.
        """
        stmt = (
            select(PriceHistory)
            .where(PriceHistory.security_id == security_id)
            .order_by(PriceHistory.price_date.desc(), PriceHistory.fetched_at.desc())
            .limit(1)
        )
        return self._s.scalars(stmt).first()

    def get_latest_before(
        self,
        security_id: str,
        before_date: date,
    ) -> PriceHistory | None:
        """
        Return the most recent price-history row strictly before before_date.
        """
        stmt = (
            select(PriceHistory)
            .where(
                PriceHistory.security_id == security_id,
                PriceHistory.price_date < before_date,
            )
            .order_by(PriceHistory.price_date.desc(), PriceHistory.fetched_at.desc())
            .limit(1)
        )
        return self._s.scalars(stmt).first()

    def get_latest_on_or_before(
        self,
        security_id: str,
        on_or_before_date: date,
    ) -> PriceHistory | None:
        """
        Return the most recent price-history row on or before on_or_before_date.
        """
        stmt = (
            select(PriceHistory)
            .where(
                PriceHistory.security_id == security_id,
                PriceHistory.price_date <= on_or_before_date,
            )
            .order_by(PriceHistory.price_date.desc(), PriceHistory.fetched_at.desc())
            .limit(1)
        )
        return self._s.scalars(stmt).first()

    def get_earliest_price_date(self, security_id: str) -> date | None:
        """
        Return the oldest stored price_date for a security, if any.
        """
        stmt = select(func.min(PriceHistory.price_date)).where(
            PriceHistory.security_id == security_id
        )
        return self._s.scalar(stmt)

    def get_latest_ticker_snapshot(self, security_id: str) -> PriceTickerSnapshot | None:
        """
        Return the latest per-refresh ticker snapshot for a security.
        """
        stmt = (
            select(PriceTickerSnapshot)
            .where(PriceTickerSnapshot.security_id == security_id)
            .order_by(PriceTickerSnapshot.observed_at.desc())
            .limit(1)
        )
        return self._s.scalars(stmt).first()

    def list_recent_ticker_snapshots(
        self,
        security_id: str,
        *,
        limit: int | None = 16,
        price_date: date | None = None,
    ) -> list[PriceTickerSnapshot]:
        """
        Return the most recent per-refresh ticker snapshots for a security,
        newest first.
        """
        stmt = (
            select(PriceTickerSnapshot)
            .where(PriceTickerSnapshot.security_id == security_id)
            .order_by(PriceTickerSnapshot.observed_at.desc())
        )
        if price_date is not None:
            stmt = stmt.where(PriceTickerSnapshot.price_date == price_date)
        if limit is not None:
            stmt = stmt.limit(max(1, int(limit)))
        return list(self._s.scalars(stmt).all())

    def get_current_price_run_started_at(self, security_id: str) -> datetime | None:
        """
        Return when the currently displayed ticker price last changed.

        This is the first observed_at in the latest contiguous run where
        price_gbp equals the current/latest snapshot value.
        """
        latest = self.get_latest_ticker_snapshot(security_id)
        if latest is None:
            return None

        prev_diff_ts = self._s.scalar(
            select(PriceTickerSnapshot.observed_at)
            .where(
                PriceTickerSnapshot.security_id == security_id,
                PriceTickerSnapshot.observed_at < latest.observed_at,
                PriceTickerSnapshot.price_gbp != latest.price_gbp,
            )
            .order_by(PriceTickerSnapshot.observed_at.desc())
            .limit(1)
        )

        run_start_stmt = select(func.min(PriceTickerSnapshot.observed_at)).where(
            PriceTickerSnapshot.security_id == security_id,
            PriceTickerSnapshot.price_gbp == latest.price_gbp,
        )
        if prev_diff_ts is not None:
            run_start_stmt = run_start_stmt.where(
                PriceTickerSnapshot.observed_at > prev_diff_ts
            )

        run_start = self._s.scalar(run_start_stmt)
        return run_start or latest.observed_at

    def get_history_range(
        self,
        security_id: str,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[PriceHistory]:
        """
        Return all PriceHistory rows for a security in [from_date, to_date],
        ordered by price_date ASC then fetched_at ASC.

        If from_date is None, all rows from the earliest stored date are returned.
        If to_date is None, all rows up to today are returned.

        Multiple rows per date may be returned (different sources). Use
        HistoryService._dedup_daily_rows() to select one GBP price per date.
        """
        stmt = (
            select(PriceHistory)
            .where(PriceHistory.security_id == security_id)
            .order_by(PriceHistory.price_date.asc(), PriceHistory.fetched_at.asc())
        )
        if from_date is not None:
            stmt = stmt.where(PriceHistory.price_date >= from_date)
        if to_date is not None:
            stmt = stmt.where(PriceHistory.price_date <= to_date)
        return list(self._s.scalars(stmt).all())

    def list_latest_all(self) -> list[PriceHistory]:
        """
        Return the latest price-history row per security.
        """
        subq = (
            select(
                PriceHistory.security_id,
                func.max(PriceHistory.price_date).label("max_date"),
            )
            .group_by(PriceHistory.security_id)
            .subquery()
        )
        stmt = select(PriceHistory).join(
            subq,
            (PriceHistory.security_id == subq.c.security_id)
            & (PriceHistory.price_date == subq.c.max_date),
        )
        return list(self._s.scalars(stmt).all())
