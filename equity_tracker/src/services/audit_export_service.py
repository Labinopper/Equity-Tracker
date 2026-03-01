"""
AuditExportService — lossless Portfolio audit export.

Assembles a single JSON-serialisable dict capturing all raw inputs and
per-lot calculated outputs used by the Portfolio page.  The export is
deterministic for a given DB state and settings snapshot.

Schema top-level keys (must not be altered):
  metadata, tax_settings, fx_rates, securities, lots,
  per_lot_calculations, portfolio_aggregates, tax_brackets_used,
  additional_diagnostics

Design:
  - Calls PortfolioService.get_portfolio_summary() so every calculated value
    matches exactly what the Portfolio page displays.
  - Re-runs the employment-tax engine per lot to capture IT/NI/SL components
    that the portfolio summary only exposes as an aggregate.
  - All monetary fields output as 2dp ROUND_HALF_UP strings.
  - No floats anywhere in the calculation path.
"""

from __future__ import annotations

import subprocess
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..app_context import AppContext
from ..core.tax_engine import (
    SIPEvent,
    SIPEventType,
    SIPHolding,
    SIPHoldingPeriodCategory,
    SIPShareType,
    TaxContext,
    get_bands,
    get_marginal_rates,
    marginal_cgt_rate,
    personal_allowance,
    process_sip_event,
    tax_year_for_date,
)
from ..core.tax_engine.employment_tax_engine import (
    EmploymentTaxContext,
    EmploymentTaxEstimate,
    estimate_employment_tax_for_lot,
)
from ..db.models import FxRate, Lot
from ..services.portfolio_service import (
    SELLABILITY_LOCKED,
    LotSummary,
    PortfolioService,
    PortfolioSummary,
    SecuritySummary,
)
from ..settings import AppSettings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_Q2 = Decimal("0.01")
_ZERO = Decimal("0.00")

_SIP_SHARE_TYPE_MAP: dict[str, SIPShareType] = {
    "SIP_PARTNERSHIP": SIPShareType.PARTNERSHIP,
    "SIP_MATCHING": SIPShareType.MATCHING,
    "SIP_DIVIDEND": SIPShareType.DIVIDEND,
}


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------

def _q2(v: Decimal) -> Decimal:
    return v.quantize(_Q2, rounding=ROUND_HALF_UP)


def _fmt(v: Decimal | None) -> str | None:
    if v is None:
        return None
    return str(_q2(v))


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def _compute_marginal_cgt_rate(settings: AppSettings | None, today: date) -> Decimal:
    """Return marginal CGT rate from settings; falls back to 24% (current higher rate)."""
    fallback = Decimal("0.24")
    if settings is None:
        return fallback
    try:
        ty = tax_year_for_date(today)
        bands = get_bands(ty)
        ani = (
            settings.default_gross_income
            - settings.default_pension_sacrifice
            + settings.default_other_income
        )
        pa = personal_allowance(bands, ani)
        taxable = max(_ZERO, settings.default_gross_income - pa)
        return marginal_cgt_rate(bands, taxable)
    except (ValueError, KeyError):
        return fallback


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _build_metadata(
    now_utc: datetime,
    today: date,
    db_path: object,
    db_encrypted: bool,
    settings: AppSettings | None,
    tax_year: str,
) -> dict[str, Any]:
    employment_income = str(settings.default_gross_income) if settings else None
    return {
        "generated_at_utc": now_utc.isoformat(),
        "as_of_used_utc": datetime(
            today.year, today.month, today.day, tzinfo=timezone.utc
        ).isoformat(),
        "db_path": str(db_path) if db_path else None,
        "db_encrypted": db_encrypted,
        "git_commit_hash": _git_commit(),
        "app_versions": {"api": "0.2.0"},
        "rounding_rules": {
            "money_rounding": "2dp, ROUND_HALF_UP",
            "quantity_rounding": "stored as TEXT Decimal; no rounding applied to quantities",
        },
        "tax_year_used": tax_year,
        "employment_income_assumed_gbp": employment_income,
        "net_gain_definition": "SELLABLE_NET_MINUS_SELLABLE_TRUE_COST",
        "net_liquidity_scope": "SELLABLE_ONLY",
        "assumptions": [
            "All calculations use today's date as the hypothetical disposal date.",
            "Employment tax estimates assume configured gross income is the income at disposal "
            "(marginal rate applied to employment income from the equity event).",
            "RSU: employment tax = £0 in Portfolio view. RSU IT+NI crystallises at vest; "
            "any post-vest gain is CGT, not employment income.",
            "ESPP: partnership-share disposal employment tax = £0 per HMRC SIP guidance.",
            "ESPP_PLUS matched shares follow HMRC SIP holding-period rules: "
            "UNDER_THREE_YEARS → IT+NI+SL on full removal value; "
            "THREE_TO_FIVE_YEARS → IT on min(removal, award FMV); "
            "FIVE_PLUS_YEARS → zero.",
            "ESPP_PLUS matched shares inside the 6-month forfeiture window are treated as "
            "forfeit-at-risk; employment tax = £0 (the lot is forfeited, not sold).",
            "CGT per-lot estimates use the marginal CGT rate derived from income settings. "
            "The annual exempt amount (AEA) is NOT deducted at per-lot level.",
            "ISA lots: all taxes = £0; net liquidation = gross market value.",
            "BROKERAGE lots: employment tax = £0; CGT estimate shown separately.",
            "Locked lots (status=LOCKED): no tax estimate or net liquidation until unlocked.",
            "FX conversion: non-GBP security prices are converted using the latest "
            "stored FX rate; rate and timestamp are recorded in the fx_rates section.",
        ],
    }


def _build_tax_settings(settings: AppSettings | None, tax_year: str) -> dict[str, Any]:
    try:
        bands = get_bands(tax_year)
    except ValueError:
        return {"error": f"Tax bands not found for tax year '{tax_year}'."}

    section: dict[str, Any] = {
        "income_tax": {
            "personal_allowance_gbp": str(bands.personal_allowance),
            "basic_rate_threshold_gbp": str(bands.basic_rate_threshold),
            "higher_rate_threshold_gbp": str(bands.higher_rate_threshold),
            "pa_taper_start_gbp": str(bands.pa_taper_start),
            "pa_taper_end_gbp": str(bands.pa_taper_end),
            "basic_rate": str(bands.basic_rate),
            "higher_rate": str(bands.higher_rate),
            "additional_rate": str(bands.additional_rate),
            "taper_zone_effective_rate": str(bands.taper_zone_effective_it_rate),
        },
        "national_insurance": {
            "primary_threshold_gbp": str(bands.ni_primary_threshold),
            "upper_earnings_limit_gbp": str(bands.ni_upper_earnings_limit),
            "rate_below_uel": str(bands.ni_rate_below_uel),
            "rate_above_uel": str(bands.ni_rate_above_uel),
        },
        "student_loan": {
            "plan1_threshold_gbp": str(bands.student_loan_plan1_threshold),
            "plan1_rate": str(bands.student_loan_plan1_rate),
            "plan2_threshold_gbp": str(bands.student_loan_plan2_threshold),
            "plan2_rate": str(bands.student_loan_plan2_rate),
            "plan_in_use": (
                str(settings.default_student_loan_plan)
                if settings and settings.default_student_loan_plan is not None
                else None
            ),
        },
        "capital_gains_tax": {
            "annual_exempt_amount_gbp": str(bands.cgt_annual_exempt_amount),
            "basic_rate": str(bands.cgt_basic_rate),
            "higher_rate": str(bands.cgt_higher_rate),
        },
        "isa_treatment": {
            "is_tax_exempt": True,
            "employment_tax_applied": False,
            "cgt_applied": False,
            "note": "ISA lots are fully tax-exempt. Net liquidation = market value.",
        },
        "scheme_rules": {
            "RSU": (
                "IT+NI paid at vest on FMV. Post-vest gain = CGT only. "
                "No employment tax on disposal in Portfolio view."
            ),
            "ESPP": (
                "SIP partnership shares. Zero disposal employment tax per HMRC SIP guidance."
            ),
            "ESPP_PLUS": (
                "SIP matching shares. Forfeited if linked employee lot sold within 183 days. "
                "Holding period rules: <3yr → full IT+NI+SL on removal value; "
                "3–5yr → IT on min(removal, award FMV); 5yr+ → zero."
            ),
            "SIP_PARTNERSHIP": (
                "SIP partnership shares purchased pre-tax. "
                "Income-tax qualifying period rules apply (same as ESPP_PLUS)."
            ),
            "SIP_MATCHING": (
                "SIP matching shares. Same holding period rules as ESPP_PLUS."
            ),
            "SIP_DIVIDEND": (
                "SIP dividend shares. Exempt from IT if held 3+ years."
            ),
            "BROKERAGE": (
                "Post-tax broker account. No employment tax. "
                "CGT estimate shown separately in per_lot_calculations."
            ),
            "ISA": (
                "ISA wrapper. Fully tax-exempt. No employment tax, no CGT."
            ),
        },
        "income_context_used": {
            "gross_employment_income_gbp": (
                str(settings.default_gross_income) if settings else None
            ),
            "pension_sacrifice_gbp": (
                str(settings.default_pension_sacrifice) if settings else None
            ),
            "other_income_gbp": (
                str(settings.default_other_income) if settings else None
            ),
            "student_loan_plan": (
                settings.default_student_loan_plan if settings else None
            ),
            "configured": settings is not None,
        },
    }

    if settings is not None:
        try:
            ctx = TaxContext(
                tax_year=tax_year,
                gross_employment_income=settings.default_gross_income,
                pension_sacrifice=settings.default_pension_sacrifice,
                other_income=settings.default_other_income,
                student_loan_plan=settings.default_student_loan_plan,
            )
            rates = get_marginal_rates(ctx)
            section["marginal_rates_at_configured_income"] = {
                "income_tax": str(rates.income_tax),
                "national_insurance": str(rates.national_insurance),
                "student_loan": str(rates.student_loan),
                "combined": str(rates.combined),
                "in_pa_taper_zone": rates.taper_zone,
                "notes": rates.notes,
            }
        except Exception:
            pass

    return section


def _build_fx_rates(sess: Session, portfolio: PortfolioSummary) -> list[dict[str, Any]]:
    # 1. fx_rates table rows (primary source)
    stmt = select(FxRate).order_by(
        FxRate.rate_date.desc(),
        FxRate.base_currency,
        FxRate.quote_currency,
    )
    db_rows = list(sess.scalars(stmt).all())
    result: list[dict[str, Any]] = [
        {
            "from_currency": row.base_currency,
            "to_currency": row.quote_currency,
            "rate": str(row.rate),
            "timestamp_used": row.rate_date.isoformat() if row.rate_date else None,
            "source": row.source,
            "origin": "fx_rates_table",
        }
        for row in db_rows
    ]

    # 2. Acquisition FX rates embedded in lot records
    seen: set[tuple[str, str, str]] = set()
    for ss in portfolio.securities:
        security_ccy = ss.security.currency or "GBP"
        for ls in ss.active_lots:
            lot = ls.lot
            if lot.fx_rate_at_acquisition is None:
                continue
            from_ccy = lot.broker_currency or lot.original_currency or security_ccy
            if not from_ccy or from_ccy == "GBP":
                continue
            key = (from_ccy, "GBP", str(lot.fx_rate_at_acquisition))
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "from_currency": from_ccy,
                "to_currency": "GBP",
                "rate": str(lot.fx_rate_at_acquisition),
                "timestamp_used": (
                    lot.acquisition_date.isoformat() if lot.acquisition_date else None
                ),
                "source": lot.fx_rate_source or "lot_record",
                "origin": "lot_acquisition_record",
            })

    # 3. Implied current FX rates for non-GBP securities (from price data)
    seen_price: set[tuple[str, str, str]] = set()
    for ss in portfolio.securities:
        ccy = ss.security.currency or "GBP"
        if ccy == "GBP":
            continue
        if (
            ss.current_price_native is None
            or ss.current_price_gbp is None
            or ss.current_price_native == _ZERO
        ):
            continue
        implied = _q2(ss.current_price_gbp / ss.current_price_native)
        key = (ccy, "GBP", str(implied))
        if key in seen_price:
            continue
        seen_price.add(key)
        result.append({
            "from_currency": ccy,
            "to_currency": "GBP",
            "rate": str(implied),
            "timestamp_used": (
                ss.price_as_of.isoformat() if ss.price_as_of else None
            ),
            "source": "implied_from_current_price",
            "origin": "current_price_implied",
        })

    return result


def _build_securities(portfolio: PortfolioSummary) -> list[dict[str, Any]]:
    result = []
    for ss in portfolio.securities:
        sec = ss.security
        result.append({
            "security_id": sec.id,
            "ticker": sec.ticker,
            "name": sec.name,
            "currency": sec.currency,
            "latest_price_original_ccy": (
                str(ss.current_price_native) if ss.current_price_native else None
            ),
            "latest_price_gbp": (
                str(ss.current_price_gbp) if ss.current_price_gbp else None
            ),
            "price_timestamp": (
                ss.price_as_of.isoformat() if ss.price_as_of else None
            ),
        })
    return result


def _build_lots(portfolio: PortfolioSummary) -> list[dict[str, Any]]:
    result = []
    for ss in portfolio.securities:
        for ls in ss.active_lots:
            lot = ls.lot
            result.append({
                "lot_id": lot.id,
                "security_id": lot.security_id,
                "scheme": lot.scheme_type,
                "acquisition_date": lot.acquisition_date.isoformat(),
                "quantity": lot.quantity,
                "quantity_remaining": lot.quantity_remaining,
                "original_currency": lot.original_currency,
                "acquisition_price_original_ccy": lot.acquisition_price_original_ccy,
                "acquisition_price_gbp": lot.acquisition_price_gbp,
                "true_cost_per_share_gbp": lot.true_cost_per_share_gbp,
                "fmv_at_acquisition_gbp": lot.fmv_at_acquisition_gbp,
                "is_isa": lot.scheme_type == "ISA",
                "is_restricted": ls.sellability_status == SELLABILITY_LOCKED,
                "vesting_date": (
                    lot.acquisition_date.isoformat()
                    if lot.scheme_type == "RSU"
                    else None
                ),
                "forfeiture_window_end_date": (
                    lot.forfeiture_period_end.isoformat()
                    if lot.forfeiture_period_end
                    else None
                ),
                "matched_shares_quantity": None,
                "matching_lot_id": lot.matching_lot_id,
                "tax_year": lot.tax_year,
                "grant_id": lot.grant_id,
                "broker_currency": lot.broker_currency,
                "fx_rate_at_acquisition": lot.fx_rate_at_acquisition,
                "fx_rate_source": lot.fx_rate_source,
                "import_source": lot.import_source,
                "external_id": lot.external_id,
                "notes": lot.notes,
            })
    return result


# ---------------------------------------------------------------------------
# Per-lot employment tax breakdown
# ---------------------------------------------------------------------------

def _employment_tax_breakdown_espp(
    lot: Lot,
    qty: Decimal,
    price_per_share: Decimal,
    today: date,
    rates: object,
    emp_ctx: EmploymentTaxContext,
) -> tuple[Decimal, Decimal, Decimal, Decimal, list[dict], list[dict]]:
    """
    Compute IT/NI/SL breakdown for ESPP or ESPP_PLUS lot.

    Returns (it, nic, sl, total, steps, bracket_entries).
    """
    est: EmploymentTaxEstimate = estimate_employment_tax_for_lot(
        lot=lot,
        quantity=qty,
        event_date=today,
        disposal_price_per_share_gbp=price_per_share,
        rates=rates,
        context=emp_ctx,
    )
    it_c = est.est_it_gbp
    nic_c = est.est_ni_gbp
    sl_c = est.est_sl_gbp
    total = est.est_total_gbp
    steps: list[dict] = []
    brackets: list[dict] = []
    cat = est.holding_period_category

    zero_categories = {
        "ESPP_ZERO": "ESPP: partnership shares — zero disposal employment tax per HMRC SIP.",
        "FIVE_PLUS_YEARS": "ESPP_PLUS held 5+ years — exempt from IT/NI/SL on disposal.",
        "FORFEITED_MATCHED_UNDER_183D": (
            "ESPP_PLUS matched share inside 6-month forfeiture window — "
            "lot is forfeited on sale, not taxed."
        ),
        "NO_QUANTITY": "Zero quantity — no tax computed.",
        "UNSUPPORTED_SCHEME": f"Scheme {lot.scheme_type} not handled by employment-tax engine.",
    }
    if cat in zero_categories:
        steps.append({
            "name": "employment_tax",
            "formula": f"0 ({zero_categories[cat]})",
            "inputs": {},
            "output": "0.00",
            "rounding_applied": None,
        })
        return it_c, nic_c, sl_c, total, steps, brackets

    # Under three years or three-to-five years
    if est.income_taxable_base_gbp > _ZERO:
        steps.append({
            "name": "income_tax_component",
            "formula": "income_taxable_base_gbp × marginal_income_tax_rate",
            "inputs": {
                "income_taxable_base_gbp": str(est.income_taxable_base_gbp),
                "marginal_income_tax_rate": str(rates.income_tax),
                "holding_period_category": cat,
            },
            "output": str(it_c),
            "rounding_applied": "2dp ROUND_HALF_UP",
        })
        brackets.append({
            "tax_type": "income_tax",
            "band_name": f"marginal_rate_{cat}",
            "threshold_range_gbp": None,
            "rate": str(rates.income_tax),
            "taxable_amount_gbp": str(est.income_taxable_base_gbp),
            "tax_due_gbp": str(it_c),
            "calculation_detail": (
                f"ESPP_PLUS holding period: {cat}. "
                f"Income base: {'min(removal value, award FMV)' if cat == 'THREE_TO_FIVE_YEARS' else 'full removal value'}."
            ),
        })
    if est.ni_base_gbp > _ZERO:
        steps.append({
            "name": "nic_component",
            "formula": "ni_base_gbp × marginal_ni_rate",
            "inputs": {
                "ni_base_gbp": str(est.ni_base_gbp),
                "marginal_ni_rate": str(rates.national_insurance),
            },
            "output": str(nic_c),
            "rounding_applied": "2dp ROUND_HALF_UP",
        })
        brackets.append({
            "tax_type": "nic",
            "band_name": "marginal_rate",
            "threshold_range_gbp": None,
            "rate": str(rates.national_insurance),
            "taxable_amount_gbp": str(est.ni_base_gbp),
            "tax_due_gbp": str(nic_c),
            "calculation_detail": "NI base = full removal value for UNDER_THREE_YEARS holdings.",
        })
    if est.student_loan_base_gbp > _ZERO:
        steps.append({
            "name": "student_loan_component",
            "formula": "student_loan_base_gbp × marginal_sl_rate",
            "inputs": {
                "student_loan_base_gbp": str(est.student_loan_base_gbp),
                "marginal_sl_rate": str(rates.student_loan),
            },
            "output": str(sl_c),
            "rounding_applied": "2dp ROUND_HALF_UP",
        })
        brackets.append({
            "tax_type": "student_loan",
            "band_name": "marginal_rate",
            "threshold_range_gbp": None,
            "rate": str(rates.student_loan),
            "taxable_amount_gbp": str(est.student_loan_base_gbp),
            "tax_due_gbp": str(sl_c),
            "calculation_detail": "SL base = NI-liable amount (PAYE earnings treatment).",
        })
    if not steps:
        # Both bases were zero but not in a named zero-category
        steps.append({
            "name": "employment_tax",
            "formula": f"0 (all tax bases resolved to zero for {cat})",
            "inputs": {},
            "output": "0.00",
            "rounding_applied": None,
        })
    return it_c, nic_c, sl_c, total, steps, brackets


def _employment_tax_breakdown_sip(
    lot: Lot,
    qty: Decimal,
    price_per_share: Decimal,
    today: date,
    rates: object,
) -> tuple[Decimal, Decimal, Decimal, Decimal, list[dict], list[dict]]:
    """
    Compute IT/NI/SL breakdown for SIP_PARTNERSHIP / SIP_MATCHING / SIP_DIVIDEND lots.

    Returns (it, nic, sl, total, steps, bracket_entries).
    """
    share_type = _SIP_SHARE_TYPE_MAP[lot.scheme_type]
    fmv_per_share = Decimal(
        lot.fmv_at_acquisition_gbp or lot.acquisition_price_gbp
    )
    gross_salary = (
        qty * Decimal(lot.acquisition_price_gbp)
        if share_type == SIPShareType.PARTNERSHIP
        else _ZERO
    )
    holding = SIPHolding(
        lot_id=0,
        share_type=share_type,
        acquisition_date=lot.acquisition_date,
        quantity=qty,
        acquisition_market_value_gbp=fmv_per_share,
        gross_salary_deducted_gbp=gross_salary,
    )
    event = SIPEvent(
        event_type=SIPEventType.IN_PLAN_SALE,
        event_date=today,
        holding=holding,
        quantity=qty,
        market_value_per_share_gbp=price_per_share,
    )
    sip_result = process_sip_event(event)
    it_c = _q2(sip_result.income_taxable_gbp * rates.income_tax)
    nic_c = _q2(sip_result.ni_liable_gbp * rates.national_insurance)
    sl_c = _q2(sip_result.ni_liable_gbp * rates.student_loan)
    total = it_c + nic_c + sl_c
    steps: list[dict] = []
    brackets: list[dict] = []
    cat = sip_result.holding_period_category.value

    if it_c > _ZERO:
        steps.append({
            "name": "income_tax_component",
            "formula": "income_taxable_gbp × marginal_income_tax_rate",
            "inputs": {
                "income_taxable_gbp": str(sip_result.income_taxable_gbp),
                "marginal_income_tax_rate": str(rates.income_tax),
                "sip_holding_period": cat,
            },
            "output": str(it_c),
            "rounding_applied": "2dp ROUND_HALF_UP",
        })
        brackets.append({
            "tax_type": "income_tax",
            "band_name": f"marginal_rate_{cat}",
            "threshold_range_gbp": None,
            "rate": str(rates.income_tax),
            "taxable_amount_gbp": str(sip_result.income_taxable_gbp),
            "tax_due_gbp": str(it_c),
            "calculation_detail": f"SIP {lot.scheme_type} holding period: {cat}.",
        })
    if nic_c > _ZERO:
        steps.append({
            "name": "nic_component",
            "formula": "ni_liable_gbp × marginal_ni_rate",
            "inputs": {
                "ni_liable_gbp": str(sip_result.ni_liable_gbp),
                "marginal_ni_rate": str(rates.national_insurance),
            },
            "output": str(nic_c),
            "rounding_applied": "2dp ROUND_HALF_UP",
        })
        brackets.append({
            "tax_type": "nic",
            "band_name": "marginal_rate",
            "threshold_range_gbp": None,
            "rate": str(rates.national_insurance),
            "taxable_amount_gbp": str(sip_result.ni_liable_gbp),
            "tax_due_gbp": str(nic_c),
            "calculation_detail": "NI base = ni_liable_gbp from SIP rules.",
        })
    if sl_c > _ZERO:
        steps.append({
            "name": "student_loan_component",
            "formula": "ni_liable_gbp × marginal_sl_rate",
            "inputs": {
                "ni_liable_gbp": str(sip_result.ni_liable_gbp),
                "marginal_sl_rate": str(rates.student_loan),
            },
            "output": str(sl_c),
            "rounding_applied": "2dp ROUND_HALF_UP",
        })
        brackets.append({
            "tax_type": "student_loan",
            "band_name": "marginal_rate",
            "threshold_range_gbp": None,
            "rate": str(rates.student_loan),
            "taxable_amount_gbp": str(sip_result.ni_liable_gbp),
            "tax_due_gbp": str(sl_c),
            "calculation_detail": "SL base = NI-liable amount (PAYE earnings treatment).",
        })
    if not steps:
        steps.append({
            "name": "employment_tax",
            "formula": f"0 (SIP {lot.scheme_type} — all bases zero for holding period {cat})",
            "inputs": {},
            "output": "0.00",
            "rounding_applied": None,
        })
    return it_c, nic_c, sl_c, total, steps, brackets


# ---------------------------------------------------------------------------
# Per-lot calculations + tax brackets
# ---------------------------------------------------------------------------

def _build_per_lot_calculations(
    portfolio: PortfolioSummary,
    settings: AppSettings | None,
    today: date,
    tax_year: str,
    all_lots_by_id: dict[str, Lot],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Return (per_lot_calcs, tax_brackets_used).

    per_lot_calcs : one entry per lot in the portfolio.
    tax_brackets_used : one entry per (lot, tax_type) where tax > 0.
    """
    marginal_rates = None
    marginal_cgt_rate_val: Decimal | None = None
    emp_ctx: EmploymentTaxContext | None = None

    if settings is not None:
        try:
            ctx = TaxContext(
                tax_year=tax_year,
                gross_employment_income=settings.default_gross_income,
                pension_sacrifice=settings.default_pension_sacrifice,
                other_income=settings.default_other_income,
                student_loan_plan=settings.default_student_loan_plan,
            )
            marginal_rates = get_marginal_rates(ctx)
            marginal_cgt_rate_val = _compute_marginal_cgt_rate(settings, today)
        except Exception:
            pass
        emp_ctx = EmploymentTaxContext(lots_by_id=all_lots_by_id)

    per_lot_calcs: list[dict[str, Any]] = []
    tax_brackets: list[dict[str, Any]] = []

    for ss in portfolio.securities:
        current_price = ss.current_price_gbp  # May be None if no price data

        for ls in ss.active_lots:
            lot = ls.lot
            qty = ls.quantity_remaining
            cost_basis = ls.cost_basis_total_gbp
            true_cost = ls.true_cost_total_gbp
            gross_mv = ls.market_value_gbp
            is_sellable = ls.sellability_status != SELLABILITY_LOCKED

            it_c = _ZERO
            nic_c = _ZERO
            sl_c = _ZERO
            emp_tax_total = _ZERO
            cgt_estimate = _ZERO
            net_liq: Decimal | None = None
            steps: list[dict[str, Any]] = []

            # ── Step: cost basis ────────────────────────────────────────────
            steps.append({
                "name": "cost_basis",
                "formula": "quantity_remaining × acquisition_price_gbp",
                "inputs": {
                    "quantity_remaining": str(qty),
                    "acquisition_price_gbp": lot.acquisition_price_gbp,
                },
                "output": _fmt(cost_basis),
                "rounding_applied": None,
            })

            # ── Step: true economic cost ────────────────────────────────────
            steps.append({
                "name": "true_economic_cost",
                "formula": "quantity_remaining × true_cost_per_share_gbp",
                "inputs": {
                    "quantity_remaining": str(qty),
                    "true_cost_per_share_gbp": lot.true_cost_per_share_gbp,
                },
                "output": _fmt(true_cost),
                "rounding_applied": None,
            })

            if gross_mv is not None and current_price is not None:
                # ── Step: gross market value ────────────────────────────────
                steps.append({
                    "name": "gross_market_value",
                    "formula": "quantity_remaining × current_price_gbp",
                    "inputs": {
                        "quantity_remaining": str(qty),
                        "current_price_gbp": str(current_price),
                    },
                    "output": _fmt(gross_mv),
                    "rounding_applied": "2dp ROUND_HALF_UP",
                })

                # ── Step: unrealised gain ───────────────────────────────────
                steps.append({
                    "name": "unrealised_gain_cgt",
                    "formula": "gross_market_value - cost_basis",
                    "inputs": {
                        "gross_market_value": _fmt(gross_mv),
                        "cost_basis": _fmt(cost_basis),
                    },
                    "output": _fmt(gross_mv - cost_basis),
                    "rounding_applied": None,
                })

                # ── Tax estimates ───────────────────────────────────────────
                if lot.scheme_type == "ISA":
                    # ISA: fully exempt
                    net_liq = gross_mv
                    steps.append({
                        "name": "employment_tax",
                        "formula": "0 (ISA is tax-exempt)",
                        "inputs": {},
                        "output": "0.00",
                        "rounding_applied": None,
                    })
                    steps.append({
                        "name": "cgt",
                        "formula": "0 (ISA is tax-exempt)",
                        "inputs": {},
                        "output": "0.00",
                        "rounding_applied": None,
                    })
                    steps.append({
                        "name": "net_liquidation_value",
                        "formula": "gross_market_value (no taxes for ISA)",
                        "inputs": {"gross_market_value": _fmt(gross_mv)},
                        "output": _fmt(net_liq),
                        "rounding_applied": "2dp ROUND_HALF_UP",
                    })

                elif not is_sellable:
                    unlock = (
                        ls.sellability_unlock_date.isoformat()
                        if ls.sellability_unlock_date
                        else "unknown"
                    )
                    steps.append({
                        "name": "employment_tax",
                        "formula": f"N/A — lot locked until {unlock}",
                        "inputs": {},
                        "output": None,
                        "rounding_applied": None,
                    })

                elif settings is None or marginal_rates is None:
                    steps.append({
                        "name": "employment_tax",
                        "formula": "N/A — income settings not configured",
                        "inputs": {},
                        "output": None,
                        "rounding_applied": None,
                    })

                else:
                    # ── Employment tax breakdown ────────────────────────────
                    if lot.scheme_type in ("ESPP", "ESPP_PLUS"):
                        it_c, nic_c, sl_c, emp_tax_total, lot_steps, lot_brackets = (
                            _employment_tax_breakdown_espp(
                                lot, qty, current_price, today,
                                marginal_rates, emp_ctx,
                            )
                        )
                    elif lot.scheme_type in _SIP_SHARE_TYPE_MAP:
                        it_c, nic_c, sl_c, emp_tax_total, lot_steps, lot_brackets = (
                            _employment_tax_breakdown_sip(
                                lot, qty, current_price, today, marginal_rates,
                            )
                        )
                    else:
                        # RSU, BROKERAGE: no employment tax on disposal
                        lot_steps = [{
                            "name": "employment_tax",
                            "formula": (
                                f"0 ({lot.scheme_type}: no employment tax on disposal)"
                            ),
                            "inputs": {},
                            "output": "0.00",
                            "rounding_applied": None,
                        }]
                        lot_brackets = []

                    steps.extend(lot_steps)
                    tax_brackets.extend(
                        {**b, "lot_id": lot.id} for b in lot_brackets
                    )

                    # ── CGT estimate ────────────────────────────────────────
                    if marginal_cgt_rate_val is not None:
                        gain_for_cgt = max(_ZERO, gross_mv - cost_basis)
                        cgt_estimate = _q2(gain_for_cgt * marginal_cgt_rate_val)
                        steps.append({
                            "name": "cgt_estimate",
                            "formula": (
                                "max(0, gross_market_value - cost_basis) × marginal_cgt_rate. "
                                "NOTE: annual exempt amount (AEA) not deducted at per-lot level."
                            ),
                            "inputs": {
                                "gross_market_value": _fmt(gross_mv),
                                "cost_basis": _fmt(cost_basis),
                                "marginal_cgt_rate": str(marginal_cgt_rate_val),
                            },
                            "output": str(cgt_estimate),
                            "rounding_applied": "2dp ROUND_HALF_UP",
                        })
                        if gain_for_cgt > _ZERO:
                            tax_brackets.append({
                                "lot_id": lot.id,
                                "tax_type": "cgt",
                                "band_name": "marginal_cgt_rate",
                                "threshold_range_gbp": None,
                                "rate": str(marginal_cgt_rate_val),
                                "taxable_amount_gbp": _fmt(gain_for_cgt),
                                "tax_due_gbp": str(cgt_estimate),
                                "calculation_detail": (
                                    f"CGT marginal rate {float(marginal_cgt_rate_val)*100:.0f}% "
                                    f"applied to unrealised gain. "
                                    f"AEA not deducted at per-lot level."
                                ),
                            })

                    # ── Net liquidation: match portfolio display ─────────────
                    # Portfolio est_net_proceeds_gbp = market_value - employment_tax
                    net_liq = ls.est_net_proceeds_gbp
                    if net_liq is None:
                        net_liq = _q2(gross_mv - emp_tax_total)

                    steps.append({
                        "name": "net_liquidation_value",
                        "formula": "gross_market_value - employment_tax_if_sold_today",
                        "inputs": {
                            "gross_market_value": _fmt(gross_mv),
                            "employment_tax_if_sold_today": _fmt(emp_tax_total),
                        },
                        "output": _fmt(net_liq),
                        "rounding_applied": "2dp ROUND_HALF_UP",
                    })

            # ── Forfeitable value ───────────────────────────────────────────
            forfeitable_value = _ZERO
            if (
                ls.forfeiture_risk is not None
                and ls.forfeiture_risk.in_window
                and lot.matching_lot_id is not None
                and gross_mv is not None
            ):
                forfeitable_value = _q2(gross_mv)

            # ── Assemble per-lot entry ──────────────────────────────────────
            has_price = gross_mv is not None
            has_tax_est = has_price and is_sellable and settings is not None and marginal_rates is not None

            per_lot_calcs.append({
                "lot_id": lot.id,
                "security_id": lot.security_id,
                "scheme": lot.scheme_type,
                "quantity": str(qty),
                "cost_basis_gbp": _fmt(cost_basis),
                "true_economic_cost_gbp": _fmt(true_cost),
                "gross_market_value_gbp": _fmt(gross_mv),
                "unrealised_gain_gbp": (
                    _fmt(gross_mv - cost_basis) if has_price else None
                ),
                "employment_tax_if_sold_today_gbp": (
                    _fmt(emp_tax_total)
                    if has_tax_est or lot.scheme_type == "ISA"
                    else None
                ),
                "income_tax_component_gbp": (
                    _fmt(it_c)
                    if has_tax_est or lot.scheme_type == "ISA"
                    else None
                ),
                "nic_component_gbp": (
                    _fmt(nic_c)
                    if has_tax_est or lot.scheme_type == "ISA"
                    else None
                ),
                "student_loan_component_gbp": (
                    _fmt(sl_c)
                    if has_tax_est or lot.scheme_type == "ISA"
                    else None
                ),
                "cgt_if_sold_today_gbp": (
                    str(cgt_estimate)
                    if has_tax_est or lot.scheme_type == "ISA"
                    else None
                ),
                "net_liquidation_value_today_gbp": (
                    _fmt(net_liq) if net_liq is not None else None
                ),
                "forfeitable_value_gbp": str(forfeitable_value),
                "is_sellable_today": is_sellable,
                "calculation_steps": steps,
            })

    return per_lot_calcs, tax_brackets


# ---------------------------------------------------------------------------
# Portfolio aggregates
# ---------------------------------------------------------------------------

def _build_aggregates(
    portfolio: PortfolioSummary,
    per_lot_calcs: list[dict[str, Any]],
) -> dict[str, Any]:
    total_cost_basis = _ZERO
    total_true_cost = _ZERO
    total_gross_mv = _ZERO
    total_emp_tax = _ZERO
    total_it = _ZERO
    total_nic = _ZERO
    total_sl = _ZERO
    total_cgt = _ZERO
    total_net_liq = _ZERO
    total_forfeiture = _ZERO
    # Sellable / blocked split — computed from per-lot is_sellable_today flag
    total_sellable_mv = _ZERO
    total_blocked_mv = _ZERO
    has_mv_data = False
    # Sellable-only net liquidity and true cost (for net gain calculation)
    total_sellable_net_liq = _ZERO
    total_sellable_true_cost = _ZERO
    has_sellable_net_liq = False
    mv_by_security: dict[str, Decimal] = {}
    mv_by_scheme: dict[str, Decimal] = {}

    for plc in per_lot_calcs:
        cb = Decimal(plc["cost_basis_gbp"])
        tc = Decimal(plc["true_economic_cost_gbp"])
        total_cost_basis += cb
        total_true_cost += tc

        mv_str = plc["gross_market_value_gbp"]
        if mv_str is not None:
            mv = Decimal(mv_str)
            total_gross_mv += mv
            has_mv_data = True
            sid = plc["security_id"]
            mv_by_security[sid] = mv_by_security.get(sid, _ZERO) + mv
            scheme = plc["scheme"]
            mv_by_scheme[scheme] = mv_by_scheme.get(scheme, _ZERO) + mv
            if plc["is_sellable_today"]:
                total_sellable_mv += mv
            else:
                total_blocked_mv += mv

        if plc["employment_tax_if_sold_today_gbp"] is not None:
            total_emp_tax += Decimal(plc["employment_tax_if_sold_today_gbp"])
        if plc["income_tax_component_gbp"] is not None:
            total_it += Decimal(plc["income_tax_component_gbp"])
        if plc["nic_component_gbp"] is not None:
            total_nic += Decimal(plc["nic_component_gbp"])
        if plc["student_loan_component_gbp"] is not None:
            total_sl += Decimal(plc["student_loan_component_gbp"])
        if plc["cgt_if_sold_today_gbp"] is not None:
            total_cgt += Decimal(plc["cgt_if_sold_today_gbp"])

        nl_str = plc["net_liquidation_value_today_gbp"]
        if nl_str is not None:
            total_net_liq += Decimal(nl_str)
            if plc["is_sellable_today"]:
                total_sellable_net_liq += Decimal(nl_str)
                has_sellable_net_liq = True

        if plc["is_sellable_today"]:
            total_sellable_true_cost += tc

        if plc["forfeitable_value_gbp"]:
            total_forfeiture += Decimal(plc["forfeitable_value_gbp"])

    # Net gain if sold today: sellable net liquidity − sellable true economic cost
    net_gain_if_sold: Decimal | None = None
    if has_sellable_net_liq:
        net_gain_if_sold = _q2(total_sellable_net_liq - total_sellable_true_cost)

    # Reconciliation checks — per_lot_sum vs portfolio_aggregate.
    # Both sides are derived from the same per_lot_calcs data so difference must be 0.00.
    recon_cb_sum = sum(
        (Decimal(plc["cost_basis_gbp"]) for plc in per_lot_calcs), _ZERO
    )
    recon_tc_sum = sum(
        (Decimal(plc["true_economic_cost_gbp"]) for plc in per_lot_calcs), _ZERO
    )
    recon_mv_sum = sum(
        (
            Decimal(plc["gross_market_value_gbp"])
            for plc in per_lot_calcs
            if plc["gross_market_value_gbp"] is not None
        ),
        _ZERO,
    )
    recon_et_sum = sum(
        (
            Decimal(plc["employment_tax_if_sold_today_gbp"])
            for plc in per_lot_calcs
            if plc["employment_tax_if_sold_today_gbp"] is not None
        ),
        _ZERO,
    )
    recon_nl_sum = sum(
        (
            Decimal(plc["net_liquidation_value_today_gbp"])
            for plc in per_lot_calcs
            if plc["net_liquidation_value_today_gbp"] is not None
        ),
        _ZERO,
    )
    cb_diff = _q2(recon_cb_sum - total_cost_basis)
    tc_diff = _q2(recon_tc_sum - total_true_cost)
    mv_diff = _q2(recon_mv_sum - total_gross_mv)
    et_diff = _q2(recon_et_sum - total_emp_tax)
    nl_diff = _q2(recon_nl_sum - total_net_liq)

    reconciliation_checks = {
        "cost_basis": {
            "per_lot_sum": _fmt(recon_cb_sum),
            "portfolio_total": _fmt(total_cost_basis),
            "difference": _fmt(cb_diff),
            "pass": cb_diff == _ZERO,
        },
        "true_cost": {
            "per_lot_sum": _fmt(recon_tc_sum),
            "portfolio_total": _fmt(total_true_cost),
            "difference": _fmt(tc_diff),
            "pass": tc_diff == _ZERO,
        },
        "market_value": {
            "per_lot_sum": _fmt(recon_mv_sum),
            "portfolio_total": _fmt(total_gross_mv),
            "difference": _fmt(mv_diff),
            "pass": mv_diff == _ZERO,
        },
        "employment_tax": {
            "per_lot_sum": _fmt(recon_et_sum),
            "portfolio_total": _fmt(total_emp_tax),
            "difference": _fmt(et_diff),
            "pass": et_diff == _ZERO,
        },
        "net_liquidity": {
            "per_lot_sum": _fmt(recon_nl_sum),
            "portfolio_total": _fmt(total_net_liq),
            "difference": _fmt(nl_diff),
            "pass": nl_diff == _ZERO,
        },
    }

    ticker_by_id = {ss.security.id: ss.security.ticker for ss in portfolio.securities}

    concentration_by_security = []
    concentration_by_scheme = []
    if total_gross_mv > _ZERO:
        for sid, mv in sorted(mv_by_security.items(), key=lambda x: -x[1]):
            pct = _q2((mv / total_gross_mv) * Decimal("100"))
            concentration_by_security.append({
                "security_id": sid,
                "ticker": ticker_by_id.get(sid, "?"),
                "pct_of_market_value": str(pct),
            })
        for scheme, mv in sorted(mv_by_scheme.items(), key=lambda x: -x[1]):
            pct = _q2((mv / total_gross_mv) * Decimal("100"))
            concentration_by_scheme.append({
                "scheme": scheme,
                "pct_of_market_value": str(pct),
            })

    return {
        "total_cost_basis_gbp": _fmt(total_cost_basis),
        "total_true_economic_cost_gbp": _fmt(total_true_cost),
        "total_gross_market_value_gbp": (
            _fmt(total_gross_mv) if has_mv_data else None
        ),
        "sellable_market_value_gbp": (
            _fmt(total_sellable_mv) if has_mv_data else None
        ),
        "blocked_market_value_gbp": (
            _fmt(total_blocked_mv) if has_mv_data else None
        ),
        "total_employment_tax_gbp": _fmt(total_emp_tax),
        "total_income_tax_gbp": _fmt(total_it),
        "total_nic_gbp": _fmt(total_nic),
        "total_student_loan_gbp": _fmt(total_sl),
        "total_cgt_gbp": _fmt(total_cgt),
        "total_net_liquidation_value_gbp": (
            _fmt(total_net_liq) if total_net_liq > _ZERO else None
        ),
        "sellable_net_liquidity_gbp": (
            _fmt(total_sellable_net_liq) if has_sellable_net_liq else None
        ),
        "sellable_true_economic_cost_gbp": (
            _fmt(total_sellable_true_cost) if has_sellable_net_liq else None
        ),
        "net_gain_if_sold_today_gbp": (
            _fmt(net_gain_if_sold) if net_gain_if_sold is not None else None
        ),
        "total_forfeiture_risk_gbp": _fmt(total_forfeiture),
        "concentration_by_security": concentration_by_security,
        "concentration_by_scheme": concentration_by_scheme,
        "reconciliation_checks": reconciliation_checks,
    }


# ---------------------------------------------------------------------------
# Additional diagnostics
# ---------------------------------------------------------------------------

def _build_diagnostics(
    portfolio: PortfolioSummary,
    per_lot_calcs: list[dict[str, Any]],
    aggregates: dict[str, Any],
) -> dict[str, Any] | None:
    diags: dict[str, Any] = {}

    # 1. Cost basis reconciliation
    export_cost = Decimal(aggregates["total_cost_basis_gbp"])
    service_cost = portfolio.total_cost_basis_gbp
    cost_drift = abs(export_cost - service_cost)
    diags["reconciliation_cost_basis"] = {
        "name": "cost_basis_reconciliation",
        "why_it_matters": (
            "Confirms per-lot cost basis sums match portfolio service total to the penny."
        ),
        "how_computed": (
            "abs(sum(per_lot.cost_basis_gbp) - portfolio_service.total_cost_basis_gbp)"
        ),
        "values": {
            "per_lot_sum_gbp": _fmt(export_cost),
            "portfolio_service_total_gbp": _fmt(service_cost),
            "drift_gbp": _fmt(cost_drift),
            "pass": cost_drift == _ZERO,
        },
    }

    # 2. True cost reconciliation
    export_true = Decimal(aggregates["total_true_economic_cost_gbp"])
    service_true = portfolio.total_true_cost_gbp
    true_drift = abs(export_true - service_true)
    diags["reconciliation_true_cost"] = {
        "name": "true_cost_reconciliation",
        "why_it_matters": (
            "Confirms per-lot true economic cost sums match portfolio service total."
        ),
        "how_computed": (
            "abs(sum(per_lot.true_economic_cost_gbp) - portfolio_service.total_true_cost_gbp)"
        ),
        "values": {
            "per_lot_sum_gbp": _fmt(export_true),
            "portfolio_service_total_gbp": _fmt(service_true),
            "drift_gbp": _fmt(true_drift),
            "pass": true_drift == _ZERO,
        },
    }

    # 3. Missing price warnings
    missing_prices = [
        ss.security.ticker
        for ss in portfolio.securities
        if ss.current_price_gbp is None
    ]
    if missing_prices:
        diags["missing_price_warnings"] = {
            "name": "missing_price_warnings",
            "why_it_matters": (
                "Lots without a current price have no market value or tax estimate."
            ),
            "how_computed": "Securities where current_price_gbp is None.",
            "values": {"tickers_without_price": missing_prices},
        }

    # 4. Stale price warnings
    stale_prices = [
        ss.security.ticker
        for ss in portfolio.securities
        if ss.price_is_stale
    ]
    if stale_prices:
        diags["stale_price_warnings"] = {
            "name": "stale_price_warnings",
            "why_it_matters": "Stale prices reduce accuracy of market value and tax estimates.",
            "how_computed": "Securities where price_is_stale=True.",
            "values": {"tickers_with_stale_price": stale_prices},
        }

    # 5. Stale FX warning
    if portfolio.fx_is_stale:
        diags["stale_fx_warning"] = {
            "name": "stale_fx_warning",
            "why_it_matters": (
                "Stale FX rates reduce accuracy of GBP conversions for non-GBP securities."
            ),
            "how_computed": "portfolio_summary.fx_is_stale == True.",
            "values": {"fx_as_of": portfolio.fx_as_of, "is_stale": True},
        }

    # 6. Lots missing tax estimate
    lots_missing_tax = [
        plc["lot_id"]
        for plc in per_lot_calcs
        if plc["employment_tax_if_sold_today_gbp"] is None and plc["is_sellable_today"]
    ]
    if lots_missing_tax:
        diags["lots_missing_tax_estimate"] = {
            "name": "lots_missing_tax_estimate",
            "why_it_matters": (
                "These sellable lots have no employment tax estimate; "
                "net liquidation value is also unavailable."
            ),
            "how_computed": (
                "Lots where employment_tax_if_sold_today_gbp is null and is_sellable_today is true."
            ),
            "values": {"lot_ids": lots_missing_tax},
        }

    # 7. Forfeiture risk summary
    at_risk_lot_ids = [
        plc["lot_id"]
        for plc in per_lot_calcs
        if Decimal(plc["forfeitable_value_gbp"]) > _ZERO
    ]
    if at_risk_lot_ids:
        total_forfeiture = sum(
            Decimal(plc["forfeitable_value_gbp"]) for plc in per_lot_calcs
        )
        diags["forfeiture_risk_summary"] = {
            "name": "forfeiture_risk_summary",
            "why_it_matters": (
                "ESPP_PLUS matched shares currently inside 6-month forfeiture window. "
                "Selling the linked employee lot would forfeit these shares."
            ),
            "how_computed": (
                "Lots where forfeiture_risk.in_window=True and gross_market_value > 0."
            ),
            "values": {
                "lot_ids_at_risk": at_risk_lot_ids,
                "total_forfeiture_risk_gbp": _fmt(total_forfeiture),
            },
        }

    return diags if diags else None


# ---------------------------------------------------------------------------
# Net Value aggregates
# ---------------------------------------------------------------------------

def _build_net_value_aggregates(
    portfolio: PortfolioSummary,
    per_lot_calcs: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Build the net_value_aggregates section.

    Scope: ALL lots (locked + sellable) contribute gross market value.
    Employment tax is estimated for SELLABLE lots only (locked lots cannot be sold).
    Net Value total = sum(net_value_contribution_gbp):
      - LOCKED lot  → gross_market_value_gbp  (no tax deducted)
      - SELLABLE lot → net_liquidation_value_today_gbp  (after employment tax)
    This matches summary.est_total_net_liquidation_gbp = total_market - employment_tax_sellable.
    """
    total_cost_basis = _ZERO
    total_gross_mv = _ZERO
    total_emp_tax_sellable = _ZERO
    total_nv = _ZERO
    has_mv_data = False
    has_nv_data = False
    locked_lot_ids: list[str] = []
    locked_mv_total = _ZERO
    gross_mv_by_security: dict[str, Decimal] = {}
    emp_tax_by_security: dict[str, Decimal] = {}
    nv_by_security: dict[str, Decimal] = {}

    for plc in per_lot_calcs:
        total_cost_basis += Decimal(plc["cost_basis_gbp"])

        mv_str = plc["gross_market_value_gbp"]
        if mv_str is not None:
            mv = Decimal(mv_str)
            total_gross_mv += mv
            has_mv_data = True
            sid = plc["security_id"]
            gross_mv_by_security[sid] = gross_mv_by_security.get(sid, _ZERO) + mv
            if not plc["is_sellable_today"]:
                locked_lot_ids.append(plc["lot_id"])
                locked_mv_total += mv

        if plc["is_sellable_today"] and plc["employment_tax_if_sold_today_gbp"] is not None:
            et = Decimal(plc["employment_tax_if_sold_today_gbp"])
            total_emp_tax_sellable += et
            sid = plc["security_id"]
            emp_tax_by_security[sid] = emp_tax_by_security.get(sid, _ZERO) + et

        nvc_str = plc.get("net_value_contribution_gbp")
        if nvc_str is not None:
            nvc = Decimal(nvc_str)
            total_nv += nvc
            has_nv_data = True
            sid = plc["security_id"]
            nv_by_security[sid] = nv_by_security.get(sid, _ZERO) + nvc

    # Reconciliation checks against portfolio summary
    reconciliation: dict[str, Any] = {}

    diff_cb = _q2(total_cost_basis - portfolio.total_cost_basis_gbp)
    reconciliation["cost_basis"] = {
        "per_lot_sum_gbp": _fmt(total_cost_basis),
        "summary_value_gbp": _fmt(portfolio.total_cost_basis_gbp),
        "difference": _fmt(diff_cb),
        "pass": diff_cb == _ZERO,
    }

    if portfolio.total_market_value_gbp is not None and has_mv_data:
        diff_mv = _q2(total_gross_mv - portfolio.total_market_value_gbp)
        reconciliation["gross_market_value"] = {
            "per_lot_sum_gbp": _fmt(total_gross_mv),
            "summary_value_gbp": _fmt(portfolio.total_market_value_gbp),
            "difference": _fmt(diff_mv),
            "pass": diff_mv == _ZERO,
        }

    if portfolio.est_total_employment_tax_gbp is not None:
        diff_et = _q2(total_emp_tax_sellable - portfolio.est_total_employment_tax_gbp)
        reconciliation["employment_tax_sellable"] = {
            "per_lot_sum_gbp": _fmt(total_emp_tax_sellable),
            "summary_value_gbp": _fmt(portfolio.est_total_employment_tax_gbp),
            "difference": _fmt(diff_et),
            "pass": diff_et == _ZERO,
        }

    if portfolio.est_total_net_liquidation_gbp is not None and has_nv_data:
        diff_nv = _q2(total_nv - portfolio.est_total_net_liquidation_gbp)
        reconciliation["net_value_total"] = {
            "per_lot_sum_gbp": _fmt(total_nv),
            "summary_value_gbp": _fmt(portfolio.est_total_net_liquidation_gbp),
            "difference": _fmt(diff_nv),
            "pass": diff_nv == _ZERO,
        }

    ticker_by_id = {ss.security.id: ss.security.ticker for ss in portfolio.securities}
    per_security_breakdown = [
        {
            "security_id": ss.security.id,
            "ticker": ticker_by_id.get(ss.security.id, "?"),
            "total_quantity": str(ss.total_quantity),
            "gross_market_value_gbp": _fmt(
                gross_mv_by_security.get(ss.security.id)
            ),
            "cost_basis_gbp": _fmt(ss.total_cost_basis_gbp),
            "unrealised_gain_cgt_gbp": (
                _fmt(ss.unrealised_gain_cgt_gbp)
                if ss.unrealised_gain_cgt_gbp is not None
                else None
            ),
            "est_employment_tax_sellable_gbp": _fmt(
                emp_tax_by_security.get(ss.security.id, _ZERO)
            ),
            "net_value_contribution_gbp": _fmt(
                nv_by_security.get(ss.security.id)
            ),
        }
        for ss in portfolio.securities
        if ss.active_lots
    ]

    return {
        "scope": (
            "ALL lots (locked and unlocked) contribute gross market value to the total. "
            "Employment tax is estimated for SELLABLE lots only. "
            "Net Value total = gross market value (all lots) minus employment tax (sellable lots only). "
            "LOCKED lots contribute their full gross market value with no tax deduction."
        ),
        "total_cost_basis_gbp": _fmt(total_cost_basis),
        "total_gross_market_value_gbp": (
            _fmt(total_gross_mv) if has_mv_data else None
        ),
        "locked_lots_count": len(locked_lot_ids),
        "locked_lot_ids": locked_lot_ids,
        "locked_market_value_gbp": _fmt(locked_mv_total),
        "est_employment_tax_sellable_lots_gbp": _fmt(total_emp_tax_sellable),
        "est_total_net_liquidation_gbp": (
            _fmt(total_nv) if has_nv_data else None
        ),
        "per_security_breakdown": per_security_breakdown,
        "reconciliation_checks": reconciliation,
    }


def _build_net_value_diagnostics(
    portfolio: PortfolioSummary,
    per_lot_calcs: list[dict[str, Any]],
    aggregates: dict[str, Any],
) -> dict[str, Any] | None:
    diags: dict[str, Any] = {}

    # 1. Net Value total reconciliation
    recon = aggregates.get("reconciliation_checks", {})
    nv_recon = recon.get("net_value_total")
    if nv_recon is not None:
        diags["net_value_reconciliation"] = {
            "name": "net_value_total_reconciliation",
            "why_it_matters": (
                "Confirms sum of per-lot net_value_contribution_gbp matches "
                "PortfolioSummary.est_total_net_liquidation_gbp to the penny."
            ),
            "how_computed": (
                "abs(sum(per_lot.net_value_contribution_gbp) - "
                "summary.est_total_net_liquidation_gbp)"
            ),
            "values": nv_recon,
        }

    # 2. Cost basis reconciliation
    cb_recon = recon.get("cost_basis")
    if cb_recon is not None:
        diags["cost_basis_reconciliation"] = {
            "name": "cost_basis_reconciliation",
            "why_it_matters": (
                "Confirms per-lot cost basis sums match portfolio service total."
            ),
            "how_computed": (
                "abs(sum(per_lot.cost_basis_gbp) - summary.total_cost_basis_gbp)"
            ),
            "values": cb_recon,
        }

    # 3. Locked lot summary
    locked_ids = aggregates.get("locked_lot_ids", [])
    if locked_ids:
        diags["locked_lots_summary"] = {
            "name": "locked_lots_summary",
            "why_it_matters": (
                "Locked lots (unvested RSUs, etc.) are included in the gross market value "
                "and net value total at their full gross MV. No employment tax is deducted "
                "for locked lots as they cannot be sold today."
            ),
            "how_computed": "Lots where is_sellable_today=False.",
            "values": {
                "lot_ids": locked_ids,
                "locked_market_value_gbp": aggregates.get("locked_market_value_gbp"),
            },
        }

    # 4. Missing price warnings
    missing_prices = [
        ss.security.ticker
        for ss in portfolio.securities
        if ss.current_price_gbp is None
    ]
    if missing_prices:
        diags["missing_price_warnings"] = {
            "name": "missing_price_warnings",
            "why_it_matters": (
                "Lots without a current price have no market value contribution "
                "to the Net Value total."
            ),
            "how_computed": "Securities where current_price_gbp is None.",
            "values": {"tickers_without_price": missing_prices},
        }

    # 5. Stale price warnings
    stale_prices = [
        ss.security.ticker
        for ss in portfolio.securities
        if ss.price_is_stale
    ]
    if stale_prices:
        diags["stale_price_warnings"] = {
            "name": "stale_price_warnings",
            "why_it_matters": "Stale prices reduce accuracy of net value estimates.",
            "how_computed": "Securities where price_is_stale=True.",
            "values": {"tickers_with_stale_price": stale_prices},
        }

    # 6. Stale FX warning
    if portfolio.fx_is_stale:
        diags["stale_fx_warning"] = {
            "name": "stale_fx_warning",
            "why_it_matters": (
                "Stale FX rates reduce accuracy of GBP conversions for non-GBP securities."
            ),
            "how_computed": "portfolio_summary.fx_is_stale == True.",
            "values": {"fx_as_of": portfolio.fx_as_of, "is_stale": True},
        }

    # 7. Lots missing tax estimate (sellable, have price, but no tax)
    lots_missing_tax = [
        plc["lot_id"]
        for plc in per_lot_calcs
        if (
            plc["is_sellable_today"]
            and plc["gross_market_value_gbp"] is not None
            and plc["employment_tax_if_sold_today_gbp"] is None
        )
    ]
    if lots_missing_tax:
        diags["lots_missing_tax_estimate"] = {
            "name": "lots_missing_tax_estimate",
            "why_it_matters": (
                "These sellable lots have no employment tax estimate. "
                "Their net_value_contribution_gbp is None and they are excluded "
                "from the Net Value total."
            ),
            "how_computed": (
                "Sellable lots where employment_tax_if_sold_today_gbp is null "
                "and gross_market_value_gbp is not null."
            ),
            "values": {"lot_ids": lots_missing_tax},
        }

    # 8. Forfeiture risk summary
    at_risk_lot_ids = [
        plc["lot_id"]
        for plc in per_lot_calcs
        if Decimal(plc["forfeitable_value_gbp"]) > _ZERO
    ]
    if at_risk_lot_ids:
        total_forfeiture = sum(
            Decimal(plc["forfeitable_value_gbp"]) for plc in per_lot_calcs
        )
        diags["forfeiture_risk_summary"] = {
            "name": "forfeiture_risk_summary",
            "why_it_matters": (
                "ESPP_PLUS matched shares currently inside 6-month forfeiture window. "
                "Selling the linked employee lot would forfeit these shares."
            ),
            "how_computed": (
                "Lots where forfeiture_risk.in_window=True and gross_market_value > 0."
            ),
            "values": {
                "lot_ids_at_risk": at_risk_lot_ids,
                "total_forfeiture_risk_gbp": _fmt(total_forfeiture),
            },
        }

    return diags if diags else None


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

class AuditExportService:
    """
    Assembles the portfolio audit export JSON payload.

    Reuses PortfolioService.get_portfolio_summary() internally so every
    calculated value matches exactly what the Portfolio page displays.
    """

    @staticmethod
    def get_portfolio_audit_export(
        settings: AppSettings | None = None,
        db_path: object = None,
        db_encrypted: bool = False,
    ) -> dict[str, Any]:
        """
        Return the full audit export dict.

        Args:
            settings    : AppSettings loaded from the current DB path.
                          When None, tax estimates are omitted from per-lot calcs.
            db_path     : Database file path (for metadata only).
            db_encrypted: Whether the DB is SQLCipher-encrypted (for metadata).

        Returns:
            Dict matching the audit export schema.  All monetary values are
            2dp Decimal strings.  Null fields are present and set to None.
        """
        now_utc = datetime.now(timezone.utc)
        today = now_utc.date()
        tax_year = tax_year_for_date(today)

        # 1. Get portfolio summary via the same path as the Portfolio page
        portfolio = PortfolioService.get_portfolio_summary(settings=settings)

        # 2. Collect all active lot ORM objects for employment-tax context
        all_lots_by_id: dict[str, Lot] = {
            ls.lot.id: ls.lot
            for ss in portfolio.securities
            for ls in ss.active_lots
        }

        # 3. Query raw DB data inside a single read session
        with AppContext.read_session() as sess:
            fx_rates_section = _build_fx_rates(sess, portfolio)

        # 4. Assemble sections
        metadata = _build_metadata(
            now_utc, today, db_path, db_encrypted, settings, tax_year
        )
        tax_settings_section = _build_tax_settings(settings, tax_year)
        securities_section = _build_securities(portfolio)
        lots_section = _build_lots(portfolio)
        per_lot_calcs, tax_brackets = _build_per_lot_calculations(
            portfolio, settings, today, tax_year, all_lots_by_id
        )
        aggregates_section = _build_aggregates(portfolio, per_lot_calcs)
        diagnostics = _build_diagnostics(portfolio, per_lot_calcs, aggregates_section)

        return {
            "metadata": metadata,
            "tax_settings": tax_settings_section,
            "fx_rates": fx_rates_section,
            "securities": securities_section,
            "lots": lots_section,
            "per_lot_calculations": per_lot_calcs,
            "portfolio_aggregates": aggregates_section,
            "tax_brackets_used": tax_brackets,
            "additional_diagnostics": diagnostics,
        }


class NetValueAuditExportService:
    """
    Assembles the Net Value page audit export JSON payload.

    Reuses PortfolioService.get_portfolio_summary() internally so every
    calculated value matches exactly what the Net Value page displays.

    Key difference from AuditExportService:
      Net Value includes ALL lots (locked + sellable) in the gross market value
      total. Employment tax is estimated for SELLABLE lots only. The
      net_value_contribution_gbp field per lot is:
        - LOCKED  → gross_market_value_gbp   (no tax deducted)
        - SELLABLE → net_liquidation_value_today_gbp  (after employment tax)
      sum(net_value_contribution_gbp) reconciles to
      PortfolioSummary.est_total_net_liquidation_gbp.
    """

    @staticmethod
    def get_net_value_audit_export(
        settings: AppSettings | None = None,
        db_path: object = None,
        db_encrypted: bool = False,
    ) -> dict[str, Any]:
        """
        Return the full Net Value audit export dict.

        Schema top-level keys:
          metadata, tax_settings, fx_rates, securities, lots,
          per_lot_calculations, net_value_aggregates, tax_brackets_used,
          additional_diagnostics

        All monetary values are 2dp ROUND_HALF_UP strings.
        """
        now_utc = datetime.now(timezone.utc)
        today = now_utc.date()
        tax_year = tax_year_for_date(today)

        # 1. Get portfolio summary — same call path as the Net Value page
        portfolio = PortfolioService.get_portfolio_summary(settings=settings)

        # 2. Collect all active lot ORM objects for employment-tax context
        all_lots_by_id: dict[str, Lot] = {
            ls.lot.id: ls.lot
            for ss in portfolio.securities
            for ls in ss.active_lots
        }

        # 3. Query raw DB data inside a single read session
        with AppContext.read_session() as sess:
            fx_rates_section = _build_fx_rates(sess, portfolio)

        # 4. Assemble shared sections (identical to portfolio export)
        metadata = _build_metadata(
            now_utc, today, db_path, db_encrypted, settings, tax_year
        )
        # Override page-specific metadata fields
        metadata["page"] = "NET_VALUE"
        metadata["page_description"] = (
            "Hypothetical sell-all breakdown. "
            "All lots (including locked/unvested) are included in the gross total. "
            "Employment tax is estimated for SELLABLE lots only — locked lots "
            "cannot be sold today so no tax is deducted for them. "
            "Net Value total = gross market value (all lots) minus employment tax "
            "(sellable lots only)."
        )
        metadata["scope_notes"] = [
            "LOCKED lots: net_value_contribution_gbp = gross_market_value_gbp "
            "(full gross MV; no employment tax deducted).",
            "SELLABLE lots: net_value_contribution_gbp = net_liquidation_value_today_gbp "
            "(gross MV minus employment tax estimate).",
            "sum(net_value_contribution_gbp) reconciles to "
            "PortfolioSummary.est_total_net_liquidation_gbp.",
            "employment_tax field in net_value_aggregates covers SELLABLE lots only.",
        ]

        tax_settings_section = _build_tax_settings(settings, tax_year)
        securities_section = _build_securities(portfolio)
        lots_section = _build_lots(portfolio)

        # 5. Per-lot calculations — same as portfolio, then enriched with
        #    net_value_contribution_gbp
        per_lot_calcs, tax_brackets = _build_per_lot_calculations(
            portfolio, settings, today, tax_year, all_lots_by_id
        )

        # Enrich each lot entry with Net Value specific contribution field
        for entry in per_lot_calcs:
            if (
                not entry["is_sellable_today"]
                and entry["gross_market_value_gbp"] is not None
            ):
                # Locked lot: contributes full gross MV; no tax deducted
                entry["net_value_contribution_gbp"] = entry["gross_market_value_gbp"]
                entry["net_value_contribution_basis"] = (
                    "LOCKED: full gross market value included "
                    "(no employment tax deducted for locked lots)"
                )
            elif entry["net_liquidation_value_today_gbp"] is not None:
                # Sellable lot: net after employment tax
                entry["net_value_contribution_gbp"] = (
                    entry["net_liquidation_value_today_gbp"]
                )
                entry["net_value_contribution_basis"] = (
                    "SELLABLE: net liquidation value after employment tax"
                )
            else:
                entry["net_value_contribution_gbp"] = None
                entry["net_value_contribution_basis"] = (
                    "No price available"
                    if entry["gross_market_value_gbp"] is None
                    else "Employment-tax estimate unavailable; excluded from total"
                )

        # 6. Net Value specific aggregates
        net_value_aggregates = _build_net_value_aggregates(portfolio, per_lot_calcs)

        # 7. Net Value diagnostics
        diagnostics = _build_net_value_diagnostics(
            portfolio, per_lot_calcs, net_value_aggregates
        )

        return {
            "metadata": metadata,
            "tax_settings": tax_settings_section,
            "fx_rates": fx_rates_section,
            "securities": securities_section,
            "lots": lots_section,
            "per_lot_calculations": per_lot_calcs,
            "net_value_aggregates": net_value_aggregates,
            "tax_brackets_used": tax_brackets,
            "additional_diagnostics": diagnostics,
        }
