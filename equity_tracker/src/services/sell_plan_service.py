"""
SellPlanService - deterministic staged-disposal plan storage and calendar events.

Scope:
- Persist sell plans to a JSON sidecar file next to the active DB path.
- Support deterministic staged plans across explicit execution methods
  (calendar, threshold, limit ladder, broker algo wrapper).
- Emit calendar-ready sell-tranche events with optional filters.
- Enforce deterministic plan constraints and provide impact previews.
- Provide deterministic IBKR order staging CSV export for approved plans.
"""

from __future__ import annotations

import json
import csv
import io
from collections import defaultdict
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import uuid4

from ..core.tax_engine import calculate_cgt, get_bands, tax_year_for_date
from ..core.tax_engine.income_tax import personal_allowance
from ..settings import AppSettings
from .portfolio_service import PortfolioService
from .report_service import ReportService

_QTY_Q = Decimal("0.0001")
_MONEY_Q = Decimal("0.01")
_OVERDUE_WINDOW_DAYS = 30

PLAN_METHOD_CALENDAR_TRANCHES = "CALENDAR_TRANCHES"
PLAN_METHOD_THRESHOLD_BANDS = "THRESHOLD_BANDS"
PLAN_METHOD_LIMIT_LADDER = "LIMIT_LADDER"
PLAN_METHOD_BROKER_ALGO = "BROKER_ALGO"

BROKER_ALGO_TWAP = "TWAP"
BROKER_ALGO_VWAP = "VWAP"

PROFILE_HYBRID_DE_RISK = "HYBRID_DE_RISK"
PROFILE_CUSTOM = "CUSTOM"

APPROVAL_STATUS_DRAFT = "DRAFT"
APPROVAL_STATUS_APPROVED = "APPROVED"

TRANCHE_STATUS_PLANNED = "PLANNED"
TRANCHE_STATUS_DUE = "DUE"
TRANCHE_STATUS_EXECUTED = "EXECUTED"
TRANCHE_STATUS_CANCELLED = "CANCELLED"

_PLAN_STATUS_ACTIVE = "ACTIVE"
_PLAN_STATUS_COMPLETED = "COMPLETED"
_PLAN_STATUS_CANCELLED = "CANCELLED"

_DEFAULT_MIN_SPACING_DAYS = 1
_DEFAULT_THRESHOLD_UPPER_PCT = Decimal("70.00")
_DEFAULT_THRESHOLD_TARGET_PCT = Decimal("40.00")
_DEFAULT_THRESHOLD_REVIEW_DAYS = 7
_DEFAULT_LIMIT_STEP_GBP = Decimal("0.50")
_DEFAULT_BROKER_ALGO_WINDOW_MINUTES = 60
_DEFAULT_PROFILE_CONCENTRATION_TRIGGER_PCT = Decimal("40.00")
_DEFAULT_PROFILE_LIMIT_GUARDRAIL_DISCOUNT_PCT = Decimal("1.00")

_PLAN_METHODS = frozenset(
    {
        PLAN_METHOD_CALENDAR_TRANCHES,
        PLAN_METHOD_THRESHOLD_BANDS,
        PLAN_METHOD_LIMIT_LADDER,
        PLAN_METHOD_BROKER_ALGO,
    }
)

_BROKER_ALGOS = frozenset({BROKER_ALGO_TWAP, BROKER_ALGO_VWAP})
_PROFILE_CODES = frozenset({PROFILE_HYBRID_DE_RISK, PROFILE_CUSTOM})
_APPROVAL_STATUSES = frozenset({APPROVAL_STATUS_DRAFT, APPROVAL_STATUS_APPROVED})

_TRANCHE_STATUSES = frozenset(
    {
        TRANCHE_STATUS_PLANNED,
        TRANCHE_STATUS_EXECUTED,
        TRANCHE_STATUS_CANCELLED,
    }
)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plans_path(db_path: Path) -> Path:
    return Path(str(db_path) + ".sell_plans.json")


def _q_qty(value: Decimal) -> Decimal:
    return value.quantize(_QTY_Q, rounding=ROUND_HALF_UP)


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _floor_whole(value: Decimal) -> Decimal:
    return value.to_integral_value(rounding=ROUND_FLOOR)


def _is_whole_quantity(value: Decimal) -> bool:
    return value == _floor_whole(value)


def _qty_str(value: Decimal) -> str:
    if _is_whole_quantity(value):
        return str(int(value))
    return str(_q_qty(value))


def _safe_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _safe_decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except Exception:
        return None


def _safe_int_or_none(value: object) -> int | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def _normalized_method(value: object) -> str:
    method = str(value or PLAN_METHOD_CALENDAR_TRANCHES).strip().upper()
    if method not in _PLAN_METHODS:
        raise ValueError("Unsupported execution method.")
    return method


def _normalized_profile(value: object) -> str:
    profile = str(value or PROFILE_HYBRID_DE_RISK).strip().upper()
    if profile not in _PROFILE_CODES:
        raise ValueError("Unsupported execution profile.")
    return profile


def _normalized_approval_status(value: object) -> str:
    status = str(value or APPROVAL_STATUS_DRAFT).strip().upper()
    if status not in _APPROVAL_STATUSES:
        raise ValueError("Unsupported approval status.")
    return status


def _load_payload(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "plans": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "plans": []}

    if isinstance(data, list):
        return {"version": 1, "plans": data}
    if isinstance(data, dict):
        plans = data.get("plans", [])
        if isinstance(plans, list):
            return {"version": int(data.get("version", 1)), "plans": plans}
    return {"version": 1, "plans": []}


def _save_payload(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _split_quantity(total_quantity: Decimal, tranche_count: int) -> list[Decimal]:
    total_q = _floor_whole(total_quantity)
    if total_q <= Decimal("0"):
        raise ValueError("Total quantity must be greater than zero.")
    if not _is_whole_quantity(total_q):
        raise ValueError("Total quantity must be a whole number of shares.")
    if tranche_count < 1:
        raise ValueError("Tranche count must be at least 1.")
    if Decimal(tranche_count) > total_q:
        raise ValueError(
            "Tranche count cannot exceed total quantity when selling whole shares only."
        )

    total_int = int(total_q)
    base = total_int // tranche_count
    remainder = total_int % tranche_count

    parts = [Decimal(base + (1 if idx < remainder else 0)) for idx in range(tranche_count)]
    if sum(parts, Decimal("0")) != total_q:
        raise ValueError("Could not split total quantity deterministically.")
    if any(part <= Decimal("0") for part in parts):
        raise ValueError("Each tranche must include at least one whole share.")
    return parts


def _effective_tranche_status(*, tranche_status: str, event_date: date_type, as_of: date_type) -> str:
    status = (tranche_status or TRANCHE_STATUS_PLANNED).upper()
    if status == TRANCHE_STATUS_PLANNED and event_date <= as_of:
        return TRANCHE_STATUS_DUE
    return status


def _constraints_for_plan(plan: dict) -> dict:
    raw = plan.get("constraints", {})
    if isinstance(raw, dict):
        return raw
    return {}


def _validate_calendar_constraints(
    *,
    tranche_dates: list[date_type],
    tranche_quantities: list[Decimal],
    min_spacing_days: int,
    max_daily_quantity: Decimal | None,
    max_daily_notional_gbp: Decimal | None,
    reference_price_gbp: Decimal | None,
) -> list[str]:
    reasons: list[str] = []

    if min_spacing_days < 1:
        reasons.append("Minimum spacing must be at least 1 day.")

    for idx in range(1, len(tranche_dates)):
        gap_days = (tranche_dates[idx] - tranche_dates[idx - 1]).days
        if gap_days < min_spacing_days:
            reasons.append(
                "Minimum spacing breach: "
                f"tranche #{idx} to #{idx + 1} is {gap_days} day(s), "
                f"minimum is {min_spacing_days}."
            )

    qty_by_date: dict[date_type, Decimal] = defaultdict(lambda: Decimal("0"))
    for tranche_date, qty in zip(tranche_dates, tranche_quantities):
        qty_by_date[tranche_date] += _q_qty(qty)

    if max_daily_quantity is not None:
        for tranche_date, day_qty in sorted(qty_by_date.items(), key=lambda kv: kv[0]):
            if _q_qty(day_qty) > _q_qty(max_daily_quantity):
                reasons.append(
                    "Daily quantity cap breach: "
                    f"{tranche_date.isoformat()} quantity {_q_qty(day_qty)} > cap {_q_qty(max_daily_quantity)}."
                )

    if max_daily_notional_gbp is not None:
        if reference_price_gbp is None:
            reasons.append(
                "Max daily notional cap requires a reference price (GBP)."
            )
        else:
            for tranche_date, day_qty in sorted(qty_by_date.items(), key=lambda kv: kv[0]):
                day_notional = _q_money(_q_qty(day_qty) * reference_price_gbp)
                if day_notional > _q_money(max_daily_notional_gbp):
                    reasons.append(
                        "Daily notional cap breach: "
                        f"{tranche_date.isoformat()} notional {day_notional} > cap {_q_money(max_daily_notional_gbp)}."
                    )

    return reasons


def _in_calendar_window(*, event_date: date_type, as_of: date_type, horizon_days: int, effective_status: str) -> bool:
    if as_of <= event_date <= (as_of + timedelta(days=horizon_days)):
        return True
    if effective_status == TRANCHE_STATUS_DUE and event_date < as_of:
        return (as_of - event_date).days <= _OVERDUE_WINDOW_DAYS
    return False


def _taxable_income_ex_gains(
    *,
    tax_year: str,
    settings: AppSettings | None,
) -> Decimal:
    if settings is None:
        return Decimal("0")
    bands = get_bands(tax_year)
    adjusted_net_income = (
        settings.default_gross_income
        - settings.default_pension_sacrifice
        + settings.default_other_income
    )
    allowance = personal_allowance(bands, adjusted_net_income)
    return max(Decimal("0"), adjusted_net_income - allowance)


def _cgt_baseline_state(
    *,
    tax_year: str,
    settings: AppSettings | None,
) -> dict[str, Decimal]:
    report = ReportService.cgt_summary(tax_year)
    cgt = calculate_cgt(
        bands=get_bands(tax_year),
        realised_gains=[report.total_gains_gbp] if report.total_gains_gbp > Decimal("0") else [],
        realised_losses=[report.total_losses_gbp] if report.total_losses_gbp > Decimal("0") else [],
        taxable_income_ex_gains=_taxable_income_ex_gains(
            tax_year=tax_year,
            settings=settings,
        ),
        prior_year_losses=Decimal("0"),
    )
    return {
        "projected_total_gains_gbp": _q_money(report.total_gains_gbp),
        "projected_total_losses_gbp": _q_money(report.total_losses_gbp),
        "base_total_cgt_gbp": _q_money(cgt.total_cgt),
        "incremental_cgt_gbp": Decimal("0.00"),
    }


def _default_method_config(
    *,
    method: str,
    tranche_count: int,
    reference_price_gbp: Decimal | None,
    threshold_upper_pct: Decimal | None,
    threshold_target_pct: Decimal | None,
    threshold_review_days: int | None,
    limit_start_gbp: Decimal | None,
    limit_step_gbp: Decimal | None,
    broker_algo_name: str | None,
    broker_algo_window_minutes: int | None,
) -> dict:
    if method == PLAN_METHOD_THRESHOLD_BANDS:
        upper = threshold_upper_pct or _DEFAULT_THRESHOLD_UPPER_PCT
        target = threshold_target_pct or _DEFAULT_THRESHOLD_TARGET_PCT
        review_days = threshold_review_days or _DEFAULT_THRESHOLD_REVIEW_DAYS
        if upper <= target:
            raise ValueError("Threshold upper percentage must be greater than target percentage.")
        if review_days < 1 or review_days > 365:
            raise ValueError("Threshold review cadence must be between 1 and 365 days.")
        if upper <= Decimal("0") or target <= Decimal("0"):
            raise ValueError("Threshold percentages must be greater than zero.")
        return {
            "threshold_upper_pct": str(_q_money(upper)),
            "threshold_target_pct": str(_q_money(target)),
            "threshold_review_cadence_days": review_days,
            "formula": (
                "At each review cadence, execute next tranche if employer concentration "
                "is above target band."
            ),
        }

    if method == PLAN_METHOD_LIMIT_LADDER:
        base = limit_start_gbp if limit_start_gbp is not None else reference_price_gbp
        if base is None or base <= Decimal("0"):
            raise ValueError("Limit ladder requires a positive limit start or reference price.")
        step = limit_step_gbp or _DEFAULT_LIMIT_STEP_GBP
        if step <= Decimal("0"):
            raise ValueError("Limit ladder step must be greater than zero.")
        return {
            "limit_start_gbp": str(_q_money(base)),
            "limit_step_gbp": str(_q_money(step)),
            "formula": (
                "Per-tranche limit price = limit_start_gbp + "
                "(sequence_index * limit_step_gbp)."
            ),
        }

    if method == PLAN_METHOD_BROKER_ALGO:
        algo = str(broker_algo_name or BROKER_ALGO_TWAP).strip().upper()
        if algo not in _BROKER_ALGOS:
            raise ValueError("Broker algorithm must be TWAP or VWAP.")
        window_minutes = broker_algo_window_minutes or _DEFAULT_BROKER_ALGO_WINDOW_MINUTES
        if window_minutes < 1 or window_minutes > 1440:
            raise ValueError("Broker algorithm window must be between 1 and 1440 minutes.")
        return {
            "broker_algo": algo,
            "broker_algo_window_minutes": window_minutes,
            "formula": "Broker-native algorithm wrapper with fixed execution window.",
        }

    if method == PLAN_METHOD_CALENDAR_TRANCHES:
        return {
            "formula": "Even whole-share allocation by sequence over fixed cadence dates."
        }

    raise ValueError("Unsupported execution method.")


def _profile_payload(
    *,
    profile_code: str,
    concentration_trigger_pct: Decimal | None,
    limit_guardrail_discount_pct: Decimal | None,
) -> dict:
    trigger = concentration_trigger_pct or _DEFAULT_PROFILE_CONCENTRATION_TRIGGER_PCT
    guardrail = (
        limit_guardrail_discount_pct
        if limit_guardrail_discount_pct is not None
        else _DEFAULT_PROFILE_LIMIT_GUARDRAIL_DISCOUNT_PCT
    )
    if trigger <= Decimal("0") or trigger >= Decimal("100"):
        raise ValueError("Profile concentration trigger must be between 0 and 100.")
    if guardrail < Decimal("0") or guardrail >= Decimal("100"):
        raise ValueError("Profile limit guardrail discount must be between 0 and 100.")

    rationale = (
        "Non-advisory default profile combining calendar tranches, concentration-band "
        "review discipline, and limit-order guardrails."
    )
    if profile_code == PROFILE_CUSTOM:
        rationale = (
            "Custom profile. Keep deterministic quantity/date/guardrail parameters "
            "explicit and auditable."
        )

    return {
        "profile_code": profile_code,
        "concentration_trigger_pct": str(_q_money(trigger)),
        "limit_guardrail_discount_pct": str(_q_money(guardrail)),
        "rationale": rationale,
    }


def _enrich_tranches_with_method_fields(
    *,
    method: str,
    tranches: list[dict],
    method_config: dict,
) -> None:
    if method == PLAN_METHOD_LIMIT_LADDER:
        base = _safe_decimal(method_config.get("limit_start_gbp"))
        step = _safe_decimal(method_config.get("limit_step_gbp"))
        for idx, tranche in enumerate(tranches):
            limit_price = _q_money(base + (step * Decimal(idx)))
            tranche["limit_price_gbp"] = str(limit_price)
            tranche["order_type"] = "LMT"
    elif method == PLAN_METHOD_THRESHOLD_BANDS:
        upper = _safe_decimal(method_config.get("threshold_upper_pct"))
        target = _safe_decimal(method_config.get("threshold_target_pct"))
        if len(tranches) == 1:
            levels = [upper]
        else:
            step = (upper - target) / Decimal(max(1, len(tranches) - 1))
            levels = [upper - (step * Decimal(i)) for i in range(len(tranches))]
        for idx, tranche in enumerate(tranches):
            tranche["threshold_trigger_pct"] = str(_q_money(levels[idx]))
            tranche["order_type"] = "MKT"
    elif method == PLAN_METHOD_BROKER_ALGO:
        algo = str(method_config.get("broker_algo") or BROKER_ALGO_TWAP).upper()
        window = _safe_int_or_none(method_config.get("broker_algo_window_minutes")) or 60
        for tranche in tranches:
            tranche["broker_algo"] = algo
            tranche["broker_algo_window_minutes"] = window
            tranche["order_type"] = "MKT"
    else:
        for tranche in tranches:
            tranche["order_type"] = "MKT"


def _find_plan(
    *,
    plans: list[dict],
    plan_id: str,
) -> dict | None:
    target = (plan_id or "").strip()
    if not target:
        return None
    return next((plan for plan in plans if str(plan.get("plan_id") or "") == target), None)


def _csv_safe(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _deterministic_external_id(*, plan_id: str, sequence: int, event_date: str) -> str:
    compact = event_date.replace("-", "")
    return f"SP-{plan_id[:12]}-{sequence:03d}-{compact}"


def _normalize_plan_record(plan: dict) -> dict:
    out = dict(plan)
    method_raw = out.get("method", PLAN_METHOD_CALENDAR_TRANCHES)
    try:
        method = _normalized_method(method_raw)
    except ValueError:
        method = PLAN_METHOD_CALENDAR_TRANCHES
    out["method"] = method

    approval_raw = out.get("approval_status", APPROVAL_STATUS_DRAFT)
    try:
        approval = _normalized_approval_status(approval_raw)
    except ValueError:
        approval = APPROVAL_STATUS_DRAFT
    out["approval_status"] = approval
    if approval != APPROVAL_STATUS_APPROVED:
        out["approved_at_utc"] = None
    else:
        out["approved_at_utc"] = out.get("approved_at_utc")

    method_config = out.get("method_config")
    if not isinstance(method_config, dict):
        method_config = _default_method_config(
            method=method,
            tranche_count=int(out.get("tranche_count") or 1),
            reference_price_gbp=_safe_decimal_or_none(_constraints_for_plan(out).get("reference_price_gbp")),
            threshold_upper_pct=None,
            threshold_target_pct=None,
            threshold_review_days=None,
            limit_start_gbp=None,
            limit_step_gbp=None,
            broker_algo_name=None,
            broker_algo_window_minutes=None,
        )
    out["method_config"] = method_config

    execution_profile = out.get("execution_profile")
    if not isinstance(execution_profile, dict):
        execution_profile = _profile_payload(
            profile_code=PROFILE_HYBRID_DE_RISK,
            concentration_trigger_pct=None,
            limit_guardrail_discount_pct=None,
        )
    else:
        profile_code = execution_profile.get("profile_code", PROFILE_HYBRID_DE_RISK)
        try:
            profile_code = _normalized_profile(profile_code)
        except ValueError:
            profile_code = PROFILE_HYBRID_DE_RISK
        execution_profile = _profile_payload(
            profile_code=profile_code,
            concentration_trigger_pct=_safe_decimal_or_none(
                execution_profile.get("concentration_trigger_pct")
            ),
            limit_guardrail_discount_pct=_safe_decimal_or_none(
                execution_profile.get("limit_guardrail_discount_pct")
            ),
        )
    out["execution_profile"] = execution_profile

    tranches = out.get("tranches", [])
    if isinstance(tranches, list):
        new_tranches = [dict(t) for t in tranches if isinstance(t, dict)]
        _enrich_tranches_with_method_fields(
            method=method,
            tranches=new_tranches,
            method_config=method_config,
        )
        out["tranches"] = new_tranches
    else:
        out["tranches"] = []
    return out


class SellPlanService:
    @staticmethod
    def load_plans(db_path: Path | None) -> list[dict]:
        if db_path is None:
            return []
        payload = _load_payload(_plans_path(db_path))
        plans = payload.get("plans", [])
        if not isinstance(plans, list):
            return []
        return [_normalize_plan_record(plan) for plan in plans if isinstance(plan, dict)]

    @staticmethod
    def save_plans(db_path: Path | None, plans: list[dict]) -> None:
        if db_path is None:
            raise ValueError("Database path is required to save sell plans.")
        path = _plans_path(db_path)
        payload = {"version": 1, "plans": plans}
        _save_payload(path, payload)

    @staticmethod
    def create_calendar_tranche_plan(
        *,
        db_path: Path | None,
        security_id: str,
        ticker: str,
        total_quantity: Decimal,
        tranche_count: int,
        start_date: date_type,
        cadence_days: int,
        max_sellable_quantity: Decimal,
        max_daily_quantity: Decimal | None = None,
        max_daily_notional_gbp: Decimal | None = None,
        min_spacing_days: int = _DEFAULT_MIN_SPACING_DAYS,
        reference_price_gbp: Decimal | None = None,
        fee_per_tranche_gbp: Decimal | None = None,
    ) -> dict:
        return SellPlanService.create_plan(
            db_path=db_path,
            security_id=security_id,
            ticker=ticker,
            method=PLAN_METHOD_CALENDAR_TRANCHES,
            total_quantity=total_quantity,
            tranche_count=tranche_count,
            start_date=start_date,
            cadence_days=cadence_days,
            max_sellable_quantity=max_sellable_quantity,
            max_daily_quantity=max_daily_quantity,
            max_daily_notional_gbp=max_daily_notional_gbp,
            min_spacing_days=min_spacing_days,
            reference_price_gbp=reference_price_gbp,
            fee_per_tranche_gbp=fee_per_tranche_gbp,
        )

    @staticmethod
    def create_plan(
        *,
        db_path: Path | None,
        security_id: str,
        ticker: str,
        method: str,
        total_quantity: Decimal,
        tranche_count: int,
        start_date: date_type,
        cadence_days: int,
        max_sellable_quantity: Decimal,
        max_daily_quantity: Decimal | None = None,
        max_daily_notional_gbp: Decimal | None = None,
        min_spacing_days: int = _DEFAULT_MIN_SPACING_DAYS,
        reference_price_gbp: Decimal | None = None,
        fee_per_tranche_gbp: Decimal | None = None,
        threshold_upper_pct: Decimal | None = None,
        threshold_target_pct: Decimal | None = None,
        threshold_review_days: int | None = None,
        limit_start_gbp: Decimal | None = None,
        limit_step_gbp: Decimal | None = None,
        broker_algo_name: str | None = None,
        broker_algo_window_minutes: int | None = None,
        execution_profile: str = PROFILE_HYBRID_DE_RISK,
        profile_concentration_trigger_pct: Decimal | None = None,
        profile_limit_guardrail_discount_pct: Decimal | None = None,
    ) -> dict:
        if db_path is None:
            raise ValueError("Database path is required.")
        method_code = _normalized_method(method)
        profile_code = _normalized_profile(execution_profile)
        if cadence_days < 1:
            raise ValueError("Cadence days must be at least 1.")
        if tranche_count < 1 or tranche_count > 120:
            raise ValueError("Tranche count must be between 1 and 120.")
        if min_spacing_days < 1 or min_spacing_days > 365:
            raise ValueError("Minimum spacing must be between 1 and 365 days.")

        total_q = _safe_decimal(total_quantity)
        if total_q <= Decimal("0"):
            raise ValueError("Total quantity must be greater than zero.")
        if not _is_whole_quantity(total_q):
            raise ValueError("Total quantity must be a whole number of shares.")
        total_q = _floor_whole(total_q)

        max_q = _safe_decimal(max_sellable_quantity)
        max_whole_sellable = _floor_whole(max_q)
        if max_whole_sellable <= Decimal("0"):
            raise ValueError("No whole sellable shares are available for this security.")

        if total_q <= Decimal("0"):
            raise ValueError("Total quantity must be greater than zero.")
        if total_q > max_whole_sellable:
            raise ValueError(
                "Requested quantity "
                f"({int(total_q)}) exceeds whole-share sellable quantity "
                f"({int(max_whole_sellable)}) for this security."
            )

        max_daily_qty_cap: Decimal | None = None
        if max_daily_quantity is not None:
            if max_daily_quantity <= Decimal("0"):
                raise ValueError("Max daily quantity must be greater than zero when provided.")
            if not _is_whole_quantity(max_daily_quantity):
                raise ValueError("Max daily quantity must be a whole number of shares.")
            max_daily_qty_cap = _floor_whole(max_daily_quantity)

        if max_daily_notional_gbp is not None and max_daily_notional_gbp <= Decimal("0"):
            raise ValueError("Max daily notional (GBP) must be greater than zero when provided.")
        if reference_price_gbp is not None and reference_price_gbp <= Decimal("0"):
            raise ValueError("Reference price (GBP) must be greater than zero when provided.")
        if fee_per_tranche_gbp is not None and fee_per_tranche_gbp < Decimal("0"):
            raise ValueError("Fee per tranche (GBP) cannot be negative.")
        if Decimal(tranche_count) > total_q:
            raise ValueError(
                "Tranche count cannot exceed total quantity when selling whole shares only."
            )

        tranche_quantities = _split_quantity(total_q, tranche_count)
        tranche_dates = [
            start_date + timedelta(days=(idx * cadence_days))
            for idx in range(tranche_count)
        ]

        breaches = _validate_calendar_constraints(
            tranche_dates=tranche_dates,
            tranche_quantities=tranche_quantities,
            min_spacing_days=min_spacing_days,
            max_daily_quantity=max_daily_qty_cap,
            max_daily_notional_gbp=max_daily_notional_gbp,
            reference_price_gbp=reference_price_gbp,
        )
        if breaches:
            raise ValueError("Constraint breach: " + " | ".join(breaches))

        method_config = _default_method_config(
            method=method_code,
            tranche_count=tranche_count,
            reference_price_gbp=reference_price_gbp,
            threshold_upper_pct=threshold_upper_pct,
            threshold_target_pct=threshold_target_pct,
            threshold_review_days=threshold_review_days,
            limit_start_gbp=limit_start_gbp,
            limit_step_gbp=limit_step_gbp,
            broker_algo_name=broker_algo_name,
            broker_algo_window_minutes=broker_algo_window_minutes,
        )
        profile_payload = _profile_payload(
            profile_code=profile_code,
            concentration_trigger_pct=profile_concentration_trigger_pct,
            limit_guardrail_discount_pct=profile_limit_guardrail_discount_pct,
        )

        created_at = _now_utc_iso()
        plan_id = uuid4().hex
        tranches: list[dict] = []
        for idx, qty in enumerate(tranche_quantities):
            event_date = tranche_dates[idx]
            tranches.append(
                {
                    "tranche_id": uuid4().hex,
                    "sequence": idx + 1,
                    "event_date": event_date.isoformat(),
                    "quantity": _qty_str(qty),
                    "status": TRANCHE_STATUS_PLANNED,
                    "updated_at_utc": created_at,
                }
            )
        _enrich_tranches_with_method_fields(
            method=method_code,
            tranches=tranches,
            method_config=method_config,
        )

        plan = {
            "plan_id": plan_id,
            "created_at_utc": created_at,
            "updated_at_utc": created_at,
            "method": method_code,
            "status": _PLAN_STATUS_ACTIVE,
            "approval_status": APPROVAL_STATUS_DRAFT,
            "approved_at_utc": None,
            "security_id": security_id,
            "ticker": ticker,
            "total_quantity": _qty_str(total_q),
            "max_sellable_quantity_at_create": _qty_str(max_whole_sellable),
            "cadence_days": cadence_days,
            "tranche_count": tranche_count,
            "method_config": method_config,
            "execution_profile": profile_payload,
            "constraints": {
                "max_daily_quantity": (
                    _qty_str(max_daily_qty_cap)
                    if max_daily_qty_cap is not None
                    else None
                ),
                "max_daily_notional_gbp": (
                    str(_q_money(max_daily_notional_gbp))
                    if max_daily_notional_gbp is not None
                    else None
                ),
                "min_spacing_days": min_spacing_days,
                "reference_price_gbp": (
                    str(_q_money(reference_price_gbp))
                    if reference_price_gbp is not None
                    else None
                ),
                "fee_per_tranche_gbp": str(
                    _q_money(fee_per_tranche_gbp or Decimal("0"))
                ),
            },
            "tranches": tranches,
        }

        plans = SellPlanService.load_plans(db_path)
        plans.append(plan)
        SellPlanService.save_plans(db_path, plans)
        return plan

    @staticmethod
    def list_plans(db_path: Path | None) -> list[dict]:
        plans = SellPlanService.load_plans(db_path)
        plans.sort(key=lambda p: (p.get("created_at_utc") or "", p.get("plan_id") or ""))
        return plans

    @staticmethod
    def get_plan_by_id(
        *,
        db_path: Path | None,
        plan_id: str,
    ) -> dict | None:
        plans = SellPlanService.load_plans(db_path)
        return _find_plan(plans=plans, plan_id=plan_id)

    @staticmethod
    def set_plan_approval_status(
        *,
        db_path: Path | None,
        plan_id: str,
        approval_status: str,
    ) -> dict:
        if db_path is None:
            raise ValueError("Database path is required.")
        status = _normalized_approval_status(approval_status)

        plans = SellPlanService.load_plans(db_path)
        target_plan = _find_plan(plans=plans, plan_id=plan_id)
        if target_plan is None:
            raise ValueError("Plan not found.")
        if str(target_plan.get("status") or "").upper() == _PLAN_STATUS_CANCELLED:
            raise ValueError("Cancelled plans cannot be approved.")

        now = _now_utc_iso()
        target_plan["approval_status"] = status
        target_plan["approved_at_utc"] = now if status == APPROVAL_STATUS_APPROVED else None
        target_plan["updated_at_utc"] = now

        SellPlanService.save_plans(db_path, plans)
        return target_plan

    @staticmethod
    def export_ibkr_order_staging_csv(
        *,
        db_path: Path | None,
        plan_id: str,
        include_closed: bool = False,
    ) -> str:
        plan = SellPlanService.get_plan_by_id(db_path=db_path, plan_id=plan_id)
        if plan is None:
            raise ValueError("Plan not found.")
        if str(plan.get("approval_status") or APPROVAL_STATUS_DRAFT).upper() != APPROVAL_STATUS_APPROVED:
            raise ValueError("Plan must be approved before export.")

        method = _normalized_method(plan.get("method"))
        method_config = plan.get("method_config", {}) if isinstance(plan.get("method_config"), dict) else {}
        ticker = str(plan.get("ticker") or "")
        if not ticker:
            raise ValueError("Plan ticker is required for export.")

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "ExternalId",
                "PlanId",
                "TrancheId",
                "Security",
                "Action",
                "Quantity",
                "OrderType",
                "LimitPrice",
                "AlgoStrategy",
                "AlgoWindowMinutes",
                "GoodAfterDate",
                "TIF",
                "Method",
                "Sequence",
                "TrancheDate",
                "TrancheStatus",
                "ThresholdTriggerPct",
            ]
        )

        for tranche in plan.get("tranches", []):
            status = str(tranche.get("status") or TRANCHE_STATUS_PLANNED).upper()
            if not include_closed and status in {TRANCHE_STATUS_CANCELLED, TRANCHE_STATUS_EXECUTED}:
                continue

            sequence = int(tranche.get("sequence") or 0)
            event_date = str(tranche.get("event_date") or "")
            if sequence <= 0 or not event_date:
                continue

            order_type = str(tranche.get("order_type") or "MKT").upper()
            limit_price = ""
            if order_type == "LMT":
                limit_price = _csv_safe(tranche.get("limit_price_gbp"))

            algo_strategy = ""
            algo_window = ""
            if method == PLAN_METHOD_BROKER_ALGO:
                algo_strategy = _csv_safe(method_config.get("broker_algo") or tranche.get("broker_algo"))
                algo_window = _csv_safe(
                    method_config.get("broker_algo_window_minutes") or tranche.get("broker_algo_window_minutes")
                )

            writer.writerow(
                [
                    _deterministic_external_id(plan_id=str(plan.get("plan_id") or ""), sequence=sequence, event_date=event_date),
                    _csv_safe(plan.get("plan_id")),
                    _csv_safe(tranche.get("tranche_id")),
                    ticker,
                    "SELL",
                    _csv_safe(tranche.get("quantity")),
                    order_type,
                    limit_price,
                    algo_strategy,
                    algo_window,
                    f"{event_date} 09:30:00",
                    "DAY",
                    method,
                    sequence,
                    event_date,
                    status,
                    _csv_safe(tranche.get("threshold_trigger_pct")),
                ]
            )

        return output.getvalue()

    @staticmethod
    def delete_plan(
        *,
        db_path: Path | None,
        plan_id: str,
    ) -> bool:
        if db_path is None:
            raise ValueError("Database path is required.")
        target = (plan_id or "").strip()
        if not target:
            raise ValueError("Plan ID is required.")

        plans = SellPlanService.load_plans(db_path)
        remaining = [plan for plan in plans if str(plan.get("plan_id") or "") != target]
        removed = len(remaining) != len(plans)
        if not removed:
            return False

        SellPlanService.save_plans(db_path, remaining)
        return True

    @staticmethod
    def update_tranche_status(
        *,
        db_path: Path | None,
        plan_id: str,
        tranche_id: str,
        new_status: str,
    ) -> dict:
        if db_path is None:
            raise ValueError("Database path is required.")
        status = (new_status or "").strip().upper()
        if status not in _TRANCHE_STATUSES:
            raise ValueError("Unsupported tranche status.")

        plans = SellPlanService.load_plans(db_path)
        target_plan: dict | None = None
        target_tranche: dict | None = None

        for plan in plans:
            if plan.get("plan_id") != plan_id:
                continue
            target_plan = plan
            for tranche in plan.get("tranches", []):
                if tranche.get("tranche_id") == tranche_id:
                    target_tranche = tranche
                    break
            break

        if target_plan is None or target_tranche is None:
            raise ValueError("Plan or tranche not found.")

        now = _now_utc_iso()
        target_tranche["status"] = status
        target_tranche["updated_at_utc"] = now
        target_plan["updated_at_utc"] = now

        tranche_statuses = {
            (t.get("status") or TRANCHE_STATUS_PLANNED).upper()
            for t in target_plan.get("tranches", [])
        }
        if tranche_statuses.issubset({TRANCHE_STATUS_CANCELLED}):
            target_plan["status"] = _PLAN_STATUS_CANCELLED
        elif tranche_statuses.issubset({TRANCHE_STATUS_EXECUTED, TRANCHE_STATUS_CANCELLED}):
            target_plan["status"] = _PLAN_STATUS_COMPLETED
        else:
            target_plan["status"] = _PLAN_STATUS_ACTIVE

        SellPlanService.save_plans(db_path, plans)
        return target_plan

    @staticmethod
    def calendar_events(
        *,
        db_path: Path | None,
        as_of: date_type,
        horizon_days: int,
        sell_plan_id: str | None = None,
        sell_method: str | None = None,
        sell_status: str | None = None,
    ) -> list[dict]:
        plan_id_filter = (sell_plan_id or "").strip()
        method_filter = (sell_method or "").strip().upper()
        status_filter = (sell_status or "").strip().upper()
        events: list[dict] = []

        for plan in SellPlanService.list_plans(db_path):
            plan_id = str(plan.get("plan_id") or "")
            plan_method = str(plan.get("method") or PLAN_METHOD_CALENDAR_TRANCHES).upper()
            ticker = str(plan.get("ticker") or "")
            security_id = plan.get("security_id")

            if plan_id_filter and plan_id != plan_id_filter:
                continue
            if method_filter and plan_method != method_filter:
                continue

            for tranche in plan.get("tranches", []):
                event_date_raw = tranche.get("event_date")
                if not event_date_raw:
                    continue
                try:
                    event_date = date_type.fromisoformat(str(event_date_raw))
                except ValueError:
                    continue

                raw_status = str(tranche.get("status") or TRANCHE_STATUS_PLANNED).upper()
                effective_status = _effective_tranche_status(
                    tranche_status=raw_status,
                    event_date=event_date,
                    as_of=as_of,
                )

                if status_filter and effective_status != status_filter:
                    continue
                if not status_filter and effective_status in {
                    TRANCHE_STATUS_EXECUTED,
                    TRANCHE_STATUS_CANCELLED,
                }:
                    continue
                if not _in_calendar_window(
                    event_date=event_date,
                    as_of=as_of,
                    horizon_days=horizon_days,
                    effective_status=effective_status,
                ):
                    continue

                qty = _safe_decimal(tranche.get("quantity"))
                events.append(
                    {
                        "event_id": f"sell-plan:{plan_id}:{tranche.get('tranche_id')}",
                        "event_type": "SELL_TRANCHE",
                        "event_date": event_date.isoformat(),
                        "days_until": (event_date - as_of).days,
                        "title": f"{ticker}: Sell tranche #{tranche.get('sequence')}",
                        "subtitle": (
                            f"Method: {plan_method.replace('_', ' ').title()} | "
                            f"Status: {effective_status.title()}"
                        ),
                        "security_id": security_id,
                        "ticker": ticker,
                        "scheme_type": "SELL_PLAN",
                        "lot_id": None,
                        "quantity": _qty_str(qty),
                        "value_at_stake_gbp": None,
                        "has_live_value": False,
                        "plan_id": plan_id,
                        "plan_method": plan_method,
                        "tranche_id": tranche.get("tranche_id"),
                        "tranche_status": effective_status,
                        "deep_link": f"/sell-plan?plan_id={plan_id}#plan-{plan_id}",
                    }
                )

        return events

    @staticmethod
    def plan_with_impact_preview(
        *,
        plan: dict,
        settings: AppSettings | None,
    ) -> dict:
        tranches_in = plan.get("tranches", [])
        tranches: list[dict] = [{**tr} for tr in tranches_in if isinstance(tr, dict)]
        out = {**plan, "tranches": tranches}
        constraints = _constraints_for_plan(plan)

        reference_price = _safe_decimal_or_none(constraints.get("reference_price_gbp"))
        fee_per_tranche = _safe_decimal_or_none(constraints.get("fee_per_tranche_gbp"))
        fee = _q_money(fee_per_tranche or Decimal("0"))

        out["impact_reference_price_gbp"] = (
            str(_q_money(reference_price)) if reference_price is not None else None
        )
        out["impact_fee_per_tranche_gbp"] = str(fee)

        notes: list[str] = []
        if reference_price is None:
            notes.append(
                "Impact preview unavailable: set reference price (GBP) on plan creation."
            )
            out["impact_notes"] = notes
            return out
        if reference_price <= Decimal("0"):
            notes.append("Impact preview unavailable: reference price must be > 0.")
            out["impact_notes"] = notes
            return out

        today = date_type.today()
        per_year_state: dict[str, dict[str, Decimal]] = {}

        cumulative_qty = Decimal("0")
        prev_proceeds = Decimal("0")
        prev_emp_tax = Decimal("0")
        prev_realised_gain = Decimal("0")

        cumulative_gross = Decimal("0")
        cumulative_emp_tax = Decimal("0")
        cumulative_cgt = Decimal("0")
        cumulative_fees = Decimal("0")
        cumulative_net = Decimal("0")

        for tranche in tranches:
            qty = _safe_decimal(tranche.get("quantity"))
            status = str(tranche.get("status") or TRANCHE_STATUS_PLANNED).upper()
            tranche["impact_available"] = False

            if status == TRANCHE_STATUS_CANCELLED:
                tranche["impact_gross_proceeds_gbp"] = "0.00"
                tranche["impact_employment_tax_gbp"] = "0.00"
                tranche["impact_cgt_gbp"] = "0.00"
                tranche["impact_fees_gbp"] = "0.00"
                tranche["impact_net_cash_gbp"] = "0.00"
                tranche["impact_cumulative_net_cash_gbp"] = str(_q_money(cumulative_net))
                tranche["impact_note"] = "Cancelled tranche excluded."
                tranche["impact_available"] = True
                continue

            cumulative_qty += qty
            try:
                sim = PortfolioService.simulate_disposal(
                    security_id=str(plan.get("security_id") or ""),
                    quantity=_q_qty(cumulative_qty),
                    price_per_share_gbp=reference_price,
                    as_of_date=today,
                    settings=settings,
                    use_live_true_cost=False,
                )
            except ValueError as exc:
                tranche["impact_note"] = f"Impact preview unavailable: {exc}"
                notes.append(f"Tranche #{tranche.get('sequence')}: {exc}")
                continue

            gross = _q_money(sim.total_proceeds_gbp - prev_proceeds)
            emp_tax = _q_money(sim.total_sip_employment_tax_gbp - prev_emp_tax)
            realised_gain = _q_money(sim.total_realised_gain_gbp - prev_realised_gain)

            prev_proceeds = sim.total_proceeds_gbp
            prev_emp_tax = sim.total_sip_employment_tax_gbp
            prev_realised_gain = sim.total_realised_gain_gbp

            event_date_raw = tranche.get("event_date")
            event_date = today
            if event_date_raw:
                try:
                    event_date = date_type.fromisoformat(str(event_date_raw))
                except ValueError:
                    event_date = today
            tax_year = tax_year_for_date(event_date)

            if tax_year not in per_year_state:
                per_year_state[tax_year] = _cgt_baseline_state(
                    tax_year=tax_year,
                    settings=settings,
                )

            year_state = per_year_state[tax_year]
            if realised_gain >= Decimal("0"):
                year_state["projected_total_gains_gbp"] = _q_money(
                    year_state["projected_total_gains_gbp"] + realised_gain
                )
            else:
                year_state["projected_total_losses_gbp"] = _q_money(
                    year_state["projected_total_losses_gbp"] + abs(realised_gain)
                )

            cgt_projection = calculate_cgt(
                bands=get_bands(tax_year),
                realised_gains=(
                    [year_state["projected_total_gains_gbp"]]
                    if year_state["projected_total_gains_gbp"] > Decimal("0")
                    else []
                ),
                realised_losses=(
                    [year_state["projected_total_losses_gbp"]]
                    if year_state["projected_total_losses_gbp"] > Decimal("0")
                    else []
                ),
                taxable_income_ex_gains=_taxable_income_ex_gains(
                    tax_year=tax_year,
                    settings=settings,
                ),
                prior_year_losses=Decimal("0"),
            )
            projected_incremental_cgt = _q_money(
                cgt_projection.total_cgt - year_state["base_total_cgt_gbp"]
            )
            tranche_cgt = _q_money(
                projected_incremental_cgt - year_state["incremental_cgt_gbp"]
            )
            year_state["incremental_cgt_gbp"] = projected_incremental_cgt

            tranche_fee = fee
            net = _q_money(gross - emp_tax - tranche_cgt - tranche_fee)

            cumulative_gross = _q_money(cumulative_gross + gross)
            cumulative_emp_tax = _q_money(cumulative_emp_tax + emp_tax)
            cumulative_cgt = _q_money(cumulative_cgt + tranche_cgt)
            cumulative_fees = _q_money(cumulative_fees + tranche_fee)
            cumulative_net = _q_money(cumulative_net + net)

            tranche["impact_gross_proceeds_gbp"] = str(gross)
            tranche["impact_employment_tax_gbp"] = str(emp_tax)
            tranche["impact_cgt_gbp"] = str(tranche_cgt)
            tranche["impact_fees_gbp"] = str(tranche_fee)
            tranche["impact_net_cash_gbp"] = str(net)
            tranche["impact_cumulative_net_cash_gbp"] = str(cumulative_net)
            tranche["impact_note"] = None
            tranche["impact_available"] = True

        out["impact_totals"] = {
            "gross_proceeds_gbp": str(_q_money(cumulative_gross)),
            "employment_tax_gbp": str(_q_money(cumulative_emp_tax)),
            "cgt_gbp": str(_q_money(cumulative_cgt)),
            "fees_gbp": str(_q_money(cumulative_fees)),
            "net_cash_gbp": str(_q_money(cumulative_net)),
        }
        out["impact_notes"] = notes
        return out
