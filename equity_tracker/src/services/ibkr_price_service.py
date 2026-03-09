"""
IbkrPriceService — bridge between ibkr_poller's ib_market_data.db
and the equity tracker's main PriceHistory / PriceTickerSnapshot tables.

Called on portfolio page load to pull the latest IB snapshot into the main
DB so the existing price-display logic (SecuritySummary, freshness badge,
staleness flags) picks up live/delayed IB prices without any template changes.

FX conversion strategy
──────────────────────
For non-GBP securities (e.g. IBM / USD) we derive the GBP rate from the
most recent PriceHistory row already in the main DB (gbp / native). If no
prior row exists we fall back to FxService live FX resolution.

Source field
────────────
PriceHistory.source is set to "ibkr" — a fixed key so that upsert() updates
the same row on every call rather than accumulating rows.

PriceTickerSnapshot.source is set to "ibkr" and observed_at is set to the
IB snapshot's as_of_utc timestamp.  This drives freshness_text ("Updated
X minutes ago") shown in the portfolio daily-change badge.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..app_context import AppContext
from ..db.models import Security
from ..db.repository import PriceRepository

logger = logging.getLogger(__name__)

# Fixed source tag written to PriceHistory and PriceTickerSnapshot.
_IBKR_SOURCE = "ibkr"

# GBP quantisation (4 dp — matches existing price_service convention).
_QUANT = Decimal("0.0001")

# Path to ib_market_data.db, resolved relative to this file's location:
#   equity_tracker/src/services/ibkr_price_service.py
#   parents[3] → project root → ib_market_data.db
_DEFAULT_IB_DB = Path(__file__).parents[3] / "ib_market_data.db"


class IbkrPriceService:
    """Static helpers — no instance state required."""

    @staticmethod
    def ingest_all(ib_db_path: str | Path | None = None) -> int:
        """
        Read the latest IB snapshot for every symbol in ib_market_data.db
        and write it into the main app's PriceHistory + PriceTickerSnapshot.

        Returns the number of securities successfully updated.
        Silently returns 0 if the IB database does not exist yet.
        """
        path = Path(ib_db_path) if ib_db_path else _DEFAULT_IB_DB
        if not path.exists():
            logger.debug("IbkrPriceService: %s not found — skipping", path)
            return 0

        snapshots = _read_latest_snapshots(path)
        if not snapshots:
            return 0

        updated = 0
        with AppContext.write_session() as sess:
            price_repo = PriceRepository(sess)
            for symbol, snap in snapshots.items():
                try:
                    if _ingest_one(sess, price_repo, symbol, snap):
                        updated += 1
                except Exception as exc:
                    logger.warning("IbkrPriceService: skip %s — %s", symbol, exc)

        logger.info("IbkrPriceService: ingested %d symbol(s)", updated)
        return updated


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_latest_snapshots(path: Path) -> dict[str, dict]:
    """
    Return {symbol: row_dict} for the most recent snapshot per symbol.
    Uses a read-only URI connection so the IB poller's WAL file is not disturbed.
    """
    uri = f"file:{path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        # Fallback for older SQLite builds that do not support URI mode.
        conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT symbol, as_of_utc, last, bid, ask, mid, source
            FROM   price_snapshot
            WHERE  (symbol, as_of_utc) IN (
                       SELECT symbol, MAX(as_of_utc)
                       FROM   price_snapshot
                       GROUP  BY symbol
                   )
            """
        ).fetchall()
        return {r["symbol"]: dict(r) for r in rows}
    except sqlite3.OperationalError as exc:
        logger.warning("IbkrPriceService: could not read price_snapshot — %s", exc)
        return {}
    finally:
        conn.close()


def _resolve_native_price(snap: dict) -> Decimal | None:
    """Pick the best available price from a snapshot row (last > mid > bid/ask)."""
    for key in ("last", "mid"):
        raw = snap.get(key)
        if raw:
            try:
                p = Decimal(str(raw))
                if p > 0:
                    return p
            except Exception:
                pass
    bid_raw = snap.get("bid")
    ask_raw = snap.get("ask")
    if bid_raw and ask_raw:
        try:
            p = (Decimal(str(bid_raw)) + Decimal(str(ask_raw))) / 2
            if p > 0:
                return p
        except Exception:
            pass
    return None


def _find_security(sess: Session, ticker: str) -> Security | None:
    """Look up a Security by ticker (case-insensitive)."""
    ticker_upper = ticker.strip().upper()
    return sess.scalars(
        select(Security)
        .where(func.upper(Security.ticker) == ticker_upper)
        .limit(1)
    ).first()


def _derive_fx_rate(
    price_repo: PriceRepository,
    security_id: str,
    price_native: Decimal,
) -> tuple[Decimal | None, str | None]:
    """
    Extract the implied GBP/native FX rate from the most recent PriceHistory
    row.  Returns None if the row is missing or the division is not possible.
    """
    row = price_repo.get_latest(security_id)
    if row is None:
        return None, None
    try:
        gbp = Decimal(row.close_price_gbp)
        native = Decimal(row.close_price_original_ccy)
        if native > 0:
            fx_as_of = None
            src = row.source or ""
            if "|fx:" in src:
                _, fx_part = src.split("|fx:", 1)
                fx_as_of = fx_part or None
            return gbp / native, fx_as_of
    except (TypeError, ValueError):
        pass
    return None, None


def _ingest_one(sess: Session, price_repo: PriceRepository,
                symbol: str, snap: dict) -> bool:
    """
    Write one symbol's IB snapshot into PriceHistory + PriceTickerSnapshot.
    Returns True on success.
    """
    price_native = _resolve_native_price(snap)
    if price_native is None:
        return False

    # Parse snapshot UTC timestamp.
    as_of_str = snap.get("as_of_utc", "")
    try:
        snapshot_dt = datetime.fromisoformat(as_of_str.rstrip("Z")).replace(
            tzinfo=timezone.utc
        )
    except (ValueError, AttributeError):
        snapshot_dt = datetime.now(timezone.utc)
    snap_date = snapshot_dt.date()

    security = _find_security(sess, symbol)
    if security is None:
        logger.debug("IbkrPriceService: no security for ticker=%s", symbol)
        return False

    currency = (security.currency or "GBP").strip().upper()

    # Convert to GBP.
    source = _IBKR_SOURCE
    if currency in ("GBP", "GBX"):
        price_gbp = price_native / 100 if currency == "GBX" else price_native
    else:
        fx_rate, fx_as_of = _derive_fx_rate(price_repo, security.id, price_native)
        if fx_rate is None:
            # No prior row — fall back to live FxService resolution.
            try:
                from .fx_service import FxService  # noqa: PLC0415
                quote = FxService.get_rate(currency, "GBP")
                fx_rate = quote.rate
                fx_as_of = quote.as_of
            except Exception as exc:
                logger.warning(
                    "IbkrPriceService: FX unavailable for %s (%s): %s",
                    symbol, currency, exc,
                )
                return False
        if fx_as_of:
            source = f"{_IBKR_SOURCE}|fx:{fx_as_of}"
        price_gbp = (price_native * fx_rate).quantize(_QUANT, rounding=ROUND_HALF_UP)

    price_gbp_str    = str(price_gbp)
    price_native_str = str(price_native)

    # Upsert PriceHistory — source="ibkr" is the upsert key, so the same row
    # is updated on each call rather than accumulating one row per poll cycle.
    price_repo.upsert(
        security.id,
        snap_date,
        price_native_str,
        currency,
        source,
        close_price_gbp=price_gbp_str,
    )

    # Append a PriceTickerSnapshot so freshness_text reflects the IB snapshot
    # time ("Updated X minutes ago" in the portfolio daily-change badge).
    price_repo.add_ticker_snapshot(
        security_id=security.id,
        price_date=snap_date,
        price_native=price_native_str,
        currency=currency,
        price_gbp=price_gbp_str,
        source=source,
        observed_at=snapshot_dt,
    )

    logger.info(
        "IbkrPriceService: %s  %s %s -> GBP %s  as_of=%s",
        symbol, price_native_str, currency, price_gbp_str, as_of_str,
    )
    return True
