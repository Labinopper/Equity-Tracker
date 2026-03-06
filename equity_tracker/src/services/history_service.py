"""
HistoryService — historical price and portfolio value computation.

Entry points
────────────
  HistoryService.get_security_history(security_id)
      Per-security price series, cost-basis overlays, lot events, and
      30d / 90d / 365d summary stats.

  HistoryService.get_portfolio_history()
      Accurate portfolio value over time, reconstructed using the
      "add-back future disposals" method: starts from lot.quantity_remaining
      (settled ground truth) and adds back LotDisposal events after each
      historical date.  Immune to pre-import disposal gaps.

Price kind classification
─────────────────────────
Only "daily" sources (yfinance_history, google_sheets:*) are included in
history charts.  Intraday IBKR snapshots (source == "ibkr") are excluded.

When multiple daily rows exist for the same (security, date), the source
priority is: google_sheets:* (0) > yfinance_history (1).

FX strategy
───────────
GBP values are taken directly from PriceHistory.close_price_gbp (stored at
fetch time with the then-current FX rate).  Today's FX rate is never
back-applied.  For GBP/GBX securities where close_price_gbp is null, the
value is derived from close_price_original_ccy.  For non-GBP securities
with no stored GBP value, the row is excluded rather than using stale FX.
"""

from __future__ import annotations

import bisect
import logging
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from types import SimpleNamespace

from sqlalchemy import select

from ..app_context import AppContext
from ..db.models import LotDisposal, PriceHistory, Security, Transaction
from ..db.repository import (
    LotRepository,
    PriceRepository,
    SecurityRepository,
)
from ..settings import AppSettings
from .portfolio_service import _estimate_sell_all_employment_tax
from .dividend_service import DividendService

logger = logging.getLogger(__name__)

_QUANT2 = Decimal("0.01")
_QUANT4 = Decimal("0.0001")

# Sources that produce end-of-day closing prices (not intraday snapshots).
_DAILY_PREFIXES = ("yfinance_history", "google_sheets")


def _is_daily(source: str) -> bool:
    return any(source.startswith(p) for p in _DAILY_PREFIXES)


def _source_priority(source: str) -> int:
    """Lower integer = higher priority when deduplicating same-date rows."""
    if source.startswith("google_sheets"):
        return 0
    if source == "yfinance_history":
        return 1
    return 99


def _safe_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def _row_to_gbp(row: PriceHistory, currency: str) -> Decimal | None:
    """
    Extract a GBP price from a PriceHistory row.
    Prefers close_price_gbp (stored at fetch time).
    Derives from native only for GBP/GBX securities where close_price_gbp is null.
    Returns None for non-GBP securities with no stored GBP value.
    """
    gbp = _safe_decimal(row.close_price_gbp)
    if gbp is not None and gbp > 0:
        return gbp

    native = _safe_decimal(row.close_price_original_ccy)
    if native is None or native <= 0:
        return None

    cur = currency.upper()
    if cur == "GBX":
        return native / 100
    if cur == "GBP":
        return native
    return None  # Non-GBP: no live FX available, exclude this row.


def _dedup_daily_rows(
    rows: list[PriceHistory], currency: str
) -> dict[date, Decimal]:
    """
    Select one GBP price per calendar date from a list of PriceHistory rows.
    Filters to daily sources only; applies source priority (lower = better).
    Returns {price_date: close_price_gbp}.
    """
    best: dict[date, tuple[int, Decimal]] = {}  # date → (priority, price_gbp)
    for row in rows:
        if not _is_daily(row.source):
            continue
        gbp = _row_to_gbp(row, currency)
        if gbp is None:
            continue
        prio = _source_priority(row.source)
        d = row.price_date
        if d not in best or prio < best[d][0]:
            best[d] = (prio, gbp)
    return {d: v[1] for d, v in best.items()}


def _dedup_native(rows: list[PriceHistory]) -> dict[date, str | None]:
    """
    Select the best native (original-currency) price string per date,
    using the same source priority. Used for display alongside GBP.
    """
    best: dict[date, tuple[int, str | None]] = {}
    for row in rows:
        if not _is_daily(row.source):
            continue
        prio = _source_priority(row.source)
        d = row.price_date
        if d not in best or prio < best[d][0]:
            best[d] = (prio, row.close_price_original_ccy)
    return {d: v[1] for d, v in best.items()}


def _is_lot_locked_on(lot, d: date) -> bool:
    """True if the lot cannot be sold on date d (still within its lock window).

    Only ESPP+ matched (free) shares have a time-based lock — 183 days from
    acquisition.
    """
    if lot.scheme_type == "ESPP_PLUS" and lot.matching_lot_id is not None:
        end = lot.forfeiture_period_end or (lot.acquisition_date + timedelta(days=183))
        return d < end
    return False


def _is_lot_sellable_on(lot, d: date) -> bool:
    """
    True if the lot is sellable on date d.

    Rule of thumb:
      - RSU: sellable once the vest/acquisition date is reached.
      - Other schemes: sellable unless explicitly lock-constrained
        (e.g. ESPP+ matched lots inside forfeiture window).
    """
    if lot.scheme_type == "RSU":
        return d >= lot.acquisition_date
    return not _is_lot_locked_on(lot, d)


def _q2(v: Decimal) -> Decimal:
    return v.quantize(_QUANT2, rounding=ROUND_HALF_UP)


def _shadow_lot_with_qty(lot, qty: Decimal):
    """
    Lightweight lot object for tax estimation at historical quantity.
    Uses only fields needed by portfolio tax helpers.
    """
    return SimpleNamespace(
        id=lot.id,
        scheme_type=lot.scheme_type,
        acquisition_date=lot.acquisition_date,
        quantity_remaining=str(qty),
        acquisition_price_gbp=lot.acquisition_price_gbp,
        true_cost_per_share_gbp=lot.true_cost_per_share_gbp,
        matching_lot_id=getattr(lot, "matching_lot_id", None),
        forfeiture_period_end=getattr(lot, "forfeiture_period_end", None),
        fmv_at_acquisition_gbp=getattr(lot, "fmv_at_acquisition_gbp", None),
    )


def _lot_sell_now_economic_gain(
    *,
    lot,
    qty: Decimal,
    price_gbp: Decimal,
    disposal_date: date,
    settings: AppSettings | None,
) -> Decimal | None:
    """
    Portfolio-aligned per-lot economic gain for a hypothetical same-day sale.
    Returns None when tax is required but settings are unavailable.
    """
    if qty <= 0:
        return Decimal("0.00")

    true_per_share = (
        _safe_decimal(lot.true_cost_per_share_gbp)
        or _safe_decimal(lot.acquisition_price_gbp)
        or Decimal("0")
    )
    lot_mkt = _q2(qty * price_gbp)
    lot_true_total = _q2(qty * true_per_share)

    shadow_lot = _shadow_lot_with_qty(lot, qty)
    est_tax = _estimate_sell_all_employment_tax(
        [shadow_lot],
        price_gbp,
        disposal_date,
        settings,
    )
    if est_tax is None:
        return None

    est_net = _q2(lot_mkt - est_tax)
    return _q2(est_net - lot_true_total)


def _pct_change_str(current: Decimal, past: Decimal) -> str | None:
    if past == 0:
        return None
    pct = (current - past) / past * 100
    return str(pct.quantize(_QUANT2, rounding=ROUND_HALF_UP))


def _compute_changes(date_price: dict[date, Decimal], today: date) -> dict:
    """
    Compute 30d / 90d / 365d absolute and % change vs the most recent price
    on or before today.
    """
    if not date_price:
        return {}

    sorted_dates = sorted(date_price.keys())
    candidates = [d for d in sorted_dates if d <= today]
    if not candidates:
        return {}
    current_date = max(candidates)
    current_price = date_price[current_date]

    def find_price_on_or_before(target: date) -> Decimal | None:
        eligible = [d for d in sorted_dates if d <= target]
        return date_price[max(eligible)] if eligible else None

    result: dict = {
        "current_price_gbp": str(current_price.quantize(_QUANT4, rounding=ROUND_HALF_UP)),
        "current_date": current_date.isoformat(),
    }
    for days, key in ((30, "30d"), (90, "90d"), (365, "365d")):
        past = find_price_on_or_before(today - timedelta(days=days))
        if past is None:
            result[f"change_{key}_gbp"] = None
            result[f"change_{key}_pct"] = None
        else:
            delta = current_price - past
            result[f"change_{key}_gbp"] = str(delta.quantize(_QUANT4, rounding=ROUND_HALF_UP))
            result[f"change_{key}_pct"] = _pct_change_str(current_price, past)
    return result


def _cumulative_on_or_before(
    *,
    cumulative_by_date: dict[str, str],
    target_date: date,
) -> Decimal:
    """Return cumulative value at the latest key date <= target_date."""
    if not cumulative_by_date:
        return Decimal("0.00")
    dated: list[tuple[date, Decimal]] = []
    for raw_date, raw_value in cumulative_by_date.items():
        try:
            dated.append((date.fromisoformat(raw_date), Decimal(raw_value)))
        except (ValueError, InvalidOperation, TypeError):
            continue
    if not dated:
        return Decimal("0.00")
    dated.sort(key=lambda item: item[0])
    latest = Decimal("0.00")
    for d, value in dated:
        if d > target_date:
            break
        latest = _q2(value)
    return latest


class HistoryService:

    @staticmethod
    def get_security_history(
        security_id: str,
        from_date: date | None = None,
        settings: AppSettings | None = None,
    ) -> dict:
        """
        Returns a dict with price series, cost-basis overlays, lot events, and
        summary stats for a single security.

        price_series items include aligned cost_basis_gbp and true_cost_gbp
        values so Chart.js can render both lines with the same date labels.
        """
        with AppContext.read_session() as sess:
            sec_repo = SecurityRepository(sess)
            price_repo = PriceRepository(sess)
            lot_repo = LotRepository(sess)

            security = sec_repo.get_by_id(security_id)
            if security is None:
                return {"error": "security_not_found", "has_data": False}

            currency = (security.currency or "GBP").strip().upper()

            # All PriceHistory rows for this security (daily + intraday).
            rows = price_repo.get_history_range(security_id, from_date=from_date)
            dividend_timeline = DividendService.get_net_dividends_timeline(
                settings=settings,
            )
            security_cumulative_dividends = (
                (dividend_timeline.get("cumulative_net_dividends_by_security") or {})
                .get(security_id, {})
            )
            security_total_net_dividends = _safe_decimal(
                (dividend_timeline.get("net_dividends_by_security_gbp") or {}).get(
                    security_id
                )
            ) or Decimal("0.00")

            # One GBP price per date (daily sources only, best priority).
            date_price: dict[date, Decimal] = _dedup_daily_rows(rows, currency)
            sorted_dates = sorted(date_price.keys())

            # Native price lookup (for display alongside GBP).
            native_lookup = _dedup_native(rows)

            # All lots including exhausted ones — needed for full cost basis history.
            lots = lot_repo.get_all_lots_for_security(security_id)
            lots_sorted = sorted(lots, key=lambda l: (l.acquisition_date, l.id))
            acquisition_qty_by_date: dict[date, Decimal] = {}
            for lot in lots_sorted:
                # Exclude non-disposal transfer shadow lots from "purchased on day"
                # quantities, otherwise internal ESPP -> BROKERAGE moves appear as
                # extra acquisitions.
                is_transfer_shadow = (
                    lot.import_source == "ui_transfer_to_brokerage"
                    or (
                        lot.external_id is not None
                        and lot.external_id.startswith("transfer-origin-lot:")
                    )
                )
                if is_transfer_shadow:
                    continue
                qty = _safe_decimal(lot.quantity) or Decimal("0")
                acquisition_qty_by_date[lot.acquisition_date] = (
                    acquisition_qty_by_date.get(lot.acquisition_date, Decimal("0")) + qty
                )

            # Load LotDisposals with transaction dates for cost-basis reconstruction.
            # {lot_id: [(transaction_date, quantity_allocated), ...]}
            lot_disposal_evts: dict[str, list[tuple[date, Decimal]]] = {}
            sold_qty_by_date: dict[date, Decimal] = {}
            all_lot_ids = [lot.id for lot in lots_sorted]
            if all_lot_ids:
                disp_rows = sess.execute(
                    select(
                        LotDisposal.lot_id,
                        LotDisposal.quantity_allocated,
                        Transaction.transaction_date,
                    )
                    .join(Transaction, LotDisposal.transaction_id == Transaction.id)
                    .where(LotDisposal.lot_id.in_(all_lot_ids))
                ).all()
                for row in disp_rows:
                    qty = _safe_decimal(row.quantity_allocated) or Decimal("0")
                    lot_disposal_evts.setdefault(row.lot_id, []).append(
                        (row.transaction_date, qty)
                    )
                    sold_qty_by_date[row.transaction_date] = (
                        sold_qty_by_date.get(row.transaction_date, Decimal("0")) + qty
                    )

            def snapshot_at_date(d: date) -> tuple[
                Decimal, Decimal, Decimal, Decimal, Decimal, Decimal
            ]:
                """
                Historical holdings/economic snapshot for date D.

                Returns:
                  total_qty, total_cost, total_true,
                  sellable_qty, sellable_true, nonsellable_qty
                """
                total_qty = Decimal("0")
                total_cost = Decimal("0")
                total_true = Decimal("0")
                sellable_qty = Decimal("0")
                sellable_true = Decimal("0")
                nonsellable_qty = Decimal("0")

                for lot in lots_sorted:
                    if lot.acquisition_date > d:
                        # Lots are not counted before their acquisition date.
                        continue

                    acq_cost = _safe_decimal(lot.acquisition_price_gbp) or Decimal("0")
                    true_c = _safe_decimal(lot.true_cost_per_share_gbp)
                    if true_c is None:
                        true_c = acq_cost

                    current_qty = _safe_decimal(lot.quantity_remaining) or Decimal("0")
                    future_disposed = sum(
                        qty
                        for tx_date, qty in lot_disposal_evts.get(lot.id, [])
                        if tx_date > d
                    )
                    remaining = max(Decimal("0"), current_qty + future_disposed)
                    if remaining <= 0:
                        continue

                    total_qty += remaining
                    total_cost += remaining * acq_cost
                    total_true += remaining * true_c

                    if _is_lot_sellable_on(lot, d):
                        sellable_qty += remaining
                        sellable_true += remaining * true_c
                    else:
                        nonsellable_qty += remaining

                return (
                    total_qty,
                    total_cost,
                    total_true,
                    sellable_qty,
                    sellable_true,
                    nonsellable_qty,
                )

            # Build price_series with aligned cost overlays.
            lot_event_dates = set(acquisition_qty_by_date.keys())
            price_series = []
            for d in sorted_dates:
                price_gbp = date_price[d]
                (
                    total_qty,
                    total_cost,
                    total_true,
                    sellable_qty,
                    sellable_true,
                    nonsellable_qty,
                ) = snapshot_at_date(d)
                cb_gbp = (total_cost / total_qty) if total_qty > 0 else None
                tc_gbp = (total_true / total_qty) if total_qty > 0 else None
                purchased_qty = acquisition_qty_by_date.get(d)
                sold_qty = sold_qty_by_date.get(d)
                position_value = (
                    (total_qty * price_gbp).quantize(_QUANT2, rounding=ROUND_HALF_UP)
                    if total_qty > 0
                    else None
                )
                sellable_value = (
                    (sellable_qty * price_gbp).quantize(_QUANT2, rounding=ROUND_HALF_UP)
                    if total_qty > 0
                    else None
                )
                nonsellable_value = (
                    (nonsellable_qty * price_gbp).quantize(_QUANT2, rounding=ROUND_HALF_UP)
                    if total_qty > 0
                    else None
                )
                sellable_gain = Decimal("0.00")
                sellable_gain_incomplete = False
                for lot in lots_sorted:
                    if lot.acquisition_date > d:
                        continue
                    if not _is_lot_sellable_on(lot, d):
                        continue
                    current_qty = _safe_decimal(lot.quantity_remaining) or Decimal("0")
                    future_disposed = sum(
                        qty
                        for tx_date, qty in lot_disposal_evts.get(lot.id, [])
                        if tx_date > d
                    )
                    remaining = max(Decimal("0"), current_qty + future_disposed)
                    if remaining <= 0:
                        continue
                    lot_gain = _lot_sell_now_economic_gain(
                        lot=lot,
                        qty=remaining,
                        price_gbp=price_gbp,
                        disposal_date=d,
                        settings=settings,
                    )
                    if lot_gain is None:
                        sellable_gain_incomplete = True
                        break
                    sellable_gain += lot_gain

                sellable_gain_out = None if sellable_gain_incomplete else _q2(sellable_gain)
                cumulative_net_dividends = _cumulative_on_or_before(
                    cumulative_by_date=security_cumulative_dividends,
                    target_date=d,
                )
                sellable_gain_plus_net_dividends = (
                    _q2(sellable_gain_out + cumulative_net_dividends)
                    if sellable_gain_out is not None
                    else None
                )
                price_series.append({
                    "date": d.isoformat(),
                    "price_gbp": str(price_gbp.quantize(_QUANT4, rounding=ROUND_HALF_UP)),
                    "price_native": native_lookup.get(d),
                    "cost_basis_gbp": str(cb_gbp.quantize(_QUANT4, rounding=ROUND_HALF_UP)) if cb_gbp is not None else None,
                    "true_cost_gbp": str(tc_gbp.quantize(_QUANT4, rounding=ROUND_HALF_UP)) if tc_gbp is not None else None,
                    "held_qty": str(total_qty.quantize(_QUANT4, rounding=ROUND_HALF_UP)),
                    "position_value_gbp": str(position_value) if position_value is not None else None,
                    "sellable_qty": str(sellable_qty.quantize(_QUANT4, rounding=ROUND_HALF_UP)),
                    "nonsellable_qty": str(nonsellable_qty.quantize(_QUANT4, rounding=ROUND_HALF_UP)),
                    "sellable_value_gbp": str(sellable_value) if sellable_value is not None else None,
                    "nonsellable_value_gbp": str(nonsellable_value) if nonsellable_value is not None else None,
                    "lock_window_active": bool(nonsellable_qty > 0),
                    "sellable_gain_gbp": (
                        str(sellable_gain_out)
                        if sellable_gain_out is not None
                        else None
                    ),
                    "cumulative_net_dividends_gbp": str(cumulative_net_dividends),
                    "sellable_gain_plus_net_dividends_gbp": (
                        str(sellable_gain_plus_net_dividends)
                        if sellable_gain_plus_net_dividends is not None
                        else None
                    ),
                    "has_lot_event": d in lot_event_dates,
                    "purchased_qty": (
                        str(purchased_qty.quantize(_QUANT4, rounding=ROUND_HALF_UP))
                        if purchased_qty is not None
                        else None
                    ),
                    "sold_qty": (
                        str(sold_qty.quantize(_QUANT4, rounding=ROUND_HALF_UP))
                        if sold_qty is not None
                        else None
                    ),
                })

            # Lot events for the acquisitions table.
            lot_events = [
                {
                    "date": lot.acquisition_date.isoformat(),
                    "quantity": lot.quantity,
                    "quantity_remaining": lot.quantity_remaining,
                    "scheme_type": lot.scheme_type,
                    "acquisition_price_gbp": lot.acquisition_price_gbp,
                    "true_cost_per_share_gbp": lot.true_cost_per_share_gbp,
                    "tax_year": lot.tax_year,
                    "lot_id": lot.id,
                }
                for lot in lots_sorted
            ]

            # Summary stats: 30d/90d/365d changes.
            today = date.today()
            stats = _compute_changes(date_price, today)
            stats["currency"] = currency

            # Add native price for current date.
            if stats.get("current_date"):
                cur_d = date.fromisoformat(stats["current_date"])
                stats["current_price_native"] = native_lookup.get(cur_d)

            # Weighted average cost basis as of today (remaining holdings only).
            today_qty = Decimal("0")
            today_cost = Decimal("0")
            today_true = Decimal("0")
            for lot in lots_sorted:
                remaining = _safe_decimal(lot.quantity_remaining) or Decimal("0")
                if remaining <= 0:
                    continue
                acq_cost = _safe_decimal(lot.acquisition_price_gbp) or Decimal("0")
                true_c = _safe_decimal(lot.true_cost_per_share_gbp)
                if true_c is None:
                    true_c = acq_cost
                today_qty += remaining
                today_cost += remaining * acq_cost
                today_true += remaining * true_c
            if today_qty > 0:
                stats["weighted_avg_cost_gbp"] = str(
                    (today_cost / today_qty).quantize(_QUANT4, rounding=ROUND_HALF_UP)
                )
                stats["weighted_avg_true_cost_gbp"] = str(
                    (today_true / today_qty).quantize(_QUANT4, rounding=ROUND_HALF_UP)
                )
            else:
                stats["weighted_avg_cost_gbp"] = None
                stats["weighted_avg_true_cost_gbp"] = None

            latest_gain: Decimal | None = None
            for point in reversed(price_series):
                gain_value = _safe_decimal(point.get("sellable_gain_gbp"))
                if gain_value is not None:
                    latest_gain = _q2(gain_value)
                    break
            stats["estimated_net_dividends_gbp"] = str(_q2(security_total_net_dividends))
            stats["gain_if_sold_plus_net_dividends_gbp"] = (
                str(_q2(latest_gain + security_total_net_dividends))
                if latest_gain is not None
                else None
            )
            stats["capital_at_risk_after_dividends_gbp"] = str(
                _q2(max(Decimal("0"), today_true - security_total_net_dividends))
            )

        return {
            "security_id": security.id,
            "ticker": security.ticker,
            "name": security.name,
            "currency": currency,
            "exchange": security.exchange,
            "price_series": price_series,
            "lot_events": lot_events,
            "summary_stats": stats,
            "has_data": len(price_series) > 0,
            "notes": [],
        }

    @staticmethod
    def get_portfolio_history(
        from_date: date | None = None,
        settings: AppSettings | None = None,
    ) -> dict:
        """
        Accurate portfolio value over time.

        Quantity reconstruction uses the "add-back future disposals" method:

            historical_qty(lot, D) = lot.quantity_remaining
                                   + Σ LotDisposal.quantity_allocated
                                     WHERE disposal.transaction_date > D

        This is correct because lot.quantity_remaining is the settled ground
        truth for TODAY, and LotDisposal records tell us when each tranche was
        sold.  Adding back future-of-D disposals gives the holding at D.

        Crucially, lots imported with a pre-reduced quantity_remaining (shares
        sold before this app started tracking) are handled correctly: those
        untracked disposals are already baked into quantity_remaining, so they
        are never double-counted.
        """
        with AppContext.read_session() as sess:
            sec_repo = SecurityRepository(sess)
            price_repo = PriceRepository(sess)
            lot_repo = LotRepository(sess)

            securities = sec_repo.list_all()
            if not securities:
                return {
                    "has_data": False, "total_series": [], "per_security": [],
                    "summary_stats": {}, "notes": [], "securities": [],
                }

            dividend_timeline = DividendService.get_net_dividends_timeline(
                settings=settings,
            )
            cumulative_portfolio_dividends = (
                dividend_timeline.get("cumulative_net_dividends_by_date") or {}
            )
            total_portfolio_net_dividends = _safe_decimal(
                dividend_timeline.get("total_net_dividends_gbp")
            ) or Decimal("0.00")

            # Build {security_id: {date: price_gbp}} — daily only, deduplicated.
            sec_date_price: dict[str, dict[date, Decimal]] = {}
            for sec in securities:
                rows = price_repo.get_history_range(sec.id, from_date=from_date)
                currency = (sec.currency or "GBP").strip().upper()
                deduped = _dedup_daily_rows(rows, currency)
                if deduped:
                    sec_date_price[sec.id] = deduped

            # Union of all dates across all securities.
            all_dates: set[date] = set()
            for d_map in sec_date_price.values():
                all_dates.update(d_map.keys())
            sorted_dates = sorted(all_dates)

            if not sorted_dates:
                return {
                    "has_data": False, "total_series": [], "per_security": [],
                    "summary_stats": {}, "notes": [], "securities": [],
                }

            # Load all lots per security: {security_id: [Lot, ...]}
            lots_by_security: dict[str, list] = {}
            all_lot_ids: list[str] = []
            lot_security_id: dict[str, str] = {}
            acquired_qty_by_date_security: dict[date, dict[str, Decimal]] = {}
            for sec in securities:
                lots = lot_repo.get_all_lots_for_security(sec.id)
                lots_by_security[sec.id] = lots
                for lot in lots:
                    all_lot_ids.append(lot.id)
                    lot_security_id[lot.id] = sec.id
                    is_transfer_shadow = (
                        lot.import_source == "ui_transfer_to_brokerage"
                        or (
                            lot.external_id is not None
                            and lot.external_id.startswith("transfer-origin-lot:")
                        )
                    )
                    if is_transfer_shadow:
                        continue
                    qty = _safe_decimal(lot.quantity) or Decimal("0")
                    sec_qty_by_date = acquired_qty_by_date_security.setdefault(
                        lot.acquisition_date,
                        {},
                    )
                    sec_qty_by_date[sec.id] = (
                        sec_qty_by_date.get(sec.id, Decimal("0")) + qty
                    )

            # Clamp to portfolio inception: pre-acquisition price data is stored
            # for per-security chart context but should not appear on the overview.
            all_acquisition_dates = [
                lot.acquisition_date
                for lots in lots_by_security.values()
                for lot in lots
            ]
            if all_acquisition_dates:
                inception_date = min(all_acquisition_dates)
                sorted_dates = [d for d in sorted_dates if d >= inception_date]

            # Load LotDisposals with transaction dates for every lot in one query.
            # {lot_id: [(transaction_date, quantity_allocated), ...]}
            lot_disposal_evts: dict[str, list[tuple[date, Decimal]]] = {}
            sold_qty_by_date_security: dict[date, dict[str, Decimal]] = {}
            if all_lot_ids:
                disp_rows = sess.execute(
                    select(
                        LotDisposal.lot_id,
                        LotDisposal.quantity_allocated,
                        Transaction.transaction_date,
                    )
                    .join(Transaction, LotDisposal.transaction_id == Transaction.id)
                    .where(LotDisposal.lot_id.in_(all_lot_ids))
                ).all()
                for row in disp_rows:
                    qty = _safe_decimal(row.quantity_allocated) or Decimal("0")
                    lot_disposal_evts.setdefault(row.lot_id, []).append(
                        (row.transaction_date, qty)
                    )
                    sec_id = lot_security_id.get(row.lot_id)
                    if sec_id is not None:
                        sec_qty_by_date = sold_qty_by_date_security.setdefault(
                            row.transaction_date,
                            {},
                        )
                        sec_qty_by_date[sec_id] = (
                            sec_qty_by_date.get(sec_id, Decimal("0")) + qty
                        )

            # Sorted date lists per security for efficient binary search.
            sec_sorted_dates: dict[str, list[date]] = {
                sid: sorted(d_map.keys())
                for sid, d_map in sec_date_price.items()
            }

            def qty_held_on(security_id: str, d: date) -> Decimal:
                """
                Historical quantity held for a security on date D.

                For each lot acquired on or before D:
                  holding = lot.quantity_remaining
                           + sum of LotDisposals that happened AFTER D
                           (because those shares hadn't been sold yet on D)
                """
                total = Decimal("0")
                for lot in lots_by_security.get(security_id, []):
                    if lot.acquisition_date > d:
                        continue
                    current_qty = _safe_decimal(lot.quantity_remaining) or Decimal("0")
                    future_disposed = sum(
                        qty
                        for tx_date, qty in lot_disposal_evts.get(lot.id, [])
                        if tx_date > d
                    )
                    total += current_qty + future_disposed
                return max(Decimal("0"), total)

            def price_on_or_before(security_id: str, d: date) -> Decimal | None:
                """Forward-fill: return the most recent daily price on or before d."""
                d_map = sec_date_price.get(security_id)
                dates = sec_sorted_dates.get(security_id)
                if not d_map or not dates:
                    return None
                idx = bisect.bisect_right(dates, d) - 1
                return d_map[dates[idx]] if idx >= 0 else None

            # Build total series and per-security series.
            per_sec_series: dict[str, list[dict]] = {s.id: [] for s in securities}
            total_series: list[dict] = []
            ticker_by_security_id: dict[str, str] = {s.id: s.ticker for s in securities}

            for d in sorted_dates:
                total = Decimal("0")
                nonsellable_total_value = Decimal("0")
                sellable_gain = Decimal("0")
                sellable_gain_incomplete = False
                priced_count = 0
                held_count = 0
                for sec in securities:
                    qty = qty_held_on(sec.id, d)
                    actual_price = sec_date_price.get(sec.id, {}).get(d)
                    price_str = str(actual_price.quantize(_QUANT4, rounding=ROUND_HALF_UP)) if actual_price is not None else None
                    if qty <= 0:
                        per_sec_series[sec.id].append({"date": d.isoformat(), "value_gbp": None, "price_gbp": price_str})
                        continue
                    held_count += 1
                    price = price_on_or_before(sec.id, d)
                    if price is not None:
                        value = (qty * price).quantize(_QUANT2, rounding=ROUND_HALF_UP)
                        per_sec_series[sec.id].append({"date": d.isoformat(), "value_gbp": str(value), "price_gbp": price_str})
                        total += value
                        priced_count += 1

                        # Gain If Sold Today: portfolio-aligned per-lot net economic gain
                        # for sellable holdings at date D.
                        sec_lots = lots_by_security.get(sec.id, [])
                        sec_nonsellable_value = Decimal("0")
                        for lot in sec_lots:
                            if lot.acquisition_date > d:
                                continue
                            lot_remaining = _safe_decimal(lot.quantity_remaining) or Decimal("0")
                            lot_future = sum(
                                q for tx_d, q in lot_disposal_evts.get(lot.id, []) if tx_d > d
                            )
                            lot_qty = max(Decimal("0"), lot_remaining + lot_future)
                            if lot_qty <= 0:
                                continue
                            if not _is_lot_sellable_on(lot, d):
                                sec_nonsellable_value += _q2(lot_qty * price)
                                continue
                            lot_gain = _lot_sell_now_economic_gain(
                                lot=lot,
                                qty=lot_qty,
                                price_gbp=price,
                                disposal_date=d,
                                settings=settings,
                            )
                            if lot_gain is None:
                                sellable_gain_incomplete = True
                                break
                            sellable_gain += lot_gain
                        nonsellable_total_value += _q2(sec_nonsellable_value)
                        if sellable_gain_incomplete:
                            continue
                    else:
                        per_sec_series[sec.id].append({"date": d.isoformat(), "value_gbp": None, "price_gbp": price_str})

                purchase_map = acquired_qty_by_date_security.get(d, {})
                sold_map = sold_qty_by_date_security.get(d, {})
                event_sec_ids = sorted(
                    set(purchase_map.keys()) | set(sold_map.keys()),
                    key=lambda sid: ticker_by_security_id.get(sid, sid),
                )
                share_events: list[dict] = []
                for sec_id in event_sec_ids:
                    purchased_qty = purchase_map.get(sec_id, Decimal("0"))
                    sold_qty = sold_map.get(sec_id, Decimal("0"))
                    if purchased_qty <= 0 and sold_qty <= 0:
                        continue
                    share_events.append({
                        "security_id": sec_id,
                        "ticker": ticker_by_security_id.get(sec_id, sec_id),
                        "purchased_qty": (
                            str(purchased_qty.quantize(_QUANT4, rounding=ROUND_HALF_UP))
                            if purchased_qty > 0
                            else None
                        ),
                        "sold_qty": (
                            str(sold_qty.quantize(_QUANT4, rounding=ROUND_HALF_UP))
                            if sold_qty > 0
                            else None
                        ),
                    })

                total_series.append({
                    "date": d.isoformat(),
                    "total_value_gbp": str(total.quantize(_QUANT2)) if held_count > 0 else None,
                    "nonsellable_value_gbp": str(_q2(nonsellable_total_value)) if held_count > 0 else None,
                    "lock_window_active": bool(nonsellable_total_value > Decimal("0")),
                    "sellable_gain_gbp": (
                        None
                        if sellable_gain_incomplete or priced_count == 0
                        else str(_q2(sellable_gain))
                    ),
                    "cumulative_net_dividends_gbp": str(
                        _cumulative_on_or_before(
                            cumulative_by_date=cumulative_portfolio_dividends,
                            target_date=d,
                        )
                    ),
                    "sellable_gain_plus_net_dividends_gbp": (
                        None
                        if sellable_gain_incomplete or priced_count == 0
                        else str(
                            _q2(
                                sellable_gain
                                + _cumulative_on_or_before(
                                    cumulative_by_date=cumulative_portfolio_dividends,
                                    target_date=d,
                                )
                            )
                        )
                    ),
                    "priced_count": priced_count,
                    "total_count": held_count,
                    "share_events": share_events,
                })

            # Portfolio-level summary stats (30d / 90d / 365d).
            today = date.today()
            total_date_price: dict[date, Decimal] = {
                date.fromisoformat(p["date"]): Decimal(p["total_value_gbp"])
                for p in total_series
                if p["total_value_gbp"] is not None
            }
            portfolio_stats = _compute_changes(total_date_price, today)
            latest_gain: Decimal | None = None
            for point in reversed(total_series):
                gain_value = _safe_decimal(point.get("sellable_gain_gbp"))
                if gain_value is not None:
                    latest_gain = _q2(gain_value)
                    break
            current_true_cost = Decimal("0")
            for lots in lots_by_security.values():
                for lot in lots:
                    remaining = _safe_decimal(lot.quantity_remaining) or Decimal("0")
                    if remaining <= Decimal("0"):
                        continue
                    true_per_share = _safe_decimal(lot.true_cost_per_share_gbp)
                    if true_per_share is None:
                        true_per_share = _safe_decimal(lot.acquisition_price_gbp) or Decimal("0")
                    current_true_cost += remaining * true_per_share
            current_true_cost = _q2(current_true_cost)
            portfolio_stats["estimated_net_dividends_gbp"] = str(
                _q2(total_portfolio_net_dividends)
            )
            portfolio_stats["gain_if_sold_plus_net_dividends_gbp"] = (
                str(_q2(latest_gain + total_portfolio_net_dividends))
                if latest_gain is not None
                else None
            )
            portfolio_stats["capital_at_risk_after_dividends_gbp"] = str(
                _q2(max(Decimal("0"), current_true_cost - total_portfolio_net_dividends))
            )

        # Build per-security output (only securities with at least one priced point).
        per_security_out = []
        for sec in securities:
            series = per_sec_series.get(sec.id, [])
            if not any(p["value_gbp"] is not None for p in series):
                continue
            per_security_out.append({
                "security_id": sec.id,
                "ticker": sec.ticker,
                "name": sec.name,
                "currency": (sec.currency or "GBP").upper(),
                "exchange": sec.exchange,
                "series": series,
            })

        return {
            "has_data": bool(total_series),
            "total_series": total_series,
            "per_security": per_security_out,
            "summary_stats": portfolio_stats,
            "notes": [],
            "securities": [
                {"id": s.id, "ticker": s.ticker, "name": s.name}
                for s in securities
            ],
        }
