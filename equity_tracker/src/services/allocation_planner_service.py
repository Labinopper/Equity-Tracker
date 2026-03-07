"""AllocationPlannerService - deterministic trim-and-redeploy planner."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from ..settings import AppSettings
from .alert_service import AlertService
from .capital_stack_service import CapitalStackService
from .exposure_service import ExposureService
from .portfolio_service import PortfolioService

_MONEY_Q = Decimal("0.01")
_PCT_Q = Decimal("0.01")
_ZERO = Decimal("0")
_DEFAULT_TARGET_MAX_PCT = Decimal("25.00")

_SOURCE_MODE_AUTO = "AUTO"
_SOURCE_MODE_EMPLOYER = "EMPLOYER"
_SOURCE_MODE_TICKER = "TICKER"
_VALID_SOURCE_MODES = frozenset(
    {_SOURCE_MODE_AUTO, _SOURCE_MODE_EMPLOYER, _SOURCE_MODE_TICKER}
)
_VALID_WRAPPERS = frozenset({"ISA", "TAXABLE", "PENSION", "CASH"})


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _q_pct(value: Decimal) -> Decimal:
    return value.quantize(_PCT_Q, rounding=ROUND_HALF_UP)


def _safe_decimal(value: object, fallback: Decimal = _ZERO) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return fallback


def _pct(part: Decimal, whole: Decimal) -> Decimal:
    if whole <= _ZERO:
        return Decimal("0.00")
    return _q_pct((part / whole) * Decimal("100"))


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _storage_path(db_path: Path) -> Path:
    return Path(str(db_path) + ".allocation_planner.json")


def _load_json(path: Path) -> dict[str, Any]:
    fallback = {
        "version": 1,
        "settings": {
            "source_selection_mode": _SOURCE_MODE_AUTO,
            "source_ticker": "",
            "target_max_pct": str(_DEFAULT_TARGET_MAX_PCT),
        },
        "candidates": [],
    }
    if not path.exists():
        return dict(fallback)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(fallback)
    if not isinstance(data, dict):
        return dict(fallback)
    settings = data.get("settings", {})
    candidates = data.get("candidates", [])
    return {
        "version": int(data.get("version", 1)),
        "settings": settings if isinstance(settings, dict) else {},
        "candidates": [dict(row) for row in candidates if isinstance(row, dict)]
        if isinstance(candidates, list)
        else [],
    }


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _with_as_of(href: str, as_of_date: date | None) -> str:
    if as_of_date is None:
        return href
    parts = urlsplit(href)
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "as_of"
    ]
    query_pairs.append(("as_of", as_of_date.isoformat()))
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query_pairs),
            parts.fragment,
        )
    )


def _normalize_source_mode(value: object) -> str:
    mode = str(value or _SOURCE_MODE_AUTO).strip().upper()
    if mode not in _VALID_SOURCE_MODES:
        raise ValueError("Source selection mode must be AUTO, EMPLOYER, or TICKER.")
    return mode


def _normalize_target_max_pct(value: object) -> Decimal:
    pct = _q_pct(_safe_decimal(value, _DEFAULT_TARGET_MAX_PCT))
    if pct <= _ZERO or pct >= Decimal("100"):
        raise ValueError("Target max concentration must be between 0 and 100.")
    return pct


def _normalize_wrapper(value: object) -> str:
    wrapper = str(value or "TAXABLE").strip().upper()
    if wrapper not in _VALID_WRAPPERS:
        raise ValueError("Target wrapper must be ISA, TAXABLE, PENSION, or CASH.")
    return wrapper


def _wrapper_from_scheme(scheme_type: object) -> str:
    return "ISA" if str(scheme_type or "").strip().upper() == "ISA" else "TAXABLE"


def _candidate_key(candidate: dict[str, Any]) -> str:
    ticker = str(candidate.get("ticker") or "").strip().upper()
    label = str(candidate.get("label") or "").strip().upper()
    return ticker or label or "UNSPECIFIED"


def _lot_is_sellable(lot_summary) -> bool:
    in_forfeiture_window = (
        lot_summary.forfeiture_risk is not None
        and lot_summary.forfeiture_risk.in_window
        and lot_summary.lot.matching_lot_id is not None
    )
    if in_forfeiture_window:
        return False
    return str(lot_summary.sellability_status or "").upper() != "LOCKED"


class AllocationPlannerService:
    @staticmethod
    def load_plan(db_path: Path | None) -> dict[str, Any]:
        payload = (
            _load_json(_storage_path(db_path))
            if db_path is not None
            else _load_json(Path("unused"))
        )
        settings = payload.get("settings", {})
        try:
            mode = _normalize_source_mode(settings.get("source_selection_mode"))
        except ValueError:
            mode = _SOURCE_MODE_AUTO
        try:
            target_pct = _normalize_target_max_pct(settings.get("target_max_pct"))
        except ValueError:
            target_pct = _DEFAULT_TARGET_MAX_PCT

        clean_candidates: list[dict[str, Any]] = []
        for row in payload.get("candidates", []):
            try:
                clean_candidates.append(
                    {
                        "candidate_id": str(row.get("candidate_id") or uuid4().hex),
                        "label": str(row.get("label") or "").strip() or "Candidate",
                        "ticker": str(row.get("ticker") or "").strip().upper() or None,
                        "currency": str(row.get("currency") or "GBP").strip().upper() or "GBP",
                        "target_wrapper": _normalize_wrapper(row.get("target_wrapper")),
                        "bucket": str(row.get("bucket") or "").strip() or "UNSPECIFIED",
                        "allocation_weight": str(
                            _q_pct(_safe_decimal(row.get("allocation_weight"), Decimal("1")))
                        ),
                        "notes": str(row.get("notes") or "").strip() or None,
                    }
                )
            except ValueError:
                continue

        return {
            "version": int(payload.get("version", 1)),
            "settings": {
                "source_selection_mode": mode,
                "source_ticker": str(settings.get("source_ticker") or "").strip().upper(),
                "target_max_pct": str(target_pct),
            },
            "candidates": clean_candidates,
        }

    @staticmethod
    def save_plan(db_path: Path | None, payload: dict[str, Any]) -> None:
        if db_path is None:
            raise ValueError("Database path is required.")
        _save_json(_storage_path(db_path), payload)

    @staticmethod
    def save_settings(
        *,
        db_path: Path | None,
        source_selection_mode: str,
        source_ticker: str = "",
        target_max_pct: str = str(_DEFAULT_TARGET_MAX_PCT),
    ) -> dict[str, Any]:
        if db_path is None:
            raise ValueError("Database path is required.")
        payload = AllocationPlannerService.load_plan(db_path)
        payload["settings"] = {
            "source_selection_mode": _normalize_source_mode(source_selection_mode),
            "source_ticker": str(source_ticker or "").strip().upper(),
            "target_max_pct": str(_normalize_target_max_pct(target_max_pct)),
        }
        AllocationPlannerService.save_plan(db_path, payload)
        return payload

    @staticmethod
    def add_candidate(
        *,
        db_path: Path | None,
        label: str,
        ticker: str = "",
        currency: str = "GBP",
        target_wrapper: str = "TAXABLE",
        bucket: str = "UNSPECIFIED",
        allocation_weight: str = "1",
        notes: str = "",
    ) -> dict[str, Any]:
        if db_path is None:
            raise ValueError("Database path is required.")
        clean_label = str(label or "").strip()
        if not clean_label:
            raise ValueError("Candidate label is required.")
        weight = _q_pct(_safe_decimal(allocation_weight))
        if weight <= _ZERO:
            raise ValueError("Allocation weight must be greater than zero.")

        payload = AllocationPlannerService.load_plan(db_path)
        payload["candidates"].append(
            {
                "candidate_id": uuid4().hex,
                "label": clean_label,
                "ticker": str(ticker or "").strip().upper() or None,
                "currency": str(currency or "GBP").strip().upper() or "GBP",
                "target_wrapper": _normalize_wrapper(target_wrapper),
                "bucket": str(bucket or "").strip() or "UNSPECIFIED",
                "allocation_weight": str(weight),
                "notes": str(notes or "").strip() or None,
            }
        )
        AllocationPlannerService.save_plan(db_path, payload)
        return payload

    @staticmethod
    def remove_candidate(
        *,
        db_path: Path | None,
        candidate_id: str,
    ) -> bool:
        if db_path is None:
            raise ValueError("Database path is required.")
        payload = AllocationPlannerService.load_plan(db_path)
        before = len(payload.get("candidates", []))
        payload["candidates"] = [
            row
            for row in payload.get("candidates", [])
            if str(row.get("candidate_id") or "") != str(candidate_id or "").strip()
        ]
        removed = len(payload["candidates"]) != before
        if removed:
            AllocationPlannerService.save_plan(db_path, payload)
        return removed

    @staticmethod
    def get_dashboard(
        *,
        settings: AppSettings | None,
        db_path: Path | None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        as_of_date = as_of or date.today()
        plan = AllocationPlannerService.load_plan(db_path)
        planner_settings = plan.get("settings", {})
        target_max_pct = _safe_decimal(
            planner_settings.get("target_max_pct"), _DEFAULT_TARGET_MAX_PCT
        )

        summary = PortfolioService.get_portfolio_summary(
            settings=settings,
            use_live_true_cost=False,
            as_of=as_of_date,
        )
        exposure = ExposureService.get_snapshot(
            settings=settings,
            db_path=db_path,
            summary=summary,
        )
        stack = CapitalStackService.get_snapshot(
            settings=settings,
            db_path=db_path,
            summary=summary,
            as_of=as_of_date,
        )
        thresholds = AlertService.concentration_thresholds(settings)

        total_before = _q_money(_safe_decimal(summary.total_market_value_gbp))
        holdings_before_by_key: dict[str, Decimal] = defaultdict(
            lambda: Decimal("0.00")
        )
        currency_before: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        wrapper_before: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        security_rows: list[dict[str, Any]] = []
        source_meta_by_ticker: dict[str, dict[str, Any]] = {}

        for security_summary in summary.securities:
            ticker = str(
                security_summary.security.ticker or security_summary.security.id
            ).strip().upper()
            currency = (
                str(security_summary.security.currency or "GBP").strip().upper() or "GBP"
            )
            gross_value = _q_money(_safe_decimal(security_summary.market_value_gbp))
            sellable_value = Decimal("0.00")
            sellable_qty = Decimal("0.00")
            lot_wrapper_map: dict[str, str] = {}
            price_per_share = _safe_decimal(security_summary.current_price_gbp)

            if gross_value > _ZERO:
                holdings_before_by_key[ticker] += gross_value
                currency_before[currency] += gross_value

            for lot_summary in security_summary.active_lots:
                if lot_summary.market_value_gbp is None:
                    continue
                wrapper = _wrapper_from_scheme(lot_summary.lot.scheme_type)
                wrapper_before[wrapper] += _q_money(Decimal(lot_summary.market_value_gbp))
                if not _lot_is_sellable(lot_summary):
                    continue
                sellable_value += _q_money(Decimal(lot_summary.market_value_gbp))
                sellable_qty += Decimal(lot_summary.quantity_remaining)
                lot_wrapper_map[str(lot_summary.lot.id)] = wrapper

            gross_pct = _pct(gross_value, total_before)
            target_value = _q_money(total_before * (target_max_pct / Decimal("100")))
            excess_value = _q_money(max(_ZERO, gross_value - target_value))
            actionable_trim = _q_money(min(excess_value, _q_money(sellable_value)))

            row = {
                "ticker": ticker,
                "security_id": str(security_summary.security.id),
                "currency": currency,
                "current_price_gbp": (
                    str(_q_money(price_per_share)) if price_per_share > _ZERO else None
                ),
                "gross_market_value_gbp": str(gross_value),
                "gross_pct": str(gross_pct),
                "sellable_market_value_gbp": str(_q_money(sellable_value)),
                "sellable_quantity": str(sellable_qty),
                "target_value_gbp": str(target_value),
                "excess_over_target_gbp": str(excess_value),
                "actionable_trim_gbp": str(actionable_trim),
            }
            security_rows.append(row)
            source_meta_by_ticker[ticker] = {
                "row": row,
                "currency": currency,
                "current_price_gbp": price_per_share,
                "lot_wrapper_map": lot_wrapper_map,
                "security_id": str(security_summary.security.id),
                "gross_value": gross_value,
                "sellable_value": _q_money(sellable_value),
                "sellable_qty": sellable_qty,
            }

        security_rows.sort(
            key=lambda row: (
                _safe_decimal(row.get("excess_over_target_gbp")),
                _safe_decimal(row.get("gross_market_value_gbp")),
                str(row.get("ticker") or ""),
            ),
            reverse=True,
        )

        employer_ticker = str(exposure.get("employer_ticker") or "").strip().upper()
        source_mode = str(
            planner_settings.get("source_selection_mode") or _SOURCE_MODE_AUTO
        ).upper()
        source_ticker = str(planner_settings.get("source_ticker") or "").strip().upper()

        selected_ticker = ""
        selection_reason = ""
        if source_mode == _SOURCE_MODE_TICKER and source_ticker in source_meta_by_ticker:
            selected_ticker = source_ticker
            selection_reason = "User-selected source ticker."
        elif (
            source_mode == _SOURCE_MODE_EMPLOYER
            and employer_ticker in source_meta_by_ticker
        ):
            selected_ticker = employer_ticker
            selection_reason = "Employer exposure selected explicitly."
        else:
            if employer_ticker:
                employer_row = source_meta_by_ticker.get(employer_ticker, {}).get("row")
                if employer_row and _safe_decimal(
                    employer_row.get("excess_over_target_gbp")
                ) > _ZERO:
                    selected_ticker = employer_ticker
                    selection_reason = (
                        "Employer exposure is overweight versus the planner target."
                    )
            if not selected_ticker and security_rows:
                selected_ticker = str(security_rows[0].get("ticker") or "")
                selection_reason = "Largest overweight holding by gross market value."

        selected_source = None
        trim_plan: dict[str, Any] = {
            "target_max_pct": str(_q_pct(target_max_pct)),
            "notes": [],
            "consumed_lot_rows": [],
        }
        candidate_allocations: list[dict[str, Any]] = []
        before_after: dict[str, Any] = {"fx_rows": [], "wrapper_rows": []}

        if selected_ticker and selected_ticker in source_meta_by_ticker:
            source_meta = source_meta_by_ticker[selected_ticker]
            source_gross = source_meta["gross_value"]
            source_sellable = source_meta["sellable_value"]
            price_per_share = source_meta["current_price_gbp"]
            sellable_qty = source_meta["sellable_qty"]
            selected_source = {
                **dict(source_meta["row"]),
                "selection_reason": selection_reason,
                "employer_ticker_match": selected_ticker == employer_ticker,
                "top_holding_threshold_pct": str(thresholds["top_holding_pct"]),
                "employer_threshold_pct": str(thresholds["employer_pct"]),
            }

            target_value_before = _q_money(total_before * (target_max_pct / Decimal("100")))
            excess_before = _q_money(max(_ZERO, source_gross - target_value_before))
            actionable_trim_value = _q_money(min(excess_before, source_sellable))
            whole_sellable_qty = sellable_qty.to_integral_value(rounding=ROUND_FLOOR)

            executable_qty = Decimal("0")
            if price_per_share <= _ZERO:
                trim_plan["notes"].append(
                    "Selected source has no current GBP price; trim sizing is unavailable."
                )
            else:
                executable_qty = min(
                    whole_sellable_qty,
                    (actionable_trim_value / price_per_share).to_integral_value(
                        rounding=ROUND_FLOOR
                    ),
                )
                if actionable_trim_value > _ZERO and executable_qty <= _ZERO:
                    trim_plan["notes"].append(
                        "Planner target is smaller than one whole share at the current price."
                    )

            gross_proceeds = (
                _q_money(executable_qty * price_per_share)
                if executable_qty > _ZERO
                else Decimal("0.00")
            )
            cgt_rate = _safe_decimal(stack.get("cgt_marginal_rate"))
            avg_fee_per_share = _safe_decimal(
                (stack.get("fee_model") or {}).get("avg_fee_per_share_gbp")
            )
            broker_fees = (
                _q_money(avg_fee_per_share * executable_qty)
                if executable_qty > _ZERO
                else Decimal("0.00")
            )
            employment_tax = Decimal("0.00")
            estimated_cgt = Decimal("0.00")
            net_redeployable = Decimal("0.00")
            remaining_overweight = excess_before

            if executable_qty > _ZERO:
                fifo_result = PortfolioService.simulate_disposal(
                    security_id=source_meta["security_id"],
                    quantity=executable_qty,
                    price_per_share_gbp=price_per_share,
                    as_of_date=as_of_date,
                    settings=settings,
                    broker_fees_gbp=broker_fees,
                    use_live_true_cost=False,
                )
                employment_tax = _q_money(fifo_result.total_sip_employment_tax_gbp)
                estimated_cgt = _q_money(
                    max(_ZERO, fifo_result.total_realised_gain_gbp) * cgt_rate
                )
                net_redeployable = _q_money(
                    fifo_result.total_proceeds_gbp
                    - employment_tax
                    - estimated_cgt
                    - broker_fees
                )
                remaining_overweight = _q_money(
                    max(_ZERO, source_gross - gross_proceeds)
                )
                after_total = _q_money(
                    max(_ZERO, total_before - employment_tax - estimated_cgt - broker_fees)
                )

                sold_wrapper_map: dict[str, Decimal] = defaultdict(
                    lambda: Decimal("0.00")
                )
                lot_wrapper_map = source_meta["lot_wrapper_map"]
                for alloc in fifo_result.allocations:
                    wrapper = lot_wrapper_map.get(str(alloc.lot_id), "TAXABLE")
                    sold_wrapper_map[wrapper] += _q_money(alloc.proceeds_gbp)
                    trim_plan["consumed_lot_rows"].append(
                        {
                            "lot_id": str(alloc.lot_id),
                            "acquisition_date": alloc.acquisition_date.isoformat(),
                            "quantity_allocated": str(alloc.quantity_allocated),
                            "wrapper": wrapper,
                            "proceeds_gbp": str(_q_money(alloc.proceeds_gbp)),
                            "realised_gain_gbp": str(_q_money(alloc.realised_gain_gbp)),
                            "audit_href": f"/audit?table_name=lots&record_id={alloc.lot_id}",
                        }
                    )

                total_candidate_weight = sum(
                    (
                        _safe_decimal(row.get("allocation_weight"))
                        for row in plan.get("candidates", [])
                    ),
                    Decimal("0.00"),
                )
                after_holdings_by_key = defaultdict(
                    lambda: Decimal("0.00"), holdings_before_by_key
                )
                after_currency = defaultdict(lambda: Decimal("0.00"), currency_before)
                after_wrapper = defaultdict(lambda: Decimal("0.00"), wrapper_before)

                after_holdings_by_key[selected_ticker] = _q_money(
                    max(_ZERO, after_holdings_by_key[selected_ticker] - gross_proceeds)
                )
                source_currency = str(source_meta["currency"] or "GBP")
                after_currency[source_currency] = _q_money(
                    max(_ZERO, after_currency[source_currency] - gross_proceeds)
                )
                for wrapper, value in sold_wrapper_map.items():
                    after_wrapper[wrapper] = _q_money(
                        max(_ZERO, after_wrapper[wrapper] - value)
                    )

                if total_candidate_weight > _ZERO and net_redeployable > _ZERO:
                    for candidate in plan.get("candidates", []):
                        weight = _safe_decimal(candidate.get("allocation_weight"))
                        allocated = _q_money(
                            net_redeployable * (weight / total_candidate_weight)
                        )
                        key = _candidate_key(candidate)
                        currency = str(candidate.get("currency") or "GBP").upper()
                        wrapper = str(candidate.get("target_wrapper") or "TAXABLE").upper()
                        after_holdings_by_key[key] = _q_money(
                            after_holdings_by_key[key] + allocated
                        )
                        after_currency[currency] = _q_money(
                            after_currency[currency] + allocated
                        )
                        after_wrapper[wrapper] = _q_money(
                            after_wrapper[wrapper] + allocated
                        )
                        flags: list[str] = []
                        if key != selected_ticker:
                            flags.append("Reduces single-name concentration")
                        if currency != source_currency:
                            flags.append(f"Adds {currency} exposure")
                        if wrapper == "ISA":
                            flags.append("Supports ISA wrapper fit")
                        candidate_allocations.append(
                            {
                                "candidate_id": candidate["candidate_id"],
                                "label": candidate["label"],
                                "ticker": candidate.get("ticker"),
                                "currency": currency,
                                "target_wrapper": wrapper,
                                "bucket": candidate.get("bucket"),
                                "allocation_weight": str(_q_pct(weight)),
                                "allocation_share_pct": str(_pct(allocated, net_redeployable)),
                                "allocated_gbp": str(allocated),
                                "after_portfolio_pct": str(_pct(allocated, after_total)),
                                "fit_flags": flags,
                                "notes": candidate.get("notes"),
                            }
                        )

                top_before_key, top_before_value = max(
                    holdings_before_by_key.items(),
                    key=lambda item: item[1],
                    default=("", Decimal("0.00")),
                )
                top_after_key, top_after_value = max(
                    after_holdings_by_key.items(),
                    key=lambda item: item[1],
                    default=("", Decimal("0.00")),
                )
                remaining_overweight = _q_money(
                    max(
                        _ZERO,
                        after_holdings_by_key[selected_ticker]
                        - _q_money(after_total * (target_max_pct / Decimal("100"))),
                    )
                )
                before_after = {
                    "total_holdings_before_gbp": str(total_before),
                    "total_holdings_after_gbp": str(after_total),
                    "source_pct_before": str(_pct(source_gross, total_before)),
                    "source_pct_after": str(
                        _pct(after_holdings_by_key[selected_ticker], after_total)
                    ),
                    "top_holding_before": top_before_key,
                    "top_holding_before_pct": str(_pct(top_before_value, total_before)),
                    "top_holding_after": top_after_key,
                    "top_holding_after_pct": str(_pct(top_after_value, after_total)),
                    "employer_pct_before": str(
                        _pct(
                            holdings_before_by_key.get(employer_ticker, Decimal("0.00")),
                            total_before,
                        )
                    ),
                    "employer_pct_after": str(
                        _pct(
                            after_holdings_by_key.get(employer_ticker, Decimal("0.00")),
                            after_total,
                        )
                    ),
                    "fx_rows": [
                        {
                            "currency": currency,
                            "before_gbp": str(
                                _q_money(currency_before.get(currency, Decimal("0.00")))
                            ),
                            "after_gbp": str(
                                _q_money(after_currency.get(currency, Decimal("0.00")))
                            ),
                            "before_pct": str(
                                _pct(
                                    currency_before.get(currency, Decimal("0.00")),
                                    total_before,
                                )
                            ),
                            "after_pct": str(
                                _pct(
                                    after_currency.get(currency, Decimal("0.00")),
                                    after_total,
                                )
                            ),
                        }
                        for currency in sorted(set(currency_before) | set(after_currency))
                        if _q_money(currency_before.get(currency, Decimal("0.00"))) > _ZERO
                        or _q_money(after_currency.get(currency, Decimal("0.00"))) > _ZERO
                    ],
                    "wrapper_rows": [
                        {
                            "wrapper": wrapper,
                            "before_gbp": str(
                                _q_money(wrapper_before.get(wrapper, Decimal("0.00")))
                            ),
                            "after_gbp": str(
                                _q_money(after_wrapper.get(wrapper, Decimal("0.00")))
                            ),
                            "before_pct": str(
                                _pct(
                                    wrapper_before.get(wrapper, Decimal("0.00")),
                                    total_before,
                                )
                            ),
                            "after_pct": str(
                                _pct(after_wrapper.get(wrapper, Decimal("0.00")), after_total)
                            ),
                        }
                        for wrapper in sorted(set(wrapper_before) | set(after_wrapper))
                        if _q_money(wrapper_before.get(wrapper, Decimal("0.00"))) > _ZERO
                        or _q_money(after_wrapper.get(wrapper, Decimal("0.00"))) > _ZERO
                    ],
                }
            else:
                trim_plan["notes"].append(
                    "No executable whole-share trim is available for the selected source."
                )

            trim_plan.update(
                {
                    "source_ticker": selected_ticker,
                    "source_security_id": source_meta["security_id"],
                    "source_currency": source_meta["currency"],
                    "target_value_before_gbp": str(target_value_before),
                    "excess_over_target_gbp": str(excess_before),
                    "actionable_trim_gbp": str(actionable_trim_value),
                    "sellable_market_value_gbp": str(source_sellable),
                    "sellable_quantity": str(whole_sellable_qty),
                    "price_per_share_gbp": (
                        str(_q_money(price_per_share)) if price_per_share > _ZERO else None
                    ),
                    "executable_quantity": str(executable_qty),
                    "gross_proceeds_gbp": str(gross_proceeds),
                    "employment_tax_gbp": str(employment_tax),
                    "estimated_cgt_gbp": str(estimated_cgt),
                    "broker_fees_gbp": str(broker_fees),
                    "net_redeployable_gbp": str(net_redeployable),
                    "remaining_overweight_gbp": str(remaining_overweight),
                    "trace_links": {
                        "risk": _with_as_of("/risk#concentration-guardrails", as_of_date),
                        "capital_stack": _with_as_of("/capital-stack", as_of_date),
                        "reconcile": _with_as_of(
                            "/reconcile#trace-contributing-lots", as_of_date
                        ),
                        "simulate": (
                            _with_as_of(
                                f"/simulate?security_id={source_meta['security_id']}&quantity={executable_qty}&price_per_share_gbp={_q_money(price_per_share)}",
                                as_of_date,
                            )
                            if executable_qty > _ZERO and price_per_share > _ZERO
                            else _with_as_of("/simulate", as_of_date)
                        ),
                    },
                }
            )
        else:
            trim_plan["notes"].append("No priced holdings are available for trim planning.")

        return {
            "generated_at_utc": _now_utc_iso(),
            "as_of_date": as_of_date.isoformat(),
            "planner_config": {
                "source_selection_mode": source_mode,
                "source_ticker": source_ticker,
                "target_max_pct": str(_q_pct(target_max_pct)),
            },
            "security_rows": security_rows,
            "selected_source": selected_source,
            "trim_plan": trim_plan,
            "candidate_rows": plan.get("candidates", []),
            "candidate_allocations": candidate_allocations,
            "before_after": before_after,
            "trace_links": {
                "risk": _with_as_of("/risk#concentration-guardrails", as_of_date),
                "capital_stack": _with_as_of("/capital-stack", as_of_date),
                "simulate": _with_as_of("/simulate", as_of_date),
            },
            "model_scope": {
                "inputs": [
                    "Current priced holdings, sellable lot states, CGT rate, and historical fee model",
                    "User-defined candidate universe and target wrapper/currency metadata",
                ],
                "assumptions": [
                    "Trim sizing uses whole-share execution at the current deterministic GBP price",
                    "Redeployment uses net proceeds after estimated employment tax, CGT, and broker fees",
                ],
                "exclusions": [
                    "No market-outperformance ranking or buy recommendation engine",
                    "No auto-selected replacement universe beyond user-defined candidates",
                ],
            },
            "notes": [
                "Planner outputs are non-advisory and remain traceable to current holdings, lot state, and user-defined candidate rules.",
                "Candidate allocations are capital-allocation estimates only; no future purchase quantities or execution certainty is implied.",
                "Before/after totals fall by estimated tax and fees because only net proceeds are redeployed.",
            ],
        }
