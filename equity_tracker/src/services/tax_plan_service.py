"""
TaxPlanService - additive tax-year realization planner payloads.

Scope:
- Remaining annual exempt amount (AEA) and current-year realized position.
- Per-lot projected gain and incremental CGT if sold now.
- Cross-year comparison (sell in current tax year vs next tax year).

No write operations are performed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from ..app_context import AppContext
from ..core.tax_engine import TaxContext, calculate_cgt, get_bands, get_marginal_rates, tax_year_for_date
from ..core.tax_engine.income_tax import (
    income_tax_on_additional_income,
    income_tax_on_context,
    personal_allowance,
)
from ..core.tax_engine.national_insurance import ni_liability, ni_on_additional_income
from ..core.tax_engine.student_loan import sl_on_additional_income, student_loan_repayment
from ..db.repository import EmploymentTaxEventRepository
from ..settings import AppSettings
from .portfolio_service import PortfolioService
from .report_service import ReportService

_GBP_Q = Decimal("0.01")
_ZERO = Decimal("0")

_SCHEME_LABELS: dict[str, str] = {
    "RSU": "RSU",
    "ESPP": "ESPP",
    "ESPP_PLUS": "ESPP+",
    "SIP_PARTNERSHIP": "SIP Partnership",
    "SIP_MATCHING": "SIP Matching",
    "SIP_DIVIDEND": "SIP Dividend",
    "BROKERAGE": "Brokerage",
    "ISA": "ISA",
}

_ASSUMPTION_QUALITY_EXACT = "Exact"
_ASSUMPTION_QUALITY_WEIGHTED = "Weighted Estimate"
_ASSUMPTION_QUALITY_UNAVAILABLE = "Unavailable"


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_GBP_Q, rounding=ROUND_HALF_UP)


def _money_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(_q_money(value))


def _assumption_quality(label: str, reason: str | None = None) -> dict[str, str | None]:
    return {
        "label": label,
        "reason": reason,
    }


def _to_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _tax_year_start_end(tax_year: str) -> tuple[date, date]:
    start_year = int(tax_year.split("-", 1)[0])
    return date(start_year, 4, 6), date(start_year + 1, 4, 5)


def _next_tax_year(tax_year: str) -> str:
    start_year = int(tax_year.split("-", 1)[0])
    next_start = start_year + 1
    return f"{next_start}-{str(next_start + 1)[2:]}"


def _taxable_income_ex_gains_for_values(
    *,
    tax_year: str,
    gross_income_gbp: Decimal,
    pension_sacrifice_gbp: Decimal,
    other_income_gbp: Decimal,
) -> Decimal:
    bands = get_bands(tax_year)
    adjusted_net_income = gross_income_gbp - pension_sacrifice_gbp + other_income_gbp
    pa = personal_allowance(bands, adjusted_net_income)
    return max(_ZERO, adjusted_net_income - pa)


def _taxable_income_ex_gains(
    *,
    settings: AppSettings | None,
    tax_year: str,
) -> Decimal:
    if settings is None:
        return _ZERO
    return _taxable_income_ex_gains_for_values(
        tax_year=tax_year,
        gross_income_gbp=settings.default_gross_income,
        pension_sacrifice_gbp=settings.default_pension_sacrifice,
        other_income_gbp=settings.default_other_income,
    )


@dataclass(frozen=True)
class _CgtBaseline:
    tax_year: str
    realised_gains_gbp: Decimal
    realised_losses_gbp: Decimal
    net_gain_gbp: Decimal
    taxable_income_ex_gains: Decimal
    annual_exempt_amount_gbp: Decimal
    remaining_aea_gbp: Decimal
    taxable_gain_gbp: Decimal
    total_cgt_gbp: Decimal
    disposal_count: int


@dataclass(frozen=True)
class _CompensationInputs:
    gross_income_gbp: Decimal
    bonus_gbp: Decimal
    sell_amount_gbp: Decimal
    additional_pension_sacrifice_gbp: Decimal


def _calculate_cgt_totals(
    *,
    tax_year: str,
    taxable_income_ex_gains: Decimal,
    gains_gbp: Decimal,
    losses_gbp: Decimal,
) -> Any:
    bands = get_bands(tax_year)
    gains = [gains_gbp] if gains_gbp > Decimal("0") else []
    losses = [losses_gbp] if losses_gbp > Decimal("0") else []
    return calculate_cgt(
        bands=bands,
        realised_gains=gains,
        realised_losses=losses,
        taxable_income_ex_gains=taxable_income_ex_gains,
        prior_year_losses=Decimal("0"),
    )


def _baseline_for_year(
    *,
    tax_year: str,
    settings: AppSettings | None,
    gross_income_gbp: Decimal | None = None,
    pension_sacrifice_gbp: Decimal | None = None,
    other_income_gbp: Decimal | None = None,
) -> _CgtBaseline:
    report = ReportService.cgt_summary(tax_year)
    if (
        gross_income_gbp is not None
        or pension_sacrifice_gbp is not None
        or other_income_gbp is not None
    ):
        gross_income = (
            gross_income_gbp
            if gross_income_gbp is not None
            else (settings.default_gross_income if settings else _ZERO)
        )
        pension_sacrifice = (
            pension_sacrifice_gbp
            if pension_sacrifice_gbp is not None
            else (settings.default_pension_sacrifice if settings else _ZERO)
        )
        other_income = (
            other_income_gbp
            if other_income_gbp is not None
            else (settings.default_other_income if settings else _ZERO)
        )
        taxable_income = _taxable_income_ex_gains_for_values(
            tax_year=tax_year,
            gross_income_gbp=gross_income,
            pension_sacrifice_gbp=pension_sacrifice,
            other_income_gbp=other_income,
        )
    else:
        taxable_income = _taxable_income_ex_gains(settings=settings, tax_year=tax_year)
    cgt_result = _calculate_cgt_totals(
        tax_year=tax_year,
        taxable_income_ex_gains=taxable_income,
        gains_gbp=report.total_gains_gbp,
        losses_gbp=report.total_losses_gbp,
    )
    annual_exempt = get_bands(tax_year).cgt_annual_exempt_amount
    remaining_aea = max(annual_exempt - max(report.net_gain_gbp, _ZERO), _ZERO)
    return _CgtBaseline(
        tax_year=tax_year,
        realised_gains_gbp=_q_money(report.total_gains_gbp),
        realised_losses_gbp=_q_money(report.total_losses_gbp),
        net_gain_gbp=_q_money(report.net_gain_gbp),
        taxable_income_ex_gains=_q_money(taxable_income),
        annual_exempt_amount_gbp=_q_money(annual_exempt),
        remaining_aea_gbp=_q_money(remaining_aea),
        taxable_gain_gbp=_q_money(cgt_result.taxable_gain),
        total_cgt_gbp=_q_money(cgt_result.total_cgt),
        disposal_count=len(report.disposal_lines),
    )


def _project_with_additional_realisation(
    *,
    baseline: _CgtBaseline,
    additional_gains_gbp: Decimal,
    additional_losses_gbp: Decimal,
) -> dict[str, Decimal]:
    total_gains = baseline.realised_gains_gbp + max(additional_gains_gbp, Decimal("0"))
    total_losses = baseline.realised_losses_gbp + max(additional_losses_gbp, Decimal("0"))
    result = _calculate_cgt_totals(
        tax_year=baseline.tax_year,
        taxable_income_ex_gains=baseline.taxable_income_ex_gains,
        gains_gbp=total_gains,
        losses_gbp=total_losses,
    )

    annual_exempt = baseline.annual_exempt_amount_gbp
    projected_net_gain = total_gains - total_losses
    remaining_aea = max(annual_exempt - max(projected_net_gain, Decimal("0")), Decimal("0"))
    projected_total_cgt = _q_money(result.total_cgt)
    incremental_cgt = _q_money(projected_total_cgt - baseline.total_cgt_gbp)

    return {
        "projected_total_gains_gbp": _q_money(total_gains),
        "projected_total_losses_gbp": _q_money(total_losses),
        "projected_net_gain_gbp": _q_money(projected_net_gain),
        "projected_remaining_aea_gbp": _q_money(remaining_aea),
        "projected_taxable_gain_gbp": _q_money(result.taxable_gain),
        "projected_total_cgt_gbp": projected_total_cgt,
        "projected_incremental_cgt_gbp": incremental_cgt,
    }


def _clamp_non_negative(value: Decimal | None, fallback: Decimal = _ZERO) -> Decimal:
    if value is None:
        return fallback
    return max(value, _ZERO)


def _employment_tax_liability_breakdown(
    *,
    tax_year: str,
    gross_income_gbp: Decimal,
    pension_sacrifice_gbp: Decimal,
    other_income_gbp: Decimal,
    student_loan_plan: int | None,
) -> dict[str, Decimal]:
    bands = get_bands(tax_year)
    adjusted_net_income = gross_income_gbp - pension_sacrifice_gbp + other_income_gbp
    ni_relevant_income = gross_income_gbp - pension_sacrifice_gbp
    sl_relevant_income = ni_relevant_income + other_income_gbp
    income_tax = _q_money(
        income_tax_on_context(
            bands=bands,
            gross_income=gross_income_gbp,
            adjusted_net_income=adjusted_net_income,
        )
    )
    ni = _q_money(ni_liability(bands, ni_relevant_income))
    sl = _q_money(student_loan_repayment(bands, sl_relevant_income, student_loan_plan))
    return {
        "income_tax_gbp": income_tax,
        "national_insurance_gbp": ni,
        "student_loan_gbp": sl,
        "total_gbp": _q_money(income_tax + ni + sl),
    }


def _bonus_tax_breakdown(
    *,
    tax_year: str,
    current_gross_income_gbp: Decimal,
    pension_sacrifice_gbp: Decimal,
    other_income_gbp: Decimal,
    student_loan_plan: int | None,
    bonus_gbp: Decimal,
) -> dict[str, Decimal]:
    if bonus_gbp <= _ZERO:
        return {
            "income_tax_gbp": _ZERO,
            "national_insurance_gbp": _ZERO,
            "student_loan_gbp": _ZERO,
            "total_gbp": _ZERO,
        }

    bands = get_bands(tax_year)
    adjusted_net_income = (
        current_gross_income_gbp - pension_sacrifice_gbp + other_income_gbp
    )
    ni_relevant_income = current_gross_income_gbp - pension_sacrifice_gbp
    sl_relevant_income = ni_relevant_income + other_income_gbp

    income_tax = _q_money(
        income_tax_on_additional_income(
            bands=bands,
            current_gross_income=current_gross_income_gbp,
            additional_income=bonus_gbp,
            current_ani=adjusted_net_income,
        )
    )
    ni = _q_money(
        ni_on_additional_income(
            bands=bands,
            current_ni_income=ni_relevant_income,
            additional_income=bonus_gbp,
        )
    )
    sl = _q_money(
        sl_on_additional_income(
            bands=bands,
            current_sl_income=sl_relevant_income,
            additional_income=bonus_gbp,
            plan=student_loan_plan,
        )
    )
    return {
        "income_tax_gbp": income_tax,
        "national_insurance_gbp": ni,
        "student_loan_gbp": sl,
        "total_gbp": _q_money(income_tax + ni + sl),
    }


def _estimate_sale_components(
    *,
    sell_amount_gbp: Decimal,
    sellable_market_value_pool_gbp: Decimal,
    sellable_cost_basis_pool_gbp: Decimal,
) -> dict[str, Decimal | str]:
    sell_amount = _clamp_non_negative(sell_amount_gbp)
    market_pool = max(sellable_market_value_pool_gbp, _ZERO)
    cost_pool = max(sellable_cost_basis_pool_gbp, _ZERO)
    if sell_amount <= _ZERO:
        return {
            "est_cost_basis_gbp": _ZERO,
            "est_gain_gbp": _ZERO,
            "est_loss_gbp": _ZERO,
            "covered_by_pool_gbp": _ZERO,
            "uncovered_sale_gbp": _ZERO,
            "est_gain_ratio_pct": _ZERO,
            "method": "no-sale",
        }

    if market_pool <= _ZERO:
        return {
            "est_cost_basis_gbp": _ZERO,
            "est_gain_gbp": _q_money(sell_amount),
            "est_loss_gbp": _ZERO,
            "covered_by_pool_gbp": _ZERO,
            "uncovered_sale_gbp": _q_money(sell_amount),
            "est_gain_ratio_pct": Decimal("100.0"),
            "method": "no-priced-sellable-taxable-pool-assume-full-gain",
        }

    covered_sale = min(sell_amount, market_pool)
    uncovered_sale = max(sell_amount - market_pool, _ZERO)
    allocation_ratio = covered_sale / market_pool if market_pool > _ZERO else _ZERO
    est_cost = _q_money(cost_pool * allocation_ratio)
    est_gain = _q_money(sell_amount - est_cost)
    est_loss = _q_money(max(-est_gain, _ZERO))
    est_gain_only = _q_money(max(est_gain, _ZERO))
    gain_ratio_pct = _q_money((est_gain_only / sell_amount) * Decimal("100"))
    method = "portfolio-weighted-cost-ratio"
    if uncovered_sale > _ZERO:
        method = "portfolio-weighted-cost-ratio-with-excess-assumed-full-gain"
    return {
        "est_cost_basis_gbp": est_cost,
        "est_gain_gbp": est_gain_only,
        "est_loss_gbp": est_loss,
        "covered_by_pool_gbp": _q_money(covered_sale),
        "uncovered_sale_gbp": _q_money(uncovered_sale),
        "est_gain_ratio_pct": gain_ratio_pct,
        "method": method,
    }


def _build_compensation_row(
    *,
    scenario_id: str,
    label: str,
    tax_year: str,
    gross_income_gbp: Decimal,
    bonus_gbp: Decimal,
    sell_amount_gbp: Decimal,
    base_pension_sacrifice_gbp: Decimal,
    additional_pension_sacrifice_gbp: Decimal,
    other_income_gbp: Decimal,
    student_loan_plan: int | None,
    estimated_sale_gain_gbp: Decimal,
    estimated_sale_loss_gbp: Decimal,
    assumption_quality: dict[str, str | None],
) -> dict[str, Any]:
    pension_total = _q_money(
        base_pension_sacrifice_gbp + max(additional_pension_sacrifice_gbp, _ZERO)
    )
    gross_after_bonus = _q_money(gross_income_gbp + bonus_gbp)
    bands = get_bands(tax_year)
    ani_before_bonus = _q_money(
        gross_income_gbp - pension_total + other_income_gbp
    )
    ani_after_bonus = _q_money(
        gross_after_bonus - pension_total + other_income_gbp
    )
    pa_before_bonus = _q_money(personal_allowance(bands, ani_before_bonus))
    pa_after_bonus = _q_money(personal_allowance(bands, ani_after_bonus))
    rates = get_marginal_rates(
        TaxContext(
            tax_year=tax_year,
            gross_employment_income=gross_after_bonus,
            pension_sacrifice=pension_total,
            other_income=other_income_gbp,
            student_loan_plan=student_loan_plan,
        )
    )

    bonus_tax = _bonus_tax_breakdown(
        tax_year=tax_year,
        current_gross_income_gbp=gross_income_gbp,
        pension_sacrifice_gbp=pension_total,
        other_income_gbp=other_income_gbp,
        student_loan_plan=student_loan_plan,
        bonus_gbp=bonus_gbp,
    )
    bonus_net_cash = _q_money(max(bonus_gbp - bonus_tax["total_gbp"], _ZERO))

    baseline_liability = _employment_tax_liability_breakdown(
        tax_year=tax_year,
        gross_income_gbp=gross_after_bonus,
        pension_sacrifice_gbp=base_pension_sacrifice_gbp,
        other_income_gbp=other_income_gbp,
        student_loan_plan=student_loan_plan,
    )
    scenario_liability = _employment_tax_liability_breakdown(
        tax_year=tax_year,
        gross_income_gbp=gross_after_bonus,
        pension_sacrifice_gbp=pension_total,
        other_income_gbp=other_income_gbp,
        student_loan_plan=student_loan_plan,
    )
    pension_tax_saving_income = _q_money(
        baseline_liability["income_tax_gbp"] - scenario_liability["income_tax_gbp"]
    )
    pension_tax_saving_ni = _q_money(
        baseline_liability["national_insurance_gbp"]
        - scenario_liability["national_insurance_gbp"]
    )
    pension_tax_saving_sl = _q_money(
        baseline_liability["student_loan_gbp"] - scenario_liability["student_loan_gbp"]
    )
    pension_tax_saving_total = _q_money(
        pension_tax_saving_income + pension_tax_saving_ni + pension_tax_saving_sl
    )
    pension_net_cash_cost = _q_money(
        max(additional_pension_sacrifice_gbp - pension_tax_saving_total, _ZERO)
    )

    baseline = _baseline_for_year(
        tax_year=tax_year,
        settings=None,
        gross_income_gbp=gross_after_bonus,
        pension_sacrifice_gbp=pension_total,
        other_income_gbp=other_income_gbp,
    )
    sale_projection = _project_with_additional_realisation(
        baseline=baseline,
        additional_gains_gbp=max(estimated_sale_gain_gbp, _ZERO),
        additional_losses_gbp=max(estimated_sale_loss_gbp, _ZERO),
    )
    incremental_cgt = _q_money(sale_projection["projected_incremental_cgt_gbp"])
    sale_net_cash = _q_money(max(sell_amount_gbp - incremental_cgt, _ZERO))
    net_decision_cash = _q_money(sale_net_cash + bonus_net_cash - pension_net_cash_cost)

    return {
        "scenario_id": scenario_id,
        "label": label,
        "planning_tax_year": tax_year,
        "gross_income_gbp": _money_str(gross_income_gbp),
        "bonus_gbp": _money_str(bonus_gbp),
        "sell_amount_gbp": _money_str(sell_amount_gbp),
        "pension_sacrifice_gbp": _money_str(pension_total),
        "additional_pension_sacrifice_gbp": _money_str(additional_pension_sacrifice_gbp),
        "adjusted_net_income_before_bonus_gbp": _money_str(ani_before_bonus),
        "adjusted_net_income_after_bonus_gbp": _money_str(ani_after_bonus),
        "personal_allowance_before_bonus_gbp": _money_str(pa_before_bonus),
        "personal_allowance_after_bonus_gbp": _money_str(pa_after_bonus),
        "pa_taper_start_gbp": _money_str(_q_money(bands.pa_taper_start)),
        "in_pa_taper_zone_after_bonus": bool(rates.taper_zone),
        "marginal_rates_pct": {
            "income_tax": str(_q_money(rates.income_tax * Decimal("100"))),
            "national_insurance": str(
                _q_money(rates.national_insurance * Decimal("100"))
            ),
            "student_finance": str(_q_money(rates.student_loan * Decimal("100"))),
            "combined": str(_q_money(rates.combined * Decimal("100"))),
        },
        "bonus_tax_breakdown_gbp": {
            "income_tax_gbp": _money_str(bonus_tax["income_tax_gbp"]),
            "national_insurance_gbp": _money_str(bonus_tax["national_insurance_gbp"]),
            "student_loan_gbp": _money_str(bonus_tax["student_loan_gbp"]),
            "total_gbp": _money_str(bonus_tax["total_gbp"]),
        },
        "pension_tax_saving_breakdown_gbp": {
            "income_tax_gbp": _money_str(pension_tax_saving_income),
            "national_insurance_gbp": _money_str(pension_tax_saving_ni),
            "student_loan_gbp": _money_str(pension_tax_saving_sl),
            "total_gbp": _money_str(pension_tax_saving_total),
        },
        "projected_incremental_cgt_from_sale_gbp": _money_str(incremental_cgt),
        "net_cash_from_sale_after_cgt_gbp": _money_str(sale_net_cash),
        "net_cash_from_bonus_after_payroll_tax_gbp": _money_str(bonus_net_cash),
        "pension_net_cash_cost_gbp": _money_str(pension_net_cash_cost),
        "combined_tax_drag_gbp": _money_str(_q_money(bonus_tax["total_gbp"] + incremental_cgt)),
        "net_decision_cash_gbp": _money_str(net_decision_cash),
        "assumption_quality": assumption_quality,
    }


class TaxPlanService:
    """
    Read-only tax planner payload assembler.
    """

    @staticmethod
    def get_summary(
        *,
        settings: AppSettings | None = None,
        as_of: date | None = None,
        compensation_gross_income_gbp: Decimal | None = None,
        compensation_bonus_gbp: Decimal | None = None,
        compensation_sell_amount_gbp: Decimal | None = None,
        compensation_additional_pension_sacrifice_gbp: Decimal | None = None,
    ) -> dict[str, Any]:
        as_of_date = as_of or date.today()
        generated_at_utc = datetime.now(timezone.utc).isoformat()
        current_tax_year = tax_year_for_date(as_of_date)
        next_tax_year = _next_tax_year(current_tax_year)
        current_year_end, _ = _tax_year_start_end(current_tax_year)
        next_year_start, _ = _tax_year_start_end(next_tax_year)

        portfolio = PortfolioService.get_portfolio_summary(
            settings=settings,
            use_live_true_cost=False,
        )
        security_ids = [ss.security.id for ss in portfolio.securities]

        stale_price_security_count = sum(
            1
            for ss in portfolio.securities
            if ss.current_price_gbp is not None and ss.price_is_stale
        )
        stale_fx_security_count = sum(1 for ss in portfolio.securities if ss.fx_is_stale)
        unpriced_lot_count = sum(
            1
            for ss in portfolio.securities
            for ls in ss.active_lots
            if ls.market_value_gbp is None
        )

        employment_event_count = 0
        employment_event_estimated_tax = Decimal("0")
        year_start, year_end = _tax_year_start_end(current_tax_year)
        with AppContext.read_session() as sess:
            repo = EmploymentTaxEventRepository(sess)
            for security_id in security_ids:
                for event in repo.list_for_security(security_id):
                    if not (year_start <= event.event_date <= year_end):
                        continue
                    employment_event_count += 1
                    amount = _to_decimal(event.estimated_tax_gbp)
                    if amount is not None:
                        employment_event_estimated_tax += amount
        employment_event_estimated_tax = _q_money(employment_event_estimated_tax)

        hide_values = bool(settings and settings.hide_values)
        assumptions: dict[str, Any] = {
            "tax_year_boundary": {
                "current_tax_year_end": current_year_end.isoformat(),
                "next_tax_year_start": next_year_start.isoformat(),
            },
            "income_context": {
                "gross_employment_income_gbp": (
                    _money_str(settings.default_gross_income) if settings else "0.00"
                ),
                "pension_sacrifice_gbp": (
                    _money_str(settings.default_pension_sacrifice) if settings else "0.00"
                ),
                "other_income_gbp": (
                    _money_str(settings.default_other_income) if settings else "0.00"
                ),
                "student_loan_plan": (
                    settings.default_student_loan_plan if settings else None
                ),
                "prior_year_losses_assumed_gbp": "0.00",
            },
            "input_freshness": {
                "stale_price_security_count": stale_price_security_count,
                "stale_fx_security_count": stale_fx_security_count,
                "unpriced_lot_count": unpriced_lot_count,
            },
            "employment_tax_events_current_year": {
                "count": employment_event_count,
                "estimated_tax_gbp": _money_str(employment_event_estimated_tax),
            },
        }

        compensation_inputs = _CompensationInputs(
            gross_income_gbp=_clamp_non_negative(
                compensation_gross_income_gbp,
                settings.default_gross_income if settings else _ZERO,
            ),
            bonus_gbp=_clamp_non_negative(compensation_bonus_gbp, _ZERO),
            sell_amount_gbp=_clamp_non_negative(
                compensation_sell_amount_gbp,
                Decimal("5000"),
            ),
            additional_pension_sacrifice_gbp=_clamp_non_negative(
                compensation_additional_pension_sacrifice_gbp,
                _ZERO,
            ),
        )

        if hide_values:
            return {
                "generated_at_utc": generated_at_utc,
                "as_of_date": as_of_date.isoformat(),
                "hide_values": True,
                "hidden_reason": "Values hidden by privacy mode.",
                "active_tax_year": current_tax_year,
                "next_tax_year": next_tax_year,
                "assumptions": assumptions,
                "summary": {},
                "lots": [],
                "compensation_plan": {
                    "inputs": {
                        "gross_income_gbp": _money_str(compensation_inputs.gross_income_gbp),
                        "bonus_gbp": _money_str(compensation_inputs.bonus_gbp),
                        "sell_amount_gbp": _money_str(compensation_inputs.sell_amount_gbp),
                        "additional_pension_sacrifice_gbp": _money_str(
                            compensation_inputs.additional_pension_sacrifice_gbp
                        ),
                    },
                    "rows": [],
                    "notes": [
                        "Compensation what-if outputs are hidden while privacy mode is enabled."
                    ],
                },
                "notes": [
                    "Monetary tax-planner outputs are hidden while privacy mode is enabled."
                ],
            }

        baseline_current = _baseline_for_year(
            tax_year=current_tax_year,
            settings=settings,
        )
        baseline_next = _baseline_for_year(
            tax_year=next_tax_year,
            settings=settings,
        )

        lot_rows: list[dict[str, Any]] = []
        total_additional_gains_now = Decimal("0")
        total_additional_losses_now = Decimal("0")
        sellable_projected_lot_count = 0
        locked_lot_count = 0
        sellable_taxable_market_pool = Decimal("0")
        sellable_taxable_cost_pool = Decimal("0")

        for security_summary in portfolio.securities:
            ticker = security_summary.security.ticker
            security_id = security_summary.security.id

            for lot_summary in security_summary.active_lots:
                lot = lot_summary.lot
                quantity = lot_summary.quantity_remaining
                market_value = lot_summary.market_value_gbp
                cost_basis = _q_money(lot_summary.cost_basis_total_gbp)
                sellability_status = lot_summary.sellability_status or "SELLABLE"
                sellable_now = sellability_status != "LOCKED"
                if not sellable_now:
                    locked_lot_count += 1
                taxable_for_cgt = lot.scheme_type != "ISA"
                projected_gain = (
                    _q_money(market_value - lot_summary.cost_basis_total_gbp)
                    if market_value is not None
                    else None
                )
                price_per_share = (
                    _q_money(market_value / quantity)
                    if (market_value is not None and quantity > Decimal("0"))
                    else None
                )

                projection_available = bool(
                    market_value is not None and sellable_now and taxable_for_cgt
                )
                unavailable_reason: str | None = None
                current_projection: dict[str, Decimal] | None = None
                next_projection: dict[str, Decimal] | None = None
                lot_assumption_quality = _assumption_quality(
                    _ASSUMPTION_QUALITY_UNAVAILABLE,
                    "Projection unavailable for this lot under current inputs.",
                )

                if market_value is None:
                    unavailable_reason = "No live price available for this lot."
                elif not sellable_now:
                    if lot_summary.sellability_unlock_date is not None:
                        unavailable_reason = (
                            "Lot is currently locked until "
                            f"{lot_summary.sellability_unlock_date.isoformat()}."
                        )
                    else:
                        unavailable_reason = "Lot is currently locked."
                elif not taxable_for_cgt:
                    unavailable_reason = "ISA lot is tax-sheltered and excluded from CGT."
                elif projected_gain is not None:
                    additional_gains = max(projected_gain, Decimal("0"))
                    additional_losses = max(-projected_gain, Decimal("0"))
                    current_projection = _project_with_additional_realisation(
                        baseline=baseline_current,
                        additional_gains_gbp=additional_gains,
                        additional_losses_gbp=additional_losses,
                    )
                    next_projection = _project_with_additional_realisation(
                        baseline=baseline_next,
                        additional_gains_gbp=additional_gains,
                        additional_losses_gbp=additional_losses,
                    )
                    total_additional_gains_now += additional_gains
                    total_additional_losses_now += additional_losses
                    sellable_projected_lot_count += 1
                    sellable_taxable_market_pool += _q_money(market_value)
                    sellable_taxable_cost_pool += cost_basis
                    lot_assumption_quality = _assumption_quality(
                        _ASSUMPTION_QUALITY_EXACT,
                        (
                            "Uses this lot's current market value and persisted cost basis; "
                            "next-year view keeps the same quantity/price assumption."
                        ),
                    )
                elif unavailable_reason is not None:
                    lot_assumption_quality = _assumption_quality(
                        _ASSUMPTION_QUALITY_UNAVAILABLE,
                        unavailable_reason,
                    )

                lot_rows.append(
                    {
                        "lot_id": lot.id,
                        "security_id": security_id,
                        "ticker": ticker,
                        "scheme_type": lot.scheme_type,
                        "scheme_label": _SCHEME_LABELS.get(lot.scheme_type, lot.scheme_type),
                        "acquisition_date": lot.acquisition_date.isoformat(),
                        "quantity_remaining": str(quantity),
                        "sellability_status": sellability_status,
                        "sellability_unlock_date": (
                            lot_summary.sellability_unlock_date.isoformat()
                            if lot_summary.sellability_unlock_date is not None
                            else None
                        ),
                        "projection_available": projection_available,
                        "projection_unavailable_reason": unavailable_reason,
                        "assumption_quality": lot_assumption_quality,
                        "is_taxable_for_cgt": taxable_for_cgt,
                        "price_per_share_gbp": _money_str(price_per_share),
                        "market_value_gbp": _money_str(_q_money(market_value))
                        if market_value is not None
                        else None,
                        "cost_basis_gbp": _money_str(cost_basis),
                        "projected_gain_gbp": _money_str(projected_gain),
                        "if_sold_current_year_total_cgt_gbp": (
                            _money_str(current_projection["projected_total_cgt_gbp"])
                            if current_projection is not None
                            else None
                        ),
                        "if_sold_current_year_incremental_cgt_gbp": (
                            _money_str(current_projection["projected_incremental_cgt_gbp"])
                            if current_projection is not None
                            else None
                        ),
                        "if_sold_next_year_total_cgt_gbp": (
                            _money_str(next_projection["projected_total_cgt_gbp"])
                            if next_projection is not None
                            else None
                        ),
                        "if_sold_next_year_incremental_cgt_gbp": (
                            _money_str(next_projection["projected_incremental_cgt_gbp"])
                            if next_projection is not None
                            else None
                        ),
                        "incremental_cgt_difference_wait_gbp": (
                            _money_str(
                                current_projection["projected_incremental_cgt_gbp"]
                                - next_projection["projected_incremental_cgt_gbp"]
                            )
                            if current_projection is not None and next_projection is not None
                            else None
                        ),
                    }
                )

        lot_rows.sort(key=lambda row: (row["ticker"], row["acquisition_date"], row["lot_id"]))

        sell_now_projection = _project_with_additional_realisation(
            baseline=baseline_current,
            additional_gains_gbp=total_additional_gains_now,
            additional_losses_gbp=total_additional_losses_now,
        )
        sell_after_projection = _project_with_additional_realisation(
            baseline=baseline_next,
            additional_gains_gbp=total_additional_gains_now,
            additional_losses_gbp=total_additional_losses_now,
        )
        waiting_difference = _q_money(
            sell_now_projection["projected_incremental_cgt_gbp"]
            - sell_after_projection["projected_incremental_cgt_gbp"]
        )
        cross_year_assumption_quality = _assumption_quality(
            _ASSUMPTION_QUALITY_WEIGHTED,
            (
                "Assumes identical disposal quantities/prices across the tax-year boundary; "
                "difference is from tax-year context only."
            ),
        )
        if sellable_projected_lot_count == 0:
            cross_year_assumption_quality = _assumption_quality(
                _ASSUMPTION_QUALITY_UNAVAILABLE,
                "No sellable priced taxable lots are available for cross-year projection.",
            )

        summary = {
            "realised_to_date": {
                "tax_year": current_tax_year,
                "disposal_count": baseline_current.disposal_count,
                "realised_gains_gbp": _money_str(baseline_current.realised_gains_gbp),
                "realised_losses_gbp": _money_str(baseline_current.realised_losses_gbp),
                "net_gain_gbp": _money_str(baseline_current.net_gain_gbp),
                "annual_exempt_amount_gbp": _money_str(
                    baseline_current.annual_exempt_amount_gbp
                ),
                "remaining_aea_gbp": _money_str(baseline_current.remaining_aea_gbp),
                "taxable_gain_gbp": _money_str(baseline_current.taxable_gain_gbp),
                "total_cgt_gbp": _money_str(baseline_current.total_cgt_gbp),
            },
            "cross_year_comparison": {
                "assumption_quality": cross_year_assumption_quality,
                "additional_realisation_scope": {
                    "sellable_projected_lot_count": sellable_projected_lot_count,
                    "projected_gains_gbp": _money_str(total_additional_gains_now),
                    "projected_losses_gbp": _money_str(total_additional_losses_now),
                    "projected_net_gain_gbp": _money_str(
                        total_additional_gains_now - total_additional_losses_now
                    ),
                },
                "sell_before_tax_year_end": {
                    "tax_year": current_tax_year,
                    "assumed_disposal_date": as_of_date.isoformat(),
                    "assumption_quality": cross_year_assumption_quality,
                    "projected_total_cgt_gbp": _money_str(
                        sell_now_projection["projected_total_cgt_gbp"]
                    ),
                    "projected_incremental_cgt_gbp": _money_str(
                        sell_now_projection["projected_incremental_cgt_gbp"]
                    ),
                    "projected_taxable_gain_gbp": _money_str(
                        sell_now_projection["projected_taxable_gain_gbp"]
                    ),
                    "projected_remaining_aea_gbp": _money_str(
                        sell_now_projection["projected_remaining_aea_gbp"]
                    ),
                },
                "sell_after_tax_year_rollover": {
                    "tax_year": next_tax_year,
                    "assumed_disposal_date": next_year_start.isoformat(),
                    "assumption_quality": cross_year_assumption_quality,
                    "projected_total_cgt_gbp": _money_str(
                        sell_after_projection["projected_total_cgt_gbp"]
                    ),
                    "projected_incremental_cgt_gbp": _money_str(
                        sell_after_projection["projected_incremental_cgt_gbp"]
                    ),
                    "projected_taxable_gain_gbp": _money_str(
                        sell_after_projection["projected_taxable_gain_gbp"]
                    ),
                    "projected_remaining_aea_gbp": _money_str(
                        sell_after_projection["projected_remaining_aea_gbp"]
                    ),
                },
                "incremental_cgt_difference_if_wait_gbp": _money_str(waiting_difference),
            },
        }

        base_pension_sacrifice = settings.default_pension_sacrifice if settings else _ZERO
        other_income = settings.default_other_income if settings else _ZERO
        student_loan_plan = settings.default_student_loan_plan if settings else None
        sale_estimate = _estimate_sale_components(
            sell_amount_gbp=compensation_inputs.sell_amount_gbp,
            sellable_market_value_pool_gbp=sellable_taxable_market_pool,
            sellable_cost_basis_pool_gbp=sellable_taxable_cost_pool,
        )
        sale_assumption_method = str(sale_estimate["method"])
        sale_assumption_quality = _assumption_quality(
            _ASSUMPTION_QUALITY_WEIGHTED,
            (
                "Estimated sale gain/loss uses portfolio-weighted cost ratio across "
                "sellable taxable lots."
            ),
        )
        if sale_assumption_method == "no-sale":
            sale_assumption_quality = _assumption_quality(
                _ASSUMPTION_QUALITY_EXACT,
                "No sale amount entered; no estimated gain/loss allocation applied.",
            )
        elif sale_assumption_method == "no-priced-sellable-taxable-pool-assume-full-gain":
            sale_assumption_quality = _assumption_quality(
                _ASSUMPTION_QUALITY_UNAVAILABLE,
                "No priced sellable taxable pool exists; sale amount assumed as full gain.",
            )
        elif sale_assumption_method == "portfolio-weighted-cost-ratio-with-excess-assumed-full-gain":
            sale_assumption_quality = _assumption_quality(
                _ASSUMPTION_QUALITY_WEIGHTED,
                (
                    "Weighted cost ratio applied for covered sale value; excess sale amount "
                    "assumed as full gain."
                ),
            )

        hold_row = _build_compensation_row(
            scenario_id="hold_baseline",
            label="Hold this tax year (no sale, no pension change)",
            tax_year=current_tax_year,
            gross_income_gbp=compensation_inputs.gross_income_gbp,
            bonus_gbp=compensation_inputs.bonus_gbp,
            sell_amount_gbp=_ZERO,
            base_pension_sacrifice_gbp=base_pension_sacrifice,
            additional_pension_sacrifice_gbp=_ZERO,
            other_income_gbp=other_income,
            student_loan_plan=student_loan_plan,
            estimated_sale_gain_gbp=_ZERO,
            estimated_sale_loss_gbp=_ZERO,
            assumption_quality=_assumption_quality(
                _ASSUMPTION_QUALITY_EXACT,
                "No sale leg is modelled in this baseline scenario.",
            ),
        )
        sell_row = _build_compensation_row(
            scenario_id="sell_baseline",
            label="Sell this tax year (current pension level)",
            tax_year=current_tax_year,
            gross_income_gbp=compensation_inputs.gross_income_gbp,
            bonus_gbp=compensation_inputs.bonus_gbp,
            sell_amount_gbp=compensation_inputs.sell_amount_gbp,
            base_pension_sacrifice_gbp=base_pension_sacrifice,
            additional_pension_sacrifice_gbp=_ZERO,
            other_income_gbp=other_income,
            student_loan_plan=student_loan_plan,
            estimated_sale_gain_gbp=sale_estimate["est_gain_gbp"],
            estimated_sale_loss_gbp=sale_estimate["est_loss_gbp"],
            assumption_quality=sale_assumption_quality,
        )
        sell_next_row = _build_compensation_row(
            scenario_id="sell_next_tax_year",
            label="Sell next tax year (current pension level)",
            tax_year=next_tax_year,
            gross_income_gbp=compensation_inputs.gross_income_gbp,
            bonus_gbp=compensation_inputs.bonus_gbp,
            sell_amount_gbp=compensation_inputs.sell_amount_gbp,
            base_pension_sacrifice_gbp=base_pension_sacrifice,
            additional_pension_sacrifice_gbp=_ZERO,
            other_income_gbp=other_income,
            student_loan_plan=student_loan_plan,
            estimated_sale_gain_gbp=sale_estimate["est_gain_gbp"],
            estimated_sale_loss_gbp=sale_estimate["est_loss_gbp"],
            assumption_quality=_assumption_quality(
                sale_assumption_quality["label"],
                (
                    "Uses the same estimated sale gain/loss and input amounts while "
                    "switching to next-year tax bands."
                ),
            ),
        )
        sell_with_pension_row = _build_compensation_row(
            scenario_id="sell_with_extra_pension",
            label="Sell this tax year + increase pension first",
            tax_year=current_tax_year,
            gross_income_gbp=compensation_inputs.gross_income_gbp,
            bonus_gbp=compensation_inputs.bonus_gbp,
            sell_amount_gbp=compensation_inputs.sell_amount_gbp,
            base_pension_sacrifice_gbp=base_pension_sacrifice,
            additional_pension_sacrifice_gbp=compensation_inputs.additional_pension_sacrifice_gbp,
            other_income_gbp=other_income,
            student_loan_plan=student_loan_plan,
            estimated_sale_gain_gbp=sale_estimate["est_gain_gbp"],
            estimated_sale_loss_gbp=sale_estimate["est_loss_gbp"],
            assumption_quality=sale_assumption_quality,
        )
        sell_next_with_pension_row = _build_compensation_row(
            scenario_id="sell_next_tax_year_with_extra_pension",
            label="Sell next tax year + increase pension first",
            tax_year=next_tax_year,
            gross_income_gbp=compensation_inputs.gross_income_gbp,
            bonus_gbp=compensation_inputs.bonus_gbp,
            sell_amount_gbp=compensation_inputs.sell_amount_gbp,
            base_pension_sacrifice_gbp=base_pension_sacrifice,
            additional_pension_sacrifice_gbp=compensation_inputs.additional_pension_sacrifice_gbp,
            other_income_gbp=other_income,
            student_loan_plan=student_loan_plan,
            estimated_sale_gain_gbp=sale_estimate["est_gain_gbp"],
            estimated_sale_loss_gbp=sale_estimate["est_loss_gbp"],
            assumption_quality=_assumption_quality(
                sale_assumption_quality["label"],
                (
                    "Uses the same estimated sale gain/loss and input amounts while "
                    "switching to next-year tax bands."
                ),
            ),
        )

        def _bonus_tax_component(row: dict[str, Any], component: str) -> Decimal:
            breakdown = row.get("bonus_tax_breakdown_gbp", {})
            if not isinstance(breakdown, dict):
                return _ZERO
            return _to_decimal(breakdown.get(component)) or _ZERO

        def _timing_delta(
            wait_row: dict[str, Any], sell_now_row: dict[str, Any]
        ) -> dict[str, str | None]:
            wait_income_tax = _bonus_tax_component(wait_row, "income_tax_gbp")
            wait_ni = _bonus_tax_component(wait_row, "national_insurance_gbp")
            wait_sl = _bonus_tax_component(wait_row, "student_loan_gbp")
            wait_payroll_total = _bonus_tax_component(wait_row, "total_gbp")
            wait_cgt = _to_decimal(wait_row["projected_incremental_cgt_from_sale_gbp"]) or _ZERO
            wait_net_cash = _to_decimal(wait_row["net_decision_cash_gbp"]) or _ZERO

            sell_now_income_tax = _bonus_tax_component(sell_now_row, "income_tax_gbp")
            sell_now_ni = _bonus_tax_component(sell_now_row, "national_insurance_gbp")
            sell_now_sl = _bonus_tax_component(sell_now_row, "student_loan_gbp")
            sell_now_payroll_total = _bonus_tax_component(sell_now_row, "total_gbp")
            sell_now_cgt = _to_decimal(sell_now_row["projected_incremental_cgt_from_sale_gbp"]) or _ZERO
            sell_now_net_cash = _to_decimal(sell_now_row["net_decision_cash_gbp"]) or _ZERO

            return {
                "income_tax_delta_wait_vs_sell_now_gbp": _money_str(
                    _q_money(wait_income_tax - sell_now_income_tax)
                ),
                "national_insurance_delta_wait_vs_sell_now_gbp": _money_str(
                    _q_money(wait_ni - sell_now_ni)
                ),
                "student_finance_delta_wait_vs_sell_now_gbp": _money_str(
                    _q_money(wait_sl - sell_now_sl)
                ),
                "payroll_tax_delta_wait_vs_sell_now_gbp": _money_str(
                    _q_money(wait_payroll_total - sell_now_payroll_total)
                ),
                "sale_cgt_delta_wait_vs_sell_now_gbp": _money_str(
                    _q_money(wait_cgt - sell_now_cgt)
                ),
                "combined_tax_drag_delta_wait_vs_sell_now_gbp": _money_str(
                    _q_money(
                        (wait_payroll_total + wait_cgt)
                        - (sell_now_payroll_total + sell_now_cgt)
                    )
                ),
                "net_cash_delta_wait_vs_sell_now_gbp": _money_str(
                    _q_money(wait_net_cash - sell_now_net_cash)
                ),
            }

        hold_net_cash = _to_decimal(hold_row["net_decision_cash_gbp"]) or _ZERO
        sell_net_cash = _to_decimal(sell_row["net_decision_cash_gbp"]) or _ZERO
        sell_next_net_cash = _to_decimal(sell_next_row["net_decision_cash_gbp"]) or _ZERO
        sell_pension_net_cash = (
            _to_decimal(sell_with_pension_row["net_decision_cash_gbp"]) or _ZERO
        )
        sell_next_pension_net_cash = (
            _to_decimal(sell_next_with_pension_row["net_decision_cash_gbp"]) or _ZERO
        )
        sell_ani = _to_decimal(sell_row["adjusted_net_income_after_bonus_gbp"]) or _ZERO
        sell_pension_ani = (
            _to_decimal(sell_with_pension_row["adjusted_net_income_after_bonus_gbp"]) or _ZERO
        )
        sell_bonus_tax = _to_decimal(sell_row["bonus_tax_breakdown_gbp"]["total_gbp"]) or _ZERO
        sell_pension_bonus_tax = (
            _to_decimal(sell_with_pension_row["bonus_tax_breakdown_gbp"]["total_gbp"]) or _ZERO
        )
        sell_cgt = _to_decimal(sell_row["projected_incremental_cgt_from_sale_gbp"]) or _ZERO
        sell_pension_cgt = (
            _to_decimal(sell_with_pension_row["projected_incremental_cgt_from_sale_gbp"]) or _ZERO
        )
        timing_delta_baseline = _timing_delta(sell_next_row, sell_row)
        timing_delta_with_pension = _timing_delta(
            sell_next_with_pension_row,
            sell_with_pension_row,
        )

        compensation_notes: list[str] = [
            (
                "Compensation what-if combines IT/NI/Student Finance with incremental CGT "
                "for estimated sale gains; figures are advisory estimates."
            ),
            (
                "Salary-sacrifice pension reduces ANI and NI/Student Finance bases; "
                "assumes changes occur before bonus/sale events."
            ),
            (
                "Sell-next-year scenarios apply the same salary, bonus, and pension inputs "
                "to next-year tax bands so IT/NI/SF and CGT can be compared to selling now."
            ),
        ]
        if sale_estimate["method"] == "no-priced-sellable-taxable-pool-assume-full-gain":
            compensation_notes.append(
                "No priced sellable taxable lots were available; sale amount was treated as full gain."
            )
        elif sale_estimate["uncovered_sale_gbp"] > _ZERO:
            compensation_notes.append(
                "Requested sale amount exceeds priced sellable taxable pool; excess treated as full gain."
            )

        compensation_plan = {
            "assumption_quality": _assumption_quality(
                _ASSUMPTION_QUALITY_WEIGHTED,
                (
                    "Combines payroll-tax modelling with estimated sale gain/loss assumptions "
                    "for deterministic scenario comparison."
                ),
            ),
            "inputs": {
                "gross_income_gbp": _money_str(compensation_inputs.gross_income_gbp),
                "bonus_gbp": _money_str(compensation_inputs.bonus_gbp),
                "sell_amount_gbp": _money_str(compensation_inputs.sell_amount_gbp),
                "additional_pension_sacrifice_gbp": _money_str(
                    compensation_inputs.additional_pension_sacrifice_gbp
                ),
                "existing_pension_sacrifice_gbp": _money_str(base_pension_sacrifice),
                "other_income_gbp": _money_str(other_income),
                "student_loan_plan": student_loan_plan,
            },
            "sale_assumption": {
                "method": sale_estimate["method"],
                "assumption_quality": sale_assumption_quality,
                "sellable_market_value_pool_gbp": _money_str(sellable_taxable_market_pool),
                "sellable_cost_basis_pool_gbp": _money_str(sellable_taxable_cost_pool),
                "covered_by_pool_gbp": _money_str(sale_estimate["covered_by_pool_gbp"]),
                "uncovered_sale_gbp": _money_str(sale_estimate["uncovered_sale_gbp"]),
                "estimated_cost_basis_gbp": _money_str(sale_estimate["est_cost_basis_gbp"]),
                "estimated_gain_gbp": _money_str(sale_estimate["est_gain_gbp"]),
                "estimated_loss_gbp": _money_str(sale_estimate["est_loss_gbp"]),
                "estimated_gain_ratio_pct": str(sale_estimate["est_gain_ratio_pct"]),
            },
            "rows": [
                hold_row,
                sell_row,
                sell_next_row,
                sell_with_pension_row,
                sell_next_with_pension_row,
            ],
            "comparison": {
                "sell_vs_hold_net_cash_delta_gbp": _money_str(
                    _q_money(sell_net_cash - hold_net_cash)
                ),
                "sell_next_vs_sell_delta_gbp": _money_str(
                    _q_money(sell_next_net_cash - sell_net_cash)
                ),
                "sell_with_pension_vs_sell_delta_gbp": _money_str(
                    _q_money(sell_pension_net_cash - sell_net_cash)
                ),
                "sell_next_with_pension_vs_sell_with_pension_delta_gbp": _money_str(
                    _q_money(sell_next_pension_net_cash - sell_pension_net_cash)
                ),
                "ani_reduction_from_extra_pension_gbp": _money_str(
                    _q_money(sell_ani - sell_pension_ani)
                ),
                "bonus_tax_saved_by_extra_pension_gbp": _money_str(
                    _q_money(sell_bonus_tax - sell_pension_bonus_tax)
                ),
                "sale_cgt_saved_by_extra_pension_gbp": _money_str(
                    _q_money(sell_cgt - sell_pension_cgt)
                ),
                "combined_tax_saved_by_extra_pension_gbp": _money_str(
                    _q_money(
                        (sell_bonus_tax - sell_pension_bonus_tax)
                        + (sell_cgt - sell_pension_cgt)
                    )
                ),
            },
            "timing_comparison": {
                "sell_this_tax_year": current_tax_year,
                "sell_next_tax_year": next_tax_year,
                "baseline_pension": timing_delta_baseline,
                "with_extra_pension": timing_delta_with_pension,
            },
            "decision_prompts": [
                "At 99k income, what is the net-cash impact of selling 5k now versus holding?",
                "At 101k income, does extra pension sacrifice improve net outcomes before selling?",
                "What changes if the same sale is delayed into the next UK tax year?",
            ],
            "notes": compensation_notes,
        }

        notes: list[str] = [
            (
                "Cross-year comparison assumes identical prices and quantities "
                "on both sides of the UK tax-year boundary."
            ),
            (
                "HMRC same-day, 30-day, and Section 104 matching are modelled "
                "for realised CGT baselines. Forward-looking scenarios remain "
                "indicative when future reacquisitions are unknown."
            ),
        ]
        if stale_price_security_count > 0:
            notes.append(
                f"{stale_price_security_count} security(ies) use stale price inputs."
            )
        if stale_fx_security_count > 0:
            notes.append(
                f"{stale_fx_security_count} security(ies) use stale FX conversion inputs."
            )
        if unpriced_lot_count > 0:
            notes.append(
                f"{unpriced_lot_count} lot(s) excluded from projected CGT due to missing prices."
            )
        if locked_lot_count > 0:
            notes.append(
                f"{locked_lot_count} lot(s) are currently locked and excluded from sell-now projections."
            )
        if sellable_projected_lot_count == 0:
            notes.append(
                "No sellable priced taxable lots are available for projected CGT comparison."
            )
        if employment_event_count > 0:
            notes.append(
                "Structured employment-tax events are present for this tax year; "
                "review transfer-time employment tax separately from CGT."
            )

        return {
            "generated_at_utc": generated_at_utc,
            "as_of_date": as_of_date.isoformat(),
            "hide_values": False,
            "active_tax_year": current_tax_year,
            "next_tax_year": next_tax_year,
            "assumptions": assumptions,
            "summary": summary,
            "compensation_plan": compensation_plan,
            "lots": lot_rows,
            "notes": notes,
        }
