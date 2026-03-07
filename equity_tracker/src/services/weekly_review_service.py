"""WeeklyReviewService - persisted guided review workflow across core decision pages."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from ..settings import AppSettings
from .alert_service import AlertService
from .calendar_service import CalendarService
from .exposure_service import ExposureService
from .portfolio_service import PortfolioService
from .sell_plan_service import SellPlanService
from .strategic_service import StrategicService

_MONEY_Q = Decimal("0.01")
_REVIEW_STATUS_ACTIVE = "ACTIVE"
_REVIEW_STATUS_COMPLETED = "COMPLETED"
_REVIEW_STATUS_SUPERSEDED = "SUPERSEDED"
_STEP_STATUS_PENDING = "PENDING"
_STEP_STATUS_COMPLETED = "COMPLETED"
_MAX_HISTORY = 12

_STEP_DEFINITIONS: tuple[dict[str, str], ...] = (
    {
        "step_key": "portfolio",
        "label": "Portfolio",
        "description": "Confirm actionable capital, locked value, and current retained-wealth framing.",
        "href": "/",
        "action_label": "Open Portfolio",
    },
    {
        "step_key": "risk",
        "label": "Risk",
        "description": "Review concentration guardrails, employer dependence, and active alerts.",
        "href": "/risk#concentration-guardrails",
        "action_label": "Open Risk",
    },
    {
        "step_key": "calendar",
        "label": "Calendar",
        "description": "Check the next constraint changes, especially forfeiture and tax-year timing.",
        "href": "/calendar",
        "action_label": "Open Calendar",
    },
    {
        "step_key": "reconcile",
        "label": "Reconcile",
        "description": "Trace recent drift and confirm that mutations are understood before acting.",
        "href": "/reconcile#trace-drift-decomposition",
        "action_label": "Open Reconcile",
    },
)


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _safe_decimal(value: object, fallback: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return fallback


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _storage_path(db_path: Path) -> Path:
    return Path(str(db_path) + ".weekly_review.json")


def _load_json(path: Path) -> dict[str, Any]:
    fallback = {"version": 1, "active_review": None, "history": []}
    if not path.exists():
        return dict(fallback)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(fallback)
    if not isinstance(data, dict):
        return dict(fallback)
    history = data.get("history", [])
    if not isinstance(history, list):
        history = []
    active_review = data.get("active_review")
    if active_review is not None and not isinstance(active_review, dict):
        active_review = None
    return {
        "version": int(data.get("version", 1)),
        "active_review": active_review,
        "history": [dict(row) for row in history if isinstance(row, dict)],
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


def _new_step_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for step in _STEP_DEFINITIONS:
        rows.append(
            {
                "step_key": step["step_key"],
                "label": step["label"],
                "status": _STEP_STATUS_PENDING,
                "notes": "",
                "completed_at_utc": None,
            }
        )
    return rows


def _new_review(as_of_date: date) -> dict[str, Any]:
    now = _now_utc_iso()
    return {
        "review_id": uuid4().hex,
        "created_at_utc": now,
        "updated_at_utc": now,
        "review_date": date.today().isoformat(),
        "as_of_date": as_of_date.isoformat(),
        "status": _REVIEW_STATUS_ACTIVE,
        "steps": _new_step_rows(),
    }


def _normalize_review(review: dict[str, Any], *, fallback_as_of: date) -> dict[str, Any]:
    clean = dict(review)
    try:
        as_of_date = date.fromisoformat(str(clean.get("as_of_date") or fallback_as_of.isoformat()))
    except ValueError:
        as_of_date = fallback_as_of
    clean["as_of_date"] = as_of_date.isoformat()
    clean["review_date"] = str(clean.get("review_date") or date.today().isoformat())
    clean["created_at_utc"] = str(clean.get("created_at_utc") or _now_utc_iso())
    clean["updated_at_utc"] = str(clean.get("updated_at_utc") or clean["created_at_utc"])
    status = str(clean.get("status") or _REVIEW_STATUS_ACTIVE).strip().upper()
    if status not in {
        _REVIEW_STATUS_ACTIVE,
        _REVIEW_STATUS_COMPLETED,
        _REVIEW_STATUS_SUPERSEDED,
    }:
        status = _REVIEW_STATUS_ACTIVE
    clean["status"] = status

    raw_steps = clean.get("steps", [])
    by_key = {
        str(row.get("step_key") or ""): dict(row)
        for row in raw_steps
        if isinstance(raw_steps, list) and isinstance(row, dict)
    }
    steps: list[dict[str, Any]] = []
    for step in _STEP_DEFINITIONS:
        raw = by_key.get(step["step_key"], {})
        step_status = str(raw.get("status") or _STEP_STATUS_PENDING).strip().upper()
        if step_status not in {_STEP_STATUS_PENDING, _STEP_STATUS_COMPLETED}:
            step_status = _STEP_STATUS_PENDING
        steps.append(
            {
                "step_key": step["step_key"],
                "label": step["label"],
                "status": step_status,
                "notes": str(raw.get("notes") or ""),
                "completed_at_utc": raw.get("completed_at_utc"),
            }
        )
    clean["steps"] = steps
    return clean


def _progress_summary(review: dict[str, Any]) -> tuple[int, int]:
    steps = review.get("steps", [])
    total = len(steps) if isinstance(steps, list) else 0
    completed = sum(
        1
        for row in steps
        if isinstance(row, dict) and str(row.get("status") or "").upper() == _STEP_STATUS_COMPLETED
    )
    return completed, total


def _wrapper_from_scheme(scheme_type: object) -> str:
    return "ISA" if str(scheme_type or "").strip().upper() == "ISA" else "TAXABLE"


class WeeklyReviewService:
    @staticmethod
    def load_state(db_path: Path | None) -> dict[str, Any]:
        if db_path is None:
            return {"version": 1, "active_review": None, "history": []}
        payload = _load_json(_storage_path(db_path))
        fallback_as_of = date.today()
        active = payload.get("active_review")
        payload["active_review"] = (
            _normalize_review(active, fallback_as_of=fallback_as_of)
            if isinstance(active, dict)
            else None
        )
        payload["history"] = [
            _normalize_review(row, fallback_as_of=fallback_as_of)
            for row in payload.get("history", [])
            if isinstance(row, dict)
        ][: _MAX_HISTORY]
        return payload

    @staticmethod
    def save_state(db_path: Path | None, payload: dict[str, Any]) -> None:
        if db_path is None:
            raise ValueError("Database path is required.")
        _save_json(_storage_path(db_path), payload)

    @staticmethod
    def ensure_active_review(
        *,
        db_path: Path | None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        if db_path is None:
            raise ValueError("Database path is required.")
        payload = WeeklyReviewService.load_state(db_path)
        active = payload.get("active_review")
        if isinstance(active, dict):
            return active
        review = _new_review(as_of or date.today())
        payload["active_review"] = review
        WeeklyReviewService.save_state(db_path, payload)
        return review

    @staticmethod
    def start_new_review(
        *,
        db_path: Path | None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        if db_path is None:
            raise ValueError("Database path is required.")
        payload = WeeklyReviewService.load_state(db_path)
        active = payload.get("active_review")
        history = list(payload.get("history", []))
        if isinstance(active, dict):
            archived = dict(active)
            completed, total = _progress_summary(archived)
            if completed < total and archived.get("status") == _REVIEW_STATUS_ACTIVE:
                archived["status"] = _REVIEW_STATUS_SUPERSEDED
            archived["updated_at_utc"] = _now_utc_iso()
            history.insert(0, archived)
        review = _new_review(as_of or date.today())
        payload["active_review"] = review
        payload["history"] = history[:_MAX_HISTORY]
        WeeklyReviewService.save_state(db_path, payload)
        return review

    @staticmethod
    def update_step(
        *,
        db_path: Path | None,
        step_key: str,
        notes: str = "",
        completed: bool = False,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        if db_path is None:
            raise ValueError("Database path is required.")
        payload = WeeklyReviewService.load_state(db_path)
        active = payload.get("active_review")
        if not isinstance(active, dict):
            active = _new_review(as_of or date.today())
            payload["active_review"] = active
        matched = False
        for row in active.get("steps", []):
            if str(row.get("step_key") or "") != step_key:
                continue
            matched = True
            row["notes"] = (notes or "").strip()
            row["status"] = _STEP_STATUS_COMPLETED if completed else _STEP_STATUS_PENDING
            row["completed_at_utc"] = _now_utc_iso() if completed else None
            break
        if not matched:
            raise ValueError("Unknown review step.")

        done, total = _progress_summary(active)
        active["status"] = (
            _REVIEW_STATUS_COMPLETED if total > 0 and done == total else _REVIEW_STATUS_ACTIVE
        )
        active["updated_at_utc"] = _now_utc_iso()
        WeeklyReviewService.save_state(db_path, payload)
        return active

    @staticmethod
    def active_review_as_of(db_path: Path | None) -> date | None:
        payload = WeeklyReviewService.load_state(db_path)
        active = payload.get("active_review")
        if not isinstance(active, dict):
            return None
        try:
            return date.fromisoformat(str(active.get("as_of_date") or ""))
        except ValueError:
            return None

    @staticmethod
    def get_dashboard(
        *,
        settings: AppSettings | None,
        db_path: Path | None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        if db_path is None:
            return {
                "generated_at_utc": _now_utc_iso(),
                "as_of_date": (as_of or date.today()).isoformat(),
                "active_review": None,
                "recent_reviews": [],
                "step_rows": [],
                "summary": {},
                "trace_links": {},
                "model_scope": None,
                "notes": ["Database path is unavailable; review workflow cannot persist state."],
            }

        active_review = WeeklyReviewService.ensure_active_review(db_path=db_path, as_of=as_of)
        state = WeeklyReviewService.load_state(db_path)
        review_as_of = date.fromisoformat(str(active_review.get("as_of_date")))

        summary = PortfolioService.get_portfolio_summary(
            settings=settings,
            use_live_true_cost=False,
            as_of=review_as_of,
        )
        exposure = ExposureService.get_snapshot(
            settings=settings,
            db_path=db_path,
            summary=summary,
        )
        alerts = AlertService.get_alert_center(
            settings=settings,
            db_path=db_path,
            as_of=review_as_of,
        )
        sell_plan_events = SellPlanService.calendar_events(
            db_path=db_path,
            as_of=review_as_of,
            horizon_days=60,
        )
        calendar_payload = CalendarService.get_events_payload(
            settings=settings,
            as_of=review_as_of,
            horizon_days=60,
            sell_plan_events=sell_plan_events,
        )
        reconcile = StrategicService.get_cross_page_reconcile(
            settings=settings,
            db_path=db_path,
            lookback_days=30,
        )

        completed_steps, total_steps = _progress_summary(active_review)
        progress_pct = (
            int((Decimal(completed_steps) / Decimal(total_steps)) * Decimal("100"))
            if total_steps > 0
            else 0
        )

        next_event = next(iter(calendar_payload.get("events", [])), None)
        countdowns = calendar_payload.get("countdowns", {})
        next_tax_year_end = countdowns.get("next_tax_year_end", {})
        drift_panel = reconcile.get("drift_panel", {}) if isinstance(reconcile, dict) else {}

        locked_value = _q_money(_safe_decimal(exposure.get("locked_capital_gbp")))
        forfeitable_value = _q_money(_safe_decimal(exposure.get("forfeitable_capital_gbp")))
        deployable_capital = _q_money(_safe_decimal(exposure.get("deployable_capital_gbp")))
        top_holding_pct = _q_money(_safe_decimal(exposure.get("top_holding_pct_gross")))
        employer_pct = _q_money(_safe_decimal(exposure.get("employer_pct_of_gross")))
        explained_change = _q_money(_safe_decimal(drift_panel.get("explained_change_gbp")))
        reconciliation_delta = _q_money(_safe_decimal(reconcile.get("reconciliation_delta_gbp")))
        drift_rows = drift_panel.get("rows", []) if isinstance(drift_panel.get("rows"), list) else []
        mutation_count = sum(int(row.get("mutation_count") or 0) for row in drift_rows if isinstance(row, dict))

        review_step_map = {
            str(row.get("step_key") or ""): row
            for row in active_review.get("steps", [])
            if isinstance(row, dict)
        }
        step_rows: list[dict[str, Any]] = []
        for step in _STEP_DEFINITIONS:
            saved = review_step_map.get(step["step_key"], {})
            signal = ""
            if step["step_key"] == "portfolio":
                signal = (
                    f"Deployable capital GBP {deployable_capital}; "
                    f"locked + forfeitable GBP {_q_money(locked_value + forfeitable_value)}."
                )
            elif step["step_key"] == "risk":
                signal = (
                    f"Top holding {top_holding_pct}% gross; "
                    f"employer exposure {employer_pct}% gross; active alerts {alerts.get('total', 0)}."
                )
            elif step["step_key"] == "calendar":
                if next_event is not None:
                    signal = (
                        f"Next event: {next_event.get('title')} "
                        f"({next_event.get('event_date')}, {next_event.get('days_until')}d)."
                    )
                else:
                    signal = "No upcoming events in the current 60-day review horizon."
            elif step["step_key"] == "reconcile":
                signal = (
                    f"Explained drift GBP {explained_change}; "
                    f"reconcile delta GBP {reconciliation_delta}; "
                    f"mutation count {mutation_count}."
                )
            step_rows.append(
                {
                    "step_key": step["step_key"],
                    "label": step["label"],
                    "description": step["description"],
                    "status": str(saved.get("status") or _STEP_STATUS_PENDING),
                    "notes": str(saved.get("notes") or ""),
                    "completed_at_utc": saved.get("completed_at_utc"),
                    "href": _with_as_of(step["href"], review_as_of),
                    "action_label": step["action_label"],
                    "signal": signal,
                }
            )

        recent_reviews: list[dict[str, Any]] = []
        for row in state.get("history", [])[:5]:
            done, total = _progress_summary(row)
            recent_reviews.append(
                {
                    "review_id": row.get("review_id"),
                    "review_date": row.get("review_date"),
                    "as_of_date": row.get("as_of_date"),
                    "status": row.get("status"),
                    "completed_steps": done,
                    "total_steps": total,
                }
            )

        resume_context_note = None
        if as_of is not None and as_of.isoformat() != active_review["as_of_date"]:
            resume_context_note = (
                f"Active review is pinned to {active_review['as_of_date']} and resumed without reconfiguration."
            )

        return {
            "generated_at_utc": _now_utc_iso(),
            "as_of_date": review_as_of.isoformat(),
            "active_review": active_review,
            "step_rows": step_rows,
            "recent_reviews": recent_reviews,
            "summary": {
                "completed_steps": completed_steps,
                "total_steps": total_steps,
                "progress_pct": progress_pct,
                "deployable_capital_gbp": str(deployable_capital),
                "active_alert_count": int(alerts.get("total", 0)),
                "next_event_title": next_event.get("title") if next_event else None,
                "next_event_date": next_event.get("event_date") if next_event else None,
                "next_tax_year_end_date": next_tax_year_end.get("event_date"),
                "next_tax_year_end_days": next_tax_year_end.get("days_until"),
                "explained_change_gbp": str(explained_change),
                "reconciliation_delta_gbp": str(reconciliation_delta),
            },
            "trace_links": {
                "portfolio": _with_as_of("/", review_as_of),
                "risk": _with_as_of("/risk#concentration-guardrails", review_as_of),
                "calendar": _with_as_of("/calendar", review_as_of),
                "reconcile": _with_as_of("/reconcile#trace-drift-decomposition", review_as_of),
            },
            "resume_context_note": resume_context_note,
            "model_scope": {
                "inputs": [
                    "Persisted weekly review checklist state",
                    "Current deterministic outputs from Portfolio, Risk, Calendar, and Reconcile",
                ],
                "assumptions": [
                    "The active review keeps its own as-of date so the workflow can be resumed unchanged",
                    "Step summaries are high-level cues; detailed decisions still live on the underlying pages",
                ],
                "exclusions": [
                    "No recommendation engine",
                    "No auto-generated conclusions or trade suggestions",
                ],
            },
            "notes": [
                "Workflow state is persisted and can be resumed without reconfiguration.",
                "Step signals reuse current deterministic service outputs only.",
                "Review notes are user-authored context, not inferred model output.",
            ],
        }
