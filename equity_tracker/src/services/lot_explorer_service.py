from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

from ..app_context import AppContext
from ..db.repository import DisposalRepository, LotRepository, LotTransferEventRepository, SecurityRepository, TransactionRepository
from ..settings import AppSettings
from .portfolio_service import PortfolioService

_MONEY_Q = Decimal("0.01")


def _q2(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _decimal_or_zero(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _money_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(_q2(value))


def _qty_str(value: Decimal) -> str:
    text = format(value, "f")
    trimmed = text.rstrip("0").rstrip(".")
    return trimmed or "0"


def _wrapper_bucket(scheme_type: str) -> str:
    return "ISA" if (scheme_type or "").strip().upper() == "ISA" else "TAXABLE"


def _structural_state(lot: Any) -> str:
    external_id = str(getattr(lot, "external_id", "") or "").strip()
    if external_id.startswith("transfer-origin-lot:"):
        return "TRANSFER_SHADOW"
    if getattr(lot, "matching_lot_id", None):
        return "MATCHED_SHARE"
    return "STANDARD"


class LotExplorerService:
    @staticmethod
    def get_payload(
        *,
        settings: AppSettings | None = None,
        security_id: str | None = None,
        scheme: str | None = None,
        sellability: str | None = None,
        wrapper: str | None = None,
        include_exhausted: bool = False,
    ) -> dict[str, Any]:
        generated_at_utc = datetime.now(UTC).isoformat()
        summary = PortfolioService.get_portfolio_summary(
            settings=settings,
            use_live_true_cost=False,
        )
        active_lot_map: dict[str, tuple[Any, Any]] = {}
        fifo_rank_by_lot_id: dict[str, int] = {}
        for security_summary in summary.securities:
            ordered_active = sorted(
                security_summary.active_lots,
                key=lambda ls: (ls.lot.acquisition_date, ls.lot.id),
            )
            for idx, lot_summary in enumerate(ordered_active, start=1):
                active_lot_map[lot_summary.lot.id] = (security_summary, lot_summary)
                fifo_rank_by_lot_id[lot_summary.lot.id] = idx

        normalized_security_id = (security_id or "").strip()
        normalized_scheme = (scheme or "").strip().upper()
        normalized_sellability = (sellability or "").strip().upper()
        normalized_wrapper = (wrapper or "").strip().upper()

        with AppContext.read_session() as sess:
            security_repo = SecurityRepository(sess)
            lot_repo = LotRepository(sess)
            disposal_repo = DisposalRepository(sess)
            transaction_repo = TransactionRepository(sess)
            transfer_repo = LotTransferEventRepository(sess)

            all_securities = sorted(
                security_repo.list_all(),
                key=lambda sec: (str(sec.ticker or ""), str(sec.id)),
            )

            rows: list[dict[str, Any]] = []
            total_open = 0
            total_sellable = 0
            total_locked_or_at_risk = 0
            total_exhausted = 0
            total_transfer_linked = 0
            total_disposal_linked = 0

            for security in all_securities:
                disposals = disposal_repo.list_for_security(security.id)
                tx_map = {
                    tx.id: tx
                    for tx in transaction_repo.list_for_security(
                        security.id,
                        transaction_type="DISPOSAL",
                    )
                }
                transfers = transfer_repo.list_for_security(security.id)

                disposal_stats_by_lot: dict[str, dict[str, Any]] = {}
                for disposal in disposals:
                    bucket = disposal_stats_by_lot.setdefault(
                        disposal.lot_id,
                        {
                            "count": 0,
                            "quantity_allocated": Decimal("0"),
                            "last_disposal_date": None,
                        },
                    )
                    bucket["count"] += 1
                    bucket["quantity_allocated"] = _q2(
                        Decimal(bucket["quantity_allocated"])
                        + _decimal_or_zero(disposal.quantity_allocated)
                    )
                    tx = tx_map.get(disposal.transaction_id)
                    if tx is not None:
                        last_date = bucket["last_disposal_date"]
                        if last_date is None or tx.transaction_date > last_date:
                            bucket["last_disposal_date"] = tx.transaction_date

                transfer_stats_by_lot: dict[str, dict[str, Any]] = {}
                for transfer in transfers:
                    for lot_id, direction in (
                        (transfer.source_lot_id, "outbound"),
                        (transfer.destination_lot_id, "inbound"),
                    ):
                        if not lot_id:
                            continue
                        bucket = transfer_stats_by_lot.setdefault(
                            lot_id,
                            {
                                "count": 0,
                                "inbound_count": 0,
                                "outbound_count": 0,
                                "last_transfer_date": None,
                            },
                        )
                        bucket["count"] += 1
                        key = "inbound_count" if direction == "inbound" else "outbound_count"
                        bucket[key] += 1
                        last_date = bucket["last_transfer_date"]
                        if last_date is None or transfer.transfer_date > last_date:
                            bucket["last_transfer_date"] = transfer.transfer_date

                lots = lot_repo.get_all_lots_for_security(security.id)
                for lot in lots:
                    quantity = _decimal_or_zero(lot.quantity)
                    quantity_remaining = _decimal_or_zero(lot.quantity_remaining)
                    is_exhausted = quantity_remaining <= Decimal("0")

                    active_tuple = active_lot_map.get(lot.id)
                    security_summary = active_tuple[0] if active_tuple else None
                    lot_summary = active_tuple[1] if active_tuple else None

                    wrapper_bucket = _wrapper_bucket(str(lot.scheme_type or ""))
                    sellability_status = "EXHAUSTED" if is_exhausted else str(
                        getattr(lot_summary, "sellability_status", "SELLABLE")
                    )
                    if normalized_security_id and security.id != normalized_security_id:
                        continue
                    if normalized_scheme and str(lot.scheme_type or "").upper() != normalized_scheme:
                        continue
                    if normalized_sellability and sellability_status != normalized_sellability:
                        continue
                    if normalized_wrapper and wrapper_bucket != normalized_wrapper:
                        continue
                    if is_exhausted and not include_exhausted:
                        total_exhausted += 1
                        continue

                    disposal_stats = disposal_stats_by_lot.get(lot.id, {})
                    transfer_stats = transfer_stats_by_lot.get(lot.id, {})

                    if is_exhausted:
                        total_exhausted += 1
                    else:
                        total_open += 1
                        if sellability_status == "SELLABLE":
                            total_sellable += 1
                        if sellability_status in {"LOCKED", "AT_RISK"}:
                            total_locked_or_at_risk += 1
                    if transfer_stats.get("count", 0):
                        total_transfer_linked += 1
                    if disposal_stats.get("count", 0):
                        total_disposal_linked += 1

                    market_value_gbp = (
                        _q2(lot_summary.market_value_gbp)
                        if lot_summary is not None and lot_summary.market_value_gbp is not None
                        else Decimal("0.00") if is_exhausted else None
                    )
                    net_if_sold_gbp = (
                        _q2(lot_summary.est_net_proceeds_gbp)
                        if lot_summary is not None and lot_summary.est_net_proceeds_gbp is not None
                        else Decimal("0.00") if is_exhausted else None
                    )
                    employment_tax_gbp = (
                        _q2(lot_summary.est_employment_tax_on_lot_gbp)
                        if lot_summary is not None and lot_summary.est_employment_tax_on_lot_gbp is not None
                        else Decimal("0.00") if is_exhausted else None
                    )
                    current_true_cost_gbp = (
                        _q2(lot_summary.true_cost_total_gbp)
                        if lot_summary is not None
                        else _q2(quantity_remaining * _decimal_or_zero(lot.true_cost_per_share_gbp))
                    )
                    current_cost_basis_gbp = (
                        _q2(lot_summary.cost_basis_total_gbp)
                        if lot_summary is not None
                        else _q2(quantity_remaining * _decimal_or_zero(lot.acquisition_price_gbp))
                    )
                    notes: list[str] = []
                    if sellability_status == "LOCKED" and getattr(lot_summary, "sellability_unlock_date", None):
                        notes.append(
                            f"Locked until {lot_summary.sellability_unlock_date.isoformat()}."
                        )
                    if (
                        lot_summary is not None
                        and lot_summary.forfeiture_risk is not None
                        and lot_summary.forfeiture_risk.in_window
                    ):
                        notes.append(
                            f"Forfeiture window: {lot_summary.forfeiture_risk.days_remaining}d remaining."
                        )
                    if disposal_stats.get("count", 0):
                        notes.append(
                            f"{disposal_stats['count']} disposal allocation(s) recorded."
                        )
                    if transfer_stats.get("count", 0):
                        notes.append(
                            f"{transfer_stats['count']} transfer event(s) recorded."
                        )
                    structural_state = _structural_state(lot)
                    if structural_state == "TRANSFER_SHADOW":
                        notes.append("Transfer-created shadow lot.")
                    elif structural_state == "MATCHED_SHARE":
                        notes.append("Matched-share linked lot.")

                    rows.append(
                        {
                            "lot_id": lot.id,
                            "security_id": security.id,
                            "ticker": str(security.ticker or "UNKNOWN"),
                            "security_name": str(security.name or ""),
                            "scheme_type": str(lot.scheme_type or ""),
                            "wrapper_bucket": wrapper_bucket,
                            "sellability_status": sellability_status,
                            "sellability_unlock_date": (
                                lot_summary.sellability_unlock_date.isoformat()
                                if lot_summary is not None and lot_summary.sellability_unlock_date is not None
                                else None
                            ),
                            "forfeiture_days_remaining": (
                                lot_summary.forfeiture_risk.days_remaining
                                if lot_summary is not None
                                and lot_summary.forfeiture_risk is not None
                                and lot_summary.forfeiture_risk.in_window
                                else None
                            ),
                            "acquisition_date": lot.acquisition_date.isoformat(),
                            "tax_year": str(lot.tax_year or ""),
                            "quantity": _qty_str(quantity),
                            "quantity_remaining": _qty_str(quantity_remaining),
                            "quantity_disposed": _qty_str(_decimal_or_zero(disposal_stats.get("quantity_allocated"))),
                            "is_exhausted": is_exhausted,
                            "fifo_rank_current": fifo_rank_by_lot_id.get(lot.id),
                            "current_cost_basis_gbp": _money_str(current_cost_basis_gbp),
                            "current_true_cost_gbp": _money_str(current_true_cost_gbp),
                            "market_value_gbp": _money_str(market_value_gbp),
                            "net_if_sold_gbp": _money_str(net_if_sold_gbp),
                            "employment_tax_gbp": _money_str(employment_tax_gbp),
                            "unrealised_gain_cgt_gbp": (
                                _money_str(_q2(lot_summary.unrealised_gain_cgt_gbp))
                                if lot_summary is not None and lot_summary.unrealised_gain_cgt_gbp is not None
                                else None
                            ),
                            "unrealised_gain_economic_gbp": (
                                _money_str(_q2(lot_summary.unrealised_gain_economic_gbp))
                                if lot_summary is not None and lot_summary.unrealised_gain_economic_gbp is not None
                                else None
                            ),
                            "original_currency": str(lot.original_currency or security.currency or "GBP"),
                            "broker_currency": str(lot.broker_currency or ""),
                            "fx_rate_at_acquisition": str(lot.fx_rate_at_acquisition or ""),
                            "fx_rate_source": str(lot.fx_rate_source or ""),
                            "structural_state": structural_state,
                            "matching_lot_id": str(lot.matching_lot_id or ""),
                            "transfer_event_count": int(transfer_stats.get("count", 0)),
                            "transfer_inbound_count": int(transfer_stats.get("inbound_count", 0)),
                            "transfer_outbound_count": int(transfer_stats.get("outbound_count", 0)),
                            "last_transfer_date": (
                                transfer_stats["last_transfer_date"].isoformat()
                                if transfer_stats.get("last_transfer_date") is not None
                                else None
                            ),
                            "disposal_event_count": int(disposal_stats.get("count", 0)),
                            "last_disposal_date": (
                                disposal_stats["last_disposal_date"].isoformat()
                                if disposal_stats.get("last_disposal_date") is not None
                                else None
                            ),
                            "notes": notes,
                            "audit_href": f"/audit?table_name=lots&record_id={lot.id}",
                            "history_href": f"/history/{security.id}",
                            "reconcile_href": "/reconcile#trace-contributing-lots",
                            "edit_href": f"/portfolio/edit-lot?lot_id={lot.id}",
                            "transfer_href": f"/portfolio/transfer-lot?lot_id={lot.id}",
                            "add_dividend_href": f"/dividends?{urlencode([('lot_ids', lot.id)])}",
                        }
                    )

        rows.sort(
            key=lambda row: (
                row["ticker"],
                1 if row["is_exhausted"] else 0,
                row["acquisition_date"],
                row["lot_id"],
            )
        )

        visible_exhausted = sum(1 for row in rows if row["is_exhausted"])
        notes = [
            "Lot Explorer is a current-state forensic surface: quantity remaining, sellability, and net-if-sold values reflect the live database state.",
            "Current disposal order is shown as acquisition-order rank within each security's active lots.",
        ]
        if not include_exhausted:
            notes.append("Exhausted lots are hidden by default; enable them when investigating old allocations or transfer chains.")
        notes.append("Use Audit for row-level mutation history and Reconcile for cross-page totals.")

        security_options = [
            {
                "id": sec.id,
                "label": f"{sec.ticker} - {sec.name}",
            }
            for sec in all_securities
        ]
        scheme_options = sorted(
            {
                str(row["scheme_type"])
                for row in rows
            }
        )

        return {
            "generated_at_utc": generated_at_utc,
            "summary": {
                "visible_rows": len(rows),
                "open_lots": total_open,
                "sellable_lots": total_sellable,
                "locked_or_at_risk_lots": total_locked_or_at_risk,
                "exhausted_lots": total_exhausted,
                "rows_with_transfers": total_transfer_linked,
                "rows_with_disposals": total_disposal_linked,
                "visible_exhausted_rows": visible_exhausted,
            },
            "filters": {
                "security_id": normalized_security_id,
                "scheme": normalized_scheme,
                "sellability": normalized_sellability,
                "wrapper": normalized_wrapper,
                "include_exhausted": include_exhausted,
            },
            "filter_options": {
                "securities": security_options,
                "schemes": scheme_options,
                "sellability": ["SELLABLE", "AT_RISK", "LOCKED", "EXHAUSTED"],
                "wrapper": ["ISA", "TAXABLE"],
            },
            "rows": rows,
            "notes": notes,
        }
