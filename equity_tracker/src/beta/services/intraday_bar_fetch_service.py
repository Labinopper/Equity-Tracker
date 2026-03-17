"""Fetch 1-minute OHLCV bars from TwelveData and write directly into BetaMinuteBar.

Live mode (HELD + ACTIVE_THESIS): fetches the latest ~10 bars every supervisor cycle.
EOD mode (GENERAL): fetches the full session (~390 bars) once after market close.

TwelveData returns timestamps in exchange-local time. This service converts them
to UTC-naive before storage, consistent with the existing BetaMinuteBar convention.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import desc, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..context import BetaContext
from ..db.models import BetaInstrument, BetaMinuteBar
from .intraday_priority_service import IntradayPriorityItem

_API_BASE_URL = "https://api.twelvedata.com"
_API_TIMEOUT_SECS = 12.0
_GBP_QUANT = Decimal("0.0001")

# Exchange code → IANA timezone for converting TwelveData local timestamps to UTC
_EXCHANGE_TIMEZONES: dict[str, str] = {
    "LSE": "Europe/London",
    "LON": "Europe/London",
    "XLON": "Europe/London",
    "NYSE": "America/New_York",
    "NASDAQ": "America/New_York",
    "BATS": "America/New_York",
    "CBOE": "America/New_York",
    "AMEX": "America/New_York",
    "ARCA": "America/New_York",
}


def _api_key() -> str | None:
    return os.environ.get("EQUITY_TWELVE_DATA_API_KEY", "").strip() or None


def _as_decimal(value: object) -> Decimal | None:
    try:
        d = Decimal(str(value))
        return d if d > 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _to_gbp(native: Decimal, currency: str, usd_gbp_rate: Decimal | None) -> Decimal | None:
    if currency == "GBP":
        return native.quantize(_GBP_QUANT, rounding=ROUND_HALF_UP)
    if currency in ("GBX", "GBP"):
        return (native / Decimal("100")).quantize(_GBP_QUANT, rounding=ROUND_HALF_UP)
    if currency == "USD" and usd_gbp_rate is not None and usd_gbp_rate > 0:
        return (native * usd_gbp_rate).quantize(_GBP_QUANT, rounding=ROUND_HALF_UP)
    return None


def _parse_bar_datetime(datetime_str: str, exchange: str | None) -> datetime | None:
    """Parse TwelveData datetime string (exchange-local) and return UTC-naive."""
    try:
        naive_dt = datetime.strptime(datetime_str.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            parsed = datetime.fromisoformat(datetime_str.strip())
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).replace(tzinfo=None)
            naive_dt = parsed
        except ValueError:
            return None

    tz_name = _EXCHANGE_TIMEZONES.get(str(exchange or "").strip().upper())
    if tz_name:
        try:
            tz = ZoneInfo(tz_name)
            return naive_dt.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
        except ZoneInfoNotFoundError:
            pass
    return naive_dt


def _build_symbol(ticker: str, exchange: str | None) -> str:
    clean_ticker = str(ticker or "").strip().upper()
    clean_exchange = str(exchange or "").strip().upper()
    return f"{clean_ticker}:{clean_exchange}" if clean_exchange else clean_ticker


def _usd_gbp_rate(*, on_date: date | None = None) -> Decimal | None:
    target_date = on_date or date.today()

    # Primary: derive from price_history where FX conversion has been applied.
    # Use the most recent row on or before the requested date so historical
    # minute-bar backfills do not reuse a single modern FX ratio everywhere.
    try:
        from ...db.models import PriceHistory
        from ..core_access import core_read_session
        with core_read_session() as sess:
            row = sess.scalar(
                select(PriceHistory)
                .where(
                    PriceHistory.currency == "USD",
                    PriceHistory.close_price_gbp.is_not(None),
                    PriceHistory.close_price_original_ccy.is_not(None),
                    PriceHistory.price_date <= target_date,
                )
                .order_by(desc(PriceHistory.price_date))
                .limit(1)
            )
            if row is not None:
                gbp = Decimal(str(row.close_price_gbp))
                native = Decimal(str(row.close_price_original_ccy))
                if native > 0 and gbp > 0:
                    return (gbp / native).quantize(Decimal("0.000001"))
    except Exception:
        pass
    # Fallback: fx_rates table
    try:
        from ...db.models import FxRate
        from ..core_access import core_read_session
        with core_read_session() as sess:
            row = sess.scalar(
                select(FxRate)
                .where(
                    FxRate.base_currency == "USD",
                    FxRate.quote_currency == "GBP",
                    FxRate.rate_date <= target_date,
                )
                .order_by(desc(FxRate.rate_date))
                .limit(1)
            )
            if row is not None and row.rate:
                return Decimal(str(row.rate))
    except Exception:
        pass
    return None


class BetaIntradayBarFetchService:
    """Fetch TwelveData 1-minute bars and write them directly into BetaMinuteBar."""

    @staticmethod
    def backfill_historical_bars(
        *,
        priority_items: list[IntradayPriorityItem],
        target_days: int = 30,
        credits_budget: int = 30,
    ) -> dict[str, object]:
        """Backfill historical 1min bars for HELD and ACTIVE_THESIS instruments.

        For each eligible instrument, checks how many days of twelvedata_1min bars
        already exist and fetches backwards in 5000-bar pages until target_days is
        covered or the credits budget is exhausted. Safe to run periodically — the
        upsert is idempotent and the since_ts guard avoids re-fetching known ranges.
        """
        if not BetaContext.is_initialized():
            return {"bars_written": 0, "instruments_backfilled": 0, "credits_used": 0}

        api_key = _api_key()
        if not api_key:
            return {"bars_written": 0, "instruments_backfilled": 0, "credits_used": 0}

        eligible = [item for item in priority_items if item.tier in {"HELD", "ACTIVE_THESIS"}]
        if not eligible:
            return {"bars_written": 0, "instruments_backfilled": 0, "credits_used": 0}

        bars_written = 0
        credits_used = 0
        instruments_backfilled = 0
        errors: list[str] = []
        usd_rate_cache: dict[date, Decimal | None] = {}
        cutoff_dt = datetime.utcnow().replace(microsecond=0) - timedelta(days=target_days)

        with BetaContext.write_session() as sess:
            instruments = {
                row.id: row
                for row in sess.scalars(
                    select(BetaInstrument).where(
                        BetaInstrument.id.in_([item.instrument_id for item in eligible])
                    )
                ).all()
            }

            for item in eligible:
                if credits_used >= credits_budget:
                    break
                instrument = instruments.get(item.instrument_id)
                if instrument is None:
                    continue

                # Find the oldest twelvedata_1min bar we already have
                oldest_bar = sess.scalar(
                    select(BetaMinuteBar)
                    .where(
                        BetaMinuteBar.instrument_id == instrument.id,
                        BetaMinuteBar.source.like("twelvedata_1min%"),
                    )
                    .order_by(BetaMinuteBar.minute_ts.asc())
                    .limit(1)
                )
                oldest_ts = oldest_bar.minute_ts if oldest_bar is not None else None

                # Already covered back to target window — skip
                if oldest_ts is not None and oldest_ts <= cutoff_dt:
                    continue

                symbol = _build_symbol(instrument.symbol, instrument.exchange)
                currency = str(instrument.currency or "GBP").strip().upper()
                instrument_bars = 0

                # Page backwards until we reach target_days or exhaust budget
                end_date: str | None = None
                if oldest_ts is not None:
                    # Fetch the page before the oldest bar we have
                    end_date = (oldest_ts - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")

                while credits_used < credits_budget:
                    params: dict[str, str] = {
                        "symbol": symbol,
                        "interval": "1min",
                        "outputsize": "5000",
                        "apikey": api_key,
                    }
                    if end_date is not None:
                        params["end_date"] = end_date

                    try:
                        raw_bars = BetaIntradayBarFetchService._fetch_bars_with_params(params)
                        credits_used += 1
                    except Exception as exc:
                        errors.append(f"{symbol}: {exc}")
                        break

                    if not raw_bars:
                        break

                    page_oldest: datetime | None = None
                    for bar in raw_bars:
                        bar_ts = _parse_bar_datetime(str(bar.get("datetime", "")), instrument.exchange)
                        if bar_ts is None:
                            continue
                        bar_date = bar_ts.date()
                        if bar_date not in usd_rate_cache:
                            usd_rate_cache[bar_date] = _usd_gbp_rate(on_date=bar_date)
                        written = BetaIntradayBarFetchService._upsert_bar(
                            sess=sess,
                            instrument_id=instrument.id,
                            bar=bar,
                            bar_ts=bar_ts,
                            currency=currency,
                            usd_gbp_rate=usd_rate_cache[bar_date],
                            source="twelvedata_1min_historical",
                        )
                        bars_written += written
                        instrument_bars += written
                        if page_oldest is None or bar_ts < page_oldest:
                            page_oldest = bar_ts

                    if page_oldest is None or page_oldest <= cutoff_dt:
                        break  # Covered the target window

                    # Advance end_date for next page
                    end_date = (page_oldest - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")

                if instrument_bars > 0:
                    instruments_backfilled += 1

        result = {
            "bars_written": bars_written,
            "instruments_backfilled": instruments_backfilled,
            "credits_used": credits_used,
        }
        if errors:
            result["errors_count"] = len(errors)
            result["errors"] = errors[:5]
        return result

    @staticmethod
    def fetch_live_bars(
        *,
        priority_items: list[IntradayPriorityItem],
        credits_budget: int = 30,
    ) -> dict[str, int]:
        """Fetch latest 1min bars for HELD and ACTIVE_THESIS items (called every cycle)."""
        if not BetaContext.is_initialized():
            return {"bars_written": 0, "instruments_fetched": 0, "credits_used": 0}

        api_key = _api_key()
        if not api_key:
            return {"bars_written": 0, "instruments_fetched": 0, "credits_used": 0}

        eligible = [item for item in priority_items if item.tier in {"HELD", "ACTIVE_THESIS"}]
        if not eligible:
            return {"bars_written": 0, "instruments_fetched": 0, "credits_used": 0}

        eligible = eligible[:credits_budget]
        usd_rate_cache: dict[date, Decimal | None] = {}
        bars_written = 0
        credits_used = 0

        with BetaContext.write_session() as sess:
            instruments = {
                row.id: row
                for row in sess.scalars(
                    select(BetaInstrument).where(
                        BetaInstrument.id.in_([item.instrument_id for item in eligible])
                    )
                ).all()
            }

            for item in eligible:
                if credits_used >= credits_budget:
                    break
                instrument = instruments.get(item.instrument_id)
                if instrument is None:
                    continue

                latest_bar = sess.scalar(
                    select(BetaMinuteBar)
                    .where(
                        BetaMinuteBar.instrument_id == instrument.id,
                        BetaMinuteBar.source.like("twelvedata_1min%"),
                    )
                    .order_by(desc(BetaMinuteBar.minute_ts))
                    .limit(1)
                )
                symbol = _build_symbol(instrument.symbol, instrument.exchange)
                try:
                    raw_bars = BetaIntradayBarFetchService._fetch_bars(
                        symbol=symbol,
                        outputsize=10,
                        api_key=api_key,
                    )
                    credits_used += 1
                except Exception:
                    continue

                currency = str(instrument.currency or "GBP").strip().upper()
                since_ts = latest_bar.minute_ts if latest_bar is not None else None

                for bar in raw_bars:
                    bar_ts = _parse_bar_datetime(str(bar.get("datetime", "")), instrument.exchange)
                    if bar_ts is None:
                        continue
                    if since_ts is not None and bar_ts <= since_ts:
                        continue
                    bar_date = bar_ts.date()
                    if bar_date not in usd_rate_cache:
                        usd_rate_cache[bar_date] = _usd_gbp_rate(on_date=bar_date)
                    bars_written += BetaIntradayBarFetchService._upsert_bar(
                        sess=sess,
                        instrument_id=instrument.id,
                        bar=bar,
                        bar_ts=bar_ts,
                        currency=currency,
                        usd_gbp_rate=usd_rate_cache[bar_date],
                        source="twelvedata_1min_live",
                    )

        return {"bars_written": bars_written, "instruments_fetched": len(eligible), "credits_used": credits_used}

    @staticmethod
    def fetch_eod_bars(
        *,
        priority_items: list[IntradayPriorityItem],
        session_date: date | None = None,
        credits_budget: int = 20,
    ) -> dict[str, int]:
        """Fetch full-session 1min bars for GENERAL tier (called once per day after close)."""
        if not BetaContext.is_initialized():
            return {"bars_written": 0, "instruments_fetched": 0, "credits_used": 0}

        api_key = _api_key()
        if not api_key:
            return {"bars_written": 0, "instruments_fetched": 0, "credits_used": 0}

        general_items = [item for item in priority_items if item.tier == "GENERAL"]
        if not general_items:
            return {"bars_written": 0, "instruments_fetched": 0, "credits_used": 0}

        general_items = general_items[:credits_budget]
        usd_rate_cache: dict[date, Decimal | None] = {}
        bars_written = 0
        credits_used = 0

        with BetaContext.write_session() as sess:
            instruments = {
                row.id: row
                for row in sess.scalars(
                    select(BetaInstrument).where(
                        BetaInstrument.id.in_([item.instrument_id for item in general_items])
                    )
                ).all()
            }

            for item in general_items:
                if credits_used >= credits_budget:
                    break
                instrument = instruments.get(item.instrument_id)
                if instrument is None:
                    continue

                symbol = _build_symbol(instrument.symbol, instrument.exchange)
                try:
                    raw_bars = BetaIntradayBarFetchService._fetch_bars(
                        symbol=symbol,
                        outputsize=390,
                        api_key=api_key,
                    )
                    credits_used += 1
                except Exception:
                    continue

                currency = str(instrument.currency or "GBP").strip().upper()

                for bar in raw_bars:
                    bar_ts = _parse_bar_datetime(str(bar.get("datetime", "")), instrument.exchange)
                    if bar_ts is None:
                        continue
                    if session_date is not None and bar_ts.date() != session_date:
                        continue
                    bar_date = bar_ts.date()
                    if bar_date not in usd_rate_cache:
                        usd_rate_cache[bar_date] = _usd_gbp_rate(on_date=bar_date)
                    bars_written += BetaIntradayBarFetchService._upsert_bar(
                        sess=sess,
                        instrument_id=instrument.id,
                        bar=bar,
                        bar_ts=bar_ts,
                        currency=currency,
                        usd_gbp_rate=usd_rate_cache[bar_date],
                        source="twelvedata_1min_eod",
                    )

        return {"bars_written": bars_written, "instruments_fetched": len(general_items), "credits_used": credits_used}

    @staticmethod
    def _fetch_bars(*, symbol: str, outputsize: int, api_key: str) -> list[dict]:
        params = {
            "symbol": symbol,
            "interval": "1min",
            "outputsize": str(outputsize),
            "apikey": api_key,
        }
        return BetaIntradayBarFetchService._fetch_bars_with_params(params)

    @staticmethod
    def _fetch_bars_with_params(params: dict[str, str]) -> list[dict]:
        with httpx.Client(base_url=_API_BASE_URL, timeout=_API_TIMEOUT_SECS) as client:
            response = client.get("/time_series", params=params)
            response.raise_for_status()
            payload = response.json()

        if isinstance(payload, dict) and payload.get("status") == "error":
            raise RuntimeError(f"TwelveData error: {payload.get('message')}")

        values = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            return []
        return [v for v in values if isinstance(v, dict) and "datetime" in v]

    @staticmethod
    def _upsert_bar(
        *,
        sess,
        instrument_id: str,
        bar: dict,
        bar_ts: datetime,
        currency: str,
        usd_gbp_rate: Decimal | None,
        source: str,
    ) -> int:
        open_n = _as_decimal(bar.get("open"))
        high_n = _as_decimal(bar.get("high"))
        low_n = _as_decimal(bar.get("low"))
        close_n = _as_decimal(bar.get("close"))
        if open_n is None or high_n is None or low_n is None or close_n is None:
            return 0

        open_gbp = _to_gbp(open_n, currency, usd_gbp_rate)
        high_gbp = _to_gbp(high_n, currency, usd_gbp_rate)
        low_gbp = _to_gbp(low_n, currency, usd_gbp_rate)
        close_gbp = _to_gbp(close_n, currency, usd_gbp_rate)
        if open_gbp is None or high_gbp is None or low_gbp is None or close_gbp is None:
            return 0

        floored_ts = bar_ts.replace(second=0, microsecond=0)
        volume_str = str(bar["volume"]) if bar.get("volume") not in (None, "", "0") else None

        values = {
            "instrument_id": instrument_id,
            "session_date": floored_ts.date(),
            "minute_ts": floored_ts,
            "open_price_gbp": str(open_gbp),
            "high_price_gbp": str(high_gbp),
            "low_price_gbp": str(low_gbp),
            "close_price_gbp": str(close_gbp),
            "close_price_native": str(close_n),
            "currency": currency,
            "volume_native": volume_str,
            "snapshot_count": 1,
            "first_snapshot_at": floored_ts,
            "last_snapshot_at": floored_ts,
            "source": source,
        }
        sess.execute(
            sqlite_insert(BetaMinuteBar)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["instrument_id", "minute_ts"],
                set_={
                    "open_price_gbp": values["open_price_gbp"],
                    "high_price_gbp": values["high_price_gbp"],
                    "low_price_gbp": values["low_price_gbp"],
                    "close_price_gbp": values["close_price_gbp"],
                    "close_price_native": values["close_price_native"],
                    "volume_native": values["volume_native"],
                    "snapshot_count": values["snapshot_count"],
                    "source": values["source"],
                },
            )
        )
        return 1
