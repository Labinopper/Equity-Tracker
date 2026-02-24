"""
Validation report generator for calculation verification.

Design goals:
- Use live database data only.
- Deterministic output (stable ordering, explicit as_of).
- Single shared generator for API and CLI.
- Provide both machine-readable JSON and copy/paste-friendly text output.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..app_context import AppContext
from ..core.lot_engine.fifo import LotForFIFO, SIPTaxEstimate, allocate_fifo
from ..core.tax_engine import TaxContext, get_bands, get_marginal_rates, tax_year_for_date
from ..db.models import Lot, PriceHistory, Security, VALID_SCHEME_TYPES
from ..db.repository import LotRepository, SecurityRepository
from ..settings import AppSettings
from .portfolio_service import (
    _SIP_LIKE_SCHEMES,
    _build_sip_tax_estimates,
    _espp_plus_employee_lot_ids,
    _estimate_sell_all_employment_tax,
    _forfeiture_risk_for_lot,
    _is_lot_sellable_on,
    _lot_to_fifo,
    _matched_employee_lot_ids_at_risk,
    _sellability_for_lot,
)
from .price_service import _parse_fx_timestamp, _parse_sheets_timestamp

_Q2 = Decimal("0.01")
_EPSILON = Decimal("0.01")


def _q2(value: Decimal) -> Decimal:
    return value.quantize(_Q2, rounding=ROUND_HALF_UP)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _to_utc(value).isoformat().replace("+00:00", "Z")


def _str_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _raw_rounded(value: Decimal | None) -> dict[str, str | None]:
    if value is None:
        return {"raw": None, "rounded_2dp": None}
    return {"raw": str(value), "rounded_2dp": str(_q2(value))}


def _dt_to_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _is_invalid_decimal(value: Decimal) -> bool:
    return value.is_nan() or value.is_infinite()


def _repo_root() -> Path:
    # .../equity_tracker/src/services/validation_report_service.py -> .../equity_tracker
    return Path(__file__).resolve().parents[2]


def _git_commit_hash() -> str | None:
    try:
        cp = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if cp.returncode != 0:
        return None
    value = cp.stdout.strip()
    return value or None


def _pyproject_version() -> str | None:
    pyproject = _repo_root() / "pyproject.toml"
    if not pyproject.exists():
        return None
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None

    in_project = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            continue
        if in_project and line.startswith("version"):
            _, rhs = line.split("=", 1)
            return rhs.strip().strip('"').strip("'")
    return None


def _api_version() -> str | None:
    try:
        from ..api.app import app

        return app.version
    except Exception:
        return None


def _select_price_row_as_of(
    session: Session,
    security_id: str,
    as_of_utc: datetime,
) -> PriceHistory | None:
    """
    Pick latest price row with price_date <= as_of.date().

    If multiple rows exist on as_of.date, prefer one whose fetched_at is <= as_of.
    """
    as_of_date = as_of_utc.date()
    as_of_naive = as_of_utc.replace(tzinfo=None)

    rows = list(
        session.scalars(
            select(PriceHistory)
            .where(
                PriceHistory.security_id == security_id,
                PriceHistory.price_date <= as_of_date,
            )
            .order_by(
                PriceHistory.price_date.desc(),
                PriceHistory.fetched_at.desc(),
                PriceHistory.created_at.desc(),
                PriceHistory.id.asc(),
            )
        )
    )

    if not rows:
        return None

    for row in rows:
        if row.price_date < as_of_date:
            return row
        fetched_at = _dt_to_naive_utc(row.fetched_at)
        if fetched_at is None or fetched_at <= as_of_naive:
            return row

    # All rows on cutoff date were fetched after as_of time; use the latest <= date.
    return rows[-1]


def _security_sort_key(security: Security) -> tuple[str, str]:
    return (security.ticker.upper(), security.id)


def _lot_sort_key(lot: Lot) -> tuple[date, str]:
    return (lot.acquisition_date, lot.id)


def _coerce_positive_int(value: int | None) -> int | None:
    if value is None:
        return None
    if value <= 0:
        raise ValueError("limit_lots must be > 0 when provided.")
    return value


def _resolve_security_selection(
    all_securities: list[Security],
    security_filter: str | None,
) -> list[Security]:
    ordered = sorted(all_securities, key=_security_sort_key)
    if not security_filter:
        return ordered

    needle = security_filter.strip()
    if not needle:
        return ordered

    by_id = [s for s in ordered if s.id == needle]
    if by_id:
        return by_id

    upper = needle.upper()
    by_ticker = [s for s in ordered if s.ticker.upper() == upper]
    if by_ticker:
        return by_ticker

    raise ValueError(
        f"security_id filter {security_filter!r} did not match any security id or ticker."
    )


def _scheme_rules_snapshot() -> dict[str, Any]:
    return {
        "supported_scheme_types_verbatim": list(VALID_SCHEME_TYPES),
        "sip_like_schemes_verbatim": sorted(_SIP_LIKE_SCHEMES),
        "rules": {
            "RSU": {
                "sellability": "Locked until acquisition_date (used as vest date).",
                "forfeiture_window": None,
                "tax_treatment_disposal": (
                    "No disposal employment tax in current model. Tax recognized at vest."
                ),
                "holding_period_logic": "No 3y/5y window logic in disposal tax path.",
                "taxes_applied": ["IT at vest (outside disposal)", "NI at vest (outside disposal)"],
                "code_constants": ["_is_lot_sellable_on: RSU lock check"],
            },
            "ESPP": {
                "sellability": "Immediately sellable (no lock in current model).",
                "forfeiture_window": None,
                "tax_treatment_disposal": "Employment tax estimate returns zero (ESPP_ZERO).",
                "holding_period_logic": "No disposal tax window for ESPP in employment_tax_engine.",
                "taxes_applied": [],
                "code_constants": ["estimate_employment_tax_for_lot: scheme_type == ESPP -> zero"],
            },
            "ESPP_PLUS": {
                "sellability": (
                    "Employee lot sellable; matched lot locked until forfeiture_period_end "
                    "(fallback acquisition_date + 183 days)."
                ),
                "forfeiture_window": "183 days for matched lots (calendar-day lock).",
                "tax_treatment_disposal": (
                    "Under 3y: IT/NI/SL on removal value; 3-5y: IT on lower(removal, award), "
                    "NI/SL zero; 5y+: zero."
                ),
                "holding_period_logic": "Thresholds at +3 years and +5 years from acquisition_date.",
                "taxes_applied": ["IT", "NI", "SL (where base > 0)"],
                "code_constants": [
                    "estimate_employment_tax_for_lot categories: UNDER_THREE_YEARS, "
                    "THREE_TO_FIVE_YEARS, FIVE_PLUS_YEARS",
                    "matched-lot forfeiture guard: FORFEITED_MATCHED_UNDER_183D",
                ],
            },
            "BROKERAGE": {
                "sellability": "Immediately sellable.",
                "forfeiture_window": None,
                "tax_treatment_disposal": "No employment tax in current model.",
                "holding_period_logic": "None.",
                "taxes_applied": [],
                "code_constants": [],
            },
            "ISA": {
                "sellability": "Immediately sellable.",
                "forfeiture_window": None,
                "tax_treatment_disposal": "Employment tax set to zero in summary/disposal estimate.",
                "holding_period_logic": "None.",
                "taxes_applied": [],
                "code_constants": ["_estimate_sell_all_employment_tax: all ISA -> 0.00"],
            },
            "SIP_PARTNERSHIP": {
                "sellability": "Sellable (no lock in current code path).",
                "forfeiture_window": None,
                "tax_treatment_disposal": "SIP event-driven logic via process_sip_event.",
                "holding_period_logic": "<3y / 3-5y / 5+y windows.",
                "taxes_applied": ["IT", "NI (<3y)", "SL on NI-liable base"],
                "code_constants": ["_SIP_LIKE_SCHEMES includes SIP_PARTNERSHIP"],
            },
            "SIP_MATCHING": {
                "sellability": "Sellable (no lock in current code path).",
                "forfeiture_window": "Forfeiture logic modelled via SIP event type for matching shares.",
                "tax_treatment_disposal": "SIP event-driven logic via process_sip_event.",
                "holding_period_logic": "<3y / 3-5y / 5+y windows.",
                "taxes_applied": ["IT", "NI (<3y)", "SL on NI-liable base"],
                "code_constants": ["_SIP_LIKE_SCHEMES includes SIP_MATCHING"],
            },
            "SIP_DIVIDEND": {
                "sellability": "Sellable (no lock in current code path).",
                "forfeiture_window": None,
                "tax_treatment_disposal": "SIP event-driven logic via process_sip_event.",
                "holding_period_logic": "<3y / 3-5y / 5+y windows.",
                "taxes_applied": ["IT", "NI (<3y)", "SL on NI-liable base"],
                "code_constants": ["_SIP_LIKE_SCHEMES includes SIP_DIVIDEND"],
            },
        },
    }


def _tax_snapshot(
    settings: AppSettings,
    as_of_date: date,
) -> dict[str, Any]:
    tax_year = tax_year_for_date(as_of_date)
    bands = get_bands(tax_year)
    ctx = TaxContext(
        tax_year=tax_year,
        gross_employment_income=settings.default_gross_income,
        pension_sacrifice=settings.default_pension_sacrifice,
        other_income=settings.default_other_income,
        student_loan_plan=settings.default_student_loan_plan,
    )
    rates = get_marginal_rates(ctx)
    pa = bands.personal_allowance

    income_tax_brackets = [
        {
            "label": "personal_allowance_zero_rate",
            "lower_bound": "0",
            "upper_bound": str(pa),
            "rate": "0",
            "lower_inclusive": True,
            "upper_inclusive": True,
            "boundary_interpretation": "Gross income <= personal allowance is taxed at 0%.",
        },
        {
            "label": "basic_rate",
            "lower_bound": str(pa),
            "upper_bound": str(bands.basic_rate_threshold),
            "rate": str(bands.basic_rate),
            "lower_inclusive": False,
            "upper_inclusive": True,
            "boundary_interpretation": "(PA, basic_rate_threshold] at basic rate.",
        },
        {
            "label": "higher_rate",
            "lower_bound": str(bands.basic_rate_threshold),
            "upper_bound": str(bands.higher_rate_threshold),
            "rate": str(bands.higher_rate),
            "lower_inclusive": False,
            "upper_inclusive": True,
            "boundary_interpretation": "(basic_rate_threshold, higher_rate_threshold] at higher rate.",
        },
        {
            "label": "additional_rate",
            "lower_bound": str(bands.higher_rate_threshold),
            "upper_bound": None,
            "rate": str(bands.additional_rate),
            "lower_inclusive": False,
            "upper_inclusive": True,
            "boundary_interpretation": "> higher_rate_threshold at additional rate.",
        },
        {
            "label": "pa_taper_effective_zone",
            "lower_bound": str(bands.pa_taper_start),
            "upper_bound": str(bands.pa_taper_end),
            "rate": str(bands.taper_zone_effective_it_rate),
            "lower_inclusive": False,
            "upper_inclusive": True,
            "boundary_interpretation": (
                "ANI in (pa_taper_start, pa_taper_end] has effective IT taper rate."
            ),
        },
    ]

    ni_brackets = [
        {
            "label": "ni_zero_band",
            "lower_bound": "0",
            "upper_bound": str(bands.ni_primary_threshold),
            "rate": "0",
            "lower_inclusive": True,
            "upper_inclusive": True,
            "boundary_interpretation": "NI-relevant income <= PT has 0 NI.",
        },
        {
            "label": "ni_main_band",
            "lower_bound": str(bands.ni_primary_threshold),
            "upper_bound": str(bands.ni_upper_earnings_limit),
            "rate": str(bands.ni_rate_below_uel),
            "lower_inclusive": False,
            "upper_inclusive": True,
            "boundary_interpretation": "(PT, UEL] at main NI rate.",
        },
        {
            "label": "ni_upper_band",
            "lower_bound": str(bands.ni_upper_earnings_limit),
            "upper_bound": None,
            "rate": str(bands.ni_rate_above_uel),
            "lower_inclusive": False,
            "upper_inclusive": True,
            "boundary_interpretation": "> UEL at upper NI rate.",
        },
    ]

    sl_brackets = [
        {
            "label": "plan_1",
            "plan": 1,
            "threshold": str(bands.student_loan_plan1_threshold),
            "rate": str(bands.student_loan_plan1_rate),
            "boundary_interpretation": "Rate applies when SL-relevant income > threshold.",
        },
        {
            "label": "plan_2",
            "plan": 2,
            "threshold": str(bands.student_loan_plan2_threshold),
            "rate": str(bands.student_loan_plan2_rate),
            "boundary_interpretation": "Rate applies when SL-relevant income > threshold.",
        },
    ]

    return {
        "settings_object_used": {
            "default_gross_income": str(settings.default_gross_income),
            "default_pension_sacrifice": str(settings.default_pension_sacrifice),
            "default_student_loan_plan": settings.default_student_loan_plan,
            "default_other_income": str(settings.default_other_income),
            "default_tax_year": settings.default_tax_year,
            "show_exhausted_lots": settings.show_exhausted_lots,
            "settings_file_path": str(settings.settings_path),
        },
        "tax_year_used": tax_year,
        "tax_context_used": {
            "tax_year": ctx.tax_year,
            "gross_employment_income": str(ctx.gross_employment_income),
            "pension_sacrifice": str(ctx.pension_sacrifice),
            "other_income": str(ctx.other_income),
            "student_loan_plan": ctx.student_loan_plan,
            "adjusted_net_income": str(ctx.adjusted_net_income),
            "ni_relevant_income": str(ctx.ni_relevant_income),
            "manual_marginal_it_rate": _str_decimal(ctx.manual_marginal_it_rate),
            "manual_marginal_ni_rate": _str_decimal(ctx.manual_marginal_ni_rate),
        },
        "income_tax_bands": income_tax_brackets,
        "nic_employee_bands": ni_brackets,
        "student_loan_bands": sl_brackets,
        "high_income_taper": {
            "pa_taper_start": str(bands.pa_taper_start),
            "pa_taper_end": str(bands.pa_taper_end),
            "taper_reduction_rule": "Personal allowance reduces by 1 for every 2 ANI above taper start.",
            "effective_it_rate_in_taper_zone": str(bands.taper_zone_effective_it_rate),
        },
        "effective_rate_overrides": {
            "uses_single_blended_override": False,
            "blended_rate": None,
            "derivation": None,
            "notes": [
                "Employment tax estimates use explicit marginal IT/NI/SL components.",
                "Combined rate is derived as IT + NI + SL for reference.",
            ],
        },
        "marginal_rates_applied": {
            "income_tax": str(rates.income_tax),
            "national_insurance": str(rates.national_insurance),
            "student_loan": str(rates.student_loan),
            "combined": str(rates.combined),
            "pence_kept_per_pound": str(rates.pence_kept_per_pound),
            "taper_zone": rates.taper_zone,
            "notes": list(rates.notes),
        },
    }


def _resolve_tax_estimates_for_security(
    lots: list[Lot],
    price_per_share_gbp: Decimal | None,
    as_of_date: date,
    settings: AppSettings | None,
) -> tuple[Decimal | None, dict[str, SIPTaxEstimate], str | None]:
    """
    Return (security_total_tax, lot_estimate_map, reason_if_unavailable).
    """
    if price_per_share_gbp is None:
        return None, {}, "No price available."

    total_tax = _estimate_sell_all_employment_tax(lots, price_per_share_gbp, as_of_date, settings)
    if total_tax is None:
        return None, {}, "Income settings are required for employment-tax estimate."

    sellable_lots = [
        lot
        for lot in lots
        if Decimal(lot.quantity_remaining) > Decimal("0") and _is_lot_sellable_on(lot, as_of_date)
    ]
    if not sellable_lots:
        return total_tax, {}, None

    if settings is None:
        return total_tax, {}, "Income settings are required for per-lot tax breakdown."

    total_qty = sum((Decimal(l.quantity_remaining) for l in sellable_lots), Decimal("0"))
    if total_qty <= Decimal("0"):
        return total_tax, {}, None

    espp_plus_employee_ids = _espp_plus_employee_lot_ids(lots)
    fifo_lots: list[LotForFIFO] = [
        _lot_to_fifo(
            lot,
            settings=settings,
            espp_plus_employee_ids=espp_plus_employee_ids,
            use_live_true_cost=False,
        )
        for lot in sellable_lots
    ]
    fifo = allocate_fifo(fifo_lots, total_qty, price_per_share_gbp)
    estimates, _total = _build_sip_tax_estimates(
        {lot.id: lot for lot in sellable_lots},
        fifo.allocations,
        price_per_share_gbp,
        as_of_date,
        settings,
    )
    estimate_map = {e.lot_id: e for e in estimates}
    return total_tax, estimate_map, None


def _true_cost_formula_for_lot(lot: Lot) -> dict[str, Any]:
    scheme = lot.scheme_type
    formula = "qty * stored_true_cost_per_share_gbp"
    notes: list[str] = [
        "UI and disposal calculations use persisted lot.true_cost_per_share_gbp directly."
    ]

    if scheme == "RSU":
        notes.append(
            "Add-lot flow model reference: true_cost/share ~= FMV_at_vest * (IT + NI) at acquisition."
        )
    elif scheme == "ESPP":
        notes.append("Add-lot flow model reference: true_cost/share = purchase_price_from_net_pay.")
    elif scheme == "ESPP_PLUS":
        if lot.matching_lot_id:
            notes.append("Matched shares are employer-funded; true_cost/share is typically zero.")
        else:
            notes.append(
                "Add-lot flow model reference: true_cost/share = purchase_price * (1 - combined_rate), "
                "unless manually overridden."
            )
    elif scheme in {"BROKERAGE", "ISA"}:
        notes.append("Add-lot flow model reference: true_cost/share = acquisition_price.")
    elif scheme.startswith("SIP_"):
        notes.append("SIP true-cost is persisted at acquisition and reused as stored.")

    return {"formula": formula, "notes": notes}


def _sellability_reason(lot: Lot, status: str, unlock_date: date | None, as_of_date: date) -> str:
    if status == "LOCKED":
        if lot.scheme_type == "RSU" and unlock_date is not None:
            return f"RSU pre-vest lock until {unlock_date.isoformat()}."
        if lot.scheme_type == "ESPP_PLUS" and lot.matching_lot_id and unlock_date is not None:
            days = max(0, (unlock_date - as_of_date).days)
            return (
                "ESPP_PLUS matched-share forfeiture lock "
                f"until {unlock_date.isoformat()} ({days} days remaining)."
            )
        if unlock_date is not None:
            return f"Locked until {unlock_date.isoformat()}."
        return "Locked."
    if status == "AT_RISK":
        return "Sellable, but linked matched shares are still in forfeiture risk window."
    return "Sellable."


def _append_warning(
    warnings: list[dict[str, Any]],
    code: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> None:
    warnings.append(
        {
            "code": code,
            "message": message,
            "context": context or {},
        }
    )


class ValidationReportService:
    @staticmethod
    def generate_report(
        *,
        security_filter: str | None = None,
        as_of: datetime | None = None,
        limit_lots: int | None = None,
        db_path: Path | None = None,
        settings: AppSettings | None = None,
    ) -> dict[str, Any]:
        as_of_utc = _to_utc(as_of or datetime.now(timezone.utc))
        as_of_date = as_of_utc.date()
        generated_at = datetime.now(timezone.utc)
        lot_limit = _coerce_positive_int(limit_lots)

        inferred_db_path = db_path.resolve() if db_path is not None else None
        if settings is None:
            if inferred_db_path is not None:
                settings_obj = AppSettings.load(inferred_db_path)
            else:
                settings_obj = AppSettings()
        else:
            settings_obj = settings

        metadata = {
            "generated_at_utc": _iso_utc(generated_at),
            "as_of_used_utc": _iso_utc(as_of_utc),
            "db_path": str(inferred_db_path) if inferred_db_path is not None else None,
            "db_encrypted": (
                Path(str(inferred_db_path) + ".salt").exists()
                if inferred_db_path is not None
                else None
            ),
            "git_commit_hash": _git_commit_hash(),
            "app_versions": {
                "api_app_version": _api_version(),
                "package_version_pyproject": _pyproject_version(),
            },
            "rounding_rules": {
                "money_rounding": "2dp, ROUND_HALF_UP",
                "true_cost_per_share_rounding": "4dp in add-lot flow, ROUND_HALF_UP",
                "bankers_rounding_used": False,
            },
        }

        tax_snapshot = _tax_snapshot(settings_obj, as_of_date)
        warnings: list[dict[str, Any]] = []
        pass_count = 0
        fail_count = 0

        with AppContext.read_session() as sess:
            sec_repo = SecurityRepository(sess)
            lot_repo = LotRepository(sess)

            all_securities = sec_repo.list_all()
            selected = _resolve_security_selection(all_securities, security_filter)

            market_data_security_rows: list[dict[str, Any]] = []
            fx_inputs_by_key: dict[str, dict[str, Any]] = {}
            security_reports: list[dict[str, Any]] = []
            lot_rows: list[dict[str, Any]] = []

            for security in selected:
                all_lots_for_security = lot_repo.get_all_lots_for_security(security.id)
                active_lots = [
                    lot
                    for lot in all_lots_for_security
                    if Decimal(lot.quantity_remaining) > Decimal("0")
                    and lot.acquisition_date <= as_of_date
                ]
                active_lots.sort(key=_lot_sort_key)

                price_row = _select_price_row_as_of(sess, security.id, as_of_utc)
                current_price_gbp: Decimal | None = None
                price_source = None
                price_timestamp = None
                fx_timestamp = None
                fx_rate_derived: Decimal | None = None
                if price_row is not None and price_row.close_price_gbp is not None:
                    current_price_gbp = Decimal(price_row.close_price_gbp)
                    price_source = price_row.source
                    price_timestamp = _parse_sheets_timestamp(price_source or "")
                    fx_timestamp = _parse_fx_timestamp(price_source or "")
                    original = Decimal(price_row.close_price_original_ccy)
                    if security.currency != "GBP" and original != Decimal("0"):
                        fx_rate_derived = current_price_gbp / original
                elif price_row is not None and price_row.close_price_gbp is None:
                    _append_warning(
                        warnings,
                        "PRICE_GBP_MISSING",
                        "Price row exists but close_price_gbp is missing.",
                        {"security_id": security.id, "ticker": security.ticker},
                    )
                    fail_count += 1

                market_data_security_rows.append(
                    {
                        "security_id": security.id,
                        "symbol": security.ticker,
                        "name": security.name,
                        "trading_currency": security.currency,
                        "price_used_gbp": _str_decimal(current_price_gbp),
                        "price_date_selected": (
                            price_row.price_date.isoformat() if price_row is not None else None
                        ),
                        "price_timestamp_selected": price_timestamp,
                        "price_fetched_at_utc": _iso_utc(price_row.fetched_at)
                        if price_row is not None
                        else None,
                        "price_source": price_source,
                    }
                )

                if security.currency != "GBP" and current_price_gbp is not None:
                    fx_key = f"{security.currency}->GBP"
                    existing = fx_inputs_by_key.get(fx_key)
                    if existing is None:
                        fx_inputs_by_key[fx_key] = {
                            "fx_pair": fx_key,
                            "fx_rate": _str_decimal(fx_rate_derived),
                            "fx_timestamp_selected": fx_timestamp,
                            "price_date_selected": (
                                price_row.price_date.isoformat() if price_row is not None else None
                            ),
                            "source": price_source,
                            "used_by_symbols": [security.ticker],
                            "derived_from": (
                                "close_price_gbp / close_price_original_ccy from selected price row"
                            ),
                        }
                    else:
                        if security.ticker not in existing["used_by_symbols"]:
                            existing["used_by_symbols"].append(security.ticker)

                    if fx_rate_derived is None:
                        _append_warning(
                            warnings,
                            "FX_RATE_MISSING",
                            "Non-GBP security has no derivable FX rate for selected row.",
                            {"security_id": security.id, "ticker": security.ticker},
                        )
                        fail_count += 1
                elif security.currency != "GBP" and current_price_gbp is None:
                    _append_warning(
                        warnings,
                        "FX_OR_PRICE_MISSING",
                        "Non-GBP security has no selected price/FX input for as_of.",
                        {"security_id": security.id, "ticker": security.ticker},
                    )
                    fail_count += 1

                total_qty = sum((Decimal(l.quantity_remaining) for l in active_lots), Decimal("0"))
                total_cost_basis = sum(
                    (Decimal(l.quantity_remaining) * Decimal(l.acquisition_price_gbp) for l in active_lots),
                    Decimal("0"),
                )
                total_true_cost = sum(
                    (
                        Decimal(l.quantity_remaining) * Decimal(l.true_cost_per_share_gbp)
                        for l in active_lots
                    ),
                    Decimal("0"),
                )
                gross_market_value = (
                    _q2(total_qty * current_price_gbp) if current_price_gbp is not None else None
                )
                sec_tax_total, lot_estimate_map, sec_tax_reason = _resolve_tax_estimates_for_security(
                    active_lots,
                    current_price_gbp,
                    as_of_date,
                    settings_obj,
                )
                broker_fees = Decimal("0")
                est_net_liquidation = (
                    _q2(gross_market_value - sec_tax_total - broker_fees)
                    if gross_market_value is not None and sec_tax_total is not None
                    else None
                )

                check_1_diff: Decimal | None = None
                if (
                    gross_market_value is not None
                    and sec_tax_total is not None
                    and est_net_liquidation is not None
                ):
                    lhs = est_net_liquidation
                    rhs = gross_market_value - sec_tax_total - broker_fees
                    check_1_diff = lhs - rhs
                    if abs(check_1_diff) <= _EPSILON:
                        pass_count += 1
                    else:
                        fail_count += 1
                        _append_warning(
                            warnings,
                            "SECURITY_CHECK_1_FAIL",
                            "est_net_liquidation mismatch with mkt - tax - fees.",
                            {
                                "security_id": security.id,
                                "ticker": security.ticker,
                                "diff": str(check_1_diff),
                            },
                        )

                pnl_cost_basis = (
                    gross_market_value - total_cost_basis
                    if gross_market_value is not None
                    else None
                )
                pnl_economic = (
                    gross_market_value - total_true_cost
                    if gross_market_value is not None
                    else None
                )

                security_reports.append(
                    {
                        "security_id": security.id,
                        "symbol": security.ticker,
                        "name": security.name,
                        "total_qty": str(total_qty),
                        "total_cost_basis_gbp": _raw_rounded(total_cost_basis),
                        "total_true_cost_gbp": _raw_rounded(total_true_cost),
                        "gross_market_value_gbp": _raw_rounded(gross_market_value),
                        "estimated_employment_tax_gbp": _raw_rounded(sec_tax_total),
                        "broker_fees_gbp": _raw_rounded(broker_fees),
                        "est_net_liquidation_gbp": _raw_rounded(est_net_liquidation),
                        "tax_estimate_reason": sec_tax_reason,
                        "checks": {
                            "check_1_net_cash_equals_mkt_minus_tax_minus_fees_diff": _str_decimal(
                                check_1_diff
                            ),
                            "check_2_pnl_cost_basis": _raw_rounded(pnl_cost_basis),
                            "check_3_pnl_economic": _raw_rounded(pnl_economic),
                        },
                    }
                )

                at_risk_ids = _matched_employee_lot_ids_at_risk(active_lots, as_of_date)

                linked_match_qty_by_employee: dict[str, Decimal] = {}
                for lot in all_lots_for_security:
                    if lot.matching_lot_id:
                        linked_match_qty_by_employee.setdefault(
                            lot.matching_lot_id, Decimal("0")
                        )
                        linked_match_qty_by_employee[lot.matching_lot_id] += Decimal(
                            lot.quantity_remaining
                        )

                for lot in active_lots:
                    qty = Decimal(lot.quantity_remaining)
                    acq_price = Decimal(lot.acquisition_price_gbp)
                    true_cost_per_share = Decimal(lot.true_cost_per_share_gbp)
                    cost_basis = qty * acq_price
                    true_cost = qty * true_cost_per_share
                    sellability_status, unlock_date = _sellability_for_lot(
                        lot,
                        as_of=as_of_date,
                        at_risk_employee_lot_ids=at_risk_ids,
                    )
                    sellability_reason = _sellability_reason(
                        lot, sellability_status, unlock_date, as_of_date
                    )

                    forfeiture = _forfeiture_risk_for_lot(lot, as_of_date)
                    price_used = current_price_gbp
                    proceeds = qty * price_used if price_used is not None else None
                    acquisition_mkt_value = qty * acq_price

                    lot_est = lot_estimate_map.get(lot.id)
                    taxable_base = Decimal("0")
                    ni_base = Decimal("0")
                    sl_base = Decimal("0")
                    est_it = Decimal("0")
                    est_ni = Decimal("0")
                    est_sl = Decimal("0")
                    employment_tax = Decimal("0")
                    tax_category = "NON_EMPLOYMENT_TAXABLE"
                    tax_reason = "No employment tax applies in current model."

                    if price_used is None:
                        tax_reason = "No price available."
                        taxable_base = Decimal("0")
                    elif sellability_status == "LOCKED":
                        tax_reason = "Lot is locked and excluded from sell-all tax estimate."
                    elif lot.scheme_type == "ISA":
                        tax_category = "ISA_EXEMPT"
                        tax_reason = "ISA lot: employment tax is zero."
                    elif settings_obj is None:
                        tax_reason = "Income settings unavailable."
                    elif lot_est is not None:
                        taxable_base = lot_est.income_taxable_gbp
                        ni_base = lot_est.ni_liable_gbp
                        sl_base = lot_est.ni_liable_gbp
                        est_it = lot_est.est_income_tax_gbp
                        est_ni = lot_est.est_ni_gbp
                        est_sl = lot_est.est_student_loan_gbp
                        employment_tax = lot_est.est_total_employment_tax_gbp
                        tax_category = lot_est.holding_period_category
                        tax_reason = "Computed from SIP/ESPP employment-tax engine."
                    elif lot.scheme_type in {"BROKERAGE", "RSU"}:
                        tax_reason = "Scheme has zero disposal employment tax in current model."
                    elif lot.scheme_type == "ESPP":
                        tax_category = "ESPP_ZERO"
                        tax_reason = "ESPP disposal employment tax is zero by rule."
                    else:
                        tax_reason = "No per-lot employment tax estimate returned."

                    broker_fees = Decimal("0")
                    net_cash = (
                        proceeds - employment_tax - broker_fees
                        if proceeds is not None
                        else None
                    )
                    economic_result = (
                        net_cash - true_cost if net_cash is not None else None
                    )
                    pnl_cost_basis = proceeds - cost_basis if proceeds is not None else None
                    pnl_economic_unrealised = (
                        proceeds - true_cost if proceeds is not None else None
                    )

                    econ_check_diff = None
                    if economic_result is not None and net_cash is not None:
                        rhs = net_cash - true_cost
                        econ_check_diff = economic_result - rhs
                        if abs(econ_check_diff) <= _EPSILON:
                            pass_count += 1
                        else:
                            fail_count += 1
                            _append_warning(
                                warnings,
                                "LOT_ECONOMIC_IDENTITY_FAIL",
                                "economic_result_if_sold != net_cash_if_sold - true_cost.",
                                {
                                    "lot_id": lot.id,
                                    "security_id": security.id,
                                    "diff": str(econ_check_diff),
                                },
                            )

                    if employment_tax < Decimal("0"):
                        fail_count += 1
                        _append_warning(
                            warnings,
                            "NEGATIVE_EMPLOYMENT_TAX",
                            "Employment tax estimate is negative.",
                            {"lot_id": lot.id, "security_id": security.id},
                        )
                    for field_name, dec_value in (
                        ("qty", qty),
                        ("cost_basis", cost_basis),
                        ("true_cost", true_cost),
                        ("employment_tax", employment_tax),
                    ):
                        if _is_invalid_decimal(dec_value):
                            fail_count += 1
                            _append_warning(
                                warnings,
                                "INVALID_DECIMAL",
                                f"Invalid decimal detected in {field_name}.",
                                {"lot_id": lot.id, "security_id": security.id},
                            )

                    lot_rows.append(
                        {
                            "sort_key": (
                                security.ticker.upper(),
                                security.id,
                                lot.acquisition_date.isoformat(),
                                lot.id,
                            ),
                            "security_id": security.id,
                            "symbol": security.ticker,
                            "lot_id": lot.id,
                            "scheme": lot.scheme_type,
                            "acquisition_date": lot.acquisition_date.isoformat(),
                            "qty": str(qty),
                            "acquisition_price_original": (
                                lot.acquisition_price_original_ccy
                                if lot.acquisition_price_original_ccy is not None
                                else lot.acquisition_price_gbp
                            ),
                            "original_currency": lot.original_currency or "GBP",
                            "cost_basis_original": lot.acquisition_price_original_ccy,
                            "converted_cost_basis_gbp": _raw_rounded(cost_basis),
                            "true_cost_gbp": {
                                **_raw_rounded(true_cost),
                                "formula_details": _true_cost_formula_for_lot(lot),
                                "inputs": {
                                    "qty": str(qty),
                                    "stored_true_cost_per_share_gbp": str(true_cost_per_share),
                                },
                            },
                            "employer_match": {
                                "linked_matched_qty_remaining": _str_decimal(
                                    linked_match_qty_by_employee.get(lot.id)
                                ),
                                "is_matched_lot": lot.matching_lot_id is not None,
                                "matched_to_employee_lot_id": lot.matching_lot_id,
                            },
                            "forfeiture_rules": {
                                "forfeiture_period_end": (
                                    forfeiture.end_date.isoformat() if forfeiture is not None else None
                                ),
                                "forfeiture_days_remaining": (
                                    forfeiture.days_remaining if forfeiture is not None else None
                                ),
                                "forfeiture_in_window": (
                                    forfeiture.in_window if forfeiture is not None else None
                                ),
                            },
                            "sellability": {
                                "status": sellability_status,
                                "unlock_date": unlock_date.isoformat() if unlock_date else None,
                                "reason": sellability_reason,
                            },
                            "market_inputs": {
                                "price_used_gbp": _str_decimal(price_used),
                                "price_date_selected": (
                                    price_row.price_date.isoformat() if price_row is not None else None
                                ),
                                "price_timestamp_selected": price_timestamp,
                                "fx_rate_used": _str_decimal(fx_rate_derived),
                                "fx_timestamp_selected": fx_timestamp,
                                "price_source": price_source,
                            },
                            "employment_tax_intermediates": {
                                "proceeds_gbp": _raw_rounded(proceeds),
                                "acquisition_mkt_value_gbp": _raw_rounded(acquisition_mkt_value),
                                "taxable_base_gbp": _raw_rounded(taxable_base),
                                "ni_base_gbp": _raw_rounded(ni_base),
                                "student_loan_base_gbp": _raw_rounded(sl_base),
                                "holding_period_category": tax_category,
                                "marginal_rates_applied": {
                                    "income_tax": tax_snapshot["marginal_rates_applied"]["income_tax"],
                                    "national_insurance": tax_snapshot["marginal_rates_applied"][
                                        "national_insurance"
                                    ],
                                    "student_loan": tax_snapshot["marginal_rates_applied"][
                                        "student_loan"
                                    ],
                                    "combined": tax_snapshot["marginal_rates_applied"]["combined"],
                                },
                                "est_income_tax_gbp": _raw_rounded(est_it),
                                "est_ni_gbp": _raw_rounded(est_ni),
                                "est_student_loan_gbp": _raw_rounded(est_sl),
                                "employment_tax_gbp": _raw_rounded(employment_tax),
                                "broker_fees_gbp": _raw_rounded(broker_fees),
                                "tax_reason": tax_reason,
                            },
                            "outcomes": {
                                "net_cash_if_sold_gbp": _raw_rounded(net_cash),
                                "economic_result_if_sold_gbp": _raw_rounded(economic_result),
                                "pnl_cost_basis_gbp": _raw_rounded(pnl_cost_basis),
                                "pnl_economic_unrealised_gbp": _raw_rounded(
                                    pnl_economic_unrealised
                                ),
                                "identity_check_diff": _str_decimal(econ_check_diff),
                            },
                            "limit_metric_gross_value": _str_decimal(
                                proceeds if proceeds is not None else cost_basis
                            ),
                        }
                    )

            # Stable output ordering.
            security_reports.sort(key=lambda r: (r["symbol"], r["security_id"]))
            lot_rows.sort(key=lambda r: r["sort_key"])

            truncation = {
                "limit_lots": lot_limit,
                "total_lots_available": len(lot_rows),
                "lots_included": len(lot_rows),
                "is_truncated": False,
                "selection_strategy": "all_lots",
            }
            if lot_limit is not None and len(lot_rows) > lot_limit:
                by_value = sorted(
                    lot_rows,
                    key=lambda r: (
                        Decimal(r["limit_metric_gross_value"] or "0"),
                        r["lot_id"],
                    ),
                    reverse=True,
                )
                selected_ids = {row["lot_id"] for row in by_value[:lot_limit]}
                lot_rows = [row for row in lot_rows if row["lot_id"] in selected_ids]
                lot_rows.sort(key=lambda r: r["sort_key"])
                truncation = {
                    "limit_lots": lot_limit,
                    "total_lots_available": truncation["total_lots_available"],
                    "lots_included": len(lot_rows),
                    "is_truncated": True,
                    "selection_strategy": "top_n_by_gross_value",
                }

        total_cost_basis = sum(
            (Decimal(s["total_cost_basis_gbp"]["raw"] or "0") for s in security_reports),
            Decimal("0"),
        )
        total_true_cost = sum(
            (Decimal(s["total_true_cost_gbp"]["raw"] or "0") for s in security_reports),
            Decimal("0"),
        )

        market_values = [
            Decimal(s["gross_market_value_gbp"]["raw"])
            for s in security_reports
            if s["gross_market_value_gbp"]["raw"] is not None
        ]
        total_market = sum(market_values, Decimal("0")) if market_values else None

        sec_taxes: list[Decimal | None] = []
        for s in security_reports:
            raw_tax = s["estimated_employment_tax_gbp"]["raw"]
            if s["gross_market_value_gbp"]["raw"] is not None:
                sec_taxes.append(Decimal(raw_tax) if raw_tax is not None else None)

        portfolio_tax_total = None
        portfolio_net_total = None
        if total_market is not None and sec_taxes and all(v is not None for v in sec_taxes):
            portfolio_tax_total = _q2(sum((v for v in sec_taxes if v is not None), Decimal("0")))
            portfolio_net_total = _q2(total_market - portfolio_tax_total)
        elif total_market is not None and sec_taxes:
            _append_warning(
                warnings,
                "PORTFOLIO_TAX_PARTIAL",
                "Portfolio has market value but missing employment-tax estimate for one or more "
                "securities.",
                {},
            )
            fail_count += 1

        if total_market is not None and portfolio_tax_total is not None and portfolio_net_total is not None:
            diff = portfolio_net_total - (total_market - portfolio_tax_total)
            if abs(diff) <= _EPSILON:
                pass_count += 1
            else:
                fail_count += 1
                _append_warning(
                    warnings,
                    "PORTFOLIO_CHECK_1_FAIL",
                    "Portfolio net liquidation mismatch with market - tax - fees.",
                    {"diff": str(diff)},
                )

        if not security_reports:
            _append_warning(
                warnings,
                "NO_SECURITIES_SELECTED",
                "No securities matched the filter.",
                {},
            )

        report = {
            "metadata": metadata,
            "global_settings_snapshot": tax_snapshot,
            "market_data_inputs": {
                "fx_pairs": sorted(
                    fx_inputs_by_key.values(),
                    key=lambda x: x["fx_pair"],
                ),
                "security_prices": sorted(
                    market_data_security_rows,
                    key=lambda x: (x["symbol"], x["security_id"]),
                ),
            },
            "scheme_rules_snapshot": _scheme_rules_snapshot(),
            "portfolio_totals": {
                "total_cost_basis_gbp": _raw_rounded(total_cost_basis),
                "total_true_cost_gbp": _raw_rounded(total_true_cost),
                "total_market_value_gbp": _raw_rounded(total_market),
                "estimated_employment_tax_gbp": _raw_rounded(portfolio_tax_total),
                "est_net_liquidation_gbp": _raw_rounded(portfolio_net_total),
            },
            "per_security_totals": security_reports,
            "per_lot_breakdown": lot_rows,
            "truncation": truncation,
            "invariant_warnings": {
                "warnings": warnings,
                "summary": {
                    "pass_count": pass_count,
                    "fail_count": fail_count,
                    "warning_count": len(warnings),
                    "status": "PASS" if fail_count == 0 else "FAIL",
                },
            },
        }
        return report

    @staticmethod
    def render_text(report: dict[str, Any]) -> str:
        lines: list[str] = []
        md = report["metadata"]
        settings = report["global_settings_snapshot"]
        market = report["market_data_inputs"]
        schemes = report["scheme_rules_snapshot"]
        portfolio = report["portfolio_totals"]
        securities = report["per_security_totals"]
        lots = report["per_lot_breakdown"]
        trunc = report["truncation"]
        inv = report["invariant_warnings"]

        lines.append("A) Report Metadata")
        lines.append(f"- generated_at_utc: {md['generated_at_utc']}")
        lines.append(f"- db_path: {md['db_path']}")
        lines.append(f"- encrypted: {md['db_encrypted']}")
        lines.append(f"- git_commit_hash: {md['git_commit_hash']}")
        lines.append(
            f"- app_version_api: {md['app_versions'].get('api_app_version')}"
        )
        lines.append(
            f"- app_version_package: {md['app_versions'].get('package_version_pyproject')}"
        )
        lines.append(f"- as_of_used_utc: {md['as_of_used_utc']}")
        lines.append("- rounding_rules:")
        for k, v in md["rounding_rules"].items():
            lines.append(f"  {k}: {v}")

        lines.append("")
        lines.append("B) Global Settings Snapshot (live)")
        for k, v in settings["settings_object_used"].items():
            lines.append(f"- {k}: {v}")
        lines.append(f"- tax_year_used: {settings['tax_year_used']}")
        lines.append("- tax_context_used:")
        for k, v in settings["tax_context_used"].items():
            lines.append(f"  {k}: {v}")
        lines.append("- income_tax_bands:")
        for band in settings["income_tax_bands"]:
            lines.append(
                "  - "
                f"{band['label']}: lower_bound={band['lower_bound']}, "
                f"upper_bound={band['upper_bound']}, rate={band['rate']}, "
                f"lower_inclusive={band['lower_inclusive']}, "
                f"upper_inclusive={band['upper_inclusive']}"
            )
            lines.append(f"    boundary: {band['boundary_interpretation']}")
        lines.append("- nic_employee_bands:")
        for band in settings["nic_employee_bands"]:
            lines.append(
                "  - "
                f"{band['label']}: lower_bound={band['lower_bound']}, "
                f"upper_bound={band['upper_bound']}, rate={band['rate']}, "
                f"lower_inclusive={band['lower_inclusive']}, "
                f"upper_inclusive={band['upper_inclusive']}"
            )
            lines.append(f"    boundary: {band['boundary_interpretation']}")
        lines.append("- student_loan_bands:")
        for band in settings["student_loan_bands"]:
            lines.append(
                f"  - {band['label']}: plan={band['plan']}, threshold={band['threshold']}, "
                f"rate={band['rate']}"
            )
            lines.append(f"    boundary: {band['boundary_interpretation']}")
        lines.append("- high_income_taper:")
        for k, v in settings["high_income_taper"].items():
            lines.append(f"  {k}: {v}")
        lines.append("- effective_rate_overrides:")
        for k, v in settings["effective_rate_overrides"].items():
            lines.append(f"  {k}: {v}")
        lines.append("- marginal_rates_applied:")
        for k, v in settings["marginal_rates_applied"].items():
            lines.append(f"  {k}: {v}")

        lines.append("")
        lines.append("C) Market Data Inputs (live)")
        lines.append("- fx_pairs:")
        if market["fx_pairs"]:
            for fx in market["fx_pairs"]:
                lines.append(
                    f"  - {fx['fx_pair']}: rate={fx['fx_rate']}, "
                    f"fx_timestamp_selected={fx['fx_timestamp_selected']}, "
                    f"price_date_selected={fx['price_date_selected']}, source={fx['source']}, "
                    f"used_by={','.join(fx['used_by_symbols'])}"
                )
        else:
            lines.append("  - none")
        lines.append("- security_prices:")
        for row in market["security_prices"]:
            lines.append(
                f"  - {row['symbol']} ({row['security_id']}): price_used_gbp={row['price_used_gbp']}, "
                f"price_date_selected={row['price_date_selected']}, "
                f"price_timestamp_selected={row['price_timestamp_selected']}, "
                f"price_source={row['price_source']}"
            )

        lines.append("")
        lines.append("D) Scheme Rules Snapshot (live + code constants)")
        lines.append(
            f"- supported_scheme_types_verbatim: {schemes['supported_scheme_types_verbatim']}"
        )
        lines.append(
            f"- sip_like_schemes_verbatim: {schemes['sip_like_schemes_verbatim']}"
        )
        for scheme, details in schemes["rules"].items():
            lines.append(f"- {scheme}:")
            lines.append(f"  sellability: {details['sellability']}")
            lines.append(f"  forfeiture_window: {details['forfeiture_window']}")
            lines.append(f"  tax_treatment_disposal: {details['tax_treatment_disposal']}")
            lines.append(f"  holding_period_logic: {details['holding_period_logic']}")
            lines.append(f"  taxes_applied: {details['taxes_applied']}")
            lines.append(f"  code_constants: {details['code_constants']}")

        lines.append("")
        lines.append("E) Per-Security Totals (recompute)")
        lines.append(
            "- portfolio_totals: "
            f"cost_basis={portfolio['total_cost_basis_gbp']}, "
            f"true_cost={portfolio['total_true_cost_gbp']}, "
            f"market={portfolio['total_market_value_gbp']}, "
            f"tax={portfolio['estimated_employment_tax_gbp']}, "
            f"net_liquidation={portfolio['est_net_liquidation_gbp']}"
        )
        for sec in securities:
            lines.append(f"- security: {sec['symbol']} ({sec['security_id']})")
            lines.append(f"  total_qty: {sec['total_qty']}")
            lines.append(f"  total_cost_basis_gbp: {sec['total_cost_basis_gbp']}")
            lines.append(f"  total_true_cost_gbp: {sec['total_true_cost_gbp']}")
            lines.append(f"  gross_market_value_gbp: {sec['gross_market_value_gbp']}")
            lines.append(
                "  estimated_employment_tax_gbp: "
                f"{sec['estimated_employment_tax_gbp']}"
            )
            lines.append(f"  broker_fees_gbp: {sec['broker_fees_gbp']}")
            lines.append(f"  est_net_liquidation_gbp: {sec['est_net_liquidation_gbp']}")
            lines.append(
                "  check_1_diff(mkt-tax-fees-net): "
                f"{sec['checks']['check_1_net_cash_equals_mkt_minus_tax_minus_fees_diff']}"
            )
            lines.append(
                f"  check_2_pnl_cost_basis: {sec['checks']['check_2_pnl_cost_basis']}"
            )
            lines.append(
                f"  check_3_pnl_economic: {sec['checks']['check_3_pnl_economic']}"
            )
            lines.append(f"  tax_estimate_reason: {sec['tax_estimate_reason']}")

        lines.append("")
        lines.append("F) Per-Lot Deep Breakdown")
        lines.append(
            f"- lot_count_total: {trunc['total_lots_available']}, "
            f"lot_count_included: {trunc['lots_included']}, "
            f"is_truncated: {trunc['is_truncated']}, "
            f"selection_strategy: {trunc['selection_strategy']}"
        )
        for i, lot in enumerate(lots, start=1):
            lines.append(f"- lot_{i}:")
            lines.append(f"  lot_id: {lot['lot_id']}")
            lines.append(f"  security_id: {lot['security_id']}")
            lines.append(f"  symbol: {lot['symbol']}")
            lines.append(f"  scheme: {lot['scheme']}")
            lines.append(f"  acquisition_date: {lot['acquisition_date']}")
            lines.append(f"  qty: {lot['qty']}")
            lines.append(f"  acquisition_price_original: {lot['acquisition_price_original']}")
            lines.append(f"  original_currency: {lot['original_currency']}")
            lines.append(f"  cost_basis_original: {lot['cost_basis_original']}")
            lines.append(
                f"  converted_cost_basis_gbp: {lot['converted_cost_basis_gbp']}"
            )
            lines.append(f"  true_cost_gbp: {lot['true_cost_gbp']}")
            lines.append(f"  employer_match: {lot['employer_match']}")
            lines.append(f"  forfeiture_rules: {lot['forfeiture_rules']}")
            lines.append(f"  sellability: {lot['sellability']}")
            lines.append(f"  market_inputs: {lot['market_inputs']}")
            lines.append(
                "  employment_tax_intermediates: "
                f"{lot['employment_tax_intermediates']}"
            )
            lines.append(f"  outcomes: {lot['outcomes']}")

        lines.append("")
        lines.append("G) Invariant / Consistency Warnings")
        lines.append(f"- summary: {inv['summary']}")
        if inv["warnings"]:
            for w in inv["warnings"]:
                lines.append(
                    f"- {w['code']}: {w['message']} | context={json.dumps(w['context'])}"
                )
        else:
            lines.append("- none")

        return "\n".join(lines)
