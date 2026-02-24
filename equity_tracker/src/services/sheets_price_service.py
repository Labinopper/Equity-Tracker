"""
SheetsPriceService — fetch and sync prices via Google Sheets.

Design notes:
  - Reads the "prices" tab from the configured Google Spreadsheet.
  - Columns: A=ticker, B=price, C=currency, D=last_refresh_timestamp
  - Column B holds the price in the security's native currency (e.g. USD for
    IBM, GBP/GBX for London-listed stocks). Currency conversion to GBP is
    handled by PriceService using FX rates from the "fx" tab — not here.
  - Exception: GBX (pence) is normalised to GBP here by dividing by 100,
    since that is a unit change within the same currency, not an FX conversion.
  - All monetary values are Decimal — never float. Column B is converted via
    str() → Decimal() to avoid float precision issues.
  - Uses a service account JSON for auth (no OAuth browser flow required).
  - Credentials path: EQUITY_SHEETS_CREDENTIALS env var, or the default path
    relative to this file: ../../secrets/equitytracker-488310-12b44164ccb2.json
    (i.e. equity_tracker/secrets/).
  - All public methods fail gracefully: network/auth errors are caught and
    re-raised as RuntimeError so callers can decide how to handle them.
  - SheetsPriceService is pure Sheets I/O — it does NOT access the database.
    DB storage is the responsibility of PriceService.
  - sync_tickers() only writes to column A; columns B–D are never modified.
  - Case-insensitive ticker matching throughout.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SPREADSHEET_ID = "1p4uCcOaJ_JYCLl9Sk6p2qFVeBKp4zgeJJPT35BlrwFU"
TAB_NAME = "prices"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_DEFAULT_CREDS_PATH = (
    Path(__file__).parent.parent.parent / "secrets" / "equitytracker-488310-12b44164ccb2.json"
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class SheetRow:
    """
    One price row read from the Google Sheet.

    ticker        : normalised to uppercase
    price         : price in the security's native currency (column B).
                    For GBX rows this is already divided by 100 and
                    currency normalised to "GBP" (see read_prices).
                    For USD rows this is the raw USD price — callers must
                    apply FX conversion before treating it as GBP.
    currency      : normalised currency code from column C (e.g. "GBP", "USD").
                    GBX is normalised to "GBP" here.
    last_refresh  : raw string from column D (e.g. "2024-01-15 10:30:00").
                    Empty string when column D is blank.
    """
    ticker: str
    price: Decimal
    currency: str
    last_refresh: str


# ---------------------------------------------------------------------------
# SheetsPriceService
# ---------------------------------------------------------------------------

class SheetsPriceService:
    """
    Pure Google Sheets I/O — no database access.

    All methods are static. Raises RuntimeError on connectivity or auth failure
    so the caller (PriceService) can log the error and return a graceful result.
    """

    # ── Private helpers ────────────────────────────────────────────────────

    @staticmethod
    def _credentials_path() -> Path:
        env_val = os.environ.get("EQUITY_SHEETS_CREDENTIALS", "").strip()
        if env_val:
            return Path(env_val)
        return _DEFAULT_CREDS_PATH

    @staticmethod
    def _open_worksheet():  # type: ignore[return]
        """
        Open the prices worksheet using service account credentials.

        Returns a gspread.Worksheet. Raises RuntimeError on any failure.
        """
        try:
            import gspread  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "gspread is not installed. Run: pip install gspread"
            ) from exc

        creds_path = SheetsPriceService._credentials_path()
        if not creds_path.exists():
            raise RuntimeError(
                f"Service account credentials not found at {creds_path}. "
                "Set EQUITY_SHEETS_CREDENTIALS env var or place the JSON at "
                f"{_DEFAULT_CREDS_PATH}."
            )

        try:
            gc = gspread.service_account(filename=str(creds_path))
            spreadsheet = gc.open_by_key(SPREADSHEET_ID)
            return spreadsheet.worksheet(TAB_NAME)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to open Google Sheet (id={SPREADSHEET_ID!r}, "
                f"tab={TAB_NAME!r}): {exc}"
            ) from exc

    # ── Read ──────────────────────────────────────────────────────────────

    @staticmethod
    def read_prices() -> dict[str, SheetRow]:
        """
        Read columns A:D from the prices tab.

        Returns a dict keyed by uppercase ticker → SheetRow.
        Rows with a blank ticker or non-numeric price are skipped with a
        warning.

        Raises RuntimeError on network / auth failure.
        """
        ws = SheetsPriceService._open_worksheet()
        try:
            rows: list[list[str]] = ws.get("A:D")
        except Exception as exc:
            raise RuntimeError(f"Failed to read prices range from Sheet: {exc}") from exc

        result: dict[str, SheetRow] = {}
        for i, row in enumerate(rows, start=1):
            # Pad to at least 4 cells so we can index safely
            padded = row + [""] * (4 - len(row))

            raw_ticker = padded[0].strip()
            raw_price  = padded[1].strip()
            raw_ccy    = padded[2].strip()
            raw_ts     = padded[3].strip()

            if not raw_ticker:
                continue  # blank ticker row — skip silently
            if not raw_price:
                logger.debug("Row %d: ticker %r has no price yet — skipping.", i, raw_ticker)
                continue

            ticker = raw_ticker.upper()

            try:
                price = Decimal(raw_price)
            except InvalidOperation:
                logger.warning(
                    "Row %d: non-numeric price %r for ticker %r — skipping.",
                    i, raw_price, ticker,
                )
                continue

            # GBX normalisation: London stocks quoted in pence (GBX / GBp).
            # Divide by 100 to convert to GBP and normalise the currency code.
            # This is a unit normalisation, not FX conversion.
            ccy = raw_ccy.strip() or "GBP"
            if ccy.upper() == "GBX":
                price = (price / Decimal("100")).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                )
                ccy = "GBP"
                logger.debug(
                    "Row %d: GBX→GBP for %r: divided by 100 → %s",
                    i, ticker, price,
                )

            result[ticker] = SheetRow(
                ticker=ticker,
                price=price,
                currency=ccy,
                last_refresh=raw_ts,
            )

        logger.info(
            "Read %d price rows from Google Sheet (tab=%r).", len(result), TAB_NAME
        )
        return result

    # ── Write ─────────────────────────────────────────────────────────────

    @staticmethod
    def sync_tickers(tickers: list[str]) -> int:
        """
        Ensure every ticker in *tickers* has a row in column A.

        Matching is case-insensitive. Only column A is modified — columns B, C,
        D are never touched. New tickers are appended as single-cell rows after
        the last existing row.

        Returns the number of tickers actually appended (0 if all were already
        present).

        Raises RuntimeError on network / auth failure.
        """
        if not tickers:
            return 0

        ws = SheetsPriceService._open_worksheet()

        # Read existing column A (upper-case set for O(1) lookup)
        try:
            col_a_rows: list[list[str]] = ws.get("A:A")
        except Exception as exc:
            raise RuntimeError(
                f"Failed to read column A from Sheet for ticker sync: {exc}"
            ) from exc

        existing: set[str] = {
            row[0].strip().upper()
            for row in col_a_rows
            if row and row[0].strip()
        }

        to_append = [t.upper() for t in tickers if t.upper() not in existing]

        if not to_append:
            logger.info("sync_tickers: all %d tickers already in Sheet.", len(tickers))
            return 0

        try:
            for ticker in to_append:
                ws.append_row([ticker], value_input_option="RAW")
                logger.info("sync_tickers: appended ticker %r to Sheet.", ticker)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to append tickers to Sheet column A: {exc}"
            ) from exc

        return len(to_append)
