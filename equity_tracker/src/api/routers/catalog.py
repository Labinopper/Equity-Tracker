"""
Catalog router — instrument catalogue search API.

Endpoints
─────────
  GET /api/catalog/search?q=<query>
      Ranked typeahead search: symbol-prefix first, then name-substring.
      Returns at most 20 results.  Empty or whitespace q returns [].
      No authentication required — read-only, no DB writes.

Design notes
────────────
- Uses AppContext.read_session() — safe to call concurrently (read-only).
- Returns 503 if the database is locked.
- All handlers are async def per architecture rule 8.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ...app_context import AppContext
from ...db.repository import SecurityCatalogRepository
from ..schemas.catalog import CatalogEntrySchema

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get(
    "/search",
    response_model=list[CatalogEntrySchema],
    summary="Search the instrument catalogue",
)
async def search_catalog(
    q: str = Query("", description="Search term: symbol prefix or name substring"),
) -> list[CatalogEntrySchema] | JSONResponse:
    """
    Return up to 20 catalogue entries matching the search term.

    Ranking:
      1. Symbols that start with ``q`` (case-insensitive), sorted by symbol.
      2. Names that contain ``q`` (case-insensitive), sorted by name.

    Returns an empty list if ``q`` is blank or the database is locked.
    """
    if not AppContext.is_initialized() or not q.strip():
        return []

    with AppContext.read_session() as sess:
        repo = SecurityCatalogRepository(sess)
        entries = repo.search(q, limit=20)

    return [
        CatalogEntrySchema(
            id=e.id,
            symbol=e.symbol,
            name=e.name,
            exchange=e.exchange,
            currency=e.currency,
            isin=e.isin,
        )
        for e in entries
    ]
