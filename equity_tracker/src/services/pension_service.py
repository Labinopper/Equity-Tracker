"""PensionService - deterministic pension ledger and projection surface."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..settings import AppSettings
from .capital_stack_service import CapitalStackService
from .portfolio_service import PortfolioService

_MONEY_Q = Decimal("0.01")
_RATE_Q = Decimal("0.01")
_ZERO = Decimal("0")

ENTRY_TYPE_EMPLOYEE = "EMPLOYEE"
ENTRY_TYPE_EMPLOYER = "EMPLOYER"
ENTRY_TYPE_ADJUSTMENT = "ADJUSTMENT"
ENTRY_TYPE_GROWTH = "GROWTH"
VALID_ENTRY_TYPES = frozenset(
    {ENTRY_TYPE_EMPLOYEE, ENTRY_TYPE_EMPLOYER, ENTRY_TYPE_ADJUSTMENT, ENTRY_TYPE_GROWTH}
)


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _q_rate(value: Decimal) -> Decimal:
    return value.quantize(_RATE_Q, rounding=ROUND_HALF_UP)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_decimal(value: object, fallback: Decimal = _ZERO) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return fallback


def _ledger_path(db_path: Path) -> Path:
    return Path(str(db_path) + ".pension_ledger.json")


def _assumptions_path(db_path: Path) -> Path:
    return Path(str(db_path) + ".pension_plan.json")


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        # Handle Feb 29 by clamping to Feb 28.
        return value.replace(month=2, day=28, year=value.year + years)


def _add_months(value: date, months: int) -> date:
    month_index = (value.month - 1) + months
    year = value.year + (month_index // 12)
    month = (month_index % 12) + 1
    day = min(
        value.day,
        (
            31,
            29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        )[month - 1],
    )
    return date(year, month, day)


def _months_between(start: date, end: date) -> int:
    if end <= start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def _load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(fallback)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(fallback)
    return data if isinstance(data, dict) else dict(fallback)


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _entry_sort_key(entry: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(entry.get("entry_date") or ""),
        str(entry.get("created_at_utc") or ""),
        str(entry.get("entry_id") or ""),
    )


def _default_assumptions(today: date | None = None) -> dict[str, str]:
    now = today or date.today()
    return {
        "current_pension_value_gbp": "0.00",
        "last_valuation_date": "",
        "monthly_employee_contribution_gbp": "0.00",
        "monthly_employer_contribution_gbp": "0.00",
        "retirement_date": _add_years(now, 20).isoformat(),
        "target_annual_income_gbp": "40000.00",
        "target_withdrawal_rate_pct": "4.00",
        "conservative_annual_return_pct": "3.00",
        "base_annual_return_pct": "5.00",
        "aggressive_annual_return_pct": "7.00",
    }


def _normalize_entry_type(value: object) -> str:
    entry_type = str(value or "").strip().upper()
    if entry_type not in VALID_ENTRY_TYPES:
        raise ValueError("Contribution type must be EMPLOYEE, EMPLOYER, ADJUSTMENT, or GROWTH.")
    return entry_type


def _projection_value(
    *,
    current_pot_gbp: Decimal,
    monthly_total_contribution_gbp: Decimal,
    annual_return_pct: Decimal,
    months: int,
) -> Decimal:
    pot = _q_money(current_pot_gbp)
    if months <= 0:
        return pot
    monthly_rate = annual_return_pct / Decimal("100") / Decimal("12")
    monthly_contribution = _q_money(monthly_total_contribution_gbp)
    for _ in range(months):
        pot = _q_money((pot * (Decimal("1") + monthly_rate)) + monthly_contribution)
    return pot


def _progress_pct(current: Decimal, target: Decimal | None) -> Decimal | None:
    if target is None or target <= _ZERO:
        return None
    return _q_rate((current / target) * Decimal("100"))


class PensionService:
    @staticmethod
    def load_entries(db_path: Path | None) -> list[dict[str, Any]]:
        if db_path is None:
            return []
        payload = _load_json(_ledger_path(db_path), {"version": 1, "entries": []})
        raw_entries = payload.get("entries", [])
        if not isinstance(raw_entries, list):
            return []
        entries = [dict(entry) for entry in raw_entries if isinstance(entry, dict)]
        entries.sort(key=_entry_sort_key)
        return entries

    @staticmethod
    def record_entry(
        *,
        db_path: Path | None,
        entry_date: date,
        entry_type: str,
        amount_gbp: Decimal,
        source: str = "manual",
        notes: str | None = None,
    ) -> dict[str, Any]:
        if db_path is None:
            raise ValueError("Database path is required.")

        normalized_type = _normalize_entry_type(entry_type)
        amount = _q_money(amount_gbp)
        if normalized_type in {ENTRY_TYPE_EMPLOYEE, ENTRY_TYPE_EMPLOYER} and amount <= _ZERO:
            raise ValueError("Employee and employer contributions must be greater than zero.")
        if normalized_type == ENTRY_TYPE_ADJUSTMENT and amount == _ZERO:
            raise ValueError("Adjustment amount must be non-zero.")
        if normalized_type == ENTRY_TYPE_GROWTH and amount == _ZERO:
            raise ValueError("Growth amount must be non-zero.")

        entries = PensionService.load_entries(db_path)
        entry = {
            "entry_id": uuid4().hex,
            "entry_date": entry_date.isoformat(),
            "entry_type": normalized_type,
            "amount_gbp": str(amount),
            "source": str(source or "manual").strip() or "manual",
            "notes": (notes or "").strip() or None,
            "created_at_utc": _now_utc_iso(),
        }
        entries.append(entry)
        entries.sort(key=_entry_sort_key)
        _save_json(_ledger_path(db_path), {"version": 1, "entries": entries})
        return entry

    @staticmethod
    def load_assumptions(db_path: Path | None) -> dict[str, str]:
        defaults = _default_assumptions()
        if db_path is None:
            return defaults
        raw = _load_json(_assumptions_path(db_path), defaults)
        clean = dict(defaults)
        for key in defaults:
            if key in raw and raw[key] is not None:
                clean[key] = str(raw[key])
        return clean

    @staticmethod
    def save_assumptions(
        *,
        db_path: Path | None,
        current_pension_value_gbp: str,
        monthly_employee_contribution_gbp: str,
        monthly_employer_contribution_gbp: str,
        retirement_date: str,
        target_annual_income_gbp: str,
        target_withdrawal_rate_pct: str,
        conservative_annual_return_pct: str,
        base_annual_return_pct: str,
        aggressive_annual_return_pct: str,
    ) -> dict[str, str]:
        if db_path is None:
            raise ValueError("Database path is required.")

        try:
            retirement = date.fromisoformat(str(retirement_date))
        except ValueError as exc:
            raise ValueError("Retirement date must use YYYY-MM-DD.") from exc

        values = {
            "current_pension_value_gbp": _q_money(_safe_decimal(current_pension_value_gbp)),
            "monthly_employee_contribution_gbp": _q_money(_safe_decimal(monthly_employee_contribution_gbp)),
            "monthly_employer_contribution_gbp": _q_money(_safe_decimal(monthly_employer_contribution_gbp)),
            "target_annual_income_gbp": _q_money(_safe_decimal(target_annual_income_gbp)),
            "target_withdrawal_rate_pct": _q_rate(_safe_decimal(target_withdrawal_rate_pct)),
            "conservative_annual_return_pct": _q_rate(_safe_decimal(conservative_annual_return_pct)),
            "base_annual_return_pct": _q_rate(_safe_decimal(base_annual_return_pct)),
            "aggressive_annual_return_pct": _q_rate(_safe_decimal(aggressive_annual_return_pct)),
        }

        if values["current_pension_value_gbp"] < _ZERO:
            raise ValueError("Current pension value cannot be negative.")
        if values["monthly_employee_contribution_gbp"] < _ZERO:
            raise ValueError("Monthly employee contribution cannot be negative.")
        if values["monthly_employer_contribution_gbp"] < _ZERO:
            raise ValueError("Monthly employer contribution cannot be negative.")
        if values["target_annual_income_gbp"] < _ZERO:
            raise ValueError("Target annual income cannot be negative.")
        if values["target_withdrawal_rate_pct"] <= _ZERO:
            raise ValueError("Withdrawal rate must be greater than zero.")
        if values["conservative_annual_return_pct"] < _ZERO:
            raise ValueError("Conservative return cannot be negative.")
        if values["base_annual_return_pct"] < values["conservative_annual_return_pct"]:
            raise ValueError("Base return must be at least conservative return.")
        if values["aggressive_annual_return_pct"] < values["base_annual_return_pct"]:
            raise ValueError("Aggressive return must be at least base return.")

        existing = PensionService.load_assumptions(db_path)
        payload = {
            "current_pension_value_gbp": str(values["current_pension_value_gbp"]),
            "last_valuation_date": str(existing.get("last_valuation_date") or "").strip(),
            "monthly_employee_contribution_gbp": str(values["monthly_employee_contribution_gbp"]),
            "monthly_employer_contribution_gbp": str(values["monthly_employer_contribution_gbp"]),
            "retirement_date": retirement.isoformat(),
            "target_annual_income_gbp": str(values["target_annual_income_gbp"]),
            "target_withdrawal_rate_pct": str(values["target_withdrawal_rate_pct"]),
            "conservative_annual_return_pct": str(values["conservative_annual_return_pct"]),
            "base_annual_return_pct": str(values["base_annual_return_pct"]),
            "aggressive_annual_return_pct": str(values["aggressive_annual_return_pct"]),
        }
        _save_json(_assumptions_path(db_path), payload)
        return payload

    @staticmethod
    def validate_current_value(
        *,
        db_path: Path | None,
        valuation_date: date,
        current_value_gbp: Decimal,
        source: str = "manual-valuation",
        notes: str | None = None,
    ) -> dict[str, Any]:
        if db_path is None:
            raise ValueError("Database path is required.")

        value = _q_money(current_value_gbp)
        if value < _ZERO:
            raise ValueError("Current pension value cannot be negative.")

        assumptions = PensionService.load_assumptions(db_path)
        entries = PensionService.load_entries(db_path)

        prior_value = _q_money(_safe_decimal(assumptions.get("current_pension_value_gbp")))
        prior_date_text = str(assumptions.get("last_valuation_date") or "").strip()
        prior_date = date.fromisoformat(prior_date_text) if prior_date_text else None
        if prior_date is not None and valuation_date < prior_date:
            raise ValueError("Valuation date cannot be earlier than the last validation date.")

        contributions_since_last_validation = Decimal("0.00")
        if prior_date is not None:
            for entry in entries:
                entry_type = _normalize_entry_type(entry.get("entry_type"))
                if entry_type not in {
                    ENTRY_TYPE_EMPLOYEE,
                    ENTRY_TYPE_EMPLOYER,
                    ENTRY_TYPE_ADJUSTMENT,
                }:
                    continue
                try:
                    entry_date = date.fromisoformat(str(entry.get("entry_date") or ""))
                except ValueError:
                    continue
                if prior_date < entry_date <= valuation_date:
                    contributions_since_last_validation += _safe_decimal(entry.get("amount_gbp"))

        growth_entry = None
        growth_amount = Decimal("0.00")
        if prior_date is not None:
            growth_amount = _q_money(value - prior_value - contributions_since_last_validation)
            if growth_amount != _ZERO:
                note_parts = []
                if notes:
                    note_parts.append(str(notes).strip())
                note_parts.append(
                    "Derived from valuation delta net of contributions since last validation."
                )
                growth_entry = PensionService.record_entry(
                    db_path=db_path,
                    entry_date=valuation_date,
                    entry_type=ENTRY_TYPE_GROWTH,
                    amount_gbp=growth_amount,
                    source=str(source or "manual-valuation").strip() or "manual-valuation",
                    notes=" ".join(part for part in note_parts if part).strip(),
                )

        assumptions["current_pension_value_gbp"] = str(value)
        assumptions["last_valuation_date"] = valuation_date.isoformat()
        _save_json(_assumptions_path(db_path), assumptions)
        return {
            "valuation_date": valuation_date.isoformat(),
            "current_pension_value_gbp": str(value),
            "prior_value_gbp": str(prior_value),
            "prior_valuation_date": prior_date.isoformat() if prior_date is not None else None,
            "contributions_since_last_validation_gbp": str(_q_money(contributions_since_last_validation)),
            "growth_recorded_gbp": str(growth_amount),
            "growth_entry": growth_entry,
        }

    @staticmethod
    def get_dashboard(
        *,
        settings: AppSettings | None,
        db_path: Path | None,
    ) -> dict[str, Any]:
        today = date.today()
        assumptions = PensionService.load_assumptions(db_path)
        entries = PensionService.load_entries(db_path)

        totals_by_type: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        for entry in entries:
            totals_by_type[_normalize_entry_type(entry.get("entry_type"))] += _safe_decimal(
                entry.get("amount_gbp")
            )

        employee_total = _q_money(totals_by_type[ENTRY_TYPE_EMPLOYEE])
        employer_total = _q_money(totals_by_type[ENTRY_TYPE_EMPLOYER])
        adjustment_total = _q_money(totals_by_type[ENTRY_TYPE_ADJUSTMENT])
        growth_total = _q_money(totals_by_type[ENTRY_TYPE_GROWTH])
        recorded_inputs = _q_money(employee_total + employer_total + adjustment_total)

        current_value_assumption = _q_money(
            _safe_decimal(assumptions.get("current_pension_value_gbp"))
        )
        current_value = current_value_assumption
        notes: list[str] = [
            "Projections use fixed monthly contributions and fixed annual return assumptions only.",
            "No market forecast, volatility simulation, or advisory recommendation is applied.",
        ]
        if current_value <= _ZERO and recorded_inputs > _ZERO:
            current_value = recorded_inputs
            notes.append(
                "Current pension value was not set; current pot defaults to recorded inputs so growth attribution is neutral."
            )

        growth_attribution = _q_money(current_value - recorded_inputs)

        retirement = date.fromisoformat(assumptions["retirement_date"])
        months_to_retirement = _months_between(today, retirement)
        years_to_retirement = (
            Decimal(months_to_retirement) / Decimal("12")
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

        target_annual_income = _q_money(
            _safe_decimal(assumptions.get("target_annual_income_gbp"))
        )
        withdrawal_rate_pct = _q_rate(
            _safe_decimal(assumptions.get("target_withdrawal_rate_pct"), Decimal("4.00"))
        )
        target_pot = (
            _q_money(target_annual_income / (withdrawal_rate_pct / Decimal("100")))
            if withdrawal_rate_pct > _ZERO
            else None
        )

        monthly_employee = _q_money(
            _safe_decimal(assumptions.get("monthly_employee_contribution_gbp"))
        )
        monthly_employer = _q_money(
            _safe_decimal(assumptions.get("monthly_employer_contribution_gbp"))
        )
        monthly_total = _q_money(monthly_employee + monthly_employer)

        return_rates = {
            "conservative": _q_rate(
                _safe_decimal(assumptions.get("conservative_annual_return_pct"), Decimal("3.00"))
            ),
            "base": _q_rate(
                _safe_decimal(assumptions.get("base_annual_return_pct"), Decimal("5.00"))
            ),
            "aggressive": _q_rate(
                _safe_decimal(assumptions.get("aggressive_annual_return_pct"), Decimal("7.00"))
            ),
        }

        timeline_specs = [
            ("Now", today, 0),
            ("5y", _add_months(today, 60), 60),
            ("10y", _add_months(today, 120), 120),
            ("Retirement", retirement, months_to_retirement),
        ]

        scenario_rows: list[dict[str, Any]] = []
        for label, horizon_date, months in timeline_specs:
            row = {
                "label": label,
                "target_date": horizon_date.isoformat(),
                "months_from_now": months,
                "future_employee_contributions_gbp": str(_q_money(monthly_employee * months)),
                "future_employer_contributions_gbp": str(_q_money(monthly_employer * months)),
                "future_total_contributions_gbp": str(_q_money(monthly_total * months)),
            }
            for scenario_name, annual_return_pct in return_rates.items():
                projected_pot = _projection_value(
                    current_pot_gbp=current_value,
                    monthly_total_contribution_gbp=monthly_total,
                    annual_return_pct=annual_return_pct,
                    months=months,
                )
                row[f"{scenario_name}_annual_return_pct"] = str(annual_return_pct)
                row[f"{scenario_name}_projected_pot_gbp"] = str(projected_pot)
                if target_pot is not None:
                    row[f"{scenario_name}_shortfall_vs_target_gbp"] = str(
                        _q_money(target_pot - projected_pot)
                    )
                else:
                    row[f"{scenario_name}_shortfall_vs_target_gbp"] = None
            scenario_rows.append(row)

        cumulative_inputs = Decimal("0.00")
        cumulative_growth = Decimal("0.00")
        chart_points: dict[str, dict[str, Any]] = {}

        def ensure_point(point_date: date) -> dict[str, Any]:
            key = point_date.isoformat()
            existing = chart_points.get(key)
            if existing is not None:
                return existing
            point = {
                "date": key,
                "label": key,
                "is_future": point_date > today,
                "cumulative_inputs_gbp": None,
                "cumulative_growth_gbp": None,
                "actual_pot_gbp": None,
                "conservative_projected_pot_gbp": None,
                "base_projected_pot_gbp": None,
                "aggressive_projected_pot_gbp": None,
                "target_pot_gbp": str(target_pot) if target_pot is not None else None,
                "progress_to_target_pct": None,
            }
            chart_points[key] = point
            return point

        for entry in entries:
            try:
                point_date = date.fromisoformat(str(entry.get("entry_date") or ""))
            except ValueError:
                continue
            point = ensure_point(point_date)
            entry_type = _normalize_entry_type(entry.get("entry_type"))
            amount = _q_money(_safe_decimal(entry.get("amount_gbp")))
            if entry_type == ENTRY_TYPE_GROWTH:
                cumulative_growth = _q_money(cumulative_growth + amount)
            else:
                cumulative_inputs = _q_money(cumulative_inputs + amount)
            point["cumulative_inputs_gbp"] = str(cumulative_inputs)
            point["cumulative_growth_gbp"] = str(cumulative_growth)

        valuation_date_text = str(assumptions.get("last_valuation_date") or "").strip()
        if valuation_date_text:
            valuation_point = ensure_point(date.fromisoformat(valuation_date_text))
            valuation_point["actual_pot_gbp"] = str(current_value)
            valuation_point["progress_to_target_pct"] = (
                str(_progress_pct(current_value, target_pot))
                if _progress_pct(current_value, target_pot) is not None
                else None
            )
        else:
            today_point = ensure_point(today)
            today_point["actual_pot_gbp"] = str(current_value)
            today_point["progress_to_target_pct"] = (
                str(_progress_pct(current_value, target_pot))
                if _progress_pct(current_value, target_pot) is not None
                else None
            )
            today_point["cumulative_inputs_gbp"] = today_point["cumulative_inputs_gbp"] or str(recorded_inputs)
            today_point["cumulative_growth_gbp"] = today_point["cumulative_growth_gbp"] or str(growth_total)

        now_point = ensure_point(today)
        now_point["conservative_projected_pot_gbp"] = str(current_value)
        now_point["base_projected_pot_gbp"] = str(current_value)
        now_point["aggressive_projected_pot_gbp"] = str(current_value)
        now_point["progress_to_target_pct"] = (
            str(_progress_pct(current_value, target_pot))
            if _progress_pct(current_value, target_pot) is not None
            else now_point["progress_to_target_pct"]
        )

        for row in scenario_rows[1:]:
            point = ensure_point(date.fromisoformat(row["target_date"]))
            point["label"] = row["label"]
            point["conservative_projected_pot_gbp"] = row["conservative_projected_pot_gbp"]
            point["base_projected_pot_gbp"] = row["base_projected_pot_gbp"]
            point["aggressive_projected_pot_gbp"] = row["aggressive_projected_pot_gbp"]
            projected_base = _q_money(_safe_decimal(row["base_projected_pot_gbp"]))
            progress = _progress_pct(projected_base, target_pot)
            point["progress_to_target_pct"] = str(progress) if progress is not None else None

        ordered_chart_points = [
            chart_points[key]
            for key in sorted(chart_points.keys())
        ]

        chart_summary = {
            "current_progress_to_target_pct": (
                str(_progress_pct(current_value, target_pot))
                if _progress_pct(current_value, target_pot) is not None
                else None
            ),
            "retirement_base_progress_to_target_pct": None,
            "retirement_base_shortfall_vs_target_gbp": None,
        }
        if scenario_rows:
            retirement_row = scenario_rows[-1]
            retirement_base_pot = _q_money(_safe_decimal(retirement_row["base_projected_pot_gbp"]))
            retirement_progress = _progress_pct(retirement_base_pot, target_pot)
            chart_summary["retirement_base_progress_to_target_pct"] = (
                str(retirement_progress) if retirement_progress is not None else None
            )
            chart_summary["retirement_base_shortfall_vs_target_gbp"] = retirement_row[
                "base_shortfall_vs_target_gbp"
            ]

        total_tracked_wealth = current_value
        portfolio_gross = Decimal("0.00")
        deployable_cash = Decimal("0.00")
        if db_path is not None:
            portfolio = PortfolioService.get_portfolio_summary(
                settings=settings,
                use_live_true_cost=False,
            )
            stack = CapitalStackService.get_snapshot(
                settings=settings,
                db_path=db_path,
                summary=portfolio,
            )
            portfolio_gross = _q_money(_safe_decimal(stack.get("gross_market_value_gbp")))
            deployable_cash = _q_money(_safe_decimal(stack.get("gbp_deployable_cash_gbp")))
            total_tracked_wealth = _q_money(current_value + portfolio_gross + deployable_cash)

        pension_share_of_tracked_wealth_pct = (
            _q_rate((current_value / total_tracked_wealth) * Decimal("100"))
            if total_tracked_wealth > _ZERO
            else Decimal("0.00")
        )

        ledger_rows = []
        for entry in reversed(entries):
            ledger_rows.append(
                {
                    "entry_id": entry["entry_id"],
                    "entry_date": str(entry["entry_date"]),
                    "entry_type": str(entry["entry_type"]),
                    "amount_gbp": str(_q_money(_safe_decimal(entry["amount_gbp"]))),
                    "source": str(entry.get("source") or ""),
                    "notes": entry.get("notes"),
                    "created_at_utc": str(entry.get("created_at_utc") or ""),
                    "trace_href": f"/pension#entry-{entry['entry_id']}",
                }
            )

        return {
            "generated_at_utc": _now_utc_iso(),
            "as_of_date": today.isoformat(),
            "current_pension_value_gbp": str(current_value),
            "current_pension_value_is_assumed": current_value_assumption > _ZERO,
            "last_valuation_date": str(assumptions.get("last_valuation_date") or ""),
            "recorded_inputs_gbp": str(recorded_inputs),
            "employee_contributions_gbp": str(employee_total),
            "employer_contributions_gbp": str(employer_total),
            "adjustments_gbp": str(adjustment_total),
            "recorded_growth_gbp": str(growth_total),
            "growth_attribution_gbp": str(growth_attribution),
            "monthly_employee_contribution_gbp": str(monthly_employee),
            "monthly_employer_contribution_gbp": str(monthly_employer),
            "monthly_total_contribution_gbp": str(monthly_total),
            "retirement_date": retirement.isoformat(),
            "years_to_retirement": str(years_to_retirement),
            "target_annual_income_gbp": str(target_annual_income),
            "target_withdrawal_rate_pct": str(withdrawal_rate_pct),
            "target_pot_gbp": str(target_pot) if target_pot is not None else None,
            "portfolio_gross_market_value_gbp": str(portfolio_gross),
            "deployable_cash_gbp": str(deployable_cash),
            "total_tracked_wealth_gbp": str(total_tracked_wealth),
            "pension_share_of_tracked_wealth_pct": str(pension_share_of_tracked_wealth_pct),
            "assumptions": assumptions,
            "scenario_rows": scenario_rows,
            "timeline_chart": {
                "points": ordered_chart_points,
                "summary": chart_summary,
            },
            "ledger_rows": ledger_rows,
            "trace_links": {
                "ledger": "/pension#pension-ledger",
                "assumptions": "/pension#pension-assumptions",
                "valuation": "/pension#pension-validation",
                "wealth_context": "/capital-stack",
            },
            "model_scope": {
                "inputs": [
                    "Append-only pension contribution ledger",
                    "Current pension value assumption and fixed monthly contribution schedule",
                    "Portfolio gross value and deployable cash for tracked-wealth context",
                ],
                "assumptions": [
                    "Projection scenarios use fixed annual returns with monthly compounding",
                    "Target pot uses annual drawdown divided by configured withdrawal rate",
                ],
                "exclusions": [
                    "No market forecast or stochastic simulation",
                    "No pension tax-relief optimization advice",
                ],
            },
            "notes": notes,
        }
