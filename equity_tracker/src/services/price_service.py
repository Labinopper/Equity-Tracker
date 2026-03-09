"""
PriceService - fetch and store live market prices.

Design notes:
  - fetch_all() reads the configured live provider and stores one PriceHistory
    row per security.
  - fetch_and_store() fetches the configured live provider for a single security.
  - Historical backfill still uses yfinance daily closes.
  - Currency conversion rules:
      GBP  -> price stored as-is.
      GBX/GBp -> normalised to GBP by dividing by 100.
      Non-GBP -> converted to GBP using FxService.
  - Source field format:
      GBP securities : "yfinance:{price_ts}"
      Non-GBP securities : "yfinance:{price_ts}|fx:{fx_ts}"
  - Per-security failures are captured and returned by fetch_all().
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import func, select

from ..app_context import AppContext
from ..db.models import Lot
from ..db.repository import PriceRepository, SecurityRepository
from .fx_service import FxService
from .twelve_data_price_service import TwelveDataPriceService

logger = logging.getLogger(__name__)

# Source prefix used when storing yfinance prices in PriceHistory.source.
# Format: "yfinance:{price_ts}" or "yfinance:{price_ts}|fx:{fx_ts}".
_LIVE_SOURCE_PREFIX = "yfinance:"
_TWELVE_DATA_SOURCE_PREFIX = "twelvedata:"
_FX_SEPARATOR = "|fx:"
_HISTORY_SOURCE = "yfinance_history"
_GBP_DECIMAL_QUANT = Decimal("0.0001")
_FX_TS_FMT = "%Y-%m-%d %H:%M:%S"

# Days of pre-acquisition price history to maintain per security.
_PRE_ACQUISITION_DAYS = 365
_DAILY_HISTORY_PREFIXES = ("yfinance:", "twelvedata:", "google_sheets", _HISTORY_SOURCE)


class _HistoryRateLimitError(Exception):
    """Raised when yfinance signals a rate-limit (HTTP 429) response."""


class CreditBudgetExceededError(RuntimeError):
    """Raised when a Twelve Data action would exceed the configured rate limit."""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class PriceSnapshot:
    """
    Latest price for one security, normalised to GBP.

    close_price_original_ccy : price in the security's native currency
                               (USD for a USD stock; same as price_gbp for GBP).
    currency                 : normalised ISO code from the live provider.
    price_gbp                : GBP price after FX conversion.
    as_of                    : the date the price refers to (today's UTC date
                               at fetch time, or parsed from the source field).
    source                   : full source field as stored in PriceHistory.
    sheets_timestamp         : raw provider timestamp extracted
                               from source for convenient display; None when
                               source has no provider timestamp.
    fx_as_of                 : FX quote timestamp used for provider conversion;
                               None for GBP securities.
    """
    security_id: str
    price_gbp: Decimal
    close_price_original_ccy: Decimal
    currency: str
    as_of: date
    source: str = field(default="yfinance:")
    sheets_timestamp: str | None = field(default=None)
    fx_as_of: str | None = field(default=None)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_source(price_timestamp: str, *, source_prefix: str = _LIVE_SOURCE_PREFIX) -> str:
    """Encode a live price timestamp into the PriceHistory.source field."""
    return f"{source_prefix}{price_timestamp}"


def _build_source_with_fx(
    price_timestamp: str,
    fx_timestamp: str,
    *,
    source_prefix: str = _LIVE_SOURCE_PREFIX,
) -> str:
    """Encode both live price and FX timestamps into the source field."""
    return f"{source_prefix}{price_timestamp}{_FX_SEPARATOR}{fx_timestamp}"


def _parse_sheets_timestamp(source: str) -> str | None:
    """
    Extract the price timestamp from a source field, stripping any FX suffix.
    Supports both legacy google_sheets and current yfinance source prefixes.
    """
    if source:
        for prefix in ("google_sheets:", _LIVE_SOURCE_PREFIX, _TWELVE_DATA_SOURCE_PREFIX):
            if source.startswith(prefix):
                ts = source[len(prefix):]
                ts = ts.split(_FX_SEPARATOR, 1)[0]
                return ts if ts else None
    return None


def _parse_fx_timestamp(source: str) -> str | None:
    """
    Extract the FX tab timestamp from a source field.
    Returns None if no FX suffix is present or the timestamp is empty.
    """
    if source and _FX_SEPARATOR in source:
        ts = source.split(_FX_SEPARATOR, 1)[1]
        return ts if ts else None
    return None


def _normalize_provider_currency(value: str | None) -> str | None:
    """Normalize provider currency labels into app currency semantics."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    upper = raw.upper()
    if upper in {"GBP", "USD", "EUR", "JPY", "CAD", "AUD", "CHF", "HKD", "SGD", "NOK", "SEK"}:
        return upper
    if raw in {"GBp", "GBX", "GBx"} or upper == "GBX":
        return "GBX"
    return upper if len(upper) == 3 else None


def _current_utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime(_FX_TS_FMT)


def _current_live_provider() -> str:
    provider = os.environ.get("EQUITY_PRICE_PROVIDER", "yfinance").strip().lower()
    if provider == "twelve_data" and TwelveDataPriceService.is_configured():
        return "twelve_data"
    return "yfinance"


def _uses_twelve_data_fx() -> bool:
    return FxService.uses_twelve_data()


def _history_symbol_for_security(ticker: str, exchange: str | None) -> str:
    """
    Return a yfinance symbol for historical daily bars.

    Only lightweight exchange mapping is applied; all unknown exchanges
    fall back to the stored ticker.
    """
    symbol = ticker.strip().upper()
    ex = (exchange or "").strip().upper()
    if ex in {"LSE", "XLON"} and "." not in symbol:
        return f"{symbol}.L"
    return symbol


def _raise_if_rate_limited(exc: Exception) -> None:
    """
    Re-raise exc as _HistoryRateLimitError if it looks like a rate-limit response.

    Detects HTTP 429 / "Too Many Requests" from both the yfinance-native
    YFRateLimitError (added in v0.2.54) and the underlying requests layer.
    """
    msg = str(exc).lower()
    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
        raise _HistoryRateLimitError(str(exc)) from exc
    try:
        from yfinance.exceptions import YFRateLimitError  # noqa: PLC0415
        if isinstance(exc, YFRateLimitError):
            raise _HistoryRateLimitError(str(exc)) from exc
    except ImportError:
        pass


def _read_history_closes(
    *,
    symbol: str,
    start_date: date,
    end_date: date,
) -> dict[date, Decimal]:
    """
    Read daily close prices from yfinance for [start_date, end_date].

    Raises _HistoryRateLimitError if yfinance returns a 429 / rate-limit response.
    """
    if start_date > end_date:
        return {}

    try:
        import yfinance as yf  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - dependency is present in app env
        raise RuntimeError("yfinance is unavailable.") from exc

    # yfinance end is exclusive, so add one day.
    try:
        hist = yf.Ticker(symbol).history(
            start=start_date.isoformat(),
            end=(end_date + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=False,
            actions=False,
        )
    except Exception as exc:
        _raise_if_rate_limited(exc)
        raise
    if hist is None or hist.empty:
        return {}
    if "Close" not in hist.columns:
        return {}

    closes: dict[date, Decimal] = {}
    for idx, row in hist.iterrows():
        close_val = row["Close"]
        if close_val is None:
            continue
        try:
            close_dec = Decimal(str(close_val))
        except (InvalidOperation, ValueError, TypeError):
            continue
        if close_dec <= Decimal("0"):
            continue
        closes[idx.date()] = close_dec.quantize(_GBP_DECIMAL_QUANT, rounding=ROUND_HALF_UP)
    return closes


def _read_live_close(
    *,
    ticker: str,
    exchange: str | None,
) -> tuple[Decimal, date, str, str | None]:
    """
    Read latest close-like price from yfinance.

    Returns:
      (native_price, price_date, provider_timestamp, provider_currency)
    """
    symbol = _history_symbol_for_security(ticker, exchange)
    try:
        import yfinance as yf  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - dependency is present in app env
        raise RuntimeError("yfinance is unavailable.") from exc

    ticker_obj = yf.Ticker(symbol)
    try:
        hist = ticker_obj.history(
            period="5d",
            interval="1d",
            auto_adjust=False,
            actions=False,
        )
    except Exception as exc:
        raise RuntimeError(f"Could not fetch live price for {ticker} via yfinance: {exc}") from exc

    if hist is None or hist.empty or "Close" not in hist.columns:
        raise RuntimeError(f"No yfinance close data available for {ticker}.")

    close_series = hist["Close"].dropna()
    if close_series.empty:
        raise RuntimeError(f"yfinance close data is empty for {ticker}.")

    idx = close_series.index[-1]
    try:
        price_date = idx.date()
    except Exception:
        price_date = datetime.now(timezone.utc).date()

    try:
        native_price = Decimal(str(close_series.iloc[-1]))
    except Exception as exc:
        raise RuntimeError(f"Invalid yfinance close value for {ticker}.") from exc

    if native_price <= 0:
        raise RuntimeError(f"Non-positive yfinance close value for {ticker}.")

    provider_currency: str | None = None
    try:
        fast_info = getattr(ticker_obj, "fast_info", None)
        if fast_info is not None:
            provider_currency = _normalize_provider_currency(fast_info.get("currency"))
    except Exception:
        provider_currency = None

    return (
        native_price.quantize(_GBP_DECIMAL_QUANT, rounding=ROUND_HALF_UP),
        price_date,
        _current_utc_timestamp(),
        provider_currency,
    )


def _read_twelve_data_close(
    *,
    ticker: str,
    exchange: str | None,
) -> tuple[Decimal, date, str, str | None]:
    config = TwelveDataPriceService.load_config()
    if config is None:
        raise RuntimeError("Twelve Data is not configured.")

    quote = TwelveDataPriceService.fetch_quote(
        ticker=ticker,
        exchange=exchange,
        api_key=config.api_key,
        extended_hours=config.extended_hours,
    )
    TwelveDataPriceService.increment_credit_usage(1)
    return (
        quote.close.quantize(_GBP_DECIMAL_QUANT, rounding=ROUND_HALF_UP),
        quote.price_date,
        quote.timestamp_text,
        quote.currency,
    )


def _normalise_twelve_data_quote(quote) -> tuple[Decimal, date, str, str]:
    native_price = quote.close.quantize(_GBP_DECIMAL_QUANT, rounding=ROUND_HALF_UP)
    quote_currency = _normalize_provider_currency(quote.currency) or "GBP"
    if quote_currency == "GBX":
        native_price = (native_price / Decimal("100")).quantize(
            _GBP_DECIMAL_QUANT,
            rounding=ROUND_HALF_UP,
        )
        quote_currency = "GBP"
    return native_price, quote.price_date, quote.timestamp_text, quote_currency


def _currency_requires_fx_credit(currency: str | None) -> bool:
    normalized = _normalize_provider_currency(currency)
    return normalized not in {None, "GBP", "GBX"}


def _price_row_gbp_value(price_row) -> Decimal | None:
    """Return GBP Decimal value from a PriceHistory row."""
    if price_row is None:
        return None
    raw = price_row.close_price_gbp or price_row.close_price_original_ccy
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError):
        return None


def _is_daily_history_source(source: str | None) -> bool:
    if not source:
        return False
    return any(source.startswith(prefix) for prefix in _DAILY_HISTORY_PREFIXES)


def _daily_direction_and_percent(
    *,
    current_price_gbp: Decimal,
    previous_row,
) -> tuple[str | None, Decimal | None]:
    """
    Compute daily direction and percent vs previous stored price date.
    """
    previous_price_gbp = _price_row_gbp_value(previous_row)
    if previous_price_gbp is None or previous_price_gbp <= Decimal("0"):
        return None, None

    delta = current_price_gbp - previous_price_gbp
    percent = ((delta / previous_price_gbp) * Decimal("100")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    if delta > Decimal("0"):
        return "up", percent
    if delta < Decimal("0"):
        return "down", percent
    return "flat", percent


# ---------------------------------------------------------------------------
# PriceService
# ---------------------------------------------------------------------------

class PriceService:
    """
    Application service for live price fetching and retrieval.

    All methods are static.  Reads use AppContext.read_session(); writes use
    AppContext.write_session(). AppContext must be initialised before calling
    any method.
    """

    # â”€â”€ Write â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @staticmethod
    def _fetch_live_components(
        *,
        ticker: str,
        currency: str,
        exchange: str | None,
        provider_name: str | None = None,
    ) -> tuple[Decimal, str, date, str, str]:
        """
        Resolve latest native price + currency metadata for one security.

        Returns:
          (native_price, quote_currency, price_date, provider_timestamp, source_prefix)
        """
        chosen_provider = provider_name or _current_live_provider()
        if chosen_provider == "twelve_data":
            native_price, price_date, provider_ts, provider_currency = _read_twelve_data_close(
                ticker=ticker,
                exchange=exchange,
            )
            source_prefix = _TWELVE_DATA_SOURCE_PREFIX
        else:
            native_price, price_date, provider_ts, provider_currency = _read_live_close(
                ticker=ticker,
                exchange=exchange,
            )
            source_prefix = _LIVE_SOURCE_PREFIX
        security_currency = (currency or "GBP").strip().upper()
        quote_currency = _normalize_provider_currency(provider_currency) or security_currency

        # yfinance uses GBp/GBX for many LSE quotes; normalize to GBP.
        if quote_currency == "GBX":
            native_price = (native_price / Decimal("100")).quantize(
                _GBP_DECIMAL_QUANT,
                rounding=ROUND_HALF_UP,
            )
            quote_currency = "GBP"

        return native_price, quote_currency, price_date, provider_ts, source_prefix

    @staticmethod
    def _persist_live_price(
        *,
        security_id: str,
        quote_currency: str,
        native_price: Decimal,
        price_gbp: Decimal,
        price_date: date,
        source: str,
    ) -> None:
        with AppContext.write_session() as sess:
            price_repo = PriceRepository(sess)
            previous_row = price_repo.get_latest_before(security_id, price_date)
            direction, percent_change = _daily_direction_and_percent(
                current_price_gbp=price_gbp,
                previous_row=previous_row,
            )
            price_repo.upsert(
                security_id=security_id,
                price_date=price_date,
                close_price_original_ccy=str(native_price),
                currency=quote_currency,
                source=source,
                close_price_gbp=str(price_gbp),
            )
            price_repo.add_ticker_snapshot(
                security_id=security_id,
                price_date=price_date,
                price_gbp=str(price_gbp),
                source=source,
                direction=direction,
                percent_change=(
                    str(percent_change) if percent_change is not None else None
                ),
            )

    @staticmethod
    def _store_provider_quote(
        *,
        security_id: str,
        ticker: str,
        exchange: str | None,
        quote_currency: str,
        native_price: Decimal,
        price_date: date,
        provider_ts: str,
        source_prefix: str,
    ) -> int:
        fx_as_of: str | None = None
        native_to_gbp_rate_for_backfill: Decimal | None = None

        if quote_currency == "GBP":
            price_gbp = native_price.quantize(_GBP_DECIMAL_QUANT, rounding=ROUND_HALF_UP)
            source = _build_source(provider_ts, source_prefix=source_prefix)
        else:
            quote = FxService.get_rate(quote_currency, "GBP")
            price_gbp = (native_price * quote.rate).quantize(
                _GBP_DECIMAL_QUANT, rounding=ROUND_HALF_UP
            )
            native_to_gbp_rate_for_backfill = quote.rate
            fx_as_of = quote.as_of or _current_utc_timestamp()
            source = _build_source_with_fx(
                provider_ts,
                fx_as_of,
                source_prefix=source_prefix,
            )

        PriceService._persist_live_price(
            security_id=security_id,
            quote_currency=quote_currency,
            native_price=native_price,
            price_gbp=price_gbp,
            price_date=price_date,
            source=source,
        )

        logger.info(
            "Stored live %s price for %s (%s): £%s [%s]",
            source_prefix.rstrip(":"),
            ticker,
            security_id,
            price_gbp,
            provider_ts,
        )

        try:
            return PriceService._backfill_history_for_security(
                security_id=security_id,
                ticker=ticker,
                currency=quote_currency,
                exchange=exchange,
                native_to_gbp_rate=native_to_gbp_rate_for_backfill,
            )
        except _HistoryRateLimitError as exc:
            logger.warning(
                "Rate limited during history backfill for %s; will retry at next run: %s",
                ticker, exc,
            )
            return 0

    @staticmethod
    def _earliest_lot_date(security_id: str) -> date | None:
        """Return the earliest recorded acquisition date for a security."""
        with AppContext.read_session() as sess:
            stmt = select(func.min(Lot.acquisition_date)).where(Lot.security_id == security_id)
            return sess.scalar(stmt)

    @staticmethod
    def _latest_daily_price_date(security_id: str) -> date | None:
        with AppContext.read_session() as sess:
            rows = PriceRepository(sess).get_history_range(security_id)
        daily_dates = [
            row.price_date
            for row in rows
            if _is_daily_history_source(row.source)
        ]
        return max(daily_dates) if daily_dates else None

    @staticmethod
    def _sync_history_window_for_security(
        *,
        security_id: str,
        ticker: str,
        currency: str,
        exchange: str | None,
        start_date: date,
        end_date: date,
        native_to_gbp_rate: Decimal | None = None,
    ) -> int:
        if start_date > end_date:
            return 0

        ccy = currency.strip().upper()
        if ccy != "GBP" and native_to_gbp_rate is None:
            logger.info(
                "Skipping %s history sync for %s (%s): %s->GBP rate unavailable.",
                ccy,
                ticker,
                security_id,
                ccy,
            )
            return 0

        symbol = _history_symbol_for_security(ticker, exchange)
        try:
            closes = _read_history_closes(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )
        except _HistoryRateLimitError:
            raise
        except Exception as exc:
            logger.warning(
                "History sync failed for %s (%s): %s",
                ticker,
                security_id,
                exc,
            )
            return 0

        if not closes:
            return 0

        rows_written = 0
        with AppContext.write_session() as sess:
            price_repo = PriceRepository(sess)
            for price_date, close_original in sorted(closes.items()):
                if ccy != "GBP":
                    close_gbp = (close_original * native_to_gbp_rate).quantize(
                        _GBP_DECIMAL_QUANT,
                        rounding=ROUND_HALF_UP,
                    )
                else:
                    close_gbp = close_original

                price_repo.upsert(
                    security_id=security_id,
                    price_date=price_date,
                    close_price_original_ccy=str(close_original),
                    close_price_gbp=str(close_gbp),
                    currency=ccy,
                    source=_HISTORY_SOURCE,
                )
                rows_written += 1

        if rows_written > 0:
            logger.info(
                "Synced %d daily history rows for %s (%s), %s to %s.",
                rows_written,
                ticker,
                security_id,
                start_date.isoformat(),
                end_date.isoformat(),
            )
        return rows_written

    @staticmethod
    def _estimate_twelve_data_cost(
        security_items: list[tuple[str, str, str | None, str | None]],
    ) -> int:
        quote_symbols = {
            TwelveDataPriceService.request_symbol(ticker, exchange)
            for _security_id, ticker, _currency, exchange in security_items
        }
        if not _uses_twelve_data_fx():
            return len(quote_symbols)
        fx_pairs = {
            f"{currency.strip().upper()}2GBP"
            for _security_id, _ticker, currency, _exchange in security_items
            if _currency_requires_fx_credit(currency)
        }
        return len(quote_symbols) + len(fx_pairs)

    @staticmethod
    def _ensure_twelve_data_budget_for(
        security_items: list[tuple[str, str, str | None, str | None]],
    ) -> None:
        config = TwelveDataPriceService.load_config()
        if config is None:
            raise RuntimeError("Twelve Data is not configured.")
        estimated_cost = PriceService._estimate_twelve_data_cost(security_items)
        remaining_calls = TwelveDataPriceService.remaining_minute_capacity(config)
        if estimated_cost > remaining_calls:
            raise CreditBudgetExceededError(
                f"Twelve Data refresh would require about {estimated_cost} calls, "
                f"but only {remaining_calls} remain in the current minute limit."
            )

    @staticmethod
    def _backfill_history_for_security(
        *,
        security_id: str,
        ticker: str,
        currency: str,
        exchange: str | None,
        native_to_gbp_rate: Decimal | None = None,
    ) -> int:
        """
        Backfill daily closes covering _PRE_ACQUISITION_DAYS before first acquisition.

        Behavior:
          - Targets [acquisition_date - 366d, yesterday] for full pre/post coverage.
          - Only fetches dates not already stored (fills gaps before earliest row).
          - Never backfills today's row (latest live price remains provider-driven).
          - Uses yfinance daily closes and stores rows in price_history.
          - Raises _HistoryRateLimitError if yfinance returns a 429 response so
            callers can back off and retry later.
        """
        acquisition_start = PriceService._earliest_lot_date(security_id)
        if acquisition_start is None:
            return 0

        extended_start = acquisition_start - timedelta(days=_PRE_ACQUISITION_DAYS)
        backfill_end = datetime.now(tz=timezone.utc).date() - timedelta(days=1)
        if extended_start > backfill_end:
            return 0

        with AppContext.read_session() as sess:
            earliest_price_date = PriceRepository(sess).get_earliest_price_date(security_id)

        # Allow a 5-calendar-day boundary buffer: if our earliest stored price is within
        # one trading week of the target start, consider the range complete.  This
        # prevents endless single-day retry loops when extended_start lands on a
        # weekend, public holiday, or a day a specific ticker was not traded.
        if earliest_price_date is not None and earliest_price_date <= extended_start + timedelta(days=5):
            return 0

        if earliest_price_date is None:
            start_date = extended_start
            end_date = backfill_end
        else:
            start_date = extended_start
            end_date = min(backfill_end, earliest_price_date - timedelta(days=1))

        if start_date > end_date:
            return 0

        ccy = currency.strip().upper()
        if ccy != "GBP" and native_to_gbp_rate is None:
            logger.info(
                "Skipping %s history backfill for %s (%s): %s->GBP rate unavailable.",
                ccy,
                ticker,
                security_id,
                ccy,
            )
            return 0

        symbol = _history_symbol_for_security(ticker, exchange)
        try:
            closes = _read_history_closes(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )
        except _HistoryRateLimitError:
            raise  # Propagate so callers can handle back-off distinctly.
        except Exception as exc:
            logger.warning(
                "Historical backfill failed for %s (%s): %s",
                ticker,
                security_id,
                exc,
            )
            return 0

        if not closes:
            return 0

        rows_written = 0
        with AppContext.write_session() as sess:
            price_repo = PriceRepository(sess)
            for price_date, close_original in sorted(closes.items()):
                close_gbp: Decimal | None
                if ccy != "GBP":
                    close_gbp = (close_original * native_to_gbp_rate).quantize(
                        _GBP_DECIMAL_QUANT,
                        rounding=ROUND_HALF_UP,
                    )
                else:
                    close_gbp = close_original

                price_repo.upsert(
                    security_id=security_id,
                    price_date=price_date,
                    close_price_original_ccy=str(close_original),
                    close_price_gbp=str(close_gbp) if close_gbp is not None else None,
                    currency=ccy,
                    source=_HISTORY_SOURCE,
                )
                rows_written += 1

        if rows_written > 0:
            logger.info(
                "Backfilled %d historical rows for %s (%s), %s to %s.",
                rows_written,
                ticker,
                security_id,
                start_date.isoformat(),
                end_date.isoformat(),
            )
        return rows_written

    @staticmethod
    def backfill_extended_history_all() -> dict:
        """
        Ensure every security has _PRE_ACQUISITION_DAYS of pre-acquisition price
        history stored.  Securities whose earliest stored price is already on or
        before (acquisition_date - 366d) are skipped immediately.

        Called on app startup (if history is incomplete) and nightly at 23:00 UK.

        FX rates are resolved on-demand via FxService for non-GBP securities.

        Returns a summary dict::

            {
                "backfilled_days":  <int>,
                "skipped":          <int>,   # already complete
                "rate_limited":     [<ticker>, ...],  # stopped early; retry next run
                "errors":           [{"ticker": ..., "error": ...}, ...],
            }
        """
        with AppContext.read_session() as sess:
            securities = SecurityRepository(sess).list_all()
            sec_items = [
                (s.id, s.ticker, s.currency or "GBP", s.exchange)
                for s in securities
            ]

        total_rows = 0
        skipped = 0
        rate_limited: list[str] = []
        errors: list[dict] = []

        for security_id, ticker, currency, exchange in sec_items:
            ccy = currency.strip().upper()
            native_to_gbp_rate: Decimal | None = None
            if ccy != "GBP":
                try:
                    quote = FxService.get_rate(ccy, "GBP")
                    native_to_gbp_rate = quote.rate
                except Exception as exc:
                    logger.info(
                        "Extended backfill: no %s->GBP rate for %s (%s).",
                        ccy, ticker, exc,
                    )

            try:
                rows = PriceService._backfill_history_for_security(
                    security_id=security_id,
                    ticker=ticker,
                    currency=currency,
                    exchange=exchange,
                    native_to_gbp_rate=native_to_gbp_rate,
                )
                total_rows += rows
                if rows == 0:
                    skipped += 1
            except _HistoryRateLimitError as exc:
                logger.warning(
                    "Extended backfill rate-limited on %s; stopping for this run: %s",
                    ticker, exc,
                )
                rate_limited.append(ticker)
                break  # Respect the rate limit; remaining securities retry next night.
            except Exception as exc:
                logger.warning(
                    "Extended backfill error for %s (%s): %s",
                    ticker, security_id, exc,
                )
                errors.append({"ticker": ticker, "error": str(exc)})

        return {
            "backfilled_days": total_rows,
            "skipped": skipped,
            "rate_limited": rate_limited,
            "errors": errors,
        }

    @staticmethod
    def ensure_recent_daily_history_for_security(
        security_id: str,
        *,
        up_to_date: date | None = None,
    ) -> dict:
        """
        Repair trailing daily-history gaps for one security.

        Intended for history/report reads when the server missed one or more
        scheduled backfill runs. Never writes today's row; live pricing remains
        provider-driven.
        """
        today_utc = datetime.now(tz=timezone.utc).date()
        requested_end = up_to_date or today_utc
        target_end = min(requested_end, today_utc - timedelta(days=1))
        if target_end < date.min + timedelta(days=1):
            return {"backfilled_days": 0, "errors": []}

        with AppContext.read_session() as sess:
            security = SecurityRepository(sess).get_by_id(security_id)
            if security is None:
                raise ValueError(f"Security {security_id!r} not found.")

        native_to_gbp_rate: Decimal | None = None
        ccy = (security.currency or "GBP").strip().upper()
        if ccy != "GBP":
            try:
                native_to_gbp_rate = FxService.get_rate(ccy, "GBP").rate
            except Exception as exc:
                logger.info(
                    "Recent history repair: no %s->GBP rate for %s (%s).",
                    ccy, security.ticker, exc,
                )

        rows_written = 0
        errors: list[dict] = []
        try:
            rows_written += PriceService._backfill_history_for_security(
                security_id=security.id,
                ticker=security.ticker,
                currency=ccy,
                exchange=security.exchange,
                native_to_gbp_rate=native_to_gbp_rate,
            )
        except _HistoryRateLimitError as exc:
            errors.append({"ticker": security.ticker, "error": str(exc)})
            return {"backfilled_days": rows_written, "errors": errors}

        latest_daily = PriceService._latest_daily_price_date(security.id)
        if latest_daily is None:
            latest_daily = (PriceService._earliest_lot_date(security.id) or target_end) - timedelta(days=1)

        start_date = latest_daily + timedelta(days=1)
        if start_date <= target_end:
            try:
                rows_written += PriceService._sync_history_window_for_security(
                    security_id=security.id,
                    ticker=security.ticker,
                    currency=ccy,
                    exchange=security.exchange,
                    start_date=start_date,
                    end_date=target_end,
                    native_to_gbp_rate=native_to_gbp_rate,
                )
            except _HistoryRateLimitError as exc:
                errors.append({"ticker": security.ticker, "error": str(exc)})

        return {"backfilled_days": rows_written, "errors": errors}

    @staticmethod
    def ensure_recent_daily_history_all(
        *,
        up_to_date: date | None = None,
    ) -> dict:
        """
        Repair trailing daily-history gaps for all securities.
        """
        with AppContext.read_session() as sess:
            securities = SecurityRepository(sess).list_all()

        total_rows = 0
        errors: list[dict] = []
        for security in securities:
            try:
                result = PriceService.ensure_recent_daily_history_for_security(
                    security.id,
                    up_to_date=up_to_date,
                )
                total_rows += int(result.get("backfilled_days", 0) or 0)
                errors.extend(result.get("errors", []))
            except Exception as exc:
                errors.append({"ticker": security.ticker, "error": str(exc)})

        return {"backfilled_days": total_rows, "errors": errors}

    @staticmethod
    def fetch_and_store(
        security_id: str,
        *,
        provider_name: str | None = None,
    ) -> PriceSnapshot:
        """
        Fetch the latest market price for a security via the active live provider,
        convert to GBP if needed via FxService, and persist a PriceHistory row.

        Raises:
            ValueError   : if the security is not found in the database.
            RuntimeError : if live pricing is unavailable or a required
                           FX conversion cannot be resolved.

        Returns a PriceSnapshot with the GBP price.
        """
        # 1. Read security from DB â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        with AppContext.read_session() as sess:
            sec_repo = SecurityRepository(sess)
            security = sec_repo.get_by_id(security_id)
            if security is None:
                raise ValueError(f"Security {security_id!r} not found.")
            ticker = security.ticker
            currency = security.currency
            exchange = security.exchange

        # 2. Read latest provider price
        native_price, quote_currency, price_date, provider_ts, source_prefix = PriceService._fetch_live_components(
            ticker=ticker,
            currency=currency,
            exchange=exchange,
            provider_name=provider_name,
        )

        PriceService._store_provider_quote(
            security_id=security_id,
            ticker=ticker,
            exchange=exchange,
            quote_currency=quote_currency,
            native_price=native_price,
            price_date=price_date,
            provider_ts=provider_ts,
            source_prefix=source_prefix,
        )

        if quote_currency == "GBP":
            price_gbp = native_price.quantize(_GBP_DECIMAL_QUANT, rounding=ROUND_HALF_UP)
            source = _build_source(provider_ts, source_prefix=source_prefix)
            fx_as_of: str | None = None
        else:
            quote = FxService.get_rate(quote_currency, "GBP")
            price_gbp = (native_price * quote.rate).quantize(
                _GBP_DECIMAL_QUANT, rounding=ROUND_HALF_UP
            )
            fx_as_of = quote.as_of or _current_utc_timestamp()
            source = _build_source_with_fx(
                provider_ts,
                fx_as_of,
                source_prefix=source_prefix,
            )

        return PriceSnapshot(
            security_id=security_id,
            price_gbp=price_gbp,
            close_price_original_ccy=native_price,
            currency=quote_currency,
            as_of=price_date,
            source=source,
            sheets_timestamp=provider_ts,
            fx_as_of=fx_as_of,
        )

    @staticmethod
    def fetch_all(*, provider_name: str | None = None) -> dict:
        """
        Read all prices from the active live provider and store a PriceHistory row for every
        security in the database.

        Returns a summary dict::

            {
                "fetched": <int>,   # securities successfully stored
                "failed":  <int>,   # securities with errors
                "backfilled_days": <int>,  # historical daily rows written
                "errors":  [        # per-failure details
                    {"security_id": "...", "ticker": "...", "error": "..."},
                    ...
                ]
            }

        Per-security failures are caught and recorded; the loop always
        completes.
        """
        # 1. Read all securities from DB
        with AppContext.read_session() as sess:
            sec_repo = SecurityRepository(sess)
            securities = sec_repo.list_all()
            sec_items = [(s.id, s.ticker, s.currency, s.exchange) for s in securities]

        chosen_provider = provider_name or _current_live_provider()
        if chosen_provider == "twelve_data":
            config = TwelveDataPriceService.load_config()
            if config is None:
                raise RuntimeError("Twelve Data is not configured.")
            PriceService._ensure_twelve_data_budget_for(sec_items)

            batch_items = [(ticker, exchange) for _id, ticker, _currency, exchange in sec_items]
            quotes_by_symbol, batch_errors = TwelveDataPriceService.fetch_quotes(
                items=batch_items,
                api_key=config.api_key,
                extended_hours=config.extended_hours,
            )
            request_symbol_count = len(
                {
                    TwelveDataPriceService.request_symbol(ticker, exchange)
                    for ticker, exchange in batch_items
                }
            )
            TwelveDataPriceService.increment_credit_usage(request_symbol_count)

            fetched = 0
            backfilled_days = 0
            errors: list[dict] = []
            for security_id, ticker, _currency, exchange in sec_items:
                request_symbol = TwelveDataPriceService.request_symbol(ticker, exchange)
                batch_error = batch_errors.get(request_symbol)
                if batch_error is not None:
                    logger.warning(
                        "Failed to store price for %s (%s): %s", ticker, security_id, batch_error
                    )
                    errors.append(
                        {
                            "security_id": security_id,
                            "ticker": ticker,
                            "error": batch_error,
                        }
                    )
                    continue

                quote = quotes_by_symbol.get(request_symbol)
                if quote is None:
                    errors.append(
                        {
                            "security_id": security_id,
                            "ticker": ticker,
                            "error": "No quote returned from Twelve Data.",
                        }
                    )
                    continue

                try:
                    native_price, price_date, provider_ts, quote_currency = _normalise_twelve_data_quote(quote)
                    backfilled_days += PriceService._store_provider_quote(
                        security_id=security_id,
                        ticker=ticker,
                        exchange=exchange,
                        quote_currency=quote_currency,
                        native_price=native_price,
                        price_date=price_date,
                        provider_ts=provider_ts,
                        source_prefix=_TWELVE_DATA_SOURCE_PREFIX,
                    )
                    fetched += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to store price for %s (%s): %s", ticker, security_id, exc
                    )
                    errors.append(
                        {
                            "security_id": security_id,
                            "ticker": ticker,
                            "error": str(exc),
                        }
                    )

            return {
                "fetched": fetched,
                "failed": len(errors),
                "errors": errors,
                "backfilled_days": backfilled_days,
            }

        # 2. Store a PriceHistory row per security
        fetched = 0
        backfilled_days = 0
        errors: list[dict] = []

        for security_id, ticker, currency, exchange in sec_items:
            try:
                native_price, quote_currency, price_date, provider_ts, source_prefix = (
                    PriceService._fetch_live_components(
                        ticker=ticker,
                        currency=currency,
                        exchange=exchange,
                        provider_name=provider_name,
                    )
                )
                native_to_gbp_rate: Decimal | None = None
                if quote_currency != "GBP":
                    quote = FxService.get_rate(quote_currency, "GBP")
                    price_gbp = (native_price * quote.rate).quantize(
                        _GBP_DECIMAL_QUANT,
                        rounding=ROUND_HALF_UP,
                    )
                    native_to_gbp_rate = quote.rate
                    fx_as_of = quote.as_of or _current_utc_timestamp()
                    source = _build_source_with_fx(
                        provider_ts,
                        fx_as_of,
                        source_prefix=source_prefix,
                    )
                else:
                    price_gbp = native_price.quantize(
                        _GBP_DECIMAL_QUANT,
                        rounding=ROUND_HALF_UP,
                    )
                    source = _build_source(provider_ts, source_prefix=source_prefix)

                PriceService._persist_live_price(
                    security_id=security_id,
                    quote_currency=quote_currency,
                    native_price=native_price,
                    price_gbp=price_gbp,
                    price_date=price_date,
                    source=source,
                )

                logger.info(
                    "Stored live %s price for %s: £%s [%s]",
                    source_prefix.rstrip(":"),
                    ticker,
                    price_gbp,
                    provider_ts,
                )
                fetched += 1
                try:
                    backfilled_days += PriceService._backfill_history_for_security(
                        security_id=security_id,
                        ticker=ticker,
                        currency=quote_currency,
                        exchange=exchange,
                        native_to_gbp_rate=native_to_gbp_rate,
                    )
                except _HistoryRateLimitError as exc:
                    logger.warning(
                        "Rate limited during history backfill for %s; will retry at next run: %s",
                        ticker, exc,
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to store price for %s (%s): %s", ticker, security_id, exc
                )
                errors.append({
                    "security_id": security_id,
                    "ticker": ticker,
                    "error": str(exc),
                })

        return {
            "fetched": fetched,
            "failed": len(errors),
            "errors": errors,
            "backfilled_days": backfilled_days,
        }

    @staticmethod
    def refresh_intraday_budgeted() -> dict:
        """
        Run one Twelve Data budget-aware refresh pass.

        Returns a summary dict. If Twelve Data is not configured, no work is done.
        """
        config = TwelveDataPriceService.load_config()
        if config is None:
            return {
                "enabled": False,
                "fetched": 0,
                "planned": 0,
                "remaining_calls": 0,
                "errors": [],
            }

        minute_capacity_remaining = TwelveDataPriceService.remaining_minute_capacity(config)
        candidates = TwelveDataPriceService.build_scheduler_candidates(config)
        try:
            from .twelve_data_stream_service import TwelveDataStreamService

            streamed_security_ids = TwelveDataStreamService.current_streamed_security_ids()
        except Exception:
            streamed_security_ids = set()
        if streamed_security_ids:
            candidates = [
                candidate
                for candidate in candidates
                if candidate.security_id not in streamed_security_ids
            ]
        candidate_by_security_id = {candidate.security_id: candidate for candidate in candidates}
        with AppContext.read_session() as sess:
            securities = SecurityRepository(sess).list_all()
            tracked_instrument_count = PriceService._estimate_twelve_data_cost(
                [
                    (security.id, security.ticker, security.currency, security.exchange)
                    for security in securities
                    if security.id in candidate_by_security_id
                ]
            )
        plan = TwelveDataPriceService.build_refresh_plan(
            candidates,
            minute_capacity_remaining=minute_capacity_remaining,
            tracked_instrument_count=max(1, tracked_instrument_count),
            max_calls_per_minute=config.max_calls_per_minute,
            now_utc=datetime.now(timezone.utc),
        )
        if not plan:
            return {
                "enabled": True,
                "fetched": 0,
                "planned": 0,
                "remaining_calls": minute_capacity_remaining,
                "tracked_instruments": tracked_instrument_count,
                "errors": [],
            }

        plan_by_security_id = {item.security_id: item for item in plan}
        with AppContext.read_session() as sess:
            securities = SecurityRepository(sess).list_all()
            securities_by_id = {security.id: security for security in securities if security.id in plan_by_security_id}

        while plan:
            estimated_cost = PriceService._estimate_twelve_data_cost(
                [
                    (
                        item.security_id,
                        securities_by_id[item.security_id].ticker,
                        securities_by_id[item.security_id].currency,
                        item.exchange or securities_by_id[item.security_id].exchange,
                    )
                    for item in plan
                    if item.security_id in securities_by_id
                ]
            )
            if estimated_cost <= minute_capacity_remaining:
                break
            plan = plan[:-1]

        if not plan:
            return {
                "enabled": True,
                "fetched": 0,
                "planned": 0,
                "remaining_calls": minute_capacity_remaining,
                "tracked_instruments": tracked_instrument_count,
                "errors": [],
            }

        batch_items = []
        for item in plan:
            security = securities_by_id.get(item.security_id)
            if security is None:
                continue
            batch_items.append((security.ticker, item.exchange or security.exchange))

        if not batch_items:
            return {
                "enabled": True,
                "fetched": 0,
                "planned": len(plan),
                "remaining_calls": minute_capacity_remaining,
                "tracked_instruments": tracked_instrument_count,
                "errors": [],
            }

        quotes_by_symbol, batch_errors = TwelveDataPriceService.fetch_quotes(
            items=batch_items,
            api_key=config.api_key,
            extended_hours=config.extended_hours,
        )
        request_symbol_count = len(
            {
                TwelveDataPriceService.request_symbol(ticker, exchange)
                for ticker, exchange in batch_items
            }
        )
        TwelveDataPriceService.increment_credit_usage(request_symbol_count)

        fetched = 0
        errors: list[dict] = []
        for item in plan:
            security = securities_by_id.get(item.security_id)
            if security is None:
                errors.append(
                    {
                        "security_id": item.security_id,
                        "ticker": item.ticker,
                        "error": "Security not found.",
                    }
                )
                continue

            request_symbol = TwelveDataPriceService.request_symbol(
                security.ticker,
                item.exchange or security.exchange,
            )
            batch_error = batch_errors.get(request_symbol)
            if batch_error is not None:
                logger.warning(
                    "Budgeted Twelve Data refresh failed for %s (%s): %s",
                    item.ticker,
                    item.security_id,
                    batch_error,
                )
                errors.append(
                    {
                        "security_id": item.security_id,
                        "ticker": item.ticker,
                        "error": batch_error,
                    }
                )
                continue

            quote = quotes_by_symbol.get(request_symbol)
            if quote is None:
                errors.append(
                    {
                        "security_id": item.security_id,
                        "ticker": item.ticker,
                        "error": "No quote returned from Twelve Data.",
                    }
                )
                continue

            try:
                native_price, price_date, provider_ts, quote_currency = _normalise_twelve_data_quote(quote)
                PriceService._store_provider_quote(
                    security_id=item.security_id,
                    ticker=security.ticker,
                    exchange=security.exchange,
                    quote_currency=quote_currency,
                    native_price=native_price,
                    price_date=price_date,
                    provider_ts=provider_ts,
                    source_prefix=_TWELVE_DATA_SOURCE_PREFIX,
                )
                fetched += 1
            except Exception as exc:
                logger.warning(
                    "Budgeted Twelve Data refresh failed for %s (%s): %s",
                    item.ticker,
                    item.security_id,
                    exc,
                )
                errors.append(
                    {
                        "security_id": item.security_id,
                        "ticker": item.ticker,
                        "error": str(exc),
                    }
                )

        return {
            "enabled": True,
            "fetched": fetched,
            "planned": len(plan),
            "remaining_calls": minute_capacity_remaining,
            "tracked_instruments": tracked_instrument_count,
            "errors": errors,
        }

    # â”€â”€ Read â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @staticmethod
    def get_latest(security_id: str) -> PriceSnapshot | None:
        """
        Return the most recently stored price for a security, or None if no
        price has ever been fetched.
        """
        with AppContext.read_session() as sess:
            price_repo = PriceRepository(sess)
            row = price_repo.get_latest(security_id)
            if row is None:
                return None
            price_gbp = (
                Decimal(row.close_price_gbp)
                if row.close_price_gbp
                else Decimal(row.close_price_original_ccy)
            )
            src = row.source or ""
            return PriceSnapshot(
                security_id=security_id,
                price_gbp=price_gbp,
                close_price_original_ccy=Decimal(row.close_price_original_ccy),
                currency=row.currency,
                as_of=row.price_date,
                source=src,
                sheets_timestamp=_parse_sheets_timestamp(src),
                fx_as_of=_parse_fx_timestamp(src),
            )

    @staticmethod
    def get_all_latest() -> list[PriceSnapshot]:
        """
        Return the most recently stored price snapshot for every security that
        has at least one PriceHistory row.
        """
        with AppContext.read_session() as sess:
            price_repo = PriceRepository(sess)
            rows = price_repo.list_latest_all()
            snapshots = []
            for row in rows:
                price_gbp = (
                    Decimal(row.close_price_gbp)
                    if row.close_price_gbp
                    else Decimal(row.close_price_original_ccy)
                )
                src = row.source or ""
                snapshots.append(PriceSnapshot(
                    security_id=row.security_id,
                    price_gbp=price_gbp,
                    close_price_original_ccy=Decimal(row.close_price_original_ccy),
                    currency=row.currency,
                    as_of=row.price_date,
                    source=src,
                    sheets_timestamp=_parse_sheets_timestamp(src),
                    fx_as_of=_parse_fx_timestamp(src),
                ))
            return snapshots
