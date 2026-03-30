"""Aggregate raw intraday snapshots into bounded minute bars."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import desc, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..context import BetaContext
from ..db.models import BetaInstrument, BetaIntradaySnapshot, BetaMinuteBar


def _as_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _floor_minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


class BetaIntradayAggregationService:
    """Create per-minute bars incrementally from stored intraday snapshots."""

    @staticmethod
    def aggregate_minute_bars(
        *,
        instrument_ids: list[str],
        lookback_minutes: int,
    ) -> dict[str, int]:
        if not BetaContext.is_initialized() or not instrument_ids:
            return {"minute_bars_written": 0, "instruments_considered": 0}

        with BetaContext.write_session() as sess:
            instruments = {
                row.id: row
                for row in sess.scalars(
                    select(BetaInstrument).where(BetaInstrument.id.in_(instrument_ids))
                ).all()
            }
            latest_bar_rows = list(
                sess.execute(
                    select(BetaMinuteBar.instrument_id, BetaMinuteBar.minute_ts)
                    .where(BetaMinuteBar.instrument_id.in_(instrument_ids))
                    .order_by(BetaMinuteBar.instrument_id, BetaMinuteBar.minute_ts.desc())
                ).all()
            )
            latest_minute_by_instrument: dict[str, datetime] = {}
            for instrument_id, minute_ts in latest_bar_rows:
                if instrument_id not in latest_minute_by_instrument:
                    latest_minute_by_instrument[instrument_id] = minute_ts
            latest_snapshot_rows = list(
                sess.execute(
                    select(BetaIntradaySnapshot.instrument_id, BetaIntradaySnapshot.observed_at)
                    .where(BetaIntradaySnapshot.instrument_id.in_(instrument_ids))
                    .order_by(BetaIntradaySnapshot.instrument_id, desc(BetaIntradaySnapshot.observed_at))
                ).all()
            )
            latest_snapshot_by_instrument: dict[str, datetime] = {}
            for instrument_id, observed_at in latest_snapshot_rows:
                if instrument_id not in latest_snapshot_by_instrument:
                    latest_snapshot_by_instrument[instrument_id] = observed_at

            minute_bars_written = 0
            for instrument_id in instrument_ids:
                instrument = instruments.get(instrument_id)
                if instrument is None:
                    continue
                floor_cutoff = latest_minute_by_instrument.get(instrument_id)
                if floor_cutoff is None:
                    latest_snapshot_at = latest_snapshot_by_instrument.get(instrument_id)
                    if latest_snapshot_at is None:
                        continue
                    floor_cutoff = latest_snapshot_at - timedelta(minutes=max(30, lookback_minutes))
                snapshot_rows = list(
                    sess.scalars(
                        select(BetaIntradaySnapshot)
                        .where(
                            BetaIntradaySnapshot.instrument_id == instrument_id,
                            BetaIntradaySnapshot.observed_at >= floor_cutoff,
                        )
                        .order_by(BetaIntradaySnapshot.observed_at.asc())
                    ).all()
                )
                grouped: dict[datetime, list[BetaIntradaySnapshot]] = defaultdict(list)
                for row in snapshot_rows:
                    grouped[_floor_minute(row.observed_at)].append(row)
                for minute_ts, rows in grouped.items():
                    prices = []
                    for row in rows:
                        price = _as_decimal(row.price_gbp)
                        if price is None or price <= 0:
                            continue
                        prices.append(price)
                    if not prices:
                        continue
                    rows = sorted(rows, key=lambda item: item.observed_at)
                    first_row = rows[0]
                    last_row = rows[-1]
                    values = {
                        "instrument_id": instrument_id,
                        "session_date": minute_ts.date(),
                        "minute_ts": minute_ts,
                        "open_price_gbp": str(prices[0]),
                        "high_price_gbp": str(max(prices)),
                        "low_price_gbp": str(min(prices)),
                        "close_price_gbp": str(prices[-1]),
                        "close_price_native": last_row.price_native,
                        "currency": last_row.currency,
                        "snapshot_count": len(rows),
                        "first_snapshot_at": first_row.observed_at,
                        "last_snapshot_at": last_row.observed_at,
                        "source": last_row.source,
                    }
                    sess.execute(
                        sqlite_insert(BetaMinuteBar)
                        .values(**values)
                        .on_conflict_do_update(
                            index_elements=["instrument_id", "minute_ts"],
                            set_={
                                "session_date": values["session_date"],
                                "open_price_gbp": values["open_price_gbp"],
                                "high_price_gbp": values["high_price_gbp"],
                                "low_price_gbp": values["low_price_gbp"],
                                "close_price_gbp": values["close_price_gbp"],
                                "close_price_native": values["close_price_native"],
                                "currency": values["currency"],
                                "snapshot_count": values["snapshot_count"],
                                "first_snapshot_at": values["first_snapshot_at"],
                                "last_snapshot_at": values["last_snapshot_at"],
                                "source": values["source"],
                            },
                        )
                    )
                    minute_bars_written += 1

            return {
                "minute_bars_written": minute_bars_written,
                "instruments_considered": len(instrument_ids),
            }
