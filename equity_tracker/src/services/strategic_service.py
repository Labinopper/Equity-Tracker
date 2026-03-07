
"""Stage-10 deterministic strategic analysis service."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import select

from ..app_context import AppContext
from ..core.tax_engine import TaxContext, get_marginal_rates, tax_year_for_date
from ..core.tax_engine.employment_tax_engine import (
    EmploymentTaxContext,
    estimate_employment_tax_for_lot,
)
from ..db.models import EmploymentTaxEvent, Lot, LotDisposal, PriceHistory, Security, Transaction
from ..settings import AppSettings
from .capital_stack_service import CapitalStackService
from .dividend_service import DividendService
from .exposure_service import ExposureService
from .history_service import HistoryService
from .portfolio_service import PortfolioService, _estimate_sell_all_employment_tax
from .report_service import ReportService
from .tax_plan_service import TaxPlanService

_MONEY_Q = Decimal("0.01")
_PCT_Q = Decimal("0.01")
_DEFAULT_ISA_ALLOWANCE_GBP = Decimal("20000")


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _q_pct(value: Decimal) -> Decimal:
    return value.quantize(_PCT_Q, rounding=ROUND_HALF_UP)


def _to_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _safe_decimal(value: object, fallback: Decimal = Decimal("0")) -> Decimal:
    out = _to_decimal(value)
    return out if out is not None else fallback


def _pct(part: Decimal, whole: Decimal) -> Decimal:
    if whole <= 0:
        return Decimal("0.00")
    return _q_pct((part / whole) * Decimal("100"))


def _tax_year_bounds(tax_year: str) -> tuple[date, date]:
    year = int(tax_year.split("-")[0])
    return date(year, 4, 6), date(year + 1, 4, 5)


def _best_price_rows(rows: list[PriceHistory]) -> dict[tuple[str, date], PriceHistory]:
    best: dict[tuple[str, date], PriceHistory] = {}
    for row in rows:
        key = (row.security_id, row.price_date)
        cur = best.get(key)
        if cur is None:
            best[key] = row
            continue
        cur_ts = cur.fetched_at or cur.created_at
        row_ts = row.fetched_at or row.created_at
        if row_ts >= cur_ts:
            best[key] = row
    return best


def _audit_window_href(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    table_name: str | None = None,
) -> str:
    params: list[tuple[str, str]] = []
    if table_name:
        params.append(("table_name", table_name))
    if date_from:
        params.append(("date_from", date_from))
    if date_to:
        params.append(("date_to", date_to))
    return f"/audit?{urlencode(params)}" if params else "/audit"


def _basis_timeline_href(*, lookback_days: int) -> str:
    bounded = max(30, min(int(lookback_days), 1825))
    return f"/basis-timeline?lookback_days={bounded}"


def _model_scope_map() -> dict[str, dict[str, list[str]]]:
    return {
        "portfolio": {
            "inputs": [
                "Active lots, live prices, stored FX basis, configured tax inputs",
                "Lot-level lock and forfeiture states",
            ],
            "assumptions": [
                "Employment tax is estimated for sellable lots only",
                "Locked and forfeitable values are reported separately",
            ],
            "exclusions": [
                "No forecasts",
                "No advisory signals",
            ],
        },
        "net_value": {
            "inputs": [
                "Current market values and employment-tax estimate model",
            ],
            "assumptions": [
                "Hypothetical sell-all framing at current inputs",
            ],
            "exclusions": [
                "No staged execution assumptions",
            ],
        },
        "capital_stack": {
            "inputs": [
                "Current holdings, deployable cash ledger, stored FX basis, configured tax inputs",
            ],
            "assumptions": [
                "Deterministic gross-to-deployable deduction chain at current inputs",
            ],
            "exclusions": [
                "No optimization or recommendation engine",
            ],
        },
        "tax_plan": {
            "inputs": [
                "Tax-year settings, disposal history, current lot inventory",
            ],
            "assumptions": [
                "Assumption quality labels disclosed on each projection block",
            ],
            "exclusions": [
                "No probabilistic outcomes",
            ],
        },
        "risk": {
            "inputs": [
                "Current holdings, lock/forfeiture states, deployable cash, alert thresholds",
            ],
            "assumptions": [
                "Optionality and concentration metrics are deterministic and current-state only",
            ],
            "exclusions": [
                "No market forecasts",
                "No recommendation logic",
            ],
        },
        "scenario_lab": {
            "inputs": [
                "User leg definitions, FIFO lots, optional deterministic shock",
            ],
            "assumptions": [
                "Execution mode explicit: independent or sequential",
            ],
            "exclusions": [
                "No expected-return engine",
            ],
        },
        "capital_efficiency": {
            "inputs": [
                "Capital stack, disposal fee history, dividend tax summary",
                "FX attribution from current holdings",
            ],
            "assumptions": [
                "Annualized drag scales observed window to 365-day equivalent",
            ],
            "exclusions": [
                "No optimization suggestions",
            ],
        },
        "employment_exit": {
            "inputs": [
                "Current lots, scheme lock/forfeiture dates, latest prices",
            ],
            "assumptions": [
                "Static-price basis with optional deterministic shock",
                "Unvested RSU and in-window ESPP+ matched lots treated as forfeited",
            ],
            "exclusions": [
                "No employer-policy inference",
            ],
        },
    }


class StrategicService:
    @staticmethod
    def model_scope(page_key: str) -> dict[str, list[str]] | None:
        return _model_scope_map().get(page_key)

    @staticmethod
    def get_capital_efficiency(*, settings: AppSettings | None, db_path, as_of: date | None = None) -> dict[str, Any]:
        as_of_date = as_of or date.today()
        summary = PortfolioService.get_portfolio_summary(settings=settings, use_live_true_cost=False)
        stack = CapitalStackService.get_snapshot(settings=settings, db_path=db_path, summary=summary, as_of=as_of_date)
        dividends = DividendService.get_summary(settings=settings, as_of=as_of_date)

        emp_tax = _safe_decimal(stack.get("estimated_employment_tax_gbp"))
        cgt = _safe_decimal(stack.get("estimated_cgt_gbp"))
        fees = _safe_decimal(stack.get("estimated_fees_gbp"))
        div_tax = _safe_decimal((dividends.get("summary") or {}).get("estimated_tax_gbp"))

        fx_drag = Decimal("0")
        for sec in summary.securities:
            if sec.market_value_gbp is None or sec.total_quantity <= 0:
                continue
            if (sec.security.currency or "GBP").upper() == "GBP":
                continue
            acq_gbp = Decimal("0")
            acq_native = Decimal("0")
            for ls in sec.active_lots:
                acq_gbp += ls.cost_basis_total_gbp
                native_px = _to_decimal(ls.lot.acquisition_price_original_ccy) or _to_decimal(ls.lot.acquisition_price_gbp)
                if native_px is None:
                    continue
                acq_native += _q_money(native_px * ls.quantity_remaining)
            if acq_gbp <= 0 or acq_native <= 0:
                continue
            cur_native_px = _to_decimal(sec.current_price_native)
            if cur_native_px is None:
                continue
            cur_native_total = _q_money(cur_native_px * sec.total_quantity)
            cur_mkt = _q_money(Decimal(sec.market_value_gbp))
            fx_at_acq = acq_gbp / acq_native
            gbp_if_native_only = _q_money(cur_native_total * fx_at_acq)
            fx_component = _q_money(cur_mkt - gbp_if_native_only)
            if fx_component < 0:
                fx_drag += abs(fx_component)

        observed_dates: list[date] = []
        realized_fees_total = Decimal("0")
        realized_fees_12m = Decimal("0")
        realized_proceeds_12m = Decimal("0")
        employment_event_tax_total = Decimal("0")
        with AppContext.read_session() as sess:
            txs = list(sess.scalars(select(Transaction).where(Transaction.transaction_type == "DISPOSAL", Transaction.is_reversal.is_(False))).all())
            events = list(sess.scalars(select(EmploymentTaxEvent)).all())
            for event in events:
                observed_dates.append(event.event_date)
                employment_event_tax_total += _safe_decimal(event.estimated_tax_gbp)
            for tx in txs:
                observed_dates.append(tx.transaction_date)
                fee = _safe_decimal(tx.broker_fees_gbp)
                if fee <= 0:
                    continue
                realized_fees_total += fee
                if tx.transaction_date >= (as_of_date - timedelta(days=365)):
                    realized_fees_12m += fee
                    realized_proceeds_12m += _safe_decimal(tx.total_proceeds_gbp)

        for row in dividends.get("entries") or []:
            try:
                observed_dates.append(date.fromisoformat(str(row.get("dividend_date"))))
            except ValueError:
                continue

        total_drag = _q_money(emp_tax + cgt + fees + div_tax + fx_drag + _q_money(employment_event_tax_total))
        capital_base = _q_money(_safe_decimal(stack.get("gross_market_value_gbp")) + _safe_decimal(stack.get("gbp_deployable_cash_gbp")))
        drag_rate_pct = _pct(total_drag, capital_base)

        if observed_dates:
            first_observed = min(observed_dates)
            period_days = max(1, (as_of_date - first_observed).days)
        else:
            first_observed = as_of_date
            period_days = 365
        annualized_drag_pct = _q_pct(drag_rate_pct * (Decimal("365") / Decimal(period_days)))

        components = [
            {"label": "Employment Tax (Hypothetical)", "amount_gbp": str(_q_money(emp_tax)), "basis": "Current sellable-lot estimate"},
            {"label": "CGT (Hypothetical)", "amount_gbp": str(_q_money(cgt)), "basis": "Capital-stack taxable gain estimate"},
            {"label": "Broker Fees (Hypothetical)", "amount_gbp": str(_q_money(fees)), "basis": "Historical avg fee/share model"},
            {"label": "Dividend Tax (Estimated)", "amount_gbp": str(_q_money(div_tax)), "basis": "Dividend ledger by tax year"},
            {"label": "FX Drag (Observed)", "amount_gbp": str(_q_money(fx_drag)), "basis": "Negative FX contribution only"},
            {"label": "Employment Tax Events (Recorded)", "amount_gbp": str(_q_money(employment_event_tax_total)), "basis": "Persisted employment_tax_events"},
        ]

        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "as_of_date": as_of_date.isoformat(),
            "components": components,
            "capital_base_gbp": str(capital_base),
            "total_structural_drag_gbp": str(total_drag),
            "structural_drag_rate_pct": str(drag_rate_pct),
            "annualized_drag_rate_pct": str(annualized_drag_pct),
            "observed_window_days": period_days,
            "observed_window_start": first_observed.isoformat(),
            "realized_fee_drag_total_gbp": str(_q_money(realized_fees_total)),
            "realized_fee_drag_12m_gbp": str(_q_money(realized_fees_12m)),
            "realized_proceeds_12m_gbp": str(_q_money(realized_proceeds_12m)),
            "realized_fee_drag_12m_pct_of_proceeds": str(_pct(_q_money(realized_fees_12m), _q_money(realized_proceeds_12m))),
            "model_scope": StrategicService.model_scope("capital_efficiency"),
            "notes": [
                "Structural drag is deterministic and formula-based.",
                "Annualized drag scales observed window to a 365-day equivalent.",
            ],
        }

    @staticmethod
    def get_isa_efficiency(*, settings: AppSettings | None, tax_year: str | None = None) -> dict[str, Any]:
        as_of_date = date.today()
        active_tax_year = tax_year or (settings.default_tax_year if settings else tax_year_for_date(as_of_date))
        start_date, end_date = _tax_year_bounds(active_tax_year)

        summary = PortfolioService.get_portfolio_summary(settings=settings, use_live_true_cost=False)
        exposure = ExposureService.get_snapshot(settings=settings, db_path=None, summary=summary)
        isa_market = _safe_decimal(exposure.get("isa_wrapper_market_value_gbp"))
        taxable_market = _safe_decimal(exposure.get("taxable_wrapper_market_value_gbp"))

        estimated_contrib = Decimal("0")
        with AppContext.read_session() as sess:
            isa_lots = list(sess.scalars(select(Lot).where(Lot.scheme_type == "ISA", Lot.acquisition_date >= start_date, Lot.acquisition_date <= end_date)).all())
            for lot in isa_lots:
                estimated_contrib += _safe_decimal(lot.quantity) * _safe_decimal(lot.acquisition_price_gbp)

        allowance = _safe_decimal(getattr(settings, "isa_annual_allowance_gbp", None), _DEFAULT_ISA_ALLOWANCE_GBP)
        headroom = _q_money(max(Decimal("0"), allowance - estimated_contrib))
        shelterable = _q_money(min(headroom, taxable_market))

        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "active_tax_year": active_tax_year,
            "tax_year_start": start_date.isoformat(),
            "tax_year_end": end_date.isoformat(),
            "isa_annual_allowance_gbp": str(_q_money(allowance)),
            "estimated_isa_contributions_gbp": str(_q_money(estimated_contrib)),
            "estimated_isa_headroom_gbp": str(headroom),
            "isa_market_value_gbp": str(_q_money(isa_market)),
            "taxable_market_value_gbp": str(_q_money(taxable_market)),
            "isa_ratio_pct": str(_pct(isa_market, isa_market + taxable_market)),
            "potential_shelterable_today_gbp": str(shelterable),
            "notes": [
                "Headroom uses ISA-lot acquisition values in the selected tax year.",
                "No future subscription forecasting is applied.",
            ],
        }
    @staticmethod
    def get_fee_drag_ledger() -> dict[str, Any]:
        with AppContext.read_session() as sess:
            security_map = {row.id: row.ticker for row in sess.scalars(select(Security)).all()}
            disposals = list(
                sess.scalars(
                    select(Transaction)
                    .where(
                        Transaction.transaction_type == "DISPOSAL",
                        Transaction.is_reversal.is_(False),
                    )
                    .order_by(Transaction.transaction_date.desc(), Transaction.id.asc())
                ).all()
            )
            lot_disposals = list(sess.scalars(select(LotDisposal)).all())

        by_tx: dict[str, list[LotDisposal]] = defaultdict(list)
        for row in lot_disposals:
            by_tx[row.transaction_id].append(row)

        tx_rows: list[dict[str, Any]] = []
        by_tax_year: dict[str, dict[str, Decimal | int]] = defaultdict(
            lambda: {
                "transaction_count": 0,
                "gross_proceeds_gbp": Decimal("0"),
                "broker_fees_gbp": Decimal("0"),
                "realised_economic_gain_gbp": Decimal("0"),
            }
        )

        total_proceeds = Decimal("0")
        total_fees = Decimal("0")
        total_realised_economic = Decimal("0")

        for tx in disposals:
            proceeds = _safe_decimal(tx.total_proceeds_gbp)
            fees = _safe_decimal(tx.broker_fees_gbp)
            realised_economic = _q_money(
                sum(
                    (_safe_decimal(ld.realised_gain_economic_gbp) for ld in by_tx.get(tx.id, [])),
                    Decimal("0"),
                )
            )
            total_proceeds += proceeds
            total_fees += fees
            total_realised_economic += realised_economic

            tax_year = tax_year_for_date(tx.transaction_date)
            bucket = by_tax_year[tax_year]
            bucket["transaction_count"] = int(bucket["transaction_count"]) + 1
            bucket["gross_proceeds_gbp"] = Decimal(bucket["gross_proceeds_gbp"]) + proceeds
            bucket["broker_fees_gbp"] = Decimal(bucket["broker_fees_gbp"]) + fees
            bucket["realised_economic_gain_gbp"] = Decimal(bucket["realised_economic_gain_gbp"]) + realised_economic

            tx_rows.append(
                {
                    "transaction_id": tx.id,
                    "transaction_date": tx.transaction_date.isoformat(),
                    "tax_year": tax_year,
                    "ticker": security_map.get(tx.security_id, "UNKNOWN"),
                    "quantity": str(_q_money(_safe_decimal(tx.quantity))),
                    "gross_proceeds_gbp": str(_q_money(proceeds)),
                    "broker_fees_gbp": str(_q_money(fees)),
                    "fee_pct_of_proceeds": str(_pct(_q_money(fees), _q_money(proceeds))),
                    "realised_economic_gain_gbp": str(_q_money(realised_economic)),
                }
            )

        tax_year_rows: list[dict[str, Any]] = []
        for tax_year in sorted(by_tax_year.keys(), reverse=True):
            bucket = by_tax_year[tax_year]
            proceeds = _q_money(Decimal(bucket["gross_proceeds_gbp"]))
            fees = _q_money(Decimal(bucket["broker_fees_gbp"]))
            realised = _q_money(Decimal(bucket["realised_economic_gain_gbp"]))
            tax_year_rows.append(
                {
                    "tax_year": tax_year,
                    "transaction_count": int(bucket["transaction_count"]),
                    "gross_proceeds_gbp": str(proceeds),
                    "broker_fees_gbp": str(fees),
                    "fee_pct_of_proceeds": str(_pct(fees, proceeds)),
                    "realised_economic_gain_gbp": str(realised),
                    "realised_economic_after_fees_gbp": str(_q_money(realised - fees)),
                }
            )

        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "totals": {
                "gross_proceeds_gbp": str(_q_money(total_proceeds)),
                "broker_fees_gbp": str(_q_money(total_fees)),
                "fee_pct_of_proceeds": str(_pct(_q_money(total_fees), _q_money(total_proceeds))),
                "realised_economic_gain_gbp": str(_q_money(total_realised_economic)),
                "realised_economic_after_fees_gbp": str(_q_money(total_realised_economic - total_fees)),
            },
            "tax_year_rows": tax_year_rows,
            "transaction_rows": tx_rows,
            "notes": [
                "Broker fee drag uses committed disposal transactions only.",
                "Economic gain after fees is traceable to transaction and lot-disposal rows.",
            ],
        }

    @staticmethod
    def get_data_quality(settings: AppSettings | None) -> dict[str, Any]:
        summary = PortfolioService.get_portfolio_summary(settings=settings, use_live_true_cost=False)

        stale_price_security_count = 0
        stale_fx_security_count = 0
        missing_price_security_count = 0
        missing_price_lot_count = 0
        missing_tax_estimate_lot_count = 0

        for sec in summary.securities:
            if sec.price_is_stale:
                stale_price_security_count += 1
            if sec.fx_is_stale:
                stale_fx_security_count += 1
            if sec.market_value_gbp is None and sec.active_lots:
                missing_price_security_count += 1

            for lot_summary in sec.active_lots:
                if lot_summary.market_value_gbp is None:
                    missing_price_lot_count += 1
                    continue
                if lot_summary.sellability_status != "LOCKED" and lot_summary.est_employment_tax_on_lot_gbp is None:
                    missing_tax_estimate_lot_count += 1

        tax_plan = TaxPlanService.get_summary(settings=settings)
        assumptions = tax_plan.get("assumptions") or {}
        freshness = assumptions.get("input_freshness") or {}

        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "stale_price_security_count": stale_price_security_count,
                "stale_fx_security_count": stale_fx_security_count,
                "missing_price_security_count": missing_price_security_count,
                "missing_price_lot_count": missing_price_lot_count,
                "missing_tax_estimate_lot_count": missing_tax_estimate_lot_count,
            },
            "tax_plan_freshness": {
                "stale_price_security_count": int(_safe_decimal(freshness.get("stale_price_security_count"))),
                "stale_fx_security_count": int(_safe_decimal(freshness.get("stale_fx_security_count"))),
                "unpriced_lot_count": int(_safe_decimal(freshness.get("unpriced_lot_count"))),
            },
            "impact_rows": [
                {
                    "surface": "Portfolio / Net Value",
                    "issue": "Stale or missing market inputs",
                    "impact_count": stale_price_security_count + missing_price_security_count,
                    "severity": "High" if (stale_price_security_count + missing_price_security_count) > 0 else "Low",
                },
                {
                    "surface": "Risk / Analytics",
                    "issue": "Unpriced lots reduce classification and concentration fidelity",
                    "impact_count": missing_price_lot_count,
                    "severity": "High" if missing_price_lot_count > 0 else "Low",
                },
                {
                    "surface": "Tax Plan",
                    "issue": "Projection assumptions widened by input gaps",
                    "impact_count": int(_safe_decimal(freshness.get("unpriced_lot_count"))),
                    "severity": "High" if int(_safe_decimal(freshness.get("unpriced_lot_count"))) > 0 else "Medium",
                },
                {
                    "surface": "Scenario / Simulate",
                    "issue": "Employment-tax totals incomplete for some sellable lots",
                    "impact_count": missing_tax_estimate_lot_count,
                    "severity": "High" if missing_tax_estimate_lot_count > 0 else "Low",
                },
            ],
            "notes": [
                "Data quality is deterministic and sourced from current service outputs.",
                "No inferred backfill is applied in this page.",
            ],
        }

    @staticmethod
    def get_employment_tax_events(settings: AppSettings | None) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        with AppContext.read_session() as sess:
            securities = {s.id: s.ticker for s in sess.scalars(select(Security)).all()}
            lots = {lot.id: lot for lot in sess.scalars(select(Lot)).all()}

            persisted = list(
                sess.scalars(
                    select(EmploymentTaxEvent)
                    .order_by(EmploymentTaxEvent.event_date.desc(), EmploymentTaxEvent.id.asc())
                ).all()
            )
            for event in persisted:
                est = _safe_decimal(event.estimated_tax_gbp)
                rows.append(
                    {
                        "event_source": "PERSISTED",
                        "event_date": event.event_date.isoformat(),
                        "tax_year": tax_year_for_date(event.event_date),
                        "ticker": securities.get(event.security_id, "UNKNOWN"),
                        "security_id": event.security_id,
                        "lot_id": event.lot_id,
                        "event_type": event.event_type,
                        "estimated_tax_gbp": str(_q_money(est)) if est > 0 else None,
                        "note": event.estimation_notes,
                    }
                )

            disposal_pairs = list(
                sess.execute(
                    select(LotDisposal, Transaction)
                    .join(Transaction, LotDisposal.transaction_id == Transaction.id)
                    .where(Transaction.transaction_type == "DISPOSAL", Transaction.is_reversal.is_(False))
                ).all()
            )

            lots_context = EmploymentTaxContext(lots_by_id=lots)
            for lot_disposal, tx in disposal_pairs:
                lot = lots.get(lot_disposal.lot_id)
                if lot is None:
                    continue

                est_tax: Decimal | None = None
                note = None
                if settings is None:
                    note = "Tax settings unavailable; derived disposal employment tax omitted."
                else:
                    try:
                        ctx = TaxContext(
                            tax_year=tax_year_for_date(tx.transaction_date),
                            gross_employment_income=settings.default_gross_income,
                            pension_sacrifice=settings.default_pension_sacrifice,
                            other_income=settings.default_other_income,
                            student_loan_plan=settings.default_student_loan_plan,
                        )
                        rates = get_marginal_rates(ctx)
                        estimate = estimate_employment_tax_for_lot(
                            lot=lot,
                            quantity=_safe_decimal(lot_disposal.quantity_allocated),
                            event_date=tx.transaction_date,
                            disposal_price_per_share_gbp=_safe_decimal(tx.price_per_share_gbp),
                            rates=rates,
                            context=lots_context,
                        )
                        est_tax = _q_money(estimate.est_total_gbp)
                        note = f"Derived from disposal allocation ({estimate.holding_period_category})."
                    except Exception as exc:
                        note = f"Could not derive disposal employment tax: {exc}"

                rows.append(
                    {
                        "event_source": "DERIVED_DISPOSAL",
                        "event_date": tx.transaction_date.isoformat(),
                        "tax_year": tax_year_for_date(tx.transaction_date),
                        "ticker": securities.get(tx.security_id, "UNKNOWN"),
                        "security_id": tx.security_id,
                        "lot_id": lot_disposal.lot_id,
                        "event_type": "DISPOSAL_EMPLOYMENT_TAX_ESTIMATE",
                        "estimated_tax_gbp": str(est_tax) if est_tax is not None else None,
                        "note": note,
                    }
                )

        rows.sort(key=lambda row: (row["event_date"], row["event_source"], row["ticker"], row["lot_id"]), reverse=True)

        totals_by_year: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        counts_by_year: dict[str, int] = defaultdict(int)
        for row in rows:
            tax_year = str(row["tax_year"])
            counts_by_year[tax_year] += 1
            est = _to_decimal(row.get("estimated_tax_gbp"))
            if est is not None:
                totals_by_year[tax_year] += est

        tax_year_rows = [
            {
                "tax_year": tax_year,
                "event_count": counts_by_year[tax_year],
                "estimated_tax_total_gbp": str(_q_money(totals_by_year[tax_year])),
            }
            for tax_year in sorted(counts_by_year.keys(), reverse=True)
        ]

        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "tax_year_rows": tax_year_rows,
            "event_rows": rows,
            "notes": [
                "Persisted and derived events are shown together with explicit source tags.",
                "Derived rows are deterministic from disposal allocations and configured tax rates.",
            ],
        }

    @staticmethod
    def _reconcile_drift_panel(
        *,
        settings: AppSettings | None,
        lookback_days: int,
    ) -> dict[str, Any]:
        history = HistoryService.get_portfolio_history(settings=settings)
        series = history.get("total_series") or []
        if len(series) < 2:
            return {
                "has_data": False,
                "lookback_days": lookback_days,
                "rows": [],
                "notes": [
                    "Not enough portfolio history points to build drift decomposition.",
                ],
            }

        lookback = max(int(lookback_days), 1)
        cutoff = date.today() - timedelta(days=lookback)

        prior_idx = 0
        for idx, row in enumerate(series):
            try:
                row_date = date.fromisoformat(str(row.get("date")))
            except ValueError:
                continue
            if row_date <= cutoff:
                prior_idx = idx
            else:
                break

        if prior_idx >= len(series) - 1:
            prior_idx = max(0, len(series) - 2)

        prior_row = series[prior_idx]
        current_row = series[-1]
        interval_rows = series[prior_idx + 1 :]

        price_component = _q_money(
            sum((_safe_decimal(row.get("decomp_price_gbp")) for row in interval_rows), Decimal("0"))
        )
        fx_component = _q_money(
            sum((_safe_decimal(row.get("decomp_fx_gbp")) for row in interval_rows), Decimal("0"))
        )
        quantity_component = _q_money(
            sum((_safe_decimal(row.get("decomp_quantity_gbp")) for row in interval_rows), Decimal("0"))
        )
        transaction_component = _q_money(
            sum((_safe_decimal(row.get("decomp_dividends_gbp")) for row in interval_rows), Decimal("0"))
        )
        settings_component = Decimal("0.00")

        total_change = _q_money(
            _safe_decimal(current_row.get("total_value_gbp"))
            - _safe_decimal(prior_row.get("total_value_gbp"))
        )
        explained_change = _q_money(
            price_component
            + fx_component
            + quantity_component
            + transaction_component
            + settings_component
        )
        residual = _q_money(total_change - explained_change)

        prior_date_raw = str(prior_row.get("date") or "")
        current_date_raw = str(current_row.get("date") or "")
        try:
            prior_date = date.fromisoformat(prior_date_raw)
            since_dt = datetime.combine(prior_date, datetime.min.time())
        except ValueError:
            since_dt = None
            prior_date_raw = ""

        mutation_counts = {
            "price_fx": 0,
            "quantity": 0,
            "transactions": 0,
            "settings": 0,
            "other_audit": 0,
        }
        if since_dt is not None:
            for entry in ReportService.audit_log(since=since_dt):
                table_name = str(entry.table_name or "").lower()
                if table_name in {"price_history", "fx_rates"}:
                    mutation_counts["price_fx"] += 1
                elif table_name == "lots":
                    mutation_counts["quantity"] += 1
                elif table_name in {"transactions", "lot_disposals", "employment_tax_events", "dividend_entries"}:
                    mutation_counts["transactions"] += 1
                elif table_name in {"settings", "app_settings"}:
                    mutation_counts["settings"] += 1
                else:
                    mutation_counts["other_audit"] += 1

        audit_window_href = _audit_window_href(
            date_from=prior_date_raw or None,
            date_to=current_date_raw or None,
        )
        basis_timeline_href = _basis_timeline_href(lookback_days=lookback)
        settings_audit_count = mutation_counts["settings"] + mutation_counts["other_audit"]

        return {
            "has_data": True,
            "lookback_days": lookback,
            "prior_date": prior_date_raw or None,
            "current_date": current_date_raw or None,
            "total_change_gbp": str(total_change),
            "explained_change_gbp": str(explained_change),
            "residual_gbp": str(residual),
            "audit_window_href": audit_window_href,
            "rows": [
                {
                    "cause": "Price",
                    "amount_gbp": str(price_component),
                    "detail": "Daily price move at prior-day quantity basis.",
                    "mutation_count": mutation_counts["price_fx"],
                    "trace_href": basis_timeline_href,
                    "trace_label": "Open basis timeline",
                },
                {
                    "cause": "FX",
                    "amount_gbp": str(fx_component),
                    "detail": "Residual FX move after price/quantity/dividend effects.",
                    "mutation_count": mutation_counts["price_fx"],
                    "trace_href": basis_timeline_href,
                    "trace_label": "Open basis timeline",
                },
                {
                    "cause": "Quantity",
                    "amount_gbp": str(quantity_component),
                    "detail": "Quantity changes at prior-day price (buy/sell/transfer effect).",
                    "mutation_count": mutation_counts["quantity"],
                    "trace_href": _audit_window_href(
                        table_name="lots",
                        date_from=prior_date_raw or None,
                        date_to=current_date_raw or None,
                    ),
                    "trace_label": "Open lot audit window",
                },
                {
                    "cause": "Transactions",
                    "amount_gbp": str(transaction_component),
                    "detail": "Cashflow component from dividend-ledger deltas in the interval.",
                    "mutation_count": mutation_counts["transactions"],
                    "trace_href": audit_window_href,
                    "trace_label": "Open audit window",
                },
                {
                    "cause": "Settings / Audit",
                    "amount_gbp": str(settings_component),
                    "detail": (
                        "Assumption or metadata mutations in the window are counted here; "
                        "no deterministic settings-value reprice is applied in this panel."
                    ),
                    "mutation_count": settings_audit_count,
                    "trace_href": audit_window_href,
                    "trace_label": "Open audit window",
                },
                {
                    "cause": "Residual",
                    "amount_gbp": str(residual),
                    "detail": "Rounding remainder after component aggregation.",
                    "mutation_count": 0,
                    "trace_href": "/history",
                    "trace_label": "Open history",
                },
            ],
            "notes": [
                "Drift components are aggregated from portfolio-history decomposition rows.",
                "Price and FX trace links open the basis timeline; quantity and audit-context rows open the filtered audit window.",
                "Settings impact is shown as mutation count only; value-effect remains zero without a deterministic replay engine.",
            ],
        }

    @staticmethod
    def get_cross_page_reconcile(
        *,
        settings: AppSettings | None,
        db_path,
        lookback_days: int = 30,
    ) -> dict[str, Any]:
        summary = PortfolioService.get_portfolio_summary(settings=settings, use_live_true_cost=False)
        exposure = ExposureService.get_snapshot(settings=settings, db_path=db_path, summary=summary)
        stack = CapitalStackService.get_snapshot(settings=settings, db_path=db_path, summary=summary)
        tax_plan = TaxPlanService.get_summary(settings=settings)
        drift_panel = StrategicService._reconcile_drift_panel(
            settings=settings,
            lookback_days=lookback_days,
        )

        gross = _safe_decimal(summary.total_market_value_gbp)
        net_value = _safe_decimal(summary.est_total_net_liquidation_gbp)
        emp_tax = _safe_decimal(summary.est_total_employment_tax_gbp)
        locked = _safe_decimal(exposure.get("locked_capital_gbp"))
        forfeitable = _safe_decimal(exposure.get("forfeitable_capital_gbp"))
        cgt = _safe_decimal(stack.get("estimated_cgt_gbp"))
        fees = _safe_decimal(stack.get("estimated_fees_gbp"))
        deployable_cash = _safe_decimal(exposure.get("deployable_cash_gbp"))

        reconciled_deployable = _q_money(net_value - locked - forfeitable - cgt - fees + deployable_cash)
        reported_deployable = _q_money(_safe_decimal(exposure.get("deployable_capital_gbp")))
        realised_to_date = (tax_plan.get("summary") or {}).get("realised_to_date") or {}

        contributing_lot_rows: list[dict[str, Any]] = []
        for security_summary in summary.securities:
            ticker = str(security_summary.security.ticker or "UNKNOWN")
            for lot_summary in security_summary.active_lots:
                lot = lot_summary.lot
                contributing_lot_rows.append(
                    {
                        "ticker": ticker,
                        "security_id": lot.security_id,
                        "lot_id": lot.id,
                        "scheme_type": lot.scheme_type,
                        "acquisition_date": lot.acquisition_date.isoformat(),
                        "quantity_remaining": str(lot_summary.quantity_remaining),
                        "market_value_gbp": (
                            str(_q_money(lot_summary.market_value_gbp))
                            if lot_summary.market_value_gbp is not None
                            else None
                        ),
                        "sellability_status": lot_summary.sellability_status,
                        "sellability_unlock_date": (
                            lot_summary.sellability_unlock_date.isoformat()
                            if lot_summary.sellability_unlock_date is not None
                            else None
                        ),
                        "forfeiture_days_remaining": (
                            lot_summary.forfeiture_risk.days_remaining
                            if lot_summary.forfeiture_risk is not None
                            else None
                        ),
                        "audit_href": f"/audit?table_name=lots&record_id={lot.id}",
                    }
                )
        contributing_lot_rows.sort(
            key=lambda row: abs(_safe_decimal(row.get("market_value_gbp"))),
            reverse=True,
        )

        recent_audit_rows = []
        for entry in ReportService.audit_log()[:40]:
            recent_audit_rows.append(
                {
                    "changed_at_utc": (
                        entry.changed_at.isoformat(sep=" ")
                        if entry.changed_at is not None
                        else None
                    ),
                    "table_name": entry.table_name,
                    "action": entry.action,
                    "record_id": entry.record_id,
                    "notes": entry.notes,
                    "audit_href": (
                        f"/audit?table_name={entry.table_name}&record_id={entry.record_id}"
                    ),
                }
            )

        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "components": [
                {
                    "step": "Portfolio Gross Market Value",
                    "amount_gbp": str(_q_money(gross)),
                    "scope": "All priced lots (sellable + locked + forfeitable)",
                },
                {
                    "step": "Less Estimated Employment Tax",
                    "amount_gbp": str(_q_money(-emp_tax)),
                    "scope": "Sellable lots only",
                },
                {
                    "step": "Equals Net Value (Sell-All Surface)",
                    "amount_gbp": str(_q_money(net_value)),
                    "scope": "Hypothetical sell-all at current basis",
                },
                {
                    "step": "Less Locked + Forfeitable Capital",
                    "amount_gbp": str(_q_money(-(locked + forfeitable))),
                    "scope": "Liquid-pool scope conversion",
                },
                {
                    "step": "Less Estimated CGT + Fees",
                    "amount_gbp": str(_q_money(-(cgt + fees))),
                    "scope": "Capital-stack deployable friction",
                },
                {
                    "step": "Plus Deployable GBP Cash",
                    "amount_gbp": str(_q_money(deployable_cash)),
                    "scope": "BROKER + BANK GBP cash",
                },
                {
                    "step": "Reconciled Deployable Capital",
                    "amount_gbp": str(reconciled_deployable),
                    "scope": "Deterministic cross-surface reconcile",
                },
            ],
            "reported_deployable_capital_gbp": str(reported_deployable),
            "reconciled_deployable_capital_gbp": str(reconciled_deployable),
            "reconciliation_delta_gbp": str(_q_money(reconciled_deployable - reported_deployable)),
            "tax_plan_realised_net_gain_gbp": str(_q_money(_safe_decimal(realised_to_date.get("net_gain_gbp")))),
            "tax_plan_realised_cgt_gbp": str(_q_money(_safe_decimal(realised_to_date.get("total_cgt_gbp")))),
            "contributing_lot_rows": contributing_lot_rows[:120],
            "recent_audit_rows": recent_audit_rows,
            "drift_panel": drift_panel,
            "trace_links": {
                "contributing_lots": "/reconcile#trace-contributing-lots",
                "audit_mutations": "/reconcile#trace-audit-mutations",
                "drift_panel": "/reconcile#trace-drift-decomposition",
            },
            "notes": [
                "Cross-page differences are scope differences, not arithmetic contradictions.",
                "Top-value contributing lots and recent audit mutations are included for traceability.",
                "This utility uses current deterministic service outputs only.",
            ],
        }

    @staticmethod
    def get_price_fx_basis_timeline(*, settings: AppSettings | None, lookback_days: int = 365) -> dict[str, Any]:
        summary = PortfolioService.get_portfolio_summary(settings=settings, use_live_true_cost=False)
        qty_by_security = {
            sec.security.id: sec.total_quantity
            for sec in summary.securities
            if sec.total_quantity > Decimal("0")
        }
        if not qty_by_security:
            return {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "lookback_days": lookback_days,
                "date_rows": [],
                "security_rows": [],
                "notes": ["No active quantities available for basis timeline."],
            }

        cutoff = date.today() - timedelta(days=max(1, lookback_days))
        with AppContext.read_session() as sess:
            rows = list(
                sess.scalars(
                    select(PriceHistory)
                    .where(
                        PriceHistory.security_id.in_(list(qty_by_security.keys())),
                        PriceHistory.price_date >= cutoff,
                    )
                    .order_by(
                        PriceHistory.security_id.asc(),
                        PriceHistory.price_date.asc(),
                        PriceHistory.fetched_at.asc(),
                        PriceHistory.id.asc(),
                    )
                ).all()
            )
            ticker_map = {sec.id: sec.ticker for sec in sess.scalars(select(Security)).all()}

        best_rows = _best_price_rows(rows)
        per_security: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for (_security_id, _price_date), row in best_rows.items():
            native = _to_decimal(row.close_price_original_ccy)
            gbp = _to_decimal(row.close_price_gbp)
            if gbp is None and (row.currency or "").upper() == "GBP":
                gbp = native
            if native is None or gbp is None or native <= 0:
                continue

            currency = (row.currency or "GBP").upper()
            implied_fx = Decimal("1") if currency == "GBP" else _q_money(gbp / native)
            per_security[row.security_id].append(
                {
                    "date": row.price_date,
                    "currency": currency,
                    "native_price": native,
                    "gbp_price": gbp,
                    "implied_fx": implied_fx,
                    "source": row.source,
                    "fetched_at": ((row.fetched_at or row.created_at).isoformat() if (row.fetched_at or row.created_at) else None),
                }
            )

        aggregate_by_date: dict[date, dict[str, Decimal]] = defaultdict(
            lambda: {"native_move_gbp": Decimal("0"), "fx_move_gbp": Decimal("0"), "total_change_gbp": Decimal("0")}
        )
        security_rows: list[dict[str, Any]] = []

        for security_id, points in per_security.items():
            points.sort(key=lambda p: p["date"])
            qty = qty_by_security.get(security_id, Decimal("0"))
            prev: dict[str, Any] | None = None
            for point in points:
                if prev is None:
                    prev = point
                    continue

                native_move = _q_money((point["native_price"] - prev["native_price"]) * prev["implied_fx"] * qty)
                fx_move = _q_money(point["native_price"] * (point["implied_fx"] - prev["implied_fx"]) * qty)
                total_change = _q_money((point["gbp_price"] - prev["gbp_price"]) * qty)

                agg = aggregate_by_date[point["date"]]
                agg["native_move_gbp"] += native_move
                agg["fx_move_gbp"] += fx_move
                agg["total_change_gbp"] += total_change

                security_rows.append(
                    {
                        "date": point["date"].isoformat(),
                        "ticker": ticker_map.get(security_id, "UNKNOWN"),
                        "security_id": security_id,
                        "currency": point["currency"],
                        "quantity_basis": str(_q_money(qty)),
                        "native_move_gbp": str(_q_money(native_move)),
                        "fx_move_gbp": str(_q_money(fx_move)),
                        "total_change_gbp": str(_q_money(total_change)),
                        "source": point["source"],
                        "fetched_at": point["fetched_at"],
                    }
                )
                prev = point

        date_rows = [
            {
                "date": d.isoformat(),
                "native_move_gbp": str(_q_money(values["native_move_gbp"])),
                "fx_move_gbp": str(_q_money(values["fx_move_gbp"])),
                "total_change_gbp": str(_q_money(values["total_change_gbp"])),
            }
            for d, values in sorted(aggregate_by_date.items(), key=lambda item: item[0], reverse=True)
        ]

        security_rows.sort(key=lambda row: (row["date"], abs(_safe_decimal(row["total_change_gbp"]))), reverse=True)

        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "lookback_days": lookback_days,
            "date_rows": date_rows,
            "security_rows": security_rows[:600],
            "notes": [
                "Attribution uses currently held quantities as deterministic quantity basis.",
                "Total GBP change is split into native-price and FX contribution components.",
            ],
        }

    @staticmethod
    def get_employment_exit(*, settings: AppSettings | None, exit_date: date, price_shock_pct: Decimal = Decimal("0")) -> dict[str, Any]:
        summary = PortfolioService.get_portfolio_summary(settings=settings, use_live_true_cost=False)

        shock_mult = Decimal("1") + (price_shock_pct / Decimal("100"))
        rows: list[dict[str, Any]] = []
        totals = {
            "gross_value_gbp": Decimal("0"),
            "retained_value_gbp": Decimal("0"),
            "forfeited_value_gbp": Decimal("0"),
            "estimated_tax_gbp": Decimal("0"),
            "net_after_tax_gbp": Decimal("0"),
        }
        unpriced_security_count = 0

        for sec in summary.securities:
            if sec.current_price_gbp is None:
                unpriced_security_count += 1
                continue

            shocked_price = _q_money(Decimal(sec.current_price_gbp) * shock_mult)
            gross_value = Decimal("0")
            retained_value = Decimal("0")
            forfeited_value = Decimal("0")
            retained_lots: list[Lot] = []
            forfeiture_count = 0

            for lot_summary in sec.active_lots:
                qty = lot_summary.quantity_remaining
                lot_value = _q_money(qty * shocked_price)
                gross_value += lot_value
                lot = lot_summary.lot

                if lot.scheme_type == "RSU" and exit_date < lot.acquisition_date:
                    forfeited_value += lot_value
                    forfeiture_count += 1
                    continue

                if lot.scheme_type == "ESPP_PLUS" and lot.matching_lot_id is not None:
                    forfeiture_end = lot.forfeiture_period_end or (lot.acquisition_date + timedelta(days=183))
                    if exit_date < forfeiture_end:
                        forfeited_value += lot_value
                        forfeiture_count += 1
                        continue

                retained_value += lot_value
                retained_lots.append(lot)

            est_tax = _estimate_sell_all_employment_tax(retained_lots, shocked_price, exit_date, settings)
            est_tax_value = _q_money(est_tax) if est_tax is not None else None
            net_after_tax = _q_money(retained_value - est_tax_value) if est_tax_value is not None else None

            totals["gross_value_gbp"] += gross_value
            totals["retained_value_gbp"] += retained_value
            totals["forfeited_value_gbp"] += forfeited_value
            if est_tax_value is not None:
                totals["estimated_tax_gbp"] += est_tax_value
            if net_after_tax is not None:
                totals["net_after_tax_gbp"] += net_after_tax

            rows.append(
                {
                    "ticker": sec.security.ticker,
                    "security_id": sec.security.id,
                    "exit_price_used_gbp": str(_q_money(shocked_price)),
                    "gross_value_gbp": str(_q_money(gross_value)),
                    "retained_value_gbp": str(_q_money(retained_value)),
                    "forfeited_value_gbp": str(_q_money(forfeited_value)),
                    "forfeiture_lot_count": forfeiture_count,
                    "estimated_employment_tax_gbp": str(est_tax_value) if est_tax_value is not None else None,
                    "net_after_tax_gbp": str(net_after_tax) if net_after_tax is not None else None,
                }
            )

        rows.sort(key=lambda row: _safe_decimal(row["forfeited_value_gbp"]), reverse=True)

        notes = [
            "Exit scenario uses static current pricing basis with optional deterministic shock.",
            "Pre-vest RSU and in-window ESPP+ matched lots are treated as forfeited.",
            "No market forecast or employment-policy projection is applied.",
        ]
        if unpriced_security_count > 0:
            notes.append(f"{unpriced_security_count} security(ies) excluded due to missing current price.")

        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "exit_date": exit_date.isoformat(),
            "price_shock_pct": str(_q_pct(price_shock_pct)),
            "rows": rows,
            "totals": {
                "gross_value_gbp": str(_q_money(totals["gross_value_gbp"])),
                "retained_value_gbp": str(_q_money(totals["retained_value_gbp"])),
                "forfeited_value_gbp": str(_q_money(totals["forfeited_value_gbp"])),
                "estimated_tax_gbp": str(_q_money(totals["estimated_tax_gbp"])),
                "net_after_tax_gbp": str(_q_money(totals["net_after_tax_gbp"])),
            },
            "model_scope": StrategicService.model_scope("employment_exit"),
            "notes": notes,
        }
