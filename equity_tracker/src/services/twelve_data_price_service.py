"""
Twelve Data intraday quote service with market-aware per-minute rate limiting.

Design goals:
  - Spend calls only while a security's exchange is open.
  - Share a configurable per-minute cap across quote and FX calls.
  - Prioritise larger positions when tracked instruments exceed that cap.
  - Keep write/persistence logic in PriceService; this service only decides
    *what* to refresh and fetches raw provider quotes.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from ..app_context import AppContext
from ..db.models import Lot
from ..db.repository import PriceRepository, SecurityRepository

logger = logging.getLogger(__name__)

_API_BASE_URL = "https://api.twelvedata.com"
_API_TIMEOUT_SECS = 10.0
_DEFAULT_MAX_CALLS_PER_MINUTE = 50
_DEFAULT_EXTENDED_HOURS = False
_QUOTE_SOURCE_PREFIX = "twelvedata:"
_USD_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "ARCA", "IEX", "XNYS", "XNAS"}
_LSE_EXCHANGES = {"LSE", "XLON", "LON"}
_NY_TZ = ZoneInfo("America/New_York")
_LONDON_TZ = ZoneInfo("Europe/London")
_UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class TwelveDataConfig:
    api_key: str
    max_calls_per_minute: int
    extended_hours: bool


@dataclass(frozen=True)
class TwelveDataQuote:
    symbol: str
    exchange: str | None
    currency: str
    close: Decimal
    timestamp_text: str
    price_date: date
    is_market_open: bool | None


@dataclass(frozen=True)
class TwelveDataDailyClose:
    symbol: str
    exchange: str | None
    currency: str
    close: Decimal
    price_date: date


@dataclass(frozen=True)
class MarketWindow:
    is_open: bool
    minutes_until_close: int


@dataclass(frozen=True)
class RefreshCandidate:
    security_id: str
    ticker: str
    exchange: str | None
    weight: Decimal
    is_market_open: bool
    minutes_until_close: int
    last_refreshed_at: datetime | None


@dataclass(frozen=True)
class RefreshPlanItem:
    security_id: str
    ticker: str
    exchange: str | None
    interval_seconds: int
    overdue_score: Decimal


class TwelveDataServiceError(RuntimeError):
    """Raised when the Twelve Data API returns an unusable response."""


def _utc_now() -> datetime:
    return datetime.now(_UTC)


def _normalize_exchange(exchange: str | None) -> str | None:
    cleaned = (exchange or "").strip().upper()
    return cleaned or None


def _normalize_currency(currency: str | None) -> str:
    cleaned = (currency or "").strip().upper()
    if not cleaned:
        return "GBP"
    if cleaned in {"GBX", "GBP", "USD", "EUR", "JPY", "CAD", "AUD", "CHF", "HKD", "SGD", "NOK", "SEK"}:
        return cleaned
    return cleaned


def _parse_decimal(raw_value: object, *, field_name: str) -> Decimal:
    try:
        value = Decimal(str(raw_value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TwelveDataServiceError(f"Invalid Twelve Data field {field_name!r}: {raw_value!r}") from exc
    if value <= Decimal("0"):
        raise TwelveDataServiceError(f"Non-positive Twelve Data field {field_name!r}: {raw_value!r}")
    return value


def _build_symbol(ticker: str, exchange: str | None) -> str:
    cleaned_ticker = ticker.strip().upper()
    cleaned_exchange = _normalize_exchange(exchange)
    if cleaned_exchange:
        return f"{cleaned_ticker}:{cleaned_exchange}"
    return cleaned_ticker


def _build_symbol_map(
    items: Iterable[tuple[str, str | None]],
) -> dict[str, tuple[str, str | None]]:
    symbol_map: dict[str, tuple[str, str | None]] = {}
    for ticker, exchange in items:
        symbol_map[_build_symbol(ticker, exchange)] = (ticker, exchange)
    return symbol_map


def _session_for_exchange(
    exchange: str | None,
    *,
    extended_hours: bool,
) -> tuple[ZoneInfo, time, time] | None:
    cleaned_exchange = _normalize_exchange(exchange)
    if cleaned_exchange in _USD_EXCHANGES:
        if extended_hours:
            return _NY_TZ, time(7, 0), time(20, 0)
        return _NY_TZ, time(9, 30), time(16, 0)
    if cleaned_exchange in _LSE_EXCHANGES:
        return _LONDON_TZ, time(8, 0), time(16, 30)
    return None


def market_window_for_exchange(
    exchange: str | None,
    *,
    now_utc: datetime,
    extended_hours: bool = False,
) -> MarketWindow:
    session = _session_for_exchange(exchange, extended_hours=extended_hours)
    if session is None:
        return MarketWindow(is_open=False, minutes_until_close=0)

    tz, open_time, close_time = session
    local_now = now_utc.astimezone(tz)
    if local_now.weekday() >= 5:
        return MarketWindow(is_open=False, minutes_until_close=0)

    start = datetime.combine(local_now.date(), open_time, tzinfo=tz)
    end = datetime.combine(local_now.date(), close_time, tzinfo=tz)
    if local_now < start or local_now >= end:
        return MarketWindow(is_open=False, minutes_until_close=0)

    minutes_until_close = max(1, math.ceil((end - local_now).total_seconds() / 60))
    return MarketWindow(is_open=True, minutes_until_close=minutes_until_close)


def build_refresh_plan(
    candidates: Iterable[RefreshCandidate],
    *,
    minute_capacity_remaining: int,
    tracked_instrument_count: int,
    max_calls_per_minute: int,
    now_utc: datetime,
) -> list[RefreshPlanItem]:
    all_candidates = list(candidates)
    if not all_candidates or minute_capacity_remaining <= 0:
        return []
    open_refresh_interval_seconds = max(
        60,
        math.ceil((60 * max(1, tracked_instrument_count)) / max(1, max_calls_per_minute)),
    )
    closed_refresh_interval_seconds = 10 * 60

    due_items: list[RefreshPlanItem] = []
    for candidate in all_candidates:
        refresh_interval_seconds = (
            open_refresh_interval_seconds
            if candidate.is_market_open
            else closed_refresh_interval_seconds
        )
        if candidate.last_refreshed_at is None:
            overdue_score = Decimal("999999")
        else:
            last_refreshed_at = candidate.last_refreshed_at
            if last_refreshed_at.tzinfo is None:
                last_refreshed_at = last_refreshed_at.replace(tzinfo=_UTC)
            seconds_since = max(
                Decimal("0"),
                Decimal((now_utc - last_refreshed_at).total_seconds()),
            )
            overdue_score = seconds_since / Decimal(refresh_interval_seconds)

        if overdue_score >= Decimal("1"):
            due_items.append(
                RefreshPlanItem(
                    security_id=candidate.security_id,
                    ticker=candidate.ticker,
                    exchange=candidate.exchange,
                    interval_seconds=refresh_interval_seconds,
                    overdue_score=overdue_score,
                )
            )

    due_items.sort(key=lambda item: (item.overdue_score, item.interval_seconds), reverse=True)
    return due_items[:minute_capacity_remaining]


class _MinuteCounter:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, object]:
        if not self._path.exists():
            return {"minute": _utc_now().replace(second=0, microsecond=0).isoformat(), "used": 0}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {"minute": _utc_now().replace(second=0, microsecond=0).isoformat(), "used": 0}
        if not isinstance(payload, dict):
            return {"minute": _utc_now().replace(second=0, microsecond=0).isoformat(), "used": 0}
        return {
            "minute": str(
                payload.get("minute")
                or _utc_now().replace(second=0, microsecond=0).isoformat()
            ),
            "used": int(payload.get("used") or 0),
        }

    def get_used_this_minute(self) -> int:
        payload = self.load()
        minute_bucket = _utc_now().replace(second=0, microsecond=0).isoformat()
        if payload["minute"] != minute_bucket:
            self._write(minute_bucket, 0)
            return 0
        return int(payload["used"])

    def increment(self, amount: int = 1) -> None:
        minute_bucket = _utc_now().replace(second=0, microsecond=0).isoformat()
        used = self.get_used_this_minute() + amount
        self._write(minute_bucket, used)

    def _write(self, minute_bucket: str, used: int) -> None:
        self._path.write_text(
            json.dumps({"minute": minute_bucket, "used": used}, indent=2),
            encoding="utf-8",
        )


class TwelveDataPriceService:
    @staticmethod
    def load_config() -> TwelveDataConfig | None:
        api_key = os.environ.get("EQUITY_TWELVE_DATA_API_KEY", "").strip()
        if not api_key:
            return None
        max_calls_per_minute = int(
            os.environ.get(
                "EQUITY_TWELVE_DATA_MAX_CALLS_PER_MINUTE",
                str(_DEFAULT_MAX_CALLS_PER_MINUTE),
            )
        )
        extended_hours = os.environ.get("EQUITY_TWELVE_DATA_EXTENDED_HOURS", "false").lower() == "true"
        return TwelveDataConfig(
            api_key=api_key,
            max_calls_per_minute=max(1, max_calls_per_minute),
            extended_hours=extended_hours,
        )

    @staticmethod
    def is_configured() -> bool:
        return TwelveDataPriceService.load_config() is not None

    @staticmethod
    def source_prefix() -> str:
        return _QUOTE_SOURCE_PREFIX

    @staticmethod
    def request_symbol(ticker: str, exchange: str | None) -> str:
        return _build_symbol(ticker, exchange)

    @staticmethod
    def _minute_counter() -> _MinuteCounter | None:
        db_path = os.environ.get("EQUITY_DB_PATH", "").strip()
        if not db_path:
            return None
        return _MinuteCounter(Path(f"{db_path}.twelve_data_rate_limit.json"))

    @staticmethod
    def remaining_minute_capacity(config: TwelveDataConfig) -> int:
        counter = TwelveDataPriceService._minute_counter()
        used = counter.get_used_this_minute() if counter is not None else 0
        return max(0, config.max_calls_per_minute - used)

    @staticmethod
    def fetch_quote(*, ticker: str, exchange: str | None, api_key: str, extended_hours: bool) -> TwelveDataQuote:
        params = {
            "symbol": _build_symbol(ticker, exchange),
            "prepost": "true" if extended_hours else "false",
            "apikey": api_key,
        }
        with httpx.Client(base_url=_API_BASE_URL, timeout=_API_TIMEOUT_SECS) as client:
            response = client.get("/quote", params=params)
            response.raise_for_status()
            payload = response.json()

        if payload.get("status") == "error":
            raise TwelveDataServiceError(payload.get("message") or "Unknown Twelve Data API error.")

        return TwelveDataPriceService._parse_quote_payload(
            payload,
            ticker=ticker,
            exchange=exchange,
        )

    @staticmethod
    def _parse_quote_payload(
        payload: object,
        *,
        ticker: str,
        exchange: str | None,
    ) -> TwelveDataQuote:
        if not isinstance(payload, dict):
            raise TwelveDataServiceError(f"Unexpected Twelve Data quote payload for {ticker!r}.")

        raw_price = payload.get("price")
        raw_close = payload.get("close")
        price_field = raw_price if raw_price not in {None, ""} else raw_close
        field_name = "price" if raw_price not in {None, ""} else "close"
        close = _parse_decimal(price_field, field_name=field_name)
        timestamp_text = str(payload.get("datetime") or "").strip()
        if not timestamp_text:
            raise TwelveDataServiceError("Missing Twelve Data 'datetime' field.")

        try:
            quote_dt = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
        except ValueError:
            try:
                quote_dt = datetime.strptime(timestamp_text, "%Y-%m-%d %H:%M:%S")
            except ValueError as exc:
                raise TwelveDataServiceError(f"Invalid Twelve Data datetime: {timestamp_text!r}") from exc

        return TwelveDataQuote(
            symbol=str(payload.get("symbol") or ticker).strip().upper(),
            exchange=str(payload.get("exchange") or exchange or "").strip().upper() or None,
            currency=_normalize_currency(payload.get("currency")),
            close=close,
            timestamp_text=timestamp_text,
            price_date=quote_dt.date(),
            is_market_open=payload.get("is_market_open"),
        )

    @staticmethod
    def fetch_quotes(
        *,
        items: Iterable[tuple[str, str | None]],
        api_key: str,
        extended_hours: bool,
    ) -> tuple[dict[str, TwelveDataQuote], dict[str, str]]:
        symbol_map = _build_symbol_map(items)
        if not symbol_map:
            return {}, {}

        request_keys = list(symbol_map.keys())
        params = {
            "symbol": ",".join(request_keys),
            "prepost": "true" if extended_hours else "false",
            "apikey": api_key,
        }
        with httpx.Client(base_url=_API_BASE_URL, timeout=_API_TIMEOUT_SECS) as client:
            response = client.get("/quote", params=params)
            response.raise_for_status()
            payload = response.json()

        if isinstance(payload, dict) and payload.get("status") == "error":
            raise TwelveDataServiceError(payload.get("message") or "Unknown Twelve Data API error.")

        results: dict[str, TwelveDataQuote] = {}
        errors: dict[str, str] = {}

        entries: list[tuple[str, object]]
        if isinstance(payload, dict):
            if "symbol" in payload:
                entries = [(request_keys[0], payload)]
            else:
                entries = [(str(key).strip().upper(), value) for key, value in payload.items()]
        elif isinstance(payload, list):
            entries = []
            for idx, item in enumerate(payload):
                if idx >= len(request_keys):
                    break
                entries.append((request_keys[idx], item))
        else:
            raise TwelveDataServiceError("Unexpected Twelve Data batch response.")

        seen_keys: set[str] = set()
        for request_key, item_payload in entries:
            normalized_key = request_key.strip().upper()
            seen_keys.add(normalized_key)
            ticker, exchange = symbol_map.get(normalized_key, (normalized_key, None))
            try:
                quote = TwelveDataPriceService._parse_quote_payload(
                    item_payload,
                    ticker=ticker,
                    exchange=exchange,
                )
                results[normalized_key] = quote
            except TwelveDataServiceError as exc:
                errors[normalized_key] = str(exc)

        for request_key, (ticker, _exchange) in symbol_map.items():
            if request_key not in seen_keys and request_key not in results and request_key not in errors:
                errors[request_key] = "No quote returned from Twelve Data."

        return results, errors

    @staticmethod
    def fetch_daily_closes(
        *,
        ticker: str,
        exchange: str | None,
        api_key: str,
        outputsize: int = 8,
        end_date: date | None = None,
    ) -> list[TwelveDataDailyClose]:
        params = {
            "symbol": _build_symbol(ticker, exchange),
            "interval": "1day",
            "outputsize": max(1, outputsize),
            "apikey": api_key,
        }
        if end_date is not None:
            params["end_date"] = end_date.isoformat()

        with httpx.Client(base_url=_API_BASE_URL, timeout=_API_TIMEOUT_SECS) as client:
            response = client.get("/time_series", params=params)
            response.raise_for_status()
            payload = response.json()

        if not isinstance(payload, dict):
            raise TwelveDataServiceError("Unexpected Twelve Data daily close response.")
        if payload.get("status") == "error":
            raise TwelveDataServiceError(payload.get("message") or "Unknown Twelve Data API error.")

        meta = payload.get("meta") or {}
        values = payload.get("values")
        if not isinstance(values, list):
            raise TwelveDataServiceError("Missing Twelve Data daily close values.")

        closes: list[TwelveDataDailyClose] = []
        for entry in values:
            if not isinstance(entry, dict):
                continue
            close = _parse_decimal(entry.get("close"), field_name="close")
            raw_datetime = str(entry.get("datetime") or "").strip()
            if not raw_datetime:
                continue
            try:
                price_date = datetime.fromisoformat(raw_datetime.replace("Z", "+00:00")).date()
            except ValueError:
                try:
                    price_date = datetime.strptime(raw_datetime, "%Y-%m-%d").date()
                except ValueError:
                    try:
                        price_date = datetime.strptime(raw_datetime, "%Y-%m-%d %H:%M:%S").date()
                    except ValueError as exc:
                        raise TwelveDataServiceError(
                            f"Invalid Twelve Data daily datetime: {raw_datetime!r}"
                        ) from exc
            closes.append(
                TwelveDataDailyClose(
                    symbol=str(meta.get("symbol") or ticker).strip().upper(),
                    exchange=str(meta.get("exchange") or exchange or "").strip().upper() or None,
                    currency=_normalize_currency(meta.get("currency")),
                    close=close,
                    price_date=price_date,
                )
            )
        return closes

    @staticmethod
    def build_scheduler_candidates(config: TwelveDataConfig) -> list[RefreshCandidate]:
        now_utc = _utc_now()
        with AppContext.read_session() as sess:
            securities = SecurityRepository(sess).list_all()
            lots = list(sess.execute(select(Lot)).scalars())
            price_repo = PriceRepository(sess)

            quantity_by_security: dict[str, Decimal] = {}
            for lot in lots:
                try:
                    remaining = Decimal(lot.quantity_remaining)
                except (InvalidOperation, TypeError):
                    continue
                if remaining <= Decimal("0"):
                    continue
                quantity_by_security[lot.security_id] = quantity_by_security.get(lot.security_id, Decimal("0")) + remaining

            candidates: list[RefreshCandidate] = []
            for security in securities:
                quantity = quantity_by_security.get(security.id, Decimal("0"))
                if quantity <= Decimal("0"):
                    continue
                window = market_window_for_exchange(
                    security.exchange,
                    now_utc=now_utc,
                    extended_hours=config.extended_hours,
                )
                latest_price = price_repo.get_latest(security.id)
                latest_snapshot = price_repo.get_latest_ticker_snapshot(security.id)
                try:
                    latest_price_gbp = Decimal(
                        latest_price.close_price_gbp or latest_price.close_price_original_ccy
                    ) if latest_price is not None else Decimal("1")
                except (InvalidOperation, TypeError):
                    latest_price_gbp = Decimal("1")

                weight = quantity * max(latest_price_gbp, Decimal("1"))
                candidates.append(
                    RefreshCandidate(
                        security_id=security.id,
                        ticker=security.ticker,
                        exchange=security.exchange,
                        weight=max(weight, Decimal("1")),
                        is_market_open=window.is_open,
                        minutes_until_close=window.minutes_until_close,
                        last_refreshed_at=latest_snapshot.observed_at if latest_snapshot is not None else None,
                    )
                )

        return candidates

    @staticmethod
    def build_refresh_plan(
        candidates: Iterable[RefreshCandidate],
        *,
        minute_capacity_remaining: int,
        tracked_instrument_count: int,
        max_calls_per_minute: int,
        now_utc: datetime,
    ) -> list[RefreshPlanItem]:
        return build_refresh_plan(
            candidates,
            minute_capacity_remaining=minute_capacity_remaining,
            tracked_instrument_count=tracked_instrument_count,
            max_calls_per_minute=max_calls_per_minute,
            now_utc=now_utc,
        )

    @staticmethod
    def increment_credit_usage(amount: int = 1) -> None:
        counter = TwelveDataPriceService._minute_counter()
        if counter is not None:
            counter.increment(amount)
