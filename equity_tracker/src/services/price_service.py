"""
PriceService - fetch and store live market prices via yfinance.

Design notes:
  - fetch_all() reads yfinance and stores one PriceHistory row per security.
  - fetch_and_store() fetches yfinance for a single security.
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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import func, select

from ..app_context import AppContext
from ..db.models import Lot
from ..db.repository import PriceRepository, SecurityRepository
from .fx_service import FxService

logger = logging.getLogger(__name__)

# Source prefix used when storing yfinance prices in PriceHistory.source.
# Format: "yfinance:{price_ts}" or "yfinance:{price_ts}|fx:{fx_ts}".
_LIVE_SOURCE_PREFIX = "yfinance:"
_FX_SEPARATOR = "|fx:"
_HISTORY_SOURCE = "yfinance_history"
_GBP_DECIMAL_QUANT = Decimal("0.0001")
_FX_TS_FMT = "%Y-%m-%d %H:%M:%S"

# Days of pre-acquisition price history to maintain per security.
_PRE_ACQUISITION_DAYS = 365


class _HistoryRateLimitError(Exception):
    """Raised when yfinance signals a rate-limit (HTTP 429) response."""


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

def _build_source(price_timestamp: str) -> str:
    """Encode a live price timestamp into the PriceHistory.source field."""
    return f"{_LIVE_SOURCE_PREFIX}{price_timestamp}"


def _build_source_with_fx(price_timestamp: str, fx_timestamp: str) -> str:
    """Encode both live price and FX timestamps into the source field."""
    return f"{_LIVE_SOURCE_PREFIX}{price_timestamp}{_FX_SEPARATOR}{fx_timestamp}"


def _parse_sheets_timestamp(source: str) -> str | None:
    """
    Extract the price timestamp from a source field, stripping any FX suffix.
    Supports both legacy google_sheets and current yfinance source prefixes.
    """
    if source:
        for prefix in ("google_sheets:", _LIVE_SOURCE_PREFIX):
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
    ) -> tuple[Decimal, str, date, str]:
        """
        Resolve latest native price + currency metadata for one security.

        Returns:
          (native_price, quote_currency, price_date, provider_timestamp)
        """
        native_price, price_date, provider_ts, provider_currency = _read_live_close(
            ticker=ticker,
            exchange=exchange,
        )
        security_currency = (currency or "GBP").strip().upper()
        quote_currency = _normalize_provider_currency(provider_currency) or security_currency

        # yfinance uses GBp/GBX for many LSE quotes; normalize to GBP.
        if quote_currency == "GBX":
            native_price = (native_price / Decimal("100")).quantize(
                _GBP_DECIMAL_QUANT,
                rounding=ROUND_HALF_UP,
            )
            quote_currency = "GBP"

        return native_price, quote_currency, price_date, provider_ts

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
    def _earliest_lot_date(security_id: str) -> date | None:
        """Return the earliest recorded acquisition date for a security."""
        with AppContext.read_session() as sess:
            stmt = select(func.min(Lot.acquisition_date)).where(Lot.security_id == security_id)
            return sess.scalar(stmt)

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
    def fetch_and_store(security_id: str) -> PriceSnapshot:
        """
        Fetch the latest market price for a security via yfinance,
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
        native_price, quote_currency, price_date, provider_ts = PriceService._fetch_live_components(
            ticker=ticker,
            currency=currency,
            exchange=exchange,
        )

        # 3. Apply FX conversion for non-GBP currencies
        fx_as_of: str | None = None
        native_to_gbp_rate_for_backfill: Decimal | None = None

        if quote_currency == "GBP":
            price_gbp = native_price.quantize(_GBP_DECIMAL_QUANT, rounding=ROUND_HALF_UP)
            source = _build_source(provider_ts)
        else:
            quote = FxService.get_rate(quote_currency, "GBP")
            price_gbp = (native_price * quote.rate).quantize(
                _GBP_DECIMAL_QUANT, rounding=ROUND_HALF_UP
            )
            native_to_gbp_rate_for_backfill = quote.rate
            fx_as_of = quote.as_of or _current_utc_timestamp()
            source = _build_source_with_fx(provider_ts, fx_as_of)

        # 4. Persist PriceHistory
        PriceService._persist_live_price(
            security_id=security_id,
            quote_currency=quote_currency,
            native_price=native_price,
            price_gbp=price_gbp,
            price_date=price_date,
            source=source,
        )

        logger.info(
            "Stored live yfinance price for %s (%s): £%s [%s]",
            ticker,
            security_id,
            price_gbp,
            provider_ts,
        )

        try:
            PriceService._backfill_history_for_security(
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
    def fetch_all() -> dict:
        """
        Read all prices from yfinance and store a PriceHistory row for every
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

        # 2. Store a PriceHistory row per security
        fetched = 0
        backfilled_days = 0
        errors: list[dict] = []

        for security_id, ticker, currency, exchange in sec_items:
            try:
                native_price, quote_currency, price_date, provider_ts = (
                    PriceService._fetch_live_components(
                        ticker=ticker,
                        currency=currency,
                        exchange=exchange,
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
                    source = _build_source_with_fx(provider_ts, fx_as_of)
                else:
                    price_gbp = native_price.quantize(
                        _GBP_DECIMAL_QUANT,
                        rounding=ROUND_HALF_UP,
                    )
                    source = _build_source(provider_ts)

                PriceService._persist_live_price(
                    security_id=security_id,
                    quote_currency=quote_currency,
                    native_price=native_price,
                    price_gbp=price_gbp,
                    price_date=price_date,
                    source=source,
                )

                logger.info(
                    "Stored live yfinance price for %s: £%s [%s]",
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
