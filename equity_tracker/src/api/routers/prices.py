"""
Prices router - live market price fetching and retrieval.

Phase L endpoints
-----------------
  POST /prices/refresh       Fetch latest prices for all securities
  GET  /prices               Latest stored price per security as JSON
  POST /prices/sync-tickers  Legacy no-op endpoint

Error mapping
-------------
  AppContextError (DB not initialised) -> HTTP 503 (caught by db_required dep)
  Per-security fetch failures are captured and returned in the response body.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...services.price_service import PriceService
from .. import _state
from ..dependencies import db_required, session_required

router = APIRouter(prefix="/prices", tags=["prices"], dependencies=[Depends(session_required)])


# ---------------------------------------------------------------------------
# Response schemas (local â€” price-specific, not worth adding to portfolio.py)
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
    Read live provider prices and store the latest price for every
    security.

    Returns a count of successful fetches and a list of per-security errors.
    Partial failures do not cause a non-200 response
    â€” the result body indicates which securities failed and why.
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
    summary="Legacy no-op ticker sync endpoint",
)
async def sync_tickers(
    _: None = Depends(db_required),
) -> SyncTickersResultSchema:
    """
    Legacy compatibility endpoint.

    The pricing stack no longer requires ticker-sheet synchronization, so this
    route returns a deterministic no-op response.
    """
    return SyncTickersResultSchema(appended=0, tickers_appended=[], error=None)
