"""
Pydantic response schemas for report endpoints.

Design contract — same as schemas/portfolio.py:
  - Monetary fields: always decimal strings, never float
  - date / datetime fields: Python objects serialised as ISO 8601 by Pydantic v2
  - ORM objects never returned directly; factory classmethods do the mapping
  - Service-level Decimal values converted with str()

Note on DisposalLine
────────────────────
``DisposalLine`` (from ReportService) carries BOTH the CGT gain
(``total_gain_gbp``) and the economic gain (``total_economic_gain_gbp``) on
every disposal.  Both ``CgtSummarySchema`` and ``EconomicGainSummarySchema``
therefore share the same ``DisposalLineSchema`` — they differ only in which
aggregate total fields they surface.

Note on AuditLog timestamps
────────────────────────────
``AuditLog.changed_at`` is stored as a naive UTC datetime (no tzinfo).
Pydantic v2 serialises it as ``"2024-04-06T14:23:01"`` — without a
timezone suffix.  Callers should treat this as UTC.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from ...db.models import AuditLog, LotDisposal
from ...services.report_service import (
    CgtSummaryReport,
    DisposalLine,
    EconomicGainReport,
)


# ---------------------------------------------------------------------------
# LotDisposal  (per-lot FIFO allocation within a disposal transaction)
# ---------------------------------------------------------------------------

class LotDisposalSchema(BaseModel):
    """
    Records how one lot's shares were consumed by a disposal transaction.

    All monetary fields are ``TEXT`` decimal strings from the ORM — passed
    through without conversion.  Positive ``realised_gain_*`` = gain;
    negative = loss.
    """

    id: str
    lot_id: str
    quantity_allocated: str          # Decimal as string
    cost_basis_gbp: str              # qty × lot.acquisition_price_gbp
    true_cost_gbp: str               # qty × lot.true_cost_per_share_gbp
    proceeds_gbp: str                # qty × disposal price per share
    realised_gain_gbp: str           # proceeds - cost_basis  (CGT view)
    realised_gain_economic_gbp: str  # proceeds - true_cost   (economic view)

    @classmethod
    def from_orm(cls, d: LotDisposal) -> "LotDisposalSchema":
        return cls(
            id=d.id,
            lot_id=d.lot_id,
            quantity_allocated=d.quantity_allocated,
            cost_basis_gbp=d.cost_basis_gbp,
            true_cost_gbp=d.true_cost_gbp,
            proceeds_gbp=d.proceeds_gbp,
            realised_gain_gbp=d.realised_gain_gbp,
            realised_gain_economic_gbp=d.realised_gain_economic_gbp,
        )


# ---------------------------------------------------------------------------
# DisposalLine  (one disposal transaction + all its lot allocations)
# ---------------------------------------------------------------------------

class DisposalLineSchema(BaseModel):
    """
    A single disposal transaction with per-lot FIFO allocation detail.

    Carries both CGT (``total_gain_gbp``) and economic (``total_economic_gain_gbp``)
    gain totals so that a single query populates both report types.

    ``transaction_date`` — Pydantic v2 serialises as ISO 8601 date string.
    ``quantity`` / ``proceeds_gbp`` — decimal strings from the ORM / service.
    ``total_*`` fields — converted from service-level Decimal via str().
    """

    transaction_id: str
    security_id: str
    security_ticker: str
    security_name: str

    # date → serialised as ISO 8601 string by Pydantic
    transaction_date: date

    quantity: str          # Decimal as string — from ORM Transaction.quantity
    proceeds_gbp: str      # Decimal as string — total disposal proceeds

    # Aggregated from LotDisposal rows — converted from service Decimal
    total_gain_gbp: str           # sum of realised_gain_gbp (CGT basis)
    total_economic_gain_gbp: str  # sum of realised_gain_economic_gbp

    lot_disposals: list[LotDisposalSchema]

    @classmethod
    def from_service(cls, dl: DisposalLine) -> "DisposalLineSchema":
        return cls(
            transaction_id=dl.transaction.id,
            security_id=dl.security.id,
            security_ticker=dl.security.ticker,
            security_name=dl.security.name,
            transaction_date=dl.transaction.transaction_date,
            # quantity comes from the ORM (already a string)
            quantity=dl.transaction.quantity,
            # proceeds from service Decimal (ensures consistent precision)
            proceeds_gbp=str(dl.total_proceeds_gbp),
            total_gain_gbp=str(dl.total_gain_gbp),
            total_economic_gain_gbp=str(dl.total_economic_gain_gbp),
            lot_disposals=[
                LotDisposalSchema.from_orm(d) for d in dl.lot_disposals
            ],
        )


# ---------------------------------------------------------------------------
# CGT summary report
# ---------------------------------------------------------------------------

class CgtResultSchema(BaseModel):
    """
    Full CGT calculation breakdown — populated when ``include_tax_due=true``
    is passed to GET /reports/cgt and income settings are configured.

    ``effective_rate`` is stored as a Decimal fraction (e.g. ``"0.1500"`` = 15%).
    All monetary fields are decimal strings.
    """

    total_gain: str
    total_loss: str
    net_gain: str
    annual_exempt_amount: str
    taxable_gain: str
    tax_at_basic_rate: str
    tax_at_higher_rate: str
    total_cgt: str
    effective_rate: str
    notes: list[str]

    @classmethod
    def from_cgt_result(cls, r) -> "CgtResultSchema":  # r: CgtResult (avoid import)
        return cls(
            total_gain=str(r.total_gain),
            total_loss=str(r.total_loss),
            net_gain=str(r.net_gain),
            annual_exempt_amount=str(r.annual_exempt_amount),
            taxable_gain=str(r.taxable_gain),
            tax_at_basic_rate=str(r.tax_at_basic_rate),
            tax_at_higher_rate=str(r.tax_at_higher_rate),
            total_cgt=str(r.total_cgt),
            effective_rate=str(r.effective_rate),
            notes=list(r.notes),
        )


class CgtSummarySchema(BaseModel):
    """
    Capital Gains Tax summary for a single UK tax year.

    ``total_gains_gbp``  — sum of positive disposal gains only.
    ``total_losses_gbp`` — absolute value of disposal losses.
    ``net_gain_gbp``     — gains minus losses (may be negative).
    ``cgt_result``       — CGT due calculation; None unless ``include_tax_due=true``
                           is requested and income settings are non-zero.

    All monetary values are decimal strings — never floats.
    """

    tax_year: str
    disposals: list[DisposalLineSchema]
    total_proceeds_gbp: str
    total_gains_gbp: str
    total_losses_gbp: str
    net_gain_gbp: str
    cgt_result: CgtResultSchema | None = None

    @classmethod
    def from_service(cls, r: CgtSummaryReport) -> "CgtSummarySchema":
        return cls(
            tax_year=r.tax_year,
            disposals=[
                DisposalLineSchema.from_service(dl) for dl in r.disposal_lines
            ],
            total_proceeds_gbp=str(r.total_proceeds_gbp),
            total_gains_gbp=str(r.total_gains_gbp),
            total_losses_gbp=str(r.total_losses_gbp),
            net_gain_gbp=str(r.net_gain_gbp),
            cgt_result=(
                CgtResultSchema.from_cgt_result(r.cgt_result)
                if r.cgt_result is not None
                else None
            ),
        )


# ---------------------------------------------------------------------------
# Economic gain summary report
# ---------------------------------------------------------------------------

class EconomicGainSummarySchema(BaseModel):
    """
    Economic (true-cost) gain summary for a single UK tax year.

    Uses ``realised_gain_economic_gbp`` instead of ``realised_gain_gbp``.
    The difference is largest for SIP Partnership shares where the
    economic cost (post-tax-saving) is lower than the CGT cost basis.

    All monetary values are decimal strings — never floats.
    """

    tax_year: str
    disposals: list[DisposalLineSchema]
    total_proceeds_gbp: str           # Decimal → str
    total_economic_gains_gbp: str     # Decimal → str
    total_economic_losses_gbp: str    # Decimal → str (absolute value)
    net_economic_gain_gbp: str        # Decimal → str

    @classmethod
    def from_service(cls, r: EconomicGainReport) -> "EconomicGainSummarySchema":
        return cls(
            tax_year=r.tax_year,
            disposals=[
                DisposalLineSchema.from_service(dl) for dl in r.disposal_lines
            ],
            total_proceeds_gbp=str(r.total_proceeds_gbp),
            total_economic_gains_gbp=str(r.total_economic_gains_gbp),
            total_economic_losses_gbp=str(r.total_economic_losses_gbp),
            net_economic_gain_gbp=str(r.net_economic_gain_gbp),
        )


# ---------------------------------------------------------------------------
# Audit log entry
# ---------------------------------------------------------------------------

class AuditLogEntrySchema(BaseModel):
    """
    A single audit trail entry.

    ``old_values_json`` / ``new_values_json`` are raw JSON strings as stored
    in the database.  Callers should parse them if structured access is needed.

    ``changed_at`` is a naive UTC datetime; Pydantic v2 serialises it as an
    ISO 8601 string without a timezone suffix.  Treat as UTC.
    """

    id: str
    table_name: str           # e.g. "lots", "transactions", "securities"
    record_id: str            # UUID of the affected row
    action: str               # INSERT | UPDATE | CORRECTION | REVERSAL
    old_values_json: str | None = None
    new_values_json: str | None = None
    notes: str | None = None
    changed_at: datetime      # naive UTC — serialised as ISO 8601

    @classmethod
    def from_orm_entry(cls, entry: AuditLog) -> "AuditLogEntrySchema":
        return cls(
            id=entry.id,
            table_name=entry.table_name,
            record_id=entry.record_id,
            action=entry.action,
            old_values_json=entry.old_values_json,
            new_values_json=entry.new_values_json,
            notes=entry.notes,
            changed_at=entry.changed_at,
        )
