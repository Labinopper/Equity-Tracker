"""
Pydantic schemas for the security catalogue API.
"""

from __future__ import annotations

from pydantic import BaseModel


class CatalogEntrySchema(BaseModel):
    """A single result from the catalogue search endpoint."""

    id: str
    symbol: str
    name: str
    exchange: str
    currency: str
    isin: str | None = None
