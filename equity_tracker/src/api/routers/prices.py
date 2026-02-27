"""
Prices router — live market price fetching and retrieval.

Phase L endpoints
──────────────────
  POST /prices/refresh       Fetch latest prices for all securities from Sheets
  GET  /prices               Latest stored price per security as JSON
  POST /prices/sync-tickers  Append missing ticker rows to the Sheets prices tab

Error mapping
─────────────
  AppContextError (DB not initialised) → HTTP 503 (caught by db_required dep)
  Per-security fetch failures are captured and returned in the response body
  (the endpoint itself always returns 200 as long as the DB is available).
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...db.repository import SecurityRepository
from ...app_context import AppContext
from ...services.price_service import PriceService
from ...services.sheets_price_service import SheetsPriceService
from .. import _state
from ..dependencies import db_required, session_required

router = APIRouter(prefix="/prices", tags=["prices"], dependencies=[Depends(session_required)])


# ---------------------------------------------------------------------------
# Response schemas (local — price-specific, not worth adding to portfolio.py)
# ---------------------------------------------------------------------------

class PriceSnapshotSchema(BaseModel):
    """Latest price for one security."""
    security_id: str
    price_gbp: str
    close_price_original_ccy: str
    currency: str
    as_of: date
    source: str


class RefreshErrorSchema(BaseModel):
    security_id: str
    ticker: str
    error: str


class RefreshResultSchema(BaseModel):
    fetched: int
    failed: int
    errors: list[RefreshErrorSchema]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/refresh",
    response_model=RefreshResultSchema,
    summary="Fetch latest prices for all securities",
)
async def refresh_prices(
    _: None = Depends(db_required),
) -> RefreshResultSchema:
    """
    Read the Google Sheets prices tab and store the latest price for every
    matching security.

    Returns a count of successful fetches and a list of per-security errors.
    Partial failures (e.g. ticker not in sheet) do not cause a non-200 response
    — the result body indicates which securities failed and why.
    """
    try:
        result = PriceService.fetch_all()
        _state.record_refresh_result(result)
    except Exception as exc:
        _state.record_refresh_exception(str(exc))
        raise
    return RefreshResultSchema(
        fetched=result["fetched"],
        failed=result["failed"],
        errors=[RefreshErrorSchema(**e) for e in result["errors"]],
    )


@router.get(
    "",
    response_model=list[PriceSnapshotSchema],
    summary="Latest stored price per security",
)
async def get_prices(
    _: None = Depends(db_required),
) -> list[PriceSnapshotSchema]:
    """
    Return the most recently stored price snapshot for every security that
    has at least one price_history row.

    Securities with no stored price are not included.
    """
    snapshots = PriceService.get_all_latest()
    return [
        PriceSnapshotSchema(
            security_id=snap.security_id,
            price_gbp=str(snap.price_gbp),
            close_price_original_ccy=str(snap.close_price_original_ccy),
            currency=snap.currency,
            as_of=snap.as_of,
            source=snap.source,
        )
        for snap in snapshots
    ]


# ---------------------------------------------------------------------------
# Sync tickers
# ---------------------------------------------------------------------------

class SyncTickersResultSchema(BaseModel):
    appended: int
    tickers_appended: list[str]
    error: str | None = None


@router.post(
    "/sync-tickers",
    response_model=SyncTickersResultSchema,
    summary="Append missing tickers to the Google Sheets prices tab",
)
async def sync_tickers(
    _: None = Depends(db_required),
) -> SyncTickersResultSchema:
    """
    Read all security tickers from the database and append any that are missing
    from column A of the Google Sheets prices tab.

    Matching is case-insensitive. Only column A is modified — existing prices
    in columns B–D are never touched.

    Returns the count and list of tickers actually appended. If the Sheet is
    unavailable the error is returned in the response body (HTTP 200) so the
    caller can display it gracefully.
    """
    with AppContext.read_session() as sess:
        sec_repo = SecurityRepository(sess)
        tickers = [s.ticker for s in sec_repo.list_all()]

    if not tickers:
        return SyncTickersResultSchema(appended=0, tickers_appended=[])

    try:
        # Read which tickers are already in the sheet
        sheet_prices = SheetsPriceService.read_prices()
        existing_upper = set(sheet_prices.keys())
        to_append = [t for t in tickers if t.upper() not in existing_upper]

        appended = SheetsPriceService.sync_tickers(tickers)
        return SyncTickersResultSchema(
            appended=appended,
            tickers_appended=to_append[:appended],
        )
    except RuntimeError as exc:
        return SyncTickersResultSchema(
            appended=0,
            tickers_appended=[],
            error=str(exc),
        )
