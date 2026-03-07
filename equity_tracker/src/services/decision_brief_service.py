"""Decision-brief export service for major decision surfaces."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from ..core.tax_engine import tax_year_for_date
from ..settings import AppSettings
from .alert_service import AlertService
from .capital_stack_service import CapitalStackService
from .exposure_service import ExposureService
from .portfolio_service import PortfolioService
from .risk_service import RiskService
from .strategic_service import StrategicService
from .tax_plan_service import TaxPlanService

_MONEY_Q = Decimal("0.01")
_SURFACE_ORDER = (
    "portfolio",
    "net_value",
    "capital_stack",
    "tax_plan",
    "risk",
)
_SURFACE_LABELS = {
    "portfolio": "Portfolio",
    "net_value": "Net Value",
    "capital_stack": "Capital Stack",
    "tax_plan": "Tax Plan",
    "risk": "Risk",
}
_TAX_PLAN_QUERY_KEYS = (
    "gross_income_gbp",
    "bonus_gbp",
    "sell_amount_gbp",
    "additional_pension_sacrifice_gbp",
)


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _money_str(value: object) -> str | None:
    if value is None:
        return None
    try:
        return str(_q_money(Decimal(str(value))))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _to_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _with_as_of(path: str, as_of: date | None) -> str:
    if as_of is None:
        return path
    suffix = f"as_of={as_of.isoformat()}"
    if "#" in path:
        base, anchor = path.split("#", 1)
        join = "&" if "?" in base else "?"
        return f"{base}{join}{suffix}#{anchor}"
    join = "&" if "?" in path else "?"
    return f"{path}{join}{suffix}"


def _selected_surfaces(surfaces: list[str] | None) -> list[str]:
    if not surfaces:
        return list(_SURFACE_ORDER)
    normalized = []
    seen: set[str] = set()
    for value in surfaces:
        key = str(value or "").strip().lower()
        if key not in _SURFACE_LABELS:
            raise ValueError(f"Unsupported decision-brief surface: {value!r}")
        if key in seen:
            continue
        normalized.append(key)
        seen.add(key)
    return normalized


def _valuation_basis(summary) -> dict[str, Any]:
    securities = list(summary.securities or [])
    total_security_count = len(securities)
    price_dates = [ss.price_as_of for ss in securities if ss.price_as_of is not None]
    fx_required = [
        ss
        for ss in securities
        if str(getattr(ss.security, "currency", "") or "").upper() != "GBP"
    ]
    fx_rows = [str(ss.fx_as_of) for ss in fx_required if ss.fx_as_of]

    return {
        "total_security_count": total_security_count,
        "price_tracked_count": len(price_dates),
        "price_as_of_latest": max(price_dates).isoformat() if price_dates else None,
        "price_as_of_earliest": min(price_dates).isoformat() if price_dates else None,
        "price_dates_mixed": len(set(price_dates)) > 1,
        "stale_price_count": sum(
            1 for ss in securities if ss.price_as_of is not None and ss.price_is_stale
        ),
        "missing_price_count": total_security_count - len(price_dates),
        "fx_required_count": len(fx_required),
        "fx_as_of_count": len(fx_rows),
        "fx_as_of_latest": max(fx_rows) if fx_rows else None,
        "fx_as_of_earliest": min(fx_rows) if fx_rows else None,
        "fx_dates_mixed": len(set(fx_rows)) > 1,
        "stale_fx_count": sum(1 for ss in fx_required if ss.fx_as_of and ss.fx_is_stale),
        "missing_fx_count": len(fx_required) - len(fx_rows),
        "fx_basis_note": (
            "GBP-only holdings (no FX conversion)"
            if len(fx_required) <= 0
            else None
        ),
    }


def _settings_snapshot(settings: AppSettings | None, *, as_of: date) -> dict[str, Any]:
    return {
        "default_tax_year": (
            settings.default_tax_year if settings and settings.default_tax_year else tax_year_for_date(as_of)
        ),
        "default_gross_income_gbp": _money_str(
            settings.default_gross_income if settings else Decimal("0")
        ),
        "default_pension_sacrifice_gbp": _money_str(
            settings.default_pension_sacrifice if settings else Decimal("0")
        ),
        "default_other_income_gbp": _money_str(
            settings.default_other_income if settings else Decimal("0")
        ),
        "default_student_loan_plan": (
            settings.default_student_loan_plan if settings else None
        ),
        "employer_ticker": (settings.employer_ticker or "").strip().upper() if settings else "",
        "concentration_top_holding_alert_pct": _money_str(
            settings.concentration_top_holding_alert_pct if settings else Decimal("0")
        ),
        "concentration_employer_alert_pct": _money_str(
            settings.concentration_employer_alert_pct if settings else Decimal("0")
        ),
        "price_stale_after_days": settings.price_stale_after_days if settings else 1,
        "fx_stale_after_minutes": settings.fx_stale_after_minutes if settings else 10,
        "hide_values": bool(settings.hide_values) if settings else False,
    }


def _portfolio_brief(*, summary, exposure: dict[str, Any], as_of: date) -> dict[str, Any]:
    return {
        "label": _SURFACE_LABELS["portfolio"],
        "metrics": {
            "gross_market_value_gbp": _money_str(summary.total_market_value_gbp),
            "estimated_net_liquidation_gbp": _money_str(summary.est_total_net_liquidation_gbp),
            "estimated_employment_tax_gbp": _money_str(summary.est_total_employment_tax_gbp),
            "sellable_market_value_gbp": _money_str(exposure.get("total_sellable_market_value_gbp")),
            "deployable_capital_gbp": _money_str(exposure.get("deployable_capital_gbp")),
            "locked_capital_gbp": _money_str(exposure.get("locked_capital_gbp")),
            "forfeitable_capital_gbp": _money_str(exposure.get("forfeitable_capital_gbp")),
            "top_holding_pct_gross": str(exposure.get("top_holding_pct_gross")),
            "top_holding_pct_sellable": str(exposure.get("top_holding_pct_sellable")),
            "employer_pct_of_gross": str(exposure.get("employer_pct_of_gross")),
        },
        "valuation_basis": _valuation_basis(summary),
        "trace_links": {
            "surface": _with_as_of("/", as_of),
            "reconcile_contributing_lots": _with_as_of("/reconcile#trace-contributing-lots", as_of),
            "reconcile_audit_mutations": _with_as_of("/reconcile#trace-audit-mutations", as_of),
            "data_quality": _with_as_of("/data-quality", as_of),
        },
    }


def _net_value_brief(*, summary, exposure: dict[str, Any], as_of: date) -> dict[str, Any]:
    return {
        "label": _SURFACE_LABELS["net_value"],
        "metrics": {
            "gross_market_value_gbp": _money_str(summary.total_market_value_gbp),
            "estimated_employment_tax_gbp": _money_str(summary.est_total_employment_tax_gbp),
            "hypothetical_net_value_gbp": _money_str(summary.est_total_net_liquidation_gbp),
            "locked_capital_gbp": _money_str(exposure.get("locked_capital_gbp")),
            "forfeitable_capital_gbp": _money_str(exposure.get("forfeitable_capital_gbp")),
        },
        "valuation_basis": {
            "fx_as_of": summary.fx_as_of,
            "fx_is_stale": bool(summary.fx_is_stale),
            "fx_conversion_basis": summary.fx_conversion_basis,
        },
        "trace_links": {
            "surface": _with_as_of("/net-value", as_of),
            "reconcile_contributing_lots": _with_as_of("/reconcile#trace-contributing-lots", as_of),
            "reconcile_audit_mutations": _with_as_of("/reconcile#trace-audit-mutations", as_of),
            "capital_stack": _with_as_of("/capital-stack", as_of),
        },
    }


def _capital_stack_brief(*, stack: dict[str, Any], as_of: date) -> dict[str, Any]:
    return {
        "label": _SURFACE_LABELS["capital_stack"],
        "metrics": {
            "gross_market_value_gbp": _money_str(stack.get("gross_market_value_gbp")),
            "locked_capital_gbp": _money_str(stack.get("locked_capital_gbp")),
            "forfeitable_capital_gbp": _money_str(stack.get("forfeitable_capital_gbp")),
            "hypothetical_liquid_gbp": _money_str(stack.get("hypothetical_liquid_gbp")),
            "estimated_employment_tax_gbp": _money_str(stack.get("estimated_employment_tax_gbp")),
            "estimated_cgt_gbp": _money_str(stack.get("estimated_cgt_gbp")),
            "estimated_fees_gbp": _money_str(stack.get("estimated_fees_gbp")),
            "net_deployable_today_gbp": _money_str(stack.get("net_deployable_today_gbp")),
            "gbp_deployable_cash_gbp": _money_str(stack.get("gbp_deployable_cash_gbp")),
            "combined_deployable_with_cash_gbp": _money_str(stack.get("combined_deployable_with_cash_gbp")),
            "dividend_adjusted_capital_at_risk_gbp": _money_str(
                stack.get("dividend_adjusted_capital_at_risk_gbp")
            ),
        },
        "trace_links": {
            "surface": _with_as_of("/capital-stack", as_of),
            "reconcile": _with_as_of("/reconcile", as_of),
            "tax_plan": _with_as_of("/tax-plan", as_of),
            "data_quality": _with_as_of("/data-quality", as_of),
        },
    }


def _tax_plan_brief(*, tax_plan: dict[str, Any], as_of: date) -> dict[str, Any]:
    summary = tax_plan.get("summary") or {}
    realised = summary.get("realised_to_date") or {}
    cross_year = summary.get("cross_year_comparison") or {}
    sell_now = cross_year.get("sell_before_tax_year_end") or {}
    sell_after = cross_year.get("sell_after_tax_year_rollover") or {}
    scope = cross_year.get("additional_realisation_scope") or {}
    assumptions = tax_plan.get("assumptions") or {}

    return {
        "label": _SURFACE_LABELS["tax_plan"],
        "metrics": {
            "active_tax_year": tax_plan.get("active_tax_year"),
            "net_gain_to_date_gbp": realised.get("net_gain_gbp"),
            "remaining_aea_gbp": realised.get("remaining_aea_gbp"),
            "realised_total_cgt_gbp": realised.get("total_cgt_gbp"),
            "projected_sellable_lot_count": scope.get("sellable_projected_lot_count"),
            "projected_net_gain_gbp": scope.get("projected_net_gain_gbp"),
            "sell_now_incremental_cgt_gbp": sell_now.get("projected_incremental_cgt_gbp"),
            "sell_after_incremental_cgt_gbp": sell_after.get("projected_incremental_cgt_gbp"),
            "incremental_cgt_difference_if_wait_gbp": cross_year.get(
                "incremental_cgt_difference_if_wait_gbp"
            ),
        },
        "assumption_quality": {
            "cross_year": cross_year.get("assumption_quality"),
            "input_freshness": assumptions.get("input_freshness"),
        },
        "trace_links": {
            "surface": _with_as_of("/tax-plan", as_of),
            "reconcile_contributing_lots": _with_as_of("/reconcile#trace-contributing-lots", as_of),
            "reconcile_audit_mutations": _with_as_of("/reconcile#trace-audit-mutations", as_of),
            "settings": "/settings",
        },
    }


def _risk_brief(*, risk_summary, alert_center: dict[str, Any], as_of: date) -> dict[str, Any]:
    return {
        "label": _SURFACE_LABELS["risk"],
        "metrics": {
            "total_market_value_gbp": _money_str(risk_summary.total_market_value_gbp),
            "top_holding_pct_gross": str(risk_summary.top_holding_pct),
            "top_holding_pct_sellable": str(risk_summary.top_holding_sellable_pct),
            "sellable_gbp": _money_str(risk_summary.liquidity.sellable_gbp),
            "locked_gbp": _money_str(risk_summary.liquidity.locked_gbp),
            "at_risk_gbp": _money_str(risk_summary.liquidity.at_risk_gbp),
            "deployable_capital_gbp": _money_str(risk_summary.deployable.deployable_capital_gbp),
            "employer_dependence_ratio_pct": str(risk_summary.employer_dependence.ratio_pct),
            "optionality_index_score": str(risk_summary.optionality_index.score),
            "active_alert_count": alert_center.get("total", 0),
        },
        "valuation_basis": {
            "price_as_of_latest": risk_summary.valuation_basis.price_as_of_latest,
            "price_as_of_earliest": risk_summary.valuation_basis.price_as_of_earliest,
            "stale_price_count": risk_summary.valuation_basis.stale_price_count,
            "fx_as_of_latest": risk_summary.valuation_basis.fx_as_of_latest,
            "stale_fx_count": risk_summary.valuation_basis.stale_fx_count,
            "fx_basis_note": risk_summary.valuation_basis.fx_basis_note,
        },
        "trace_links": {
            "surface": _with_as_of("/risk", as_of),
            "alert_center": _with_as_of("/risk#alert-center", as_of),
            "concentration_guardrails": _with_as_of("/risk#concentration-guardrails", as_of),
            "calendar": _with_as_of("/calendar", as_of),
            "data_quality": _with_as_of("/data-quality", as_of),
        },
    }


class DecisionBriefExportService:
    @staticmethod
    def supported_surfaces() -> tuple[str, ...]:
        return _SURFACE_ORDER

    @staticmethod
    def get_export(
        *,
        settings: AppSettings | None,
        db_path,
        db_encrypted: bool,
        as_of: date | None = None,
        surfaces: list[str] | None = None,
        tax_plan_overrides: dict[str, Decimal | None] | None = None,
    ) -> dict[str, Any]:
        as_of_date = as_of or date.today()
        selected_surfaces = _selected_surfaces(surfaces)
        overrides = tax_plan_overrides or {}

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

        stack = None
        if "capital_stack" in selected_surfaces:
            stack = CapitalStackService.get_snapshot(
                settings=settings,
                db_path=db_path,
                summary=summary,
                as_of=as_of_date,
            )

        tax_plan = None
        if "tax_plan" in selected_surfaces:
            tax_plan = TaxPlanService.get_summary(
                settings=settings,
                as_of=as_of_date,
                compensation_gross_income_gbp=overrides.get("gross_income_gbp"),
                compensation_bonus_gbp=overrides.get("bonus_gbp"),
                compensation_sell_amount_gbp=overrides.get("sell_amount_gbp"),
                compensation_additional_pension_sacrifice_gbp=overrides.get(
                    "additional_pension_sacrifice_gbp"
                ),
            )

        risk_summary = None
        alert_center = None
        if "risk" in selected_surfaces:
            risk_summary = RiskService.get_risk_summary(
                settings=settings,
                db_path=db_path,
                as_of=as_of_date,
            )
            alert_center = AlertService.get_alert_center(
                settings=settings,
                db_path=db_path,
                as_of=as_of_date,
            )

        surface_payloads: dict[str, Any] = {}
        for surface in selected_surfaces:
            if surface == "portfolio":
                surface_payloads[surface] = _portfolio_brief(
                    summary=summary,
                    exposure=exposure,
                    as_of=as_of_date,
                )
            elif surface == "net_value":
                surface_payloads[surface] = _net_value_brief(
                    summary=summary,
                    exposure=exposure,
                    as_of=as_of_date,
                )
            elif surface == "capital_stack":
                assert stack is not None
                surface_payloads[surface] = _capital_stack_brief(
                    stack=stack,
                    as_of=as_of_date,
                )
            elif surface == "tax_plan":
                assert tax_plan is not None
                surface_payloads[surface] = _tax_plan_brief(
                    tax_plan=tax_plan,
                    as_of=as_of_date,
                )
            elif surface == "risk":
                assert risk_summary is not None
                assert alert_center is not None
                surface_payloads[surface] = _risk_brief(
                    risk_summary=risk_summary,
                    alert_center=alert_center,
                    as_of=as_of_date,
                )

        model_scope = {
            surface: StrategicService.model_scope(surface)
            for surface in selected_surfaces
        }

        return {
            "metadata": {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "export_type": "decision_brief_v1",
                "as_of_date": as_of_date.isoformat(),
                "db_encrypted": db_encrypted,
                "selected_surfaces": selected_surfaces,
                "surface_labels": {
                    surface: _SURFACE_LABELS[surface] for surface in selected_surfaces
                },
                "valuation_currency": getattr(summary, "valuation_currency", "GBP"),
            },
            "assumptions": {
                "settings_snapshot": _settings_snapshot(settings, as_of=as_of_date),
                "surface_model_scope": model_scope,
                "tax_plan_query_context": {
                    key: _money_str(overrides.get(key)) for key in _TAX_PLAN_QUERY_KEYS
                },
            },
            "surfaces": surface_payloads,
            "notes": [
                "Decision brief exports selected headline metrics only; use page and trace links for drill-down.",
                "All figures are deterministic from persisted holdings, stored prices/FX, and the captured settings snapshot.",
            ],
        }
