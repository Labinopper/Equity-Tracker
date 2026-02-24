"""
SecurityCatalogRepository — data-access for the instrument catalogue.

The catalogue is a read-mostly reference table seeded from a bundled CSV.
It is never written by user actions; only seed_from_csv() populates it.

Search ranking:
  1. Symbol prefix matches (exact ticker lookups land in position 1).
  2. Name substring matches (fill remaining slots up to limit).

The caller (service / router) holds the session; this repository never
commits or rolls back on its own.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import SecurityCatalog, _new_uuid

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Path to the bundled seed CSV (relative to this file's package root).
# Layout: equity_tracker/src/data/security_catalog.csv
# __file__ is equity_tracker/src/db/repository/catalog.py
# .parent.parent.parent reaches equity_tracker/src/
_SEED_CSV = Path(__file__).parent.parent.parent / "data" / "security_catalog.csv"


class SecurityCatalogRepository:
    """Session-scoped data-access for the security_catalog table."""

    def __init__(self, session: Session) -> None:
        self._s = session

    # ── Read ──────────────────────────────────────────────────────────────

    def search(self, q: str, limit: int = 20) -> list[SecurityCatalog]:
        """
        Ranked search: symbol-prefix matches first, then name-substring.

        Returns at most `limit` entries. Empty / whitespace q returns [].

        Ranking:
          Tier 1 — symbols that start with q (case-insensitive, ascending)
          Tier 2 — names that contain q (case-insensitive, ascending by name),
                   excluding entries already returned in Tier 1.
        """
        q = q.strip()
        if not q:
            return []

        q_upper = q.upper()

        # Tier 1: symbol prefix
        tier1: list[SecurityCatalog] = list(
            self._s.execute(
                select(SecurityCatalog)
                .where(func.upper(SecurityCatalog.symbol).like(f"{q_upper}%"))
                .order_by(SecurityCatalog.symbol)
                .limit(limit)
            ).scalars()
        )

        remaining = limit - len(tier1)
        if remaining <= 0:
            return tier1

        # Tier 2: name substring — exclude already-matched IDs
        matched_ids = {e.id for e in tier1}
        tier2_stmt = (
            select(SecurityCatalog)
            .where(SecurityCatalog.name.ilike(f"%{q}%"))
            .order_by(SecurityCatalog.name)
            .limit(remaining)
        )
        if matched_ids:
            tier2_stmt = tier2_stmt.where(SecurityCatalog.id.notin_(matched_ids))

        tier2: list[SecurityCatalog] = list(
            self._s.execute(tier2_stmt).scalars()
        )

        return tier1 + tier2

    def get_by_id(self, catalog_id: str) -> SecurityCatalog | None:
        """Return a catalogue entry by its UUID primary key."""
        return self._s.get(SecurityCatalog, catalog_id)

    def get_by_symbol(self, symbol: str) -> SecurityCatalog | None:
        """Return the first catalogue entry matching the given symbol (case-insensitive)."""
        return self._s.execute(
            select(SecurityCatalog)
            .where(func.upper(SecurityCatalog.symbol) == symbol.upper())
            .limit(1)
        ).scalar_one_or_none()

    def count(self) -> int:
        """Return total number of catalogue entries."""
        result = self._s.execute(
            select(func.count()).select_from(SecurityCatalog)
        ).scalar_one()
        return result or 0

    # ── Write ─────────────────────────────────────────────────────────────

    def seed_from_csv(self, path: Path | None = None) -> int:
        """
        Load catalogue entries from a CSV file. Skips malformed rows.
        Returns the number of rows inserted.

        CSV format (header row required):
          symbol,name,exchange,currency,isin,figi

        Rows with a missing/blank symbol, name, exchange, or currency are
        skipped. Rows where currency is not exactly 3 characters are skipped.

        Duplicate (symbol, exchange) pairs are silently skipped via INSERT OR
        IGNORE semantics — callers should use this only when the table is empty
        or intentionally re-seeding.
        """
        csv_path = path or _SEED_CSV
        if not csv_path.exists():
            logger.warning("Seed CSV not found: %s", csv_path)
            return 0

        inserted = 0
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                symbol = (row.get("symbol") or "").strip().upper()
                name = (row.get("name") or "").strip()
                exchange = (row.get("exchange") or "").strip().upper()
                currency = (row.get("currency") or "").strip().upper()

                if not symbol or not name or not exchange or not currency:
                    continue
                if len(currency) != 3:
                    continue

                entry = SecurityCatalog(
                    id=_new_uuid(),
                    symbol=symbol,
                    name=name,
                    exchange=exchange,
                    currency=currency,
                    isin=(row.get("isin") or "").strip() or None,
                    figi=(row.get("figi") or "").strip() or None,
                )
                self._s.add(entry)
                inserted += 1

        logger.info("Seeded %d entries from %s", inserted, csv_path.name)
        return inserted
