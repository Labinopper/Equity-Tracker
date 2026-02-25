"""
PriceService â€” fetch and store live market prices via Google Sheets.

Design notes:
  - fetch_all() reads the Google Sheets "prices" tab once and stores a
    PriceHistory row for every security whose ticker appears in the sheet.
  - After storing the latest row, fetch_all()/fetch_and_store() can backfill
    missing historical daily closes (source="yfinance_history") down to the
    earliest lot acquisition date for that security.
  - fetch_and_store() reads the sheet and stores for one specific security.
  - Currency conversion rules:
      GBP  -> price stored as-is (no conversion needed).
      GBX  -> normalised to GBP by SheetsPriceService (÷100) before reaching
             this layer; treated identically to GBP here.
      Non-GBP -> converted to GBP using provider FX rates.
  - The source field encodes both timestamps for later display:
      GBP securities : "google_sheets:{price_ts}"
      Non-GBP securities : "google_sheets:{price_ts}|fx:{fx_ts}"
  - All monetary values are Decimal throughout.
  - PriceService methods are static â€” no instance state required.
  - Failures from SheetsPriceService or SheetsFxService are caught here;
    per-security failures are recorded but do not abort the loop.
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
from .sheets_fx_service import SheetsFxService
from .sheets_price_service import SheetsPriceService

logger = logging.getLogger(__name__)

# Source prefix used when storing Sheets prices in PriceHistory.source.
# Format: "google_sheets:{price_ts}"
#   or    "google_sheets:{price_ts}|fx:{fx_ts}"  for non-GBP securities.
_SHEETS_SOURCE_PREFIX = "google_sheets:"
_FX_SEPARATOR = "|fx:"
_HISTORY_SOURCE = "yfinance_history"
_GBP_DECIMAL_QUANT = Decimal("0.0001")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class PriceSnapshot:
    """
    Latest price for one security, normalised to GBP.

    close_price_original_ccy : price in the security's native currency
                               (USD for a USD stock; same as price_gbp for GBP).
    currency                 : normalised ISO code from the sheet (column C).
    price_gbp                : GBP price after FX conversion.
    as_of                    : the date the price refers to (today's UTC date
                               at fetch time, or parsed from the source field).
    source                   : full source field as stored in PriceHistory.
    sheets_timestamp         : the raw column D price-tab string, extracted
                               from source for convenient display; None when
                               source is not a Sheets row.
    fx_as_of                 : the "fx" tab column D timestamp used for the
                               USDâ†’GBP conversion; None for GBP securities.
    """
    security_id: str
    price_gbp: Decimal
    close_price_original_ccy: Decimal
    currency: str
    as_of: date
    source: str = field(default="google_sheets:")
    sheets_timestamp: str | None = field(default=None)
    fx_as_of: str | None = field(default=None)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_source(sheets_timestamp: str) -> str:
    """Encode a Sheets price timestamp into the PriceHistory.source field."""
    return f"{_SHEETS_SOURCE_PREFIX}{sheets_timestamp}"


def _build_source_with_fx(sheets_timestamp: str, fx_timestamp: str) -> str:
    """Encode both Sheets price and FX timestamps into the source field."""
    return f"{_SHEETS_SOURCE_PREFIX}{sheets_timestamp}{_FX_SEPARATOR}{fx_timestamp}"


def _parse_sheets_timestamp(source: str) -> str | None:
    """
    Extract the price-tab timestamp from a source field, stripping any FX suffix.
    Returns None if the source is not a google_sheets row or the timestamp is empty.
    """
    if source and source.startswith(_SHEETS_SOURCE_PREFIX):
        ts = source[len(_SHEETS_SOURCE_PREFIX):]
        # Strip FX part if present: "2025-01-15 10:00:00|fx:2025-01-15 09:58:00"
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


def _read_history_closes(
    *,
    symbol: str,
    start_date: date,
    end_date: date,
) -> dict[date, Decimal]:
    """
    Read daily close prices from yfinance for [start_date, end_date].
    """
    if start_date > end_date:
        return {}

    try:
        import yfinance as yf  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - dependency is present in app env
        raise RuntimeError("yfinance is unavailable.") from exc

    # yfinance end is exclusive, so add one day.
    hist = yf.Ticker(symbol).history(
        start=start_date.isoformat(),
        end=(end_date + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=False,
        actions=False,
    )
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
        Backfill pre-existing daily closes to acquisition-date coverage.

        Behavior:
          - Only backfills missing history before the earliest stored price_date.
          - Never backfills today's row (latest live price remains Sheets-driven).
          - Uses yfinance daily closes and stores rows in price_history.
        """
        acquisition_start = PriceService._earliest_lot_date(security_id)
        if acquisition_start is None:
            return 0

        backfill_end = datetime.now(tz=timezone.utc).date() - timedelta(days=1)
        if acquisition_start > backfill_end:
            return 0

        with AppContext.read_session() as sess:
            earliest_price_date = PriceRepository(sess).get_earliest_price_date(security_id)

        if earliest_price_date is not None and earliest_price_date <= acquisition_start:
            return 0

        if earliest_price_date is None:
            start_date = acquisition_start
            end_date = backfill_end
        else:
            start_date = acquisition_start
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
    def fetch_and_store(security_id: str) -> PriceSnapshot:
        """
        Fetch the latest market price for a security via Google Sheets,
        convert to GBP if needed via provider FX rates, and persist a
        PriceHistory row.

        Raises:
            ValueError   : if the security is not found in the database.
            RuntimeError : if the sheet is unavailable, the ticker has no
                           price row, or a required FX rate is missing.

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

        # 2. Read prices from sheet â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        prices = SheetsPriceService.read_prices()
        row = prices.get(ticker.upper())
        if row is None:
            raise RuntimeError(
                f"Ticker {ticker!r} not found in Google Sheets prices tab. "
                "Add it to the sheet or call POST /prices/sync-tickers."
            )

        # 3. Apply FX conversion for non-GBP currencies â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        price_date = datetime.now(tz=timezone.utc).date()
        fx_as_of: str | None = None
        native_to_gbp_rate_for_backfill: Decimal | None = None

        if row.currency.upper() == "GBP":
            # GBP (or GBX already normalised to GBP by SheetsPriceService)
            price_gbp = row.price.quantize(_GBP_DECIMAL_QUANT, rounding=ROUND_HALF_UP)
            source = _build_source(row.last_refresh)
        else:
            fx_rates = SheetsFxService.read_fx_rates()
            quote = FxService.get_rate(row.currency, "GBP", rates=fx_rates)
            price_gbp = (row.price * quote.rate).quantize(
                _GBP_DECIMAL_QUANT, rounding=ROUND_HALF_UP
            )
            native_to_gbp_rate_for_backfill = quote.rate
            fx_as_of = quote.as_of
            source = _build_source_with_fx(row.last_refresh, quote.as_of or "")

        # 4. Persist PriceHistory â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
                close_price_original_ccy=str(row.price),
                currency=row.currency,
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

        logger.info(
            "Stored Sheets price for %s (%s): Â£%s [%s]",
            ticker, security_id, price_gbp, row.last_refresh or "no timestamp",
        )

        PriceService._backfill_history_for_security(
            security_id=security_id,
            ticker=ticker,
            currency=currency,
            exchange=exchange,
            native_to_gbp_rate=native_to_gbp_rate_for_backfill,
        )

        return PriceSnapshot(
            security_id=security_id,
            price_gbp=price_gbp,
            close_price_original_ccy=row.price,
            currency=row.currency,
            as_of=price_date,
            source=source,
            sheets_timestamp=row.last_refresh or None,
            fx_as_of=fx_as_of,
        )

    @staticmethod
    def fetch_all() -> dict:
        """
        Read all prices from the Google Sheets "prices" tab and the FX rates
        from the "fx" tab in a single run, then store a PriceHistory row for
        every matching security.

        Non-GBP prices are converted to GBP using provider FX rates.
        GBP/GBX prices are stored as-is (GBX already normalised by
        SheetsPriceService).

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
        completes.  If the Sheet itself is unavailable, all securities are
        recorded as failed.
        """
        # 1. Read all securities from DB â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        with AppContext.read_session() as sess:
            sec_repo = SecurityRepository(sess)
            securities = sec_repo.list_all()
            sec_items = [(s.id, s.ticker, s.currency, s.exchange) for s in securities]

        # 2. Read all prices from Sheet in one call â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        try:
            sheet_prices = SheetsPriceService.read_prices()
        except RuntimeError as exc:
            logger.error("Google Sheets prices tab unavailable: %s", exc)
            return {
                "fetched": 0,
                "failed": len(sec_items),
                "backfilled_days": 0,
                "errors": [
                    {
                        "security_id": sid,
                        "ticker": ticker,
                        "error": f"Sheet unavailable: {exc}",
                    }
                    for sid, ticker, _, _ in sec_items
                ],
            }

        # 3. Read FX rates once for all securities â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        try:
            fx_rates = SheetsFxService.read_fx_rates()
        except RuntimeError as exc:
            logger.warning(
                "Google Sheets fx tab unavailable - non-GBP securities will fail: %s",
                exc,
            )
            fx_rates = {}

        # 4. Store a PriceHistory row per matched security â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        price_date = datetime.now(tz=timezone.utc).date()
        fetched = 0
        backfilled_days = 0
        errors: list[dict] = []

        for security_id, ticker, currency, exchange in sec_items:
            row = sheet_prices.get(ticker.upper())
            if row is None:
                msg = (
                    f"Ticker {ticker!r} not found in Sheets. "
                    "Run POST /prices/sync-tickers to add it."
                )
                logger.warning("No Sheets price for %s (%s): %s", ticker, security_id, msg)
                errors.append({
                    "security_id": security_id,
                    "ticker": ticker,
                    "error": msg,
                })
                continue

            try:
                native_to_gbp_rate: Decimal | None = None
                # Apply FX conversion for non-GBP currencies
                if row.currency.upper() != "GBP":
                    quote = FxService.get_rate(row.currency, "GBP", rates=fx_rates)
                    price_gbp = (row.price * quote.rate).quantize(
                        _GBP_DECIMAL_QUANT,
                        rounding=ROUND_HALF_UP,
                    )
                    native_to_gbp_rate = quote.rate
                    source = _build_source_with_fx(row.last_refresh, quote.as_of or "")
                else:
                    price_gbp = row.price.quantize(
                        _GBP_DECIMAL_QUANT,
                        rounding=ROUND_HALF_UP,
                    )
                    source = _build_source(row.last_refresh)

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
                        close_price_original_ccy=str(row.price),
                        currency=row.currency,
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

                logger.info(
                    "Stored Sheets price for %s: Â£%s [%s]",
                    ticker, price_gbp, row.last_refresh or "no timestamp",
                )
                fetched += 1
                backfilled_days += PriceService._backfill_history_for_security(
                    security_id=security_id,
                    ticker=ticker,
                    currency=currency,
                    exchange=exchange,
                    native_to_gbp_rate=native_to_gbp_rate,
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
