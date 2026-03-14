"""Observation sync from the core DB into the beta research store."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ...db.models import PriceHistory, PriceTickerSnapshot, Security
from ..context import BetaContext
from ..core_access import core_read_session
from ..db.models import BetaDailyBar, BetaInstrument, BetaIntradaySnapshot


class BetaObservationService:
    """Copy usable daily price history from the core DB into the beta DB."""

    @staticmethod
    def sync_daily_bars() -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"bars_added": 0, "instruments_considered": 0}

        with BetaContext.write_session() as beta_sess, core_read_session() as core_sess:
            beta_instruments = list(beta_sess.scalars(select(BetaInstrument)).all())
            core_securities = list(core_sess.scalars(select(Security)).all())
            core_by_id = {row.id: row for row in core_securities}
            core_by_symbol_exchange = {
                (row.ticker.upper(), str(row.exchange or "").upper()): row for row in core_securities
            }

            bars_added = 0
            bars_updated = 0
            instruments_considered = 0

            for instrument in beta_instruments:
                core_security = None
                if instrument.core_security_id is not None:
                    core_security = core_by_id.get(instrument.core_security_id)
                if core_security is None:
                    core_security = core_by_symbol_exchange.get(
                        (instrument.symbol.upper(), str(instrument.exchange or "").upper())
                    )
                if core_security is None:
                    continue

                instruments_considered += 1
                existing_bars = {
                    row.bar_date: row
                    for row in beta_sess.scalars(
                        select(BetaDailyBar).where(BetaDailyBar.instrument_id == instrument.id)
                    ).all()
                }
                max_synced = max(existing_bars) if existing_bars else None
                duplicate_tail_start = beta_sess.scalar(
                    select(func.min(BetaDailyBar.bar_date)).where(
                        BetaDailyBar.instrument_id == instrument.id,
                        BetaDailyBar.source.like("google_sheets:%"),
                    )
                )
                stmt = (
                    select(PriceHistory)
                    .where(PriceHistory.security_id == core_security.id)
                    .order_by(PriceHistory.price_date)
                )
                if max_synced is not None:
                    floor_date = duplicate_tail_start or max_synced
                    stmt = stmt.where(PriceHistory.price_date >= floor_date)
                price_rows = list(core_sess.scalars(stmt).all())
                latest_rows_by_date = {}
                for row in price_rows:
                    latest_rows_by_date[row.price_date] = row
                for row in latest_rows_by_date.values():
                    if row.close_price_gbp is None:
                        continue
                    existing_bar = existing_bars.get(row.price_date)
                    if existing_bar is None:
                        new_bar = BetaDailyBar(
                            instrument_id=instrument.id,
                            bar_date=row.price_date,
                            close_price_gbp=row.close_price_gbp,
                            close_price_native=row.close_price_original_ccy,
                            currency=row.currency,
                            source=row.source,
                            source_fetched_at=row.fetched_at,
                        )
                        beta_sess.add(new_bar)
                        existing_bars[row.price_date] = new_bar
                        bars_added += 1
                        continue
                    if (
                        existing_bar.close_price_gbp != row.close_price_gbp
                        or existing_bar.close_price_native != row.close_price_original_ccy
                        or existing_bar.currency != row.currency
                        or existing_bar.source != row.source
                        or existing_bar.source_fetched_at != row.fetched_at
                    ):
                        existing_bar.close_price_gbp = row.close_price_gbp
                        existing_bar.close_price_native = row.close_price_original_ccy
                        existing_bar.currency = row.currency
                        existing_bar.source = row.source
                        existing_bar.source_fetched_at = row.fetched_at
                        bars_updated += 1

        return {
            "bars_added": bars_added,
            "bars_updated": bars_updated,
            "instruments_considered": instruments_considered,
        }

    @staticmethod
    def sync_intraday_snapshots() -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"snapshots_added": 0, "instruments_considered": 0}

        with BetaContext.write_session() as beta_sess, core_read_session() as core_sess:
            beta_instruments = list(beta_sess.scalars(select(BetaInstrument)).all())
            core_securities = list(core_sess.scalars(select(Security)).all())
            core_by_id = {row.id: row for row in core_securities}
            core_by_symbol_exchange = {
                (row.ticker.upper(), str(row.exchange or "").upper()): row for row in core_securities
            }

            snapshots_added = 0
            snapshots_updated = 0
            instruments_considered = 0

            for instrument in beta_instruments:
                core_security = None
                if instrument.core_security_id is not None:
                    core_security = core_by_id.get(instrument.core_security_id)
                if core_security is None:
                    core_security = core_by_symbol_exchange.get(
                        (instrument.symbol.upper(), str(instrument.exchange or "").upper())
                    )
                if core_security is None:
                    continue

                instruments_considered += 1
                existing_snapshots = {
                    row.observed_at: row
                    for row in beta_sess.scalars(
                        select(BetaIntradaySnapshot).where(
                            BetaIntradaySnapshot.instrument_id == instrument.id
                        )
                    ).all()
                }
                max_synced = max(existing_snapshots) if existing_snapshots else None
                stmt = (
                    select(PriceTickerSnapshot)
                    .where(PriceTickerSnapshot.security_id == core_security.id)
                    .order_by(PriceTickerSnapshot.observed_at)
                )
                if max_synced is not None:
                    stmt = stmt.where(PriceTickerSnapshot.observed_at >= max_synced)
                snapshot_rows = list(core_sess.scalars(stmt).all())
                latest_rows_by_observed_at = {}
                for row in snapshot_rows:
                    latest_rows_by_observed_at[row.observed_at] = row
                for row in latest_rows_by_observed_at.values():
                    existing_snapshot = existing_snapshots.get(row.observed_at)
                    values = {
                        "instrument_id": instrument.id,
                        "price_date": row.price_date,
                        "price_gbp": row.price_gbp,
                        "price_native": row.price_native,
                        "currency": row.currency,
                        "direction": row.direction,
                        "percent_change": row.percent_change,
                        "source": row.source,
                        "observed_at": row.observed_at,
                    }
                    beta_sess.execute(
                        sqlite_insert(BetaIntradaySnapshot)
                        .values(**values)
                        .on_conflict_do_update(
                            index_elements=["instrument_id", "observed_at"],
                            set_={
                                "price_date": row.price_date,
                                "price_gbp": row.price_gbp,
                                "price_native": row.price_native,
                                "currency": row.currency,
                                "direction": row.direction,
                                "percent_change": row.percent_change,
                                "source": row.source,
                            },
                        )
                    )
                    if existing_snapshot is None:
                        snapshots_added += 1
                        continue
                    if (
                        existing_snapshot.price_date != row.price_date
                        or existing_snapshot.price_gbp != row.price_gbp
                        or existing_snapshot.price_native != row.price_native
                        or existing_snapshot.currency != row.currency
                        or existing_snapshot.direction != row.direction
                        or existing_snapshot.percent_change != row.percent_change
                        or existing_snapshot.source != row.source
                    ):
                        existing_snapshot.price_date = row.price_date
                        existing_snapshot.price_gbp = row.price_gbp
                        existing_snapshot.price_native = row.price_native
                        existing_snapshot.currency = row.currency
                        existing_snapshot.direction = row.direction
                        existing_snapshot.percent_change = row.percent_change
                        existing_snapshot.source = row.source
                        snapshots_updated += 1

        return {
            "snapshots_added": snapshots_added,
            "snapshots_updated": snapshots_updated,
            "instruments_considered": instruments_considered,
        }
