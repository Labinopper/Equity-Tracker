"""
ReportService — application service for CGT, economic gain, and audit log reports.

All read operations use AppContext.read_session().

Design notes:
  - cgt_summary() and economic_gain_summary() share the same disposal-line query
    via _collect_disposal_lines(); each DisposalLine carries both total_gain_gbp
    (CGT basis) and total_economic_gain_gbp (true-cost basis) so the two reports
    can differ only in which aggregation they surface.

  - cgt_summary() optionally accepts a TaxContext to compute the actual CGT due.
    Without a TaxContext, cgt_result is None — useful for showing gains/losses
    without committing to a specific income scenario.

  - audit_log() is a thin wrapper over AuditRepository.list_all().

  - Returned ORM objects (Transaction, Security, LotDisposal, AuditLog) are
    detached after the session closes. Scalar attributes are safe to access;
    lazy-loaded relationships are not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..app_context import AppContext
from ..core.tax_engine import calculate_cgt, get_bands, tax_year_for_date
from ..core.tax_engine.capital_gains import CgtResult
from ..core.tax_engine.context import TaxContext
from ..core.tax_engine.income_tax import personal_allowance
from ..db.models import AuditLog, Lot, LotDisposal, Security, Transaction
from ..db.repository import (
    AuditRepository,
    DisposalRepository,
    SecurityRepository,
    TransactionRepository,
)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DisposalLine:
    """
    Aggregated disposal event — one per disposal Transaction in the tax year.

    Carries both the CGT (cost-basis) and economic (true-cost) gain totals so
    a single query produces data for both CGT and economic gain reports.

    total_gain_gbp         : taxable gain (CGT view), excluding ISA components
    total_economic_gain_gbp: taxable economic gain, excluding ISA components
    """
    transaction: Transaction
    security: Security
    lot_disposals: list[LotDisposal]
    total_quantity: Decimal
    total_proceeds_gbp: Decimal
    total_gain_gbp: Decimal
    total_economic_gain_gbp: Decimal
    isa_exempt_proceeds_gbp: Decimal = Decimal("0")
    isa_exempt_gain_gbp: Decimal = Decimal("0")
    isa_exempt_economic_gain_gbp: Decimal = Decimal("0")


@dataclass
class CgtSummaryReport:
    """
    CGT report for a single UK tax year.

    disposal_lines : One DisposalLine per DISPOSAL transaction in the year.
    total_gains_gbp: Sum of all positive realised_gain_gbp values.
    total_losses_gbp: Absolute value of all negative realised_gain_gbp values.
    net_gain_gbp   : total_gains - total_losses.
    cgt_result     : Populated if a TaxContext was supplied; None otherwise.
    """
    tax_year: str
    disposal_lines: list[DisposalLine]
    total_proceeds_gbp: Decimal
    total_gains_gbp: Decimal
    total_losses_gbp: Decimal
    net_gain_gbp: Decimal
    isa_exempt_proceeds_gbp: Decimal = Decimal("0")
    isa_exempt_gain_gbp: Decimal = Decimal("0")
    cgt_result: CgtResult | None = field(default=None)


@dataclass
class EconomicGainReport:
    """
    Economic (true-cost) gain report for a single UK tax year.

    Uses realised_gain_economic_gbp instead of the CGT realised_gain_gbp.
    The economic gain reflects the real net cost after income tax savings at
    acquisition (e.g. SIP partnership shares bought from gross salary have a
    lower true cost than their CGT cost basis, so the economic gain is higher).
    """
    tax_year: str
    disposal_lines: list[DisposalLine]
    total_proceeds_gbp: Decimal
    total_economic_gains_gbp: Decimal
    total_economic_losses_gbp: Decimal
    net_economic_gain_gbp: Decimal
    isa_exempt_proceeds_gbp: Decimal = Decimal("0")
    isa_exempt_economic_gain_gbp: Decimal = Decimal("0")


# ---------------------------------------------------------------------------
# ReportService
# ---------------------------------------------------------------------------

class ReportService:
    """
    Application service for CGT, economic gain, and audit log reports.

    All methods are static. All read operations use AppContext.read_session().
    AppContext must be initialised before calling any method.
    """

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _collect_disposal_lines(sess: Session, tax_year: str) -> list[DisposalLine]:
        """
        Build DisposalLine objects for all DISPOSAL transactions in tax_year.

        Shared by cgt_summary() and economic_gain_summary() to avoid duplicate
        queries. Each line carries both CGT and economic gain totals.
        """
        sec_repo  = SecurityRepository(sess)
        tx_repo   = TransactionRepository(sess)
        disp_repo = DisposalRepository(sess)

        disposal_lines: list[DisposalLine] = []

        for security in sec_repo.list_all():
            transactions = tx_repo.list_for_security(
                security.id, transaction_type="DISPOSAL"
            )
            for tx in transactions:
                if tax_year_for_date(tx.transaction_date) != tax_year:
                    continue

                lot_disposals = disp_repo.list_for_transaction(tx.id)
                lot_ids = [d.lot_id for d in lot_disposals]
                scheme_by_lot: dict[str, str] = {}
                if lot_ids:
                    scheme_rows = sess.execute(
                        select(Lot.id, Lot.scheme_type).where(Lot.id.in_(lot_ids))
                    ).all()
                    scheme_by_lot = {lot_id: scheme for lot_id, scheme in scheme_rows}

                total_qty = Decimal("0")
                total_proceeds = Decimal("0")
                total_gain = Decimal("0")
                total_economic = Decimal("0")
                isa_proceeds = Decimal("0")
                isa_gain = Decimal("0")
                isa_economic = Decimal("0")

                for d in lot_disposals:
                    qty = Decimal(d.quantity_allocated)
                    proceeds = Decimal(d.proceeds_gbp)
                    gain = Decimal(d.realised_gain_gbp)
                    economic = Decimal(d.realised_gain_economic_gbp)
                    if scheme_by_lot.get(d.lot_id) == "ISA":
                        isa_proceeds += proceeds
                        isa_gain += gain
                        isa_economic += economic
                        continue
                    total_qty += qty
                    total_proceeds += proceeds
                    total_gain += gain
                    total_economic += economic

                disposal_lines.append(DisposalLine(
                    transaction=tx,
                    security=security,
                    lot_disposals=lot_disposals,
                    total_quantity=total_qty,
                    total_proceeds_gbp=total_proceeds,
                    total_gain_gbp=total_gain,
                    total_economic_gain_gbp=total_economic,
                    isa_exempt_proceeds_gbp=isa_proceeds,
                    isa_exempt_gain_gbp=isa_gain,
                    isa_exempt_economic_gain_gbp=isa_economic,
                ))

        return disposal_lines

    # ── Public read methods ──────────────────────────────────────────────────

    @staticmethod
    def cgt_summary(
        tax_year: str,
        tax_context: TaxContext | None = None,
        prior_year_losses: Decimal = Decimal("0"),
    ) -> CgtSummaryReport:
        """
        CGT summary for a single UK tax year.

        Collects all DISPOSAL transactions whose transaction_date falls within
        tax_year, aggregates gains and losses per disposal, and optionally
        calculates CGT due using the supplied TaxContext.

        Args:
            tax_year         : UK tax year string, e.g. "2024-25".
            tax_context      : If provided, determines CGT rate band (10% or 20%)
                               and computes total CGT payable. If None, cgt_result
                               is None in the returned report.
            prior_year_losses: Unused CGT losses carried forward. Only applied
                               when tax_context is provided.

        Returns CgtSummaryReport.
        """
        with AppContext.read_session() as sess:
            disposal_lines = ReportService._collect_disposal_lines(sess, tax_year)
        taxable_lines = [dl for dl in disposal_lines if dl.total_quantity > Decimal("0")]
        isa_exempt_proceeds = sum(
            (dl.isa_exempt_proceeds_gbp for dl in disposal_lines),
            Decimal("0"),
        )
        isa_exempt_gain = sum(
            (dl.isa_exempt_gain_gbp for dl in disposal_lines),
            Decimal("0"),
        )

        # Aggregate
        total_proceeds = sum(
            (dl.total_proceeds_gbp for dl in taxable_lines), Decimal("0")
        )
        gains_list = [
            dl.total_gain_gbp
            for dl in taxable_lines
            if dl.total_gain_gbp > Decimal("0")
        ]
        losses_list = [
            dl.total_gain_gbp
            for dl in taxable_lines
            if dl.total_gain_gbp < Decimal("0")
        ]
        total_gains  = sum(gains_list, Decimal("0"))
        total_losses = abs(sum(losses_list, Decimal("0")))
        net_gain     = total_gains - total_losses

        # Optional CGT calculation
        cgt_result: CgtResult | None = None
        if tax_context is not None:
            bands = get_bands(tax_year)
            pa    = personal_allowance(bands, tax_context.adjusted_net_income)
            taxable_income_ex_gains = max(
                Decimal("0"),
                tax_context.adjusted_net_income - pa,
            )
            cgt_result = calculate_cgt(
                bands=bands,
                realised_gains=gains_list,
                realised_losses=[abs(loss) for loss in losses_list],
                taxable_income_ex_gains=taxable_income_ex_gains,
                prior_year_losses=prior_year_losses,
            )

        return CgtSummaryReport(
            tax_year=tax_year,
            disposal_lines=taxable_lines,
            total_proceeds_gbp=total_proceeds,
            total_gains_gbp=total_gains,
            total_losses_gbp=total_losses,
            net_gain_gbp=net_gain,
            isa_exempt_proceeds_gbp=isa_exempt_proceeds,
            isa_exempt_gain_gbp=isa_exempt_gain,
            cgt_result=cgt_result,
        )

    @staticmethod
    def economic_gain_summary(tax_year: str) -> EconomicGainReport:
        """
        Economic (true-cost) gain summary for a single UK tax year.

        Identical data source to cgt_summary() but surfaces
        realised_gain_economic_gbp instead of realised_gain_gbp.

        The economic gain reflects the real cost after income tax savings on
        acquisition — for example, SIP partnership shares bought from gross
        salary have a lower true cost than their CGT cost basis, so the
        economic gain is larger than the CGT gain.

        Returns EconomicGainReport.
        """
        with AppContext.read_session() as sess:
            disposal_lines = ReportService._collect_disposal_lines(sess, tax_year)
        taxable_lines = [dl for dl in disposal_lines if dl.total_quantity > Decimal("0")]
        isa_exempt_proceeds = sum(
            (dl.isa_exempt_proceeds_gbp for dl in disposal_lines),
            Decimal("0"),
        )
        isa_exempt_economic = sum(
            (dl.isa_exempt_economic_gain_gbp for dl in disposal_lines),
            Decimal("0"),
        )

        total_proceeds = sum(
            (dl.total_proceeds_gbp for dl in taxable_lines), Decimal("0")
        )
        eco_gains_list = [
            dl.total_economic_gain_gbp
            for dl in taxable_lines
            if dl.total_economic_gain_gbp > Decimal("0")
        ]
        eco_losses_list = [
            dl.total_economic_gain_gbp
            for dl in taxable_lines
            if dl.total_economic_gain_gbp < Decimal("0")
        ]
        total_eco_gains  = sum(eco_gains_list, Decimal("0"))
        total_eco_losses = abs(sum(eco_losses_list, Decimal("0")))
        net_eco          = total_eco_gains - total_eco_losses

        return EconomicGainReport(
            tax_year=tax_year,
            disposal_lines=taxable_lines,
            total_proceeds_gbp=total_proceeds,
            total_economic_gains_gbp=total_eco_gains,
            total_economic_losses_gbp=total_eco_losses,
            net_economic_gain_gbp=net_eco,
            isa_exempt_proceeds_gbp=isa_exempt_proceeds,
            isa_exempt_economic_gain_gbp=isa_exempt_economic,
        )

    @staticmethod
    def audit_log(
        table_name: str | None = None,
        since: datetime | None = None,
    ) -> list[AuditLog]:
        """
        Return audit log entries, newest first.

        Args:
            table_name: Optional filter by table (e.g. "lots", "transactions").
            since     : Optional datetime — only return entries on/after this UTC time.

        Returns a list of AuditLog ORM objects (detached after session close;
        scalar attributes are safe to access).
        """
        with AppContext.read_session() as sess:
            audit = AuditRepository(sess)
            return audit.list_all(table_name=table_name, since=since)
