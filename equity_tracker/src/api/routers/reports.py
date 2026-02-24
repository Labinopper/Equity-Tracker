"""
Reports router — CGT summary, economic gain, and audit log.

All endpoints are read-only (no DB writes).

Endpoints
──────────
  GET /reports/tax-years          List all supported UK tax year strings
  GET /reports/cgt                CGT summary for a tax year
  GET /reports/economic-gain      Economic (true-cost) gain summary for a tax year
  GET /reports/audit              Audit trail, optionally filtered

Phase WB additions
───────────────────
  GET /reports/cgt?include_tax_due=true
      Builds a TaxContext from saved AppSettings and passes it to the CGT
      summary, returning tax-due figures alongside the gain breakdown.
      Also accepts an optional ``prior_year_losses`` query parameter.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query

from ...core.tax_engine import available_tax_years
from ...core.tax_engine.context import TaxContext
from ...services.report_service import ReportService
from ...settings import AppSettings
from ..dependencies import db_required
from .. import _state
from ..schemas.reports import (
    AuditLogEntrySchema,
    CgtSummarySchema,
    EconomicGainSummarySchema,
)

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get(
    "/tax-years",
    response_model=list[str],
    summary="Supported UK tax years",
    description=(
        "Returns all UK tax year strings for which band data exists in the "
        "tax engine (e.g. ``[\"2022-23\", \"2023-24\", \"2024-25\"]``).  "
        "Use these values as the ``tax_year`` parameter on other report endpoints.  "
        "Does **not** require the database to be unlocked."
    ),
)
async def get_available_tax_years() -> list[str]:
    """
    Static list of supported tax years from the tax engine.

    No DB access — the list is determined by the band definitions in
    ``src/core/tax_engine/bands.py``.
    """
    return available_tax_years()


@router.get(
    "/cgt",
    response_model=CgtSummarySchema,
    summary="CGT summary for a tax year",
    description=(
        "Capital Gains Tax summary for the given UK tax year.  "
        "Returns one disposal line per DISPOSAL transaction, plus aggregate "
        "totals (total gains, total losses, net gain).  "
        "All monetary values are decimal strings.\n\n"
        "Pass ``include_tax_due=true`` to also compute CGT due using the "
        "income figures saved in user settings (PUT /api/settings).  "
        "Optionally supply ``prior_year_losses`` (decimal string) to offset "
        "the taxable gain."
    ),
)
async def cgt_summary(
    tax_year: str = Query(
        ...,
        description="UK tax year string, e.g. '2024-25'.",
        examples=["2024-25"],
    ),
    include_tax_due: bool = Query(
        False,
        description=(
            "When true, build a TaxContext from saved user settings and "
            "include CGT due figures in the response."
        ),
    ),
    prior_year_losses: Decimal = Query(
        Decimal("0"),
        ge=0,
        description="Prior-year capital losses to offset against this year's net gain.",
    ),
    _: None = Depends(db_required),
) -> CgtSummarySchema:
    """
    CGT summary for ``tax_year``.

    When ``include_tax_due=true``, loads AppSettings and builds a TaxContext
    so that ``cgt_result`` (CGT due) is populated in the response.
    If settings have not been saved yet, falls back to no TaxContext.
    """
    supported = available_tax_years()
    if tax_year not in supported:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported tax year: '{tax_year}'. "
                f"Supported values: {supported}"
            ),
        )

    tax_context: TaxContext | None = None
    if include_tax_due:
        db_path = _state.get_db_path()
        if db_path is not None:
            settings = AppSettings.load(db_path)
            tax_context = TaxContext(
                tax_year=tax_year,
                gross_employment_income=settings.default_gross_income,
                pension_sacrifice=settings.default_pension_sacrifice,
                other_income=settings.default_other_income,
                student_loan_plan=settings.default_student_loan_plan,
            )

    report = ReportService.cgt_summary(
        tax_year,
        tax_context=tax_context,
        prior_year_losses=prior_year_losses,
    )
    return CgtSummarySchema.from_service(report)


@router.get(
    "/economic-gain",
    response_model=EconomicGainSummarySchema,
    summary="Economic gain summary for a tax year",
    description=(
        "Economic (true-cost) gain summary for the given UK tax year.  "
        "Uses ``true_cost_per_share_gbp`` instead of the CGT cost basis.  "
        "The difference is most significant for SIP Partnership shares "
        "purchased from gross salary, where the economic gain is larger "
        "than the CGT gain.  All monetary values are decimal strings."
    ),
)
async def economic_gain_summary(
    tax_year: str = Query(
        ...,
        description="UK tax year string, e.g. '2024-25'.",
        examples=["2024-25"],
    ),
    _: None = Depends(db_required),
) -> EconomicGainSummarySchema:
    """Economic gain summary for ``tax_year``."""
    supported = available_tax_years()
    if tax_year not in supported:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported tax year: '{tax_year}'. "
                f"Supported values: {supported}"
            ),
        )

    report = ReportService.economic_gain_summary(tax_year)
    return EconomicGainSummarySchema.from_service(report)


@router.get(
    "/audit",
    response_model=list[AuditLogEntrySchema],
    summary="Audit trail",
    description=(
        "All data-mutation audit entries, newest first.  "
        "Optionally filter by ``table_name`` (e.g. ``lots``, ``transactions``, "
        "``securities``) and/or a UTC datetime lower bound.\n\n"
        "The audit log is append-only; no entries are ever modified or deleted."
    ),
)
async def audit_log(
    table_name: str | None = Query(
        None,
        description=(
            "Filter by table name. "
            "Valid values: lots, transactions, securities, lot_disposals."
        ),
    ),
    since: datetime | None = Query(
        None,
        description=(
            "ISO 8601 UTC datetime lower bound (inclusive).  "
            "Example: '2024-04-06T00:00:00'."
        ),
    ),
    _: None = Depends(db_required),
) -> list[AuditLogEntrySchema]:
    """
    Audit log entries, newest first.

    Calls ``ReportService.audit_log(table_name, since)``.
    All returned ``changed_at`` timestamps are naive UTC datetimes.
    """
    entries = ReportService.audit_log(table_name=table_name, since=since)
    return [AuditLogEntrySchema.from_orm_entry(e) for e in entries]
