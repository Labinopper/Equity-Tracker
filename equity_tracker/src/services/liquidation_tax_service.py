"""
LiquidationTaxService - hypothetical tax-year disposal projection.

Scope:
- Build a hypothetical "sell now" CGT estimate from current sellable holdings.
- Reuse report-service year-to-date realised baseline and core CGT calculation.
- Treat broker commissions as allowable disposal costs for CGT.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..core.tax_engine import TaxContext, calculate_cgt, get_bands, marginal_cgt_rate, tax_year_for_date
from ..core.tax_engine.income_tax import personal_allowance
from ..settings import AppSettings
from .broker_fee_service import BrokerFeeService
from .portfolio_service import PortfolioSummary
from .report_service import ReportService

_MONEY_Q = Decimal("0.01")
_RATE_Q = Decimal("0.0001")


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _q_rate(value: Decimal) -> Decimal:
    return value.quantize(_RATE_Q, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class _SecurityProjection:
    security_id: str
    ticker: str
    gross_proceeds_gbp: Decimal
    cost_basis_gbp: Decimal
    allowable_costs_gbp: Decimal

    @property
    def gain_after_costs_gbp(self) -> Decimal:
        return self.gross_proceeds_gbp - self.allowable_costs_gbp - self.cost_basis_gbp


class LiquidationTaxService:
    @staticmethod
    def baseline_tax_year_state(
        *,
        tax_year: str,
        settings: AppSettings | None = None,
    ) -> dict[str, Any]:
        gross_income = settings.default_gross_income if settings is not None else Decimal("0")
        pension = settings.default_pension_sacrifice if settings is not None else Decimal("0")
        other_income = settings.default_other_income if settings is not None else Decimal("0")
        student_loan_plan = settings.default_student_loan_plan if settings is not None else None
        ctx = TaxContext(
            tax_year=tax_year,
            gross_employment_income=gross_income,
            pension_sacrifice=pension,
            other_income=other_income,
            student_loan_plan=student_loan_plan,
        )
        allowance = personal_allowance(get_bands(tax_year), ctx.adjusted_net_income)
        taxable_income_ex_gains = max(Decimal("0"), ctx.adjusted_net_income - allowance)
        next_pound_rate = _q_rate(
            marginal_cgt_rate(get_bands(tax_year), taxable_income_ex_gains)
        )
        rate_source = f"tax_year_projection_{tax_year}"
        baseline = ReportService.cgt_summary(tax_year, tax_context=ctx)
        baseline_total_cgt = (
            baseline.cgt_result.total_cgt
            if baseline.cgt_result is not None
            else Decimal("0")
        )
        return {
            "tax_year": tax_year,
            "taxable_income_ex_gains_gbp": taxable_income_ex_gains,
            "realised_gains_ytd_gbp": _q_money(baseline.total_gains_gbp),
            "realised_losses_ytd_gbp": _q_money(baseline.total_losses_gbp),
            "base_total_cgt_gbp": _q_money(baseline_total_cgt),
            "next_pound_cgt_rate": next_pound_rate,
            "cgt_rate_source": rate_source,
        }

    @staticmethod
    def project_tax_year_incremental(
        *,
        tax_year: str,
        settings: AppSettings | None = None,
        additional_gains: list[Decimal] | None = None,
        additional_losses: list[Decimal] | None = None,
    ) -> dict[str, Any]:
        baseline_state = LiquidationTaxService.baseline_tax_year_state(
            tax_year=tax_year,
            settings=settings,
        )
        realised_gains_ytd = _q_money(
            Decimal(str(baseline_state["realised_gains_ytd_gbp"]))
        )
        realised_losses_ytd = _q_money(
            Decimal(str(baseline_state["realised_losses_ytd_gbp"]))
        )
        taxable_income_ex_gains = _q_money(
            Decimal(str(baseline_state["taxable_income_ex_gains_gbp"]))
        )
        base_total_cgt = _q_money(Decimal(str(baseline_state["base_total_cgt_gbp"])))

        gains = [_q_money(g) for g in (additional_gains or []) if g > Decimal("0")]
        losses = [_q_money(l) for l in (additional_losses or []) if l > Decimal("0")]

        result = calculate_cgt(
            bands=get_bands(tax_year),
            realised_gains=(
                ([realised_gains_ytd] if realised_gains_ytd > Decimal("0") else [])
                + gains
            ),
            realised_losses=(
                ([realised_losses_ytd] if realised_losses_ytd > Decimal("0") else [])
                + losses
            ),
            taxable_income_ex_gains=taxable_income_ex_gains,
            prior_year_losses=Decimal("0"),
        )
        projected_total_cgt = _q_money(result.total_cgt)
        return {
            **baseline_state,
            "hypothetical_gains_gbp": _q_money(sum(gains, Decimal("0"))),
            "hypothetical_losses_gbp": _q_money(sum(losses, Decimal("0"))),
            "net_gain_after_netting_gbp": _q_money(result.net_gain),
            "aea_used_gbp": _q_money(result.annual_exempt_amount),
            "taxable_gain_gbp": _q_money(result.taxable_gain),
            "tax_at_basic_rate_gbp": _q_money(result.tax_at_basic_rate),
            "tax_at_higher_rate_gbp": _q_money(result.tax_at_higher_rate),
            "projected_total_cgt_gbp": projected_total_cgt,
            "incremental_cgt_gbp": _q_money(projected_total_cgt - base_total_cgt),
            "notes": list(result.notes),
        }

    @staticmethod
    def _tax_context(
        settings: AppSettings | None,
        *,
        as_of: date_type,
    ) -> tuple[TaxContext, Decimal, Decimal, str]:
        tax_year = (
            settings.default_tax_year
            if settings is not None and settings.default_tax_year
            else tax_year_for_date(as_of)
        )
        gross_income = settings.default_gross_income if settings is not None else Decimal("0")
        pension = settings.default_pension_sacrifice if settings is not None else Decimal("0")
        other_income = settings.default_other_income if settings is not None else Decimal("0")
        student_loan_plan = settings.default_student_loan_plan if settings is not None else None
        ctx = TaxContext(
            tax_year=tax_year,
            gross_employment_income=gross_income,
            pension_sacrifice=pension,
            other_income=other_income,
            student_loan_plan=student_loan_plan,
        )
        allowance = personal_allowance(get_bands(tax_year), ctx.adjusted_net_income)
        taxable_income_ex_gains = max(Decimal("0"), ctx.adjusted_net_income - allowance)
        next_pound_rate = marginal_cgt_rate(get_bands(tax_year), taxable_income_ex_gains)
        return (
            ctx,
            _q_money(taxable_income_ex_gains),
            _q_rate(next_pound_rate),
            f"tax_year_projection_{tax_year}",
        )

    @staticmethod
    def _security_projection(
        *,
        summary: PortfolioSummary,
        settings: AppSettings | None,
    ) -> tuple[list[_SecurityProjection], Decimal, list[dict[str, Any]]]:
        projections: list[_SecurityProjection] = []
        fee_total = Decimal("0")
        fee_details: list[dict[str, Any]] = []

        for security_summary in summary.securities:
            gross_proceeds = Decimal("0")
            taxable_cost_basis = Decimal("0")
            taxable_market_value = Decimal("0")
            quantity = Decimal("0")

            for lot_summary in security_summary.active_lots:
                mv = lot_summary.market_value_gbp
                if mv is None:
                    continue
                is_forfeitable_match = (
                    lot_summary.forfeiture_risk is not None
                    and lot_summary.forfeiture_risk.in_window
                    and lot_summary.lot.matching_lot_id is not None
                )
                if is_forfeitable_match or lot_summary.sellability_status == "LOCKED":
                    continue

                gross_proceeds += mv
                quantity += lot_summary.quantity_remaining
                if lot_summary.lot.scheme_type != "ISA":
                    taxable_market_value += mv
                    taxable_cost_basis += lot_summary.cost_basis_total_gbp

            if gross_proceeds <= Decimal("0"):
                continue

            fee_estimate = BrokerFeeService.estimate_order_fee(
                security_currency=security_summary.security.currency,
                quantity=quantity,
                price_native=security_summary.current_price_native,
                price_gbp=security_summary.current_price_gbp,
                settings=settings,
            )
            security_fee = fee_estimate["estimated_fee_gbp"]
            if not isinstance(security_fee, Decimal):
                security_fee = Decimal("0.00")
            fee_total += security_fee
            fee_details.append(
                {
                    "security_id": security_summary.security.id,
                    "ticker": security_summary.security.ticker,
                    **fee_estimate,
                }
            )

            allowable_costs = (
                _q_money(security_fee * (taxable_market_value / gross_proceeds))
                if gross_proceeds > Decimal("0") and taxable_market_value > Decimal("0")
                else Decimal("0.00")
            )
            if taxable_market_value > Decimal("0"):
                projections.append(
                    _SecurityProjection(
                        security_id=security_summary.security.id,
                        ticker=security_summary.security.ticker,
                        gross_proceeds_gbp=_q_money(taxable_market_value),
                        cost_basis_gbp=_q_money(taxable_cost_basis),
                        allowable_costs_gbp=allowable_costs,
                    )
                )

        return projections, _q_money(fee_total), fee_details

    @staticmethod
    def project_sell_now(
        *,
        summary: PortfolioSummary,
        settings: AppSettings | None = None,
        as_of: date_type | None = None,
    ) -> dict[str, Any]:
        as_of_date = as_of or date_type.today()
        ctx, taxable_income_ex_gains, next_pound_rate, rate_source = LiquidationTaxService._tax_context(
            settings,
            as_of=as_of_date,
        )
        baseline = ReportService.cgt_summary(ctx.tax_year, tax_context=ctx)
        projections, fee_total, fee_details = LiquidationTaxService._security_projection(
            summary=summary,
            settings=settings,
        )

        hypothetical_gains: list[Decimal] = []
        hypothetical_losses: list[Decimal] = []
        gross_proceeds = Decimal("0")
        allowable_costs = Decimal("0")
        hypothetical_cost_basis = Decimal("0")

        for projection in projections:
            gain = projection.gain_after_costs_gbp
            gross_proceeds += projection.gross_proceeds_gbp
            allowable_costs += projection.allowable_costs_gbp
            hypothetical_cost_basis += projection.cost_basis_gbp
            if gain >= Decimal("0"):
                hypothetical_gains.append(gain)
            else:
                hypothetical_losses.append(abs(gain))

        total_gains = (
            [baseline.total_gains_gbp] if baseline.total_gains_gbp > Decimal("0") else []
        ) + hypothetical_gains
        total_losses = (
            [baseline.total_losses_gbp] if baseline.total_losses_gbp > Decimal("0") else []
        ) + hypothetical_losses

        result = calculate_cgt(
            bands=get_bands(ctx.tax_year),
            realised_gains=total_gains,
            realised_losses=total_losses,
            taxable_income_ex_gains=taxable_income_ex_gains,
            prior_year_losses=Decimal("0"),
        )

        baseline_total_cgt = (
            baseline.cgt_result.total_cgt
            if baseline.cgt_result is not None
            else Decimal("0")
        )
        projected_cgt = _q_money(result.total_cgt)
        incremental_cgt = _q_money(projected_cgt - baseline_total_cgt)

        return {
            "tax_year": ctx.tax_year,
            "taxable_income_ex_gains_gbp": taxable_income_ex_gains,
            "gross_proceeds_gbp": _q_money(gross_proceeds),
            "allowable_disposal_costs_gbp": _q_money(allowable_costs),
            "net_proceeds_for_cgt_gbp": _q_money(gross_proceeds - allowable_costs),
            "realised_gains_ytd_gbp": _q_money(baseline.total_gains_gbp),
            "realised_losses_ytd_gbp": _q_money(baseline.total_losses_gbp),
            "hypothetical_gains_gbp": _q_money(sum(hypothetical_gains, Decimal("0"))),
            "hypothetical_losses_gbp": _q_money(sum(hypothetical_losses, Decimal("0"))),
            "hypothetical_cost_basis_gbp": _q_money(hypothetical_cost_basis),
            "net_gain_after_netting_gbp": _q_money(result.net_gain),
            "aea_used_gbp": _q_money(result.annual_exempt_amount),
            "taxable_gain_gbp": _q_money(result.taxable_gain),
            "tax_at_basic_rate_gbp": _q_money(result.tax_at_basic_rate),
            "tax_at_higher_rate_gbp": _q_money(result.tax_at_higher_rate),
            "projected_total_cgt_gbp": projected_cgt,
            "incremental_cgt_gbp": incremental_cgt,
            "next_pound_cgt_rate": next_pound_rate,
            "cgt_rate_source": rate_source,
            "estimated_fees_total_gbp": fee_total,
            "fee_details": fee_details,
            "notes": list(result.notes),
        }
