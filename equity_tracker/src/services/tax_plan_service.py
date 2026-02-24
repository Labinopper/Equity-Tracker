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
from ..core.tax_engine import calculate_cgt, get_bands, tax_year_for_date
from ..core.tax_engine.income_tax import personal_allowance
from ..db.repository import EmploymentTaxEventRepository
from ..settings import AppSettings
from .portfolio_service import PortfolioService
from .report_service import ReportService

_GBP_Q = Decimal("0.01")

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


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_GBP_Q, rounding=ROUND_HALF_UP)


def _money_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(_q_money(value))


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


def _taxable_income_ex_gains(
    *,
    settings: AppSettings | None,
    tax_year: str,
) -> Decimal:
    if settings is None:
        return Decimal("0")
    bands = get_bands(tax_year)
    adjusted_net_income = (
        settings.default_gross_income
        - settings.default_pension_sacrifice
        + settings.default_other_income
    )
    pa = personal_allowance(bands, adjusted_net_income)
    return max(Decimal("0"), adjusted_net_income - pa)


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
) -> _CgtBaseline:
    report = ReportService.cgt_summary(tax_year)
    taxable_income = _taxable_income_ex_gains(settings=settings, tax_year=tax_year)
    cgt_result = _calculate_cgt_totals(
        tax_year=tax_year,
        taxable_income_ex_gains=taxable_income,
        gains_gbp=report.total_gains_gbp,
        losses_gbp=report.total_losses_gbp,
    )
    annual_exempt = get_bands(tax_year).cgt_annual_exempt_amount
    remaining_aea = max(annual_exempt - max(report.net_gain_gbp, Decimal("0")), Decimal("0"))
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


class TaxPlanService:
    """
    Read-only tax planner payload assembler.
    """

    @staticmethod
    def get_summary(
        *,
        settings: AppSettings | None = None,
        as_of: date | None = None,
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

        notes: list[str] = [
            (
                "Cross-year comparison assumes identical prices and quantities "
                "on both sides of the UK tax-year boundary."
            ),
            (
                "HMRC same-day and 30-day share matching rules are not modelled; "
                "results are indicative."
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
            "lots": lot_rows,
            "notes": notes,
        }
