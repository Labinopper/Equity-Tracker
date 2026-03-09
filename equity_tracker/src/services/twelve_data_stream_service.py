from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import websockets

from .fx_service import FxService
from .portfolio_service import PortfolioService
from .price_service import PriceService
from .twelve_data_price_service import TwelveDataPriceService

logger = logging.getLogger(__name__)

_STREAM_URL = "wss://ws.twelvedata.com/v1/quotes/price"
_DEFAULT_MAX_STREAMS = 8
_DEFAULT_REBALANCE_SECONDS = 60
_DEFAULT_QUOTE_STALE_SECONDS = 30
_DEFAULT_REJECTION_COOLDOWN_HOURS = 24
_WS_SOURCE_PREFIX = "twelvedata_ws:"
_ELIGIBILITY_META_SUFFIX = ".twelve_data_ws_eligibility.json"


@dataclass(frozen=True)
class _StreamCandidate:
    request_symbol: str
    kind: str
    weight: Decimal
    security_id: str | None = None
    ticker: str | None = None
    exchange: str | None = None
    quote_currency: str | None = None
    from_currency: str | None = None
    to_currency: str | None = None


class TwelveDataStreamService:
    _lock = asyncio.Lock()
    _active_security_ids: set[str] = set()
    _active_symbols: set[str] = set()
    _symbol_meta: dict[str, _StreamCandidate] = {}
    _desired_symbols: set[str] = set()
    _last_error: str | None = None
    _last_message_at: datetime | None = None
    _connected: bool = False
    _eligibility_cache: dict[str, dict[str, str | None]] | None = None

    @staticmethod
    def is_enabled() -> bool:
        raw = os.environ.get("EQUITY_TWELVE_DATA_WS_ENABLED", "true").strip().lower()
        return raw not in {"0", "false", "no"}

    @staticmethod
    def max_streams() -> int:
        try:
            return max(1, int(os.environ.get("EQUITY_TWELVE_DATA_WS_MAX_SYMBOLS", str(_DEFAULT_MAX_STREAMS))))
        except ValueError:
            return _DEFAULT_MAX_STREAMS

    @staticmethod
    def rebalance_seconds() -> int:
        try:
            return max(15, int(os.environ.get("EQUITY_TWELVE_DATA_WS_REBALANCE_SECONDS", str(_DEFAULT_REBALANCE_SECONDS))))
        except ValueError:
            return _DEFAULT_REBALANCE_SECONDS

    @staticmethod
    def stale_seconds() -> int:
        try:
            return max(5, int(os.environ.get("EQUITY_TWELVE_DATA_WS_STALE_SECONDS", str(_DEFAULT_QUOTE_STALE_SECONDS))))
        except ValueError:
            return _DEFAULT_QUOTE_STALE_SECONDS

    @staticmethod
    def rejection_cooldown() -> timedelta:
        try:
            hours = max(
                1,
                int(
                    os.environ.get(
                        "EQUITY_TWELVE_DATA_WS_REJECTION_COOLDOWN_HOURS",
                        str(_DEFAULT_REJECTION_COOLDOWN_HOURS),
                    )
                ),
            )
        except ValueError:
            hours = _DEFAULT_REJECTION_COOLDOWN_HOURS
        return timedelta(hours=hours)

    @staticmethod
    def _meta_path() -> Path | None:
        db_path = os.environ.get("EQUITY_DB_PATH", "").strip()
        if not db_path:
            return None
        return Path(f"{db_path}{_ELIGIBILITY_META_SUFFIX}")

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_iso_datetime(raw_value: object) -> datetime | None:
        text = str(raw_value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _load_eligibility_cache() -> dict[str, dict[str, str | None]]:
        if TwelveDataStreamService._eligibility_cache is not None:
            return TwelveDataStreamService._eligibility_cache

        path = TwelveDataStreamService._meta_path()
        if path is None or not path.exists():
            TwelveDataStreamService._eligibility_cache = {}
            return TwelveDataStreamService._eligibility_cache

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        cache: dict[str, dict[str, str | None]] = {}
        for key, value in payload.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            cache[key.upper()] = {
                "last_failed_at": str(value.get("last_failed_at") or "") or None,
                "last_failure_reason": str(value.get("last_failure_reason") or "") or None,
                "last_succeeded_at": str(value.get("last_succeeded_at") or "") or None,
                "eligible_after": str(value.get("eligible_after") or "") or None,
            }
        TwelveDataStreamService._eligibility_cache = cache
        return cache

    @staticmethod
    def _save_eligibility_cache() -> None:
        path = TwelveDataStreamService._meta_path()
        if path is None:
            return
        cache = TwelveDataStreamService._load_eligibility_cache()
        path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _eligibility_record(symbol: str) -> dict[str, str | None]:
        cache = TwelveDataStreamService._load_eligibility_cache()
        return cache.setdefault(
            symbol.upper(),
            {
                "last_failed_at": None,
                "last_failure_reason": None,
                "last_succeeded_at": None,
                "eligible_after": None,
            },
        )

    @staticmethod
    def _is_symbol_eligible(symbol: str, *, now_utc: datetime | None = None) -> bool:
        now_utc = now_utc or TwelveDataStreamService._utc_now()
        record = TwelveDataStreamService._load_eligibility_cache().get(symbol.upper())
        if not record:
            return True
        eligible_after = TwelveDataStreamService._parse_iso_datetime(record.get("eligible_after"))
        if eligible_after is None:
            return True
        return now_utc >= eligible_after

    @staticmethod
    def _mark_subscription_success(symbol: str, *, now_utc: datetime | None = None) -> None:
        now_utc = now_utc or TwelveDataStreamService._utc_now()
        record = TwelveDataStreamService._eligibility_record(symbol)
        record["last_succeeded_at"] = now_utc.isoformat()
        record["last_failed_at"] = None
        record["last_failure_reason"] = None
        record["eligible_after"] = None
        TwelveDataStreamService._save_eligibility_cache()

    @staticmethod
    def _mark_subscription_failure(
        symbol: str,
        *,
        reason: str,
        now_utc: datetime | None = None,
    ) -> None:
        now_utc = now_utc or TwelveDataStreamService._utc_now()
        record = TwelveDataStreamService._eligibility_record(symbol)
        record["last_failed_at"] = now_utc.isoformat()
        record["last_failure_reason"] = reason
        record["eligible_after"] = (now_utc + TwelveDataStreamService.rejection_cooldown()).isoformat()
        TwelveDataStreamService._save_eligibility_cache()

    @staticmethod
    def _eligibility_summary(now_utc: datetime | None = None) -> list[dict[str, str | None]]:
        now_utc = now_utc or TwelveDataStreamService._utc_now()
        rows: list[dict[str, str | None]] = []
        for symbol, record in sorted(TwelveDataStreamService._load_eligibility_cache().items()):
            eligible_after = TwelveDataStreamService._parse_iso_datetime(record.get("eligible_after"))
            status = "eligible"
            if eligible_after is not None and eligible_after > now_utc:
                status = "cooldown"
            elif record.get("last_succeeded_at"):
                status = "eligible"
            elif record.get("last_failed_at"):
                status = "retry_due"
            rows.append(
                {
                    "symbol": symbol,
                    "status": status,
                    "last_failed_at": record.get("last_failed_at"),
                    "last_failure_reason": record.get("last_failure_reason"),
                    "last_succeeded_at": record.get("last_succeeded_at"),
                    "eligible_after": record.get("eligible_after"),
                }
            )
        return rows

    @staticmethod
    def current_streamed_security_ids() -> set[str]:
        return set(TwelveDataStreamService._active_security_ids)

    @staticmethod
    def _refresh_active_security_ids() -> None:
        TwelveDataStreamService._active_security_ids = {
            candidate.security_id
            for symbol, candidate in TwelveDataStreamService._symbol_meta.items()
            if symbol in TwelveDataStreamService._active_symbols and candidate.security_id is not None
        }

    @staticmethod
    def health_snapshot() -> dict[str, object]:
        subscribed = bool(TwelveDataStreamService._active_symbols)
        transport_connected = bool(TwelveDataStreamService._connected)
        rejected_rows = [
            row for row in TwelveDataStreamService._eligibility_summary()
            if row["status"] in {"cooldown", "retry_due"}
        ]
        last_successful_subscription = sorted(
            symbol
            for symbol, record in TwelveDataStreamService._load_eligibility_cache().items()
            if record.get("last_succeeded_at")
        )
        if not TwelveDataStreamService.is_enabled():
            status = "disabled"
        elif subscribed and rejected_rows:
            status = "partial_streaming"
        elif subscribed:
            status = "streaming"
        elif rejected_rows:
            status = "polling_fallback"
        elif transport_connected:
            status = "connected_idle"
        else:
            status = "disconnected"
        return {
            "enabled": TwelveDataStreamService.is_enabled(),
            "connected": transport_connected,
            "subscribed": subscribed,
            "status": status,
            "symbols": sorted(TwelveDataStreamService._active_symbols),
            "desired_symbols": sorted(TwelveDataStreamService._desired_symbols),
            "rejected_symbols": rejected_rows,
            "last_successful_subscription": last_successful_subscription,
            "last_error": TwelveDataStreamService._last_error,
            "last_message_at": (
                TwelveDataStreamService._last_message_at.isoformat()
                if TwelveDataStreamService._last_message_at is not None
                else None
            ),
        }

    @staticmethod
    def _candidate_weight(summary) -> Decimal:
        return (
            summary.market_value_gbp
            or summary.total_true_cost_gbp
            or summary.total_cost_basis_gbp
            or Decimal("0")
        )

    @staticmethod
    def _build_candidates() -> list[_StreamCandidate]:
        summary = PortfolioService.get_portfolio_summary(as_of=date.today())
        candidates: list[_StreamCandidate] = []
        currency_weights: dict[str, Decimal] = {}

        for security_summary in summary.securities:
            if security_summary.total_quantity <= Decimal("0"):
                continue
            weight = TwelveDataStreamService._candidate_weight(security_summary)
            security = security_summary.security
            request_symbol = TwelveDataPriceService.request_symbol(
                security.ticker,
                security.exchange,
            )
            candidates.append(
                _StreamCandidate(
                    request_symbol=request_symbol,
                    kind="security",
                    weight=weight,
                    security_id=security.id,
                    ticker=security.ticker,
                    exchange=security.exchange,
                    quote_currency=(security.currency or "GBP").strip().upper(),
                )
            )

            quote_currency = (security.currency or "GBP").strip().upper()
            if quote_currency != "GBP":
                currency_weights[quote_currency] = currency_weights.get(quote_currency, Decimal("0")) + weight

        for currency_code, weight in currency_weights.items():
            candidates.append(
                _StreamCandidate(
                    request_symbol=f"{currency_code}/GBP",
                    kind="fx",
                    weight=weight,
                    from_currency=currency_code,
                    to_currency="GBP",
                )
            )

        now_utc = TwelveDataStreamService._utc_now()
        candidates.sort(key=lambda item: (item.weight, item.request_symbol), reverse=True)
        seen: set[str] = set()
        selected: list[_StreamCandidate] = []
        for candidate in candidates:
            if candidate.request_symbol in seen:
                continue
            seen.add(candidate.request_symbol)
            if not TwelveDataStreamService._is_symbol_eligible(candidate.request_symbol, now_utc=now_utc):
                continue
            selected.append(candidate)
            if len(selected) >= TwelveDataStreamService.max_streams():
                break
        return selected

    @staticmethod
    async def _send_action(ws, action: str, symbols: set[str]) -> None:
        if not symbols:
            return
        payload = {
            "action": action,
            "params": {
                "symbols": ",".join(sorted(symbols)),
            },
        }
        await ws.send(json.dumps(payload))

    @staticmethod
    def _parse_timestamp(payload: dict[str, object]) -> tuple[date, str]:
        raw_value = payload.get("timestamp") or payload.get("datetime")
        if isinstance(raw_value, (int, float)):
            observed_at = datetime.fromtimestamp(float(raw_value), tz=timezone.utc)
        elif isinstance(raw_value, str) and raw_value.strip():
            text = raw_value.strip()
            try:
                observed_at = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                observed_at = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        else:
            observed_at = datetime.now(timezone.utc)
        return observed_at.date(), observed_at.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _parse_decimal(raw_value: object) -> Decimal | None:
        try:
            value = Decimal(str(raw_value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if value <= Decimal("0"):
            return None
        return value

    @staticmethod
    def _ingest_payload(payload: dict[str, object]) -> None:
        symbol = str(payload.get("symbol") or "").strip().upper()
        if not symbol:
            return
        meta = TwelveDataStreamService._symbol_meta.get(symbol)
        if meta is None:
            return

        TwelveDataStreamService._last_message_at = datetime.now(timezone.utc)

        if meta.kind == "fx":
            rate = TwelveDataStreamService._parse_decimal(payload.get("price") or payload.get("close"))
            if rate is None or meta.from_currency is None or meta.to_currency is None:
                return
            _, as_of = TwelveDataStreamService._parse_timestamp(payload)
            FxService.record_stream_quote(
                from_currency=meta.from_currency,
                to_currency=meta.to_currency,
                rate=rate,
                as_of=as_of,
            )
            return

        if meta.security_id is None or meta.ticker is None:
            return
        native_price = TwelveDataStreamService._parse_decimal(payload.get("price") or payload.get("close"))
        if native_price is None:
            return
        price_date, provider_ts = TwelveDataStreamService._parse_timestamp(payload)
        quote_currency = str(payload.get("currency") or meta.quote_currency or "GBP").strip().upper()
        PriceService._store_provider_quote(
            security_id=meta.security_id,
            ticker=meta.ticker,
            exchange=meta.exchange,
            quote_currency=quote_currency,
            native_price=native_price,
            price_date=price_date,
            provider_ts=provider_ts,
            source_prefix=_WS_SOURCE_PREFIX,
        )

    @staticmethod
    def _handle_status_event(payload: dict[str, object]) -> None:
        event_name = str(payload.get("event") or "").strip().lower()
        success_symbols: set[str] = set()
        for raw_success in payload.get("success") or []:
            if not isinstance(raw_success, dict):
                continue
            symbol = str(raw_success.get("symbol") or "").strip().upper()
            exchange = str(raw_success.get("exchange") or "").strip().upper()
            request_symbol = f"{symbol}:{exchange}" if exchange and "/" not in symbol else symbol
            if request_symbol:
                success_symbols.add(request_symbol)
        failed_symbols: set[str] = set()
        failure_reasons: dict[str, str] = {}
        for raw_fail in payload.get("fails") or []:
            if not isinstance(raw_fail, dict):
                continue
            symbol = str(raw_fail.get("symbol") or "").strip().upper()
            exchange = str(raw_fail.get("exchange") or "").strip().upper()
            if exchange:
                request_symbol = f"{symbol}:{exchange}"
            elif symbol:
                request_symbol = symbol
            else:
                continue
            failed_symbols.add(request_symbol)
            failure_reasons[request_symbol] = (
                str(raw_fail.get("reason") or raw_fail.get("message") or "Subscription rejected.").strip()
                or "Subscription rejected."
            )

        if event_name == "subscribe-status":
            for success_symbol in success_symbols:
                TwelveDataStreamService._mark_subscription_success(success_symbol)
            if failed_symbols:
                TwelveDataStreamService._last_error = f"Subscription failed for {', '.join(sorted(failed_symbols))}"
                TwelveDataStreamService._active_symbols.difference_update(failed_symbols)
                for failed_symbol in failed_symbols:
                    TwelveDataStreamService._symbol_meta.pop(failed_symbol, None)
                    TwelveDataStreamService._mark_subscription_failure(
                        failed_symbol,
                        reason=failure_reasons.get(failed_symbol, "Subscription rejected."),
                    )
                TwelveDataStreamService._refresh_active_security_ids()

    @staticmethod
    async def run_forever() -> None:
        config = TwelveDataPriceService.load_config()
        if config is None or not TwelveDataStreamService.is_enabled():
            logger.info("Twelve Data WebSocket stream disabled.")
            return

        backoff_seconds = 5
        while True:
            candidates = TwelveDataStreamService._build_candidates()
            candidate_map = {candidate.request_symbol: candidate for candidate in candidates}
            target_symbols = set(candidate_map.keys())
            TwelveDataStreamService._desired_symbols = set(target_symbols)
            if not target_symbols:
                TwelveDataStreamService._active_security_ids = set()
                TwelveDataStreamService._active_symbols = set()
                TwelveDataStreamService._symbol_meta = {}
                TwelveDataStreamService._connected = False
                await asyncio.sleep(TwelveDataStreamService.rebalance_seconds())
                continue

            url = f"{_STREAM_URL}?apikey={config.api_key}"
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    logger.info("Connected to Twelve Data WebSocket.")
                    TwelveDataStreamService._connected = True
                    TwelveDataStreamService._last_error = None
                    current_symbols: set[str] = set()
                    next_rebalance_at = datetime.now(timezone.utc)

                    while True:
                        now = datetime.now(timezone.utc)
                        if now >= next_rebalance_at:
                            latest_candidates = TwelveDataStreamService._build_candidates()
                            candidate_map = {
                                candidate.request_symbol: candidate
                                for candidate in latest_candidates
                            }
                            desired_symbols = set(candidate_map.keys())
                            TwelveDataStreamService._desired_symbols = set(desired_symbols)
                            to_unsubscribe = current_symbols - desired_symbols
                            to_subscribe = desired_symbols - current_symbols

                            if to_unsubscribe:
                                await TwelveDataStreamService._send_action(ws, "unsubscribe", to_unsubscribe)
                            if to_subscribe:
                                await TwelveDataStreamService._send_action(ws, "subscribe", to_subscribe)

                            current_symbols = desired_symbols
                            TwelveDataStreamService._symbol_meta = dict(candidate_map)
                            TwelveDataStreamService._active_symbols = set(current_symbols)
                            TwelveDataStreamService._refresh_active_security_ids()
                            next_rebalance_at = now.replace(microsecond=0) + timedelta(
                                seconds=TwelveDataStreamService.rebalance_seconds()
                            )

                        timeout_seconds = max(1.0, (next_rebalance_at - now).total_seconds())
                        try:
                            raw_message = await asyncio.wait_for(ws.recv(), timeout=timeout_seconds)
                        except TimeoutError:
                            continue

                        if not isinstance(raw_message, str):
                            continue
                        try:
                            payload = json.loads(raw_message)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(payload, dict):
                            if str(payload.get("event") or "").lower().startswith("subscribe"):
                                TwelveDataStreamService._handle_status_event(payload)
                                continue
                            if str(payload.get("event") or "").lower().startswith("unsubscribe"):
                                continue
                            TwelveDataStreamService._ingest_payload(payload)

                backoff_seconds = 5
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                TwelveDataStreamService._connected = False
                TwelveDataStreamService._active_symbols = set()
                TwelveDataStreamService._active_security_ids = set()
                TwelveDataStreamService._last_error = str(exc)
                logger.warning("Twelve Data WebSocket reconnect after error: %s", exc)
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 60)
