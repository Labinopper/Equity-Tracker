"""
SheetsFxService — fetch FX rates from the Google Sheets "fx" tab.

Design notes:
  - Reads the "fx" tab from the configured Google Spreadsheet.
  - Columns: A=pair (e.g. "USD2GBP"), B=rate, C=unused, D=as_of_timestamp.
  - Pair name format: "{BASE}2{QUOTE}" (e.g. "USD2GBP" means 1 USD = rate GBP).
  - Uses the same service account and spreadsheet as SheetsPriceService.
  - All rates are returned as Decimal — never float.
  - Header rows and blank/non-numeric rows are skipped silently.
  - Raises RuntimeError on network / auth failure so callers can handle
    gracefully (log + continue for GBP securities, fail for USD ones).
  - SheetsFxService is pure Sheets I/O — no database access.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (same spreadsheet as SheetsPriceService)
# ---------------------------------------------------------------------------

SPREADSHEET_ID = "1p4uCcOaJ_JYCLl9Sk6p2qFVeBKp4zgeJJPT35BlrwFU"
FX_TAB_NAME = "fx"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_DEFAULT_CREDS_PATH = (
    Path(__file__).parent.parent.parent / "secrets" / "equitytracker-488310-12b44164ccb2.json"
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class FxRow:
    """
    One FX rate row read from the Google Sheet "fx" tab.

    pair   : normalised to uppercase (e.g. "USD2GBP")
    rate   : Decimal — 1 unit of base currency = rate units of quote currency
             e.g. pair="USD2GBP", rate=0.7891 → 1 USD = 0.7891 GBP
    as_of  : raw timestamp string from column D (e.g. "2025-01-15 10:30:00"),
             or empty string if column D is blank
    """
    pair: str
    rate: Decimal
    as_of: str


# ---------------------------------------------------------------------------
# SheetsFxService
# ---------------------------------------------------------------------------

class SheetsFxService:
    """
    Pure Google Sheets I/O for FX rates — no database access.

    All methods are static. Raises RuntimeError on connectivity or auth failure
    so the caller (PriceService) can log the error and return a graceful result.
    """

    @staticmethod
    def _credentials_path() -> Path:
        env_val = os.environ.get("EQUITY_SHEETS_CREDENTIALS", "").strip()
        if env_val:
            return Path(env_val)
        return _DEFAULT_CREDS_PATH

    @staticmethod
    def _open_worksheet():  # type: ignore[return]
        """
        Open the fx worksheet using service account credentials.

        Returns a gspread.Worksheet. Raises RuntimeError on any failure.
        """
        try:
            import gspread  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "gspread is not installed. Run: pip install gspread"
            ) from exc

        creds_path = SheetsFxService._credentials_path()
        if not creds_path.exists():
            raise RuntimeError(
                f"Service account credentials not found at {creds_path}. "
                "Set EQUITY_SHEETS_CREDENTIALS env var or place the JSON at "
                f"{_DEFAULT_CREDS_PATH}."
            )

        try:
            gc = gspread.service_account(filename=str(creds_path))
            spreadsheet = gc.open_by_key(SPREADSHEET_ID)
            return spreadsheet.worksheet(FX_TAB_NAME)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to open Google Sheet fx tab "
                f"(id={SPREADSHEET_ID!r}, tab={FX_TAB_NAME!r}): {exc}"
            ) from exc

    @staticmethod
    def read_fx_rates() -> dict[str, FxRow]:
        """
        Read columns A:D from the fx tab.

        Returns a dict keyed by uppercase pair name → FxRow.
        Rows with a blank pair or non-numeric rate are skipped with a warning.

        Raises RuntimeError on network / auth failure.
        """
        ws = SheetsFxService._open_worksheet()
        try:
            rows: list[list[str]] = ws.get("A:D")
        except Exception as exc:
            raise RuntimeError(f"Failed to read fx range from Sheet: {exc}") from exc

        result: dict[str, FxRow] = {}
        for i, row in enumerate(rows, start=1):
            # Pad to at least 4 cells so we can index safely
            padded = row + [""] * (4 - len(row))

            raw_pair = padded[0].strip()
            raw_rate = padded[1].strip()
            raw_ts   = padded[3].strip()

            if not raw_pair:
                continue  # blank row — skip silently

            if not raw_rate:
                logger.debug("Row %d: pair %r has no rate yet — skipping.", i, raw_pair)
                continue

            pair = raw_pair.upper()

            try:
                rate = Decimal(raw_rate)
            except InvalidOperation:
                logger.warning(
                    "Row %d: non-numeric rate %r for pair %r — skipping.",
                    i, raw_rate, pair,
                )
                continue

            result[pair] = FxRow(pair=pair, rate=rate, as_of=raw_ts)

        logger.info(
            "Read %d FX rate rows from Google Sheet (tab=%r).", len(result), FX_TAB_NAME
        )
        return result
