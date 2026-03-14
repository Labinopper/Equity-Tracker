"""Provider-backed beta corpus acquisition outside the core holdings DB."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select

from ...app_context import AppContext
from ...db.models import SecurityCatalog
from ...services.twelve_data_catalog_service import TwelveDataCatalogService
from ..context import BetaContext
from ..core_access import core_read_session
from ..db.models import BetaBenchmarkBar, BetaDailyBar, BetaInstrument, BetaUniverseMembership

_LSE_EXCHANGES = {"LSE", "XLON", "LON"}
_BENCHMARKS = (
    {
        "benchmark_key": "UK_MKT",
        "market": "UK",
        "symbol": "^FTSE",
        "name": "FTSE 100",
        "currency": "GBP",
    },
    {
        "benchmark_key": "US_MKT",
        "market": "US",
        "symbol": "^GSPC",
        "name": "S&P 500",
        "currency": "USD",
    },
)
_DEFAULT_LOOKBACK_DAYS = 365 * 10
_DEFAULT_BATCH_SIZE = 25
_MIN_DEEP_HISTORY_BARS = 252
_FRESH_HISTORY_GRACE_DAYS = 1
_NO_HISTORY_FAILURE_THRESHOLD = 3
_NO_DEEP_HISTORY_FAILURE_THRESHOLD = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _history_symbol_for_security(ticker: str, exchange: str | None) -> str:
    symbol = ticker.strip().upper()
    normalized_exchange = str(exchange or "").strip().upper()
    if normalized_exchange in _LSE_EXCHANGES and "." not in symbol:
        return f"{symbol}.L"
    return symbol


def _fx_symbol(from_currency: str, to_currency: str) -> str:
    return f"{from_currency.upper()}{to_currency.upper()}=X"


def _d(value: object) -> Decimal | None:
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if numeric <= 0:
        return None
    return numeric


@dataclass(frozen=True)
class _HistoryPoint:
    bar_date: date
    close_native: str
    close_gbp: str
    source: str
    fetched_at: datetime


class BetaCorpusService:
    """Acquire daily research history for beta-only instruments and benchmarks."""

    @staticmethod
    def sync_catalog_if_due() -> dict[str, int | bool | str]:
        if not AppContext.is_initialized():
            return {"performed": False, "reason": "core_app_context_unavailable"}
        if not TwelveDataCatalogService.is_configured():
            return {"performed": False, "reason": "catalog_service_unavailable"}
        try:
            result = TwelveDataCatalogService.sync_if_due()
        except Exception as exc:
            return {"performed": False, "reason": "catalog_sync_failed", "error": str(exc)}
        if result is None:
            return {"performed": False, "reason": "not_due"}
        return {"performed": True, **result}

    @staticmethod
    def backfill_market_corpus(
        *,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        include_benchmarks: bool = False,
    ) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {
                "catalog_updates": 0,
                "benchmarks_added": 0,
                "instrument_bars_added": 0,
                "instruments_backfilled": 0,
            }

        catalog_result = BetaCorpusService.sync_catalog_if_due()
        benchmark_result = (
            BetaCorpusService.sync_benchmark_bars()
            if include_benchmarks
            else {"bars_added": 0}
        )
        instrument_result = BetaCorpusService.backfill_tradeable_daily_bars(batch_size=batch_size)
        return {
            "catalog_updates": int(catalog_result.get("updated", 0) or catalog_result.get("inserted", 0) or 0),
            "benchmarks_added": int(benchmark_result.get("bars_added", 0)),
            "instrument_bars_added": int(instrument_result.get("bars_added", 0)),
            "instruments_backfilled": int(instrument_result.get("instruments_backfilled", 0)),
        }

    @staticmethod
    def backfill_tradeable_daily_bars(*, batch_size: int = _DEFAULT_BATCH_SIZE) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"bars_added": 0, "instruments_backfilled": 0}

        with core_read_session() as core_sess:
            catalog_rows = list(core_sess.scalars(select(SecurityCatalog)).all())
        catalog_by_key = {
            (row.symbol.upper(), str(row.exchange or "").upper()): row
            for row in catalog_rows
        }

        with BetaContext.write_session() as sess:
            membership_rows = list(
                sess.execute(
                    select(BetaInstrument, BetaUniverseMembership)
                    .join(BetaUniverseMembership, BetaUniverseMembership.instrument_id == BetaInstrument.id)
                    .where(
                        BetaInstrument.is_active.is_(True),
                        BetaUniverseMembership.effective_to.is_(None),
                        BetaUniverseMembership.status.in_(("SEED", "ACTIVE")),
                    )
                ).all()
            )
            ranked_memberships: list[
                tuple[int, int, int, int, str, BetaInstrument, BetaUniverseMembership]
            ] = []
            for instrument, membership in membership_rows:
                existing_bar_count = int(
                    sess.scalar(
                        select(func.count()).select_from(BetaDailyBar).where(BetaDailyBar.instrument_id == instrument.id)
                    )
                    or 0
                )
                metadata = {}
                if instrument.metadata_json:
                    try:
                        metadata = json.loads(instrument.metadata_json)
                    except Exception:
                        metadata = {}
                if instrument.core_security_id is not None and existing_bar_count < _MIN_DEEP_HISTORY_BARS:
                    backlog_rank = 0
                elif existing_bar_count == 0:
                    backlog_rank = 1
                elif existing_bar_count < _MIN_DEEP_HISTORY_BARS:
                    backlog_rank = 2
                elif membership.status == "ACTIVE":
                    backlog_rank = 3
                else:
                    backlog_rank = 4
                status_rank = 0 if instrument.core_security_id else 1 if membership.status == "ACTIVE" else 2
                ranked_memberships.append(
                    (
                        backlog_rank,
                        int(membership.priority_rank or 999999),
                        existing_bar_count,
                        status_rank,
                        str(instrument.symbol),
                        instrument,
                        membership,
                    )
                )
            ranked_memberships.sort()

            bars_added = 0
            instruments_backfilled = 0
            attempted_backfills = 0
            retired_instruments = 0
            for _backlog_rank, _priority_rank, existing_bar_count, _status_rank, _symbol, instrument, membership in ranked_memberships:
                if attempted_backfills >= batch_size:
                    break
                max_synced = sess.scalar(
                    select(func.max(BetaDailyBar.bar_date)).where(BetaDailyBar.instrument_id == instrument.id)
                )
                if (
                    existing_bar_count >= _MIN_DEEP_HISTORY_BARS
                    and max_synced is not None
                    and (date.today() - max_synced).days <= _FRESH_HISTORY_GRACE_DAYS
                ):
                    continue
                start_date = (
                    date.today() - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
                    if existing_bar_count < _MIN_DEEP_HISTORY_BARS
                    else (
                        max_synced + timedelta(days=1)
                        if max_synced is not None
                        else date.today() - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
                    )
                )
                if start_date > date.today():
                    continue
                attempted_backfills += 1
                history = BetaCorpusService._fetch_history_points(
                    symbol=instrument.symbol,
                    exchange=instrument.exchange,
                    currency=instrument.currency,
                    start_date=start_date,
                    end_date=date.today(),
                )
                if not history:
                    metadata = {}
                    if instrument.metadata_json:
                        try:
                            metadata = json.loads(instrument.metadata_json)
                        except Exception:
                            metadata = {}
                    failure_count = int(metadata.get("history_fetch_failures", 0) or 0) + 1
                    metadata["history_fetch_failures"] = failure_count
                    metadata["last_history_error_code"] = "NO_PROVIDER_HISTORY"
                    metadata["last_history_error_at"] = _utcnow().isoformat()
                    instrument.metadata_json = json.dumps(metadata, sort_keys=True)
                    if (
                        instrument.core_security_id is None
                        and existing_bar_count == 0
                        and failure_count >= _NO_HISTORY_FAILURE_THRESHOLD
                    ):
                        membership.status = "REMOVED"
                        membership.effective_to = _utcnow()
                        membership.reason_code = "NO_PROVIDER_HISTORY"
                        membership.reason_text = "Automatically retired after repeated history fetch failures."
                        instrument.is_active = False
                        retired_instruments += 1
                    continue

                catalog_row = catalog_by_key.get((instrument.symbol.upper(), str(instrument.exchange or "").upper()))
                metadata = {}
                if instrument.metadata_json:
                    try:
                        metadata = json.loads(instrument.metadata_json)
                    except Exception:
                        metadata = {}
                if catalog_row is not None and instrument.core_security_id is None:
                    metadata["catalog_isin"] = catalog_row.isin or ""
                    metadata["catalog_figi"] = catalog_row.figi or ""
                metadata["history_fetch_failures"] = 0
                metadata["history_stagnation_failures"] = 0
                metadata["last_history_success_at"] = _utcnow().isoformat()
                instrument.metadata_json = json.dumps(metadata, sort_keys=True)

                added_for_instrument = 0
                for point in history:
                    existing = sess.scalar(
                        select(BetaDailyBar).where(
                            BetaDailyBar.instrument_id == instrument.id,
                            BetaDailyBar.bar_date == point.bar_date,
                        )
                    )
                    if existing is not None:
                        continue
                    sess.add(
                        BetaDailyBar(
                            instrument_id=instrument.id,
                            bar_date=point.bar_date,
                            close_price_gbp=point.close_gbp,
                            close_price_native=point.close_native,
                            currency=instrument.currency,
                            source=point.source,
                            source_fetched_at=point.fetched_at,
                        )
                    )
                    bars_added += 1
                    added_for_instrument += 1
                if (
                    added_for_instrument == 0
                    and instrument.core_security_id is None
                    and existing_bar_count < _MIN_DEEP_HISTORY_BARS
                ):
                    metadata = {}
                    if instrument.metadata_json:
                        try:
                            metadata = json.loads(instrument.metadata_json)
                        except Exception:
                            metadata = {}
                    stagnation_count = int(metadata.get("history_stagnation_failures", 0) or 0) + 1
                    metadata["history_stagnation_failures"] = stagnation_count
                    metadata["last_history_error_code"] = "NO_PROVIDER_DEEP_HISTORY"
                    metadata["last_history_error_at"] = _utcnow().isoformat()
                    instrument.metadata_json = json.dumps(metadata, sort_keys=True)
                    if stagnation_count >= _NO_DEEP_HISTORY_FAILURE_THRESHOLD:
                        membership.status = "REMOVED"
                        membership.effective_to = _utcnow()
                        membership.reason_code = "NO_PROVIDER_DEEP_HISTORY"
                        membership.reason_text = (
                            "Automatically retired after repeated history fetches failed to deepen available price history."
                        )
                        instrument.is_active = False
                        retired_instruments += 1
                elif added_for_instrument:
                    metadata = {}
                    if instrument.metadata_json:
                        try:
                            metadata = json.loads(instrument.metadata_json)
                        except Exception:
                            metadata = {}
                    metadata["history_stagnation_failures"] = 0
                    instrument.metadata_json = json.dumps(metadata, sort_keys=True)
                if added_for_instrument:
                    instruments_backfilled += 1

        return {
            "bars_added": bars_added,
            "instruments_backfilled": instruments_backfilled,
            "instruments_retired": retired_instruments,
            "attempted_backfills": attempted_backfills,
        }

    @staticmethod
    def sync_benchmark_bars() -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"bars_added": 0}

        with BetaContext.write_session() as sess:
            bars_added = 0
            for spec in _BENCHMARKS:
                max_synced = sess.scalar(
                    select(func.max(BetaBenchmarkBar.bar_date)).where(
                        BetaBenchmarkBar.benchmark_key == spec["benchmark_key"]
                    )
                )
                start_date = (
                    max_synced + timedelta(days=1)
                    if max_synced is not None
                    else date.today() - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
                )
                if start_date > date.today():
                    continue
                history = BetaCorpusService._fetch_history_points(
                    symbol=str(spec["symbol"]),
                    exchange=None,
                    currency=str(spec["currency"]),
                    start_date=start_date,
                    end_date=date.today(),
                )
                for point in history:
                    existing = sess.scalar(
                        select(BetaBenchmarkBar).where(
                            BetaBenchmarkBar.benchmark_key == spec["benchmark_key"],
                            BetaBenchmarkBar.bar_date == point.bar_date,
                        )
                    )
                    if existing is not None:
                        continue
                    sess.add(
                        BetaBenchmarkBar(
                            benchmark_key=str(spec["benchmark_key"]),
                            market=str(spec["market"]),
                            symbol=str(spec["symbol"]),
                            name=str(spec["name"]),
                            bar_date=point.bar_date,
                            close_price_gbp=point.close_gbp,
                            close_price_native=point.close_native,
                            currency=str(spec["currency"]),
                            source=point.source,
                            source_fetched_at=point.fetched_at,
                        )
                    )
                    bars_added += 1

        return {"bars_added": bars_added}

    @staticmethod
    def _fetch_history_points(
        *,
        symbol: str,
        exchange: str | None,
        currency: str,
        start_date: date,
        end_date: date,
    ) -> list[_HistoryPoint]:
        if start_date > end_date:
            return []
        try:
            import yfinance as yf  # noqa: PLC0415
        except Exception:
            return []

        request_symbol = symbol if symbol.startswith("^") else _history_symbol_for_security(symbol, exchange)
        try:
            history = yf.Ticker(request_symbol).history(
                start=start_date.isoformat(),
                end=(end_date + timedelta(days=1)).isoformat(),
                interval="1d",
                auto_adjust=False,
                actions=False,
            )
        except Exception:
            return []
        if history is None or history.empty or "Close" not in history.columns:
            return []

        fetched_at = _utcnow()
        normalized_currency = str(currency or "GBP").upper()
        fx_rates_by_date: dict[date, Decimal] = {}
        if normalized_currency not in {"GBP", "GBX"}:
            try:
                fx_history = yf.Ticker(_fx_symbol(normalized_currency, "GBP")).history(
                    start=start_date.isoformat(),
                    end=(end_date + timedelta(days=1)).isoformat(),
                    interval="1d",
                    auto_adjust=False,
                    actions=False,
                )
            except Exception:
                fx_history = None
            if fx_history is not None and not fx_history.empty and "Close" in fx_history.columns:
                for fx_index, fx_row in fx_history.iterrows():
                    fx_close = _d(fx_row["Close"])
                    if fx_close is not None and fx_close > 0:
                        fx_rates_by_date[fx_index.date()] = fx_close

        points: list[_HistoryPoint] = []
        for index, row in history.iterrows():
            close_value = _d(row["Close"])
            if close_value is None:
                continue
            native_close = str(close_value.quantize(Decimal("0.0001")))
            if normalized_currency == "GBX":
                gbp_close = close_value / Decimal("100")
            elif normalized_currency == "GBP":
                gbp_close = close_value
            else:
                fx_rate = fx_rates_by_date.get(index.date())
                if fx_rate is None or fx_rate <= 0:
                    continue
                gbp_close = close_value * fx_rate
            points.append(
                _HistoryPoint(
                    bar_date=index.date(),
                    close_native=native_close,
                    close_gbp=str(gbp_close.quantize(Decimal("0.0001"))),
                    source=f"beta_yfinance:{request_symbol}",
                    fetched_at=fetched_at,
                )
            )
        return points
