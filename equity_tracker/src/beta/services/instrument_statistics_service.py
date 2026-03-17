"""Fetch and cache per-instrument statistics from TwelveData (weekly cadence).

Statistics include avg volume, 52-week high/low, beta, P/E, market cap.
These are used as normalising context for hypothesis features and signals.
Fetched at most once per week per instrument to minimise credit usage.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from ..context import BetaContext
from ..db.models import BetaInstrument, BetaInstrumentStatistics

_API_BASE_URL = "https://api.twelvedata.com"
_API_TIMEOUT_SECS = 12.0
_DEFAULT_REFRESH_DAYS = 7


def _api_key() -> str | None:
    return os.environ.get("EQUITY_TWELVE_DATA_API_KEY", "").strip() or None


def _safe_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class BetaInstrumentStatisticsService:
    """Fetch and store per-instrument statistics with a weekly refresh cadence."""

    @staticmethod
    def refresh_stale_statistics(
        *,
        instrument_ids: list[str] | None = None,
        max_staleness_days: int = _DEFAULT_REFRESH_DAYS,
        credits_budget: int = 20,
    ) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"refreshed": 0, "skipped": 0, "credits_used": 0}

        api_key = _api_key()
        if not api_key:
            return {"refreshed": 0, "skipped": 0, "credits_used": 0}

        stale_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=max_staleness_days)
        credits_used = 0
        refreshed = 0
        skipped = 0

        with BetaContext.write_session() as sess:
            q = select(BetaInstrument).where(BetaInstrument.is_active == True)  # noqa: E712
            if instrument_ids:
                q = q.where(BetaInstrument.id.in_(instrument_ids))
            instruments = list(sess.scalars(q).all())

            existing = {
                row.instrument_id: row
                for row in sess.scalars(
                    select(BetaInstrumentStatistics).where(
                        BetaInstrumentStatistics.instrument_id.in_([i.id for i in instruments])
                    )
                ).all()
            }

            for instrument in instruments:
                if credits_used >= credits_budget:
                    break

                current = existing.get(instrument.id)
                if current is not None and current.refreshed_at > stale_cutoff:
                    skipped += 1
                    continue

                symbol = (
                    f"{instrument.symbol}:{instrument.exchange}"
                    if instrument.exchange
                    else instrument.symbol
                )
                try:
                    payload = BetaInstrumentStatisticsService._fetch(symbol, api_key)
                    credits_used += 1
                except Exception:
                    continue

                stats = payload.get("statistics", {})
                valuation = stats.get("valuations_metrics", {})
                stock_stats = stats.get("stock_statistics", {})
                price_summary = stats.get("stock_price_summary", {})

                if current is None:
                    current = BetaInstrumentStatistics(instrument_id=instrument.id)
                    sess.add(current)

                current.statistics_json = json.dumps(stats, sort_keys=True)
                current.avg_10_volume = _safe_float(stock_stats.get("avg_10_volume"))
                current.avg_90_volume = _safe_float(stock_stats.get("avg_90_volume"))
                current.fifty_two_week_high = _safe_float(price_summary.get("fifty_two_week_high"))
                current.fifty_two_week_low = _safe_float(price_summary.get("fifty_two_week_low"))
                current.beta_coefficient = _safe_float(price_summary.get("beta"))
                current.trailing_pe = _safe_float(valuation.get("trailing_pe"))
                current.market_cap = _safe_float(valuation.get("market_capitalization"))
                current.refreshed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                refreshed += 1

        return {"refreshed": refreshed, "skipped": skipped, "credits_used": credits_used}

    @staticmethod
    def get_statistics(instrument_id: str) -> BetaInstrumentStatistics | None:
        if not BetaContext.is_initialized():
            return None
        with BetaContext.read_session() as sess:
            return sess.scalar(
                select(BetaInstrumentStatistics)
                .where(BetaInstrumentStatistics.instrument_id == instrument_id)
                .limit(1)
            )

    @staticmethod
    def _fetch(symbol: str, api_key: str) -> dict:
        with httpx.Client(base_url=_API_BASE_URL, timeout=_API_TIMEOUT_SECS) as client:
            response = client.get("/statistics", params={"symbol": symbol, "apikey": api_key})
            response.raise_for_status()
            payload = response.json()
        if isinstance(payload, dict) and payload.get("status") == "error":
            raise RuntimeError(f"TwelveData error: {payload.get('message')}")
        return payload if isinstance(payload, dict) else {}
