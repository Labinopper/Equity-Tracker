"""
SellPlanService - deterministic staged-disposal plan storage and calendar events.

Scope (Stage 2 baseline):
- Persist sell plans to a JSON sidecar file next to the active DB path.
- Support deterministic calendar-tranche plans only (no market prediction).
- Emit calendar-ready sell-tranche events with optional filters.
"""

from __future__ import annotations

import json
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import uuid4

_QTY_Q = Decimal("0.0001")
_OVERDUE_WINDOW_DAYS = 30

PLAN_METHOD_CALENDAR_TRANCHES = "CALENDAR_TRANCHES"
TRANCHE_STATUS_PLANNED = "PLANNED"
TRANCHE_STATUS_DUE = "DUE"
TRANCHE_STATUS_EXECUTED = "EXECUTED"
TRANCHE_STATUS_CANCELLED = "CANCELLED"
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


def _safe_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


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
    total_q = _q_qty(total_quantity)
    if total_q <= Decimal("0"):
        raise ValueError("Total quantity must be greater than zero.")
    if tranche_count < 1:
        raise ValueError("Tranche count must be at least 1.")

    base = (total_q / Decimal(tranche_count)).quantize(_QTY_Q, rounding=ROUND_DOWN)
    remainder = total_q - (base * tranche_count)
    units = int((remainder / _QTY_Q).to_integral_value(rounding=ROUND_HALF_UP))

    parts = [base for _ in range(tranche_count)]
    for idx in range(units):
        parts[idx] += _QTY_Q

    if _q_qty(sum(parts, Decimal("0"))) != total_q:
        raise ValueError("Could not split total quantity deterministically.")
    return parts


def _effective_tranche_status(*, tranche_status: str, event_date: date_type, as_of: date_type) -> str:
    status = (tranche_status or TRANCHE_STATUS_PLANNED).upper()
    if status == TRANCHE_STATUS_PLANNED and event_date <= as_of:
        return TRANCHE_STATUS_DUE
    return status


def _in_calendar_window(*, event_date: date_type, as_of: date_type, horizon_days: int, effective_status: str) -> bool:
    if as_of <= event_date <= (as_of + timedelta(days=horizon_days)):
        return True
    if effective_status == TRANCHE_STATUS_DUE and event_date < as_of:
        return (as_of - event_date).days <= _OVERDUE_WINDOW_DAYS
    return False


class SellPlanService:
    @staticmethod
    def load_plans(db_path: Path | None) -> list[dict]:
        if db_path is None:
            return []
        payload = _load_payload(_plans_path(db_path))
        plans = payload.get("plans", [])
        return plans if isinstance(plans, list) else []

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
    ) -> dict:
        if db_path is None:
            raise ValueError("Database path is required.")
        if cadence_days < 1:
            raise ValueError("Cadence days must be at least 1.")
        if tranche_count < 1 or tranche_count > 120:
            raise ValueError("Tranche count must be between 1 and 120.")

        total_q = _q_qty(total_quantity)
        max_q = _q_qty(max_sellable_quantity)
        if total_q <= Decimal("0"):
            raise ValueError("Total quantity must be greater than zero.")
        if total_q > max_q:
            raise ValueError(
                f"Requested quantity ({total_q}) exceeds sellable quantity ({max_q}) for this security."
            )

        tranche_quantities = _split_quantity(total_q, tranche_count)
        created_at = _now_utc_iso()
        plan_id = uuid4().hex
        tranches: list[dict] = []
        for idx, qty in enumerate(tranche_quantities):
            event_date = start_date + timedelta(days=(idx * cadence_days))
            tranches.append(
                {
                    "tranche_id": uuid4().hex,
                    "sequence": idx + 1,
                    "event_date": event_date.isoformat(),
                    "quantity": str(_q_qty(qty)),
                    "status": TRANCHE_STATUS_PLANNED,
                    "updated_at_utc": created_at,
                }
            )

        plan = {
            "plan_id": plan_id,
            "created_at_utc": created_at,
            "updated_at_utc": created_at,
            "method": PLAN_METHOD_CALENDAR_TRANCHES,
            "status": "ACTIVE",
            "security_id": security_id,
            "ticker": ticker,
            "total_quantity": str(total_q),
            "max_sellable_quantity_at_create": str(max_q),
            "cadence_days": cadence_days,
            "tranche_count": tranche_count,
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
            target_plan["status"] = "CANCELLED"
        elif tranche_statuses.issubset({TRANCHE_STATUS_EXECUTED, TRANCHE_STATUS_CANCELLED}):
            target_plan["status"] = "COMPLETED"
        else:
            target_plan["status"] = "ACTIVE"

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
                        "quantity": str(_q_qty(qty)),
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
