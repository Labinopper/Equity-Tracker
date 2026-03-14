"""Observation sync from the core DB into the beta research store."""

from __future__ import annotations

from sqlalchemy import func, select

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
                max_synced = beta_sess.scalar(
                    select(func.max(BetaDailyBar.bar_date)).where(BetaDailyBar.instrument_id == instrument.id)
                )
                stmt = (
                    select(PriceHistory)
                    .where(PriceHistory.security_id == core_security.id)
                    .order_by(PriceHistory.price_date)
                )
                if max_synced is not None:
                    stmt = stmt.where(PriceHistory.price_date > max_synced)
                price_rows = list(core_sess.scalars(stmt).all())
                for row in price_rows:
                    if row.close_price_gbp is None:
                        continue
                    beta_sess.add(
                        BetaDailyBar(
                            instrument_id=instrument.id,
                            bar_date=row.price_date,
                            close_price_gbp=row.close_price_gbp,
                            close_price_native=row.close_price_original_ccy,
                            currency=row.currency,
                            source=row.source,
                            source_fetched_at=row.fetched_at,
                        )
                    )
                    bars_added += 1

        return {
            "bars_added": bars_added,
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
                max_synced = beta_sess.scalar(
                    select(func.max(BetaIntradaySnapshot.observed_at)).where(
                        BetaIntradaySnapshot.instrument_id == instrument.id
                    )
                )
                stmt = (
                    select(PriceTickerSnapshot)
                    .where(PriceTickerSnapshot.security_id == core_security.id)
                    .order_by(PriceTickerSnapshot.observed_at)
                )
                if max_synced is not None:
                    stmt = stmt.where(PriceTickerSnapshot.observed_at > max_synced)
                snapshot_rows = list(core_sess.scalars(stmt).all())
                for row in snapshot_rows:
                    beta_sess.add(
                        BetaIntradaySnapshot(
                            instrument_id=instrument.id,
                            price_date=row.price_date,
                            price_gbp=row.price_gbp,
                            price_native=row.price_native,
                            currency=row.currency,
                            direction=row.direction,
                            percent_change=row.percent_change,
                            source=row.source,
                            observed_at=row.observed_at,
                        )
                    )
                    snapshots_added += 1

        return {
            "snapshots_added": snapshots_added,
            "instruments_considered": instruments_considered,
        }
