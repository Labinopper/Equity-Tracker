"""
SQLAlchemy ORM models for equity-tracker.

Design principles:
  - All monetary values stored as TEXT (Decimal strings) — no floats ever.
  - All IDs are UUID v4 strings (TEXT, 36 chars). Portable across SQLite/SQLCipher.
  - Lots are immutable acquisition records; quantity_remaining is the only field
    updated after creation (atomically, with audit log, by the disposal repository).
  - Transactions are append-only; corrections are reversal records.
  - audit_log is strictly append-only; no UPDATE or DELETE ever runs on it.
  - Monetary TEXT fields hold strings like "1234.56" — never scientific notation.
    The repository layer converts to/from decimal.Decimal.

Enum values (stored as TEXT with CHECK constraints):
  SCHEME_TYPES           : RSU, ESPP, ESPP_PLUS, SIP_PARTNERSHIP,
                           SIP_MATCHING, SIP_DIVIDEND, BROKERAGE, ISA
  TRANSACTION_TYPES      : DISPOSAL, DIVIDEND, CORPORATE_ACTION, ADJUSTMENT
  CORPORATE_ACTION_TYPES : SPLIT, MERGE, SPIN_OFF, NAME_CHANGE, TICKER_CHANGE
  AUDIT_ACTIONS          : INSERT, UPDATE, CORRECTION, REVERSAL
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    """Return naive UTC datetime (SQLite stores as text; keep it simple)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Valid enum strings (also enforced by CHECK constraints in the schema)
# ---------------------------------------------------------------------------

VALID_SCHEME_TYPES = (
    "RSU", "ESPP", "ESPP_PLUS",
    "SIP_PARTNERSHIP", "SIP_MATCHING", "SIP_DIVIDEND",
    "BROKERAGE", "ISA",
)

VALID_TRANSACTION_TYPES = (
    "DISPOSAL", "DIVIDEND", "CORPORATE_ACTION", "ADJUSTMENT",
)

VALID_CORPORATE_ACTION_TYPES = (
    "SPLIT", "MERGE", "SPIN_OFF", "NAME_CHANGE", "TICKER_CHANGE",
)

VALID_AUDIT_ACTIONS = (
    "INSERT", "UPDATE", "CORRECTION", "REVERSAL",
)

_SCHEME_CHECK = (
    "scheme_type IN ('RSU','ESPP','ESPP_PLUS',"
    "'SIP_PARTNERSHIP','SIP_MATCHING','SIP_DIVIDEND','BROKERAGE','ISA')"
)
_TX_TYPE_CHECK = (
    "transaction_type IN ('DISPOSAL','DIVIDEND','CORPORATE_ACTION','ADJUSTMENT')"
)
_CA_TYPE_CHECK = (
    "action_type IN ('SPLIT','MERGE','SPIN_OFF','NAME_CHANGE','TICKER_CHANGE')"
)
_AUDIT_ACTION_CHECK = (
    "action IN ('INSERT','UPDATE','CORRECTION','REVERSAL')"
)
_DIVIDEND_TREATMENT_CHECK = (
    "tax_treatment IN ('TAXABLE','ISA_EXEMPT')"
)


# ---------------------------------------------------------------------------
# securities
# ---------------------------------------------------------------------------

class Security(Base):
    """
    A tradeable instrument — a stock, ETF, or fund.

    units_precision: number of decimal places supported for quantity.
      0 = whole shares only (most equities)
      8 = fractional shares at 8 d.p. (some brokers / crypto)
    """

    __tablename__ = "securities"
    __table_args__ = (
        CheckConstraint("length(currency) = 3", name="ck_securities_currency_iso"),
        CheckConstraint(
            "units_precision >= 0 AND units_precision <= 10",
            name="ck_securities_units_precision",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    isin: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)        # ISO 4217
    exchange: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    units_precision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Security catalogue link (Phase S) ─────────────────────────────────
    # catalog_id: FK to security_catalog.id; None for pre-Phase-S records.
    # is_manual_override: True when user chose to bypass the catalogue.
    catalog_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("security_catalog.id", ondelete="SET NULL"), nullable=True
    )
    is_manual_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # Relationships
    grants: Mapped[list[Grant]] = relationship(
        back_populates="security", lazy="select", cascade="all, delete-orphan"
    )
    lots: Mapped[list[Lot]] = relationship(
        back_populates="security", lazy="select", cascade="all, delete-orphan"
    )
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="security", lazy="select", cascade="all, delete-orphan"
    )
    dividend_entries: Mapped[list[DividendEntry]] = relationship(
        back_populates="security", lazy="select", cascade="all, delete-orphan"
    )
    price_history: Mapped[list[PriceHistory]] = relationship(
        back_populates="security", lazy="select", cascade="all, delete-orphan"
    )
    price_ticker_snapshots: Mapped[list[PriceTickerSnapshot]] = relationship(
        back_populates="security", lazy="select", cascade="all, delete-orphan"
    )
    corporate_actions: Mapped[list[CorporateAction]] = relationship(
        back_populates="security", lazy="select", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# grants  (equity award metadata — the "header" above individual lots)
# ---------------------------------------------------------------------------

class Grant(Base):
    """
    An equity grant or scheme enrolment.

    One Grant can correspond to multiple Lots (e.g. RSU vest schedule
    where each monthly vest is a separate Lot sharing the same Grant).

    vest_schedule_json: optional JSON string for future vest-schedule modelling.
    """

    __tablename__ = "grants"
    __table_args__ = (
        CheckConstraint(_SCHEME_CHECK, name="ck_grants_scheme_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.id", ondelete="RESTRICT"), nullable=False
    )
    scheme_type: Mapped[str] = mapped_column(String(20), nullable=False)
    grant_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    grant_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    vest_schedule_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    security: Mapped[Security] = relationship(back_populates="grants")
    lots: Mapped[list[Lot]] = relationship(
        back_populates="grant", lazy="select"
    )


# ---------------------------------------------------------------------------
# lots  (individual acquisition tranches — one row per vest / purchase event)
# ---------------------------------------------------------------------------

class Lot(Base):
    """
    An immutable acquisition record.

    Invariants (enforced by repository layer — TEXT storage prevents SQL CHECK):
      - quantity          > 0
      - quantity_remaining >= 0  and  quantity_remaining <= quantity
      - acquisition_price_gbp (CGT cost basis per share) >= 0
      - true_cost_per_share_gbp (economic cost per share) >= 0

    Mutable field:
      - quantity_remaining: reduced atomically by DisposalRepository.

    All monetary fields are Decimal-as-TEXT (e.g. "1234.56").
    """

    __tablename__ = "lots"
    __table_args__ = (
        CheckConstraint(_SCHEME_CHECK, name="ck_lots_scheme_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.id", ondelete="RESTRICT"), nullable=False
    )
    grant_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("grants.id", ondelete="SET NULL"), nullable=True
    )
    scheme_type: Mapped[str] = mapped_column(String(20), nullable=False)
    tax_year: Mapped[str] = mapped_column(String(7), nullable=False)        # "2024-25"

    acquisition_date: Mapped[date] = mapped_column(Date, nullable=False)

    # ── Quantity ──────────────────────────────────────────────────────────
    # TEXT Decimal strings; repository converts to/from Decimal.
    quantity: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity_remaining: Mapped[str] = mapped_column(String(30), nullable=False)

    # ── Cost basis (GBP, per share) ───────────────────────────────────────
    # acquisition_price_gbp : CGT cost basis per share (what HMRC sees)
    # true_cost_per_share_gbp: economic/net cost per share (after tax savings)
    # fmv_at_acquisition_gbp : FMV per share at acquisition date (reference)
    acquisition_price_gbp: Mapped[str] = mapped_column(String(30), nullable=False)
    true_cost_per_share_gbp: Mapped[str] = mapped_column(String(30), nullable=False)
    fmv_at_acquisition_gbp: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )

    # ── Original currency (for FX-denominated acquisitions) ───────────────
    acquisition_price_original_ccy: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )
    original_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    broker_currency: Mapped[Optional[str]] = mapped_column(
        String(3), nullable=True
    )    # e.g. "USD", "GBP" (broker holding currency context)
    fx_rate_at_acquisition: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )
    fx_rate_source: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )    # e.g. "exchangerate_host", "manual", "hmrc_monthly"

    # ── Import / idempotency ──────────────────────────────────────────────
    broker_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    import_source: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )    # e.g. "etrade_csv", "manual"
    external_id: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, unique=True
    )    # idempotency key; unique so duplicate imports are detected

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── ESPP+ forfeiture ──────────────────────────────────────────────────────
    # forfeiture_period_end: exact end of 6-month matching-share forfeiture window.
    #   Set on ESPP_PLUS lots at creation. NULL for all other scheme types and for
    #   legacy ESPP_PLUS lots (pre-Phase E). _forfeiture_risk_for_lot() falls back
    #   to acquisition_date + 183 days when NULL.
    # matching_lot_id: for ESPP_PLUS lots, the lot_id of the linked ESPP partnership
    #   lot. Selling the partnership lot within 6 months forfeits this matching lot.
    forfeiture_period_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    matching_lot_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("lots.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
    # updated_at should only change when quantity_remaining is reduced.
    # All other fields are immutable after creation.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    security: Mapped[Security] = relationship(back_populates="lots")
    grant: Mapped[Optional[Grant]] = relationship(back_populates="lots")
    lot_disposals: Mapped[list[LotDisposal]] = relationship(
        back_populates="lot", lazy="select"
    )


# ---------------------------------------------------------------------------
# transactions  (disposal / dividend / corporate-action events)
# ---------------------------------------------------------------------------

class Transaction(Base):
    """
    An immutable event record for a security-level action.

    Corrections are NOT made by editing this row. Instead:
      1. Create a new Transaction with is_reversal=True pointing at this id.
      2. Create a corrected Transaction.
      3. The DisposalRepository handles re-allocation atomically.

    broker_fees_gbp: allowable acquisition costs that reduce CGT proceeds.
      For disposals, this reduces the net proceeds for CGT purposes.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(_TX_TYPE_CHECK, name="ck_transactions_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.id", ondelete="RESTRICT"), nullable=False
    )
    transaction_type: Mapped[str] = mapped_column(String(20), nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)

    quantity: Mapped[str] = mapped_column(String(30), nullable=False)
    price_per_share_gbp: Mapped[str] = mapped_column(String(30), nullable=False)
    total_proceeds_gbp: Mapped[str] = mapped_column(String(30), nullable=False)

    # Original currency (if disposal was in USD etc.)
    price_per_share_original_ccy: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )
    original_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    fx_rate: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    # Broker fees reduce net CGT proceeds (HMRC allowable cost)
    broker_fees_gbp: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    # Import / idempotency
    broker_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    import_source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, unique=True
    )

    # Reversal support (append-only correction model)
    is_reversal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reverses_transaction_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=True
    )

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    security: Mapped[Security] = relationship(back_populates="transactions")
    lot_disposals: Mapped[list[LotDisposal]] = relationship(
        back_populates="transaction", lazy="select", cascade="all, delete-orphan"
    )
    reversed_transaction: Mapped[Optional[Transaction]] = relationship(
        "Transaction",
        foreign_keys=[reverses_transaction_id],
        remote_side="Transaction.id",
        lazy="select",
    )


# ---------------------------------------------------------------------------
# lot_disposals  (FIFO allocation: one row per lot consumed by a transaction)
# ---------------------------------------------------------------------------

class LotDisposal(Base):
    """
    Records how a specific Lot's shares were consumed by a disposal Transaction.

    The FIFO engine produces one LotDisposal per Lot consumed. All fields are
    computed at disposal time and stored for audit and CGT reporting.

    realised_gain_gbp        : proceeds - cost_basis (CGT gain/loss)
    realised_gain_economic_gbp: proceeds - true_cost (economic gain/loss;
                               accounts for tax savings on acquisition)
    """

    __tablename__ = "lot_disposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=False
    )
    lot_id: Mapped[str] = mapped_column(
        ForeignKey("lots.id", ondelete="RESTRICT"), nullable=False
    )

    quantity_allocated: Mapped[str] = mapped_column(String(30), nullable=False)

    # All monetary fields are Decimal-as-TEXT; total for this allocation (not per share)
    cost_basis_gbp: Mapped[str] = mapped_column(
        String(30), nullable=False
    )               # qty * lot.acquisition_price_gbp
    true_cost_gbp: Mapped[str] = mapped_column(
        String(30), nullable=False
    )               # qty * lot.true_cost_per_share_gbp
    proceeds_gbp: Mapped[str] = mapped_column(
        String(30), nullable=False
    )               # qty * disposal_price_per_share

    realised_gain_gbp: Mapped[str] = mapped_column(
        String(30), nullable=False
    )               # proceeds - cost_basis  (positive = gain, negative = loss)
    realised_gain_economic_gbp: Mapped[str] = mapped_column(
        String(30), nullable=False
    )               # proceeds - true_cost

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )

    transaction: Mapped[Transaction] = relationship(back_populates="lot_disposals")
    lot: Mapped[Lot] = relationship(back_populates="lot_disposals")


# ---------------------------------------------------------------------------
# employment_tax_events
# ---------------------------------------------------------------------------

class EmploymentTaxEvent(Base):
    """
    Structured employment-tax event emitted during non-disposal workflows.

    Current producer:
      - ESPP+ transfer to BROKERAGE in PortfolioService.transfer_lot_to_brokerage()
    """

    __tablename__ = "employment_tax_events"
    __table_args__ = (
        Index(
            "ix_employment_tax_events_lot_event_date",
            "lot_id",
            "event_date",
        ),
        Index(
            "ix_employment_tax_events_security_event_date",
            "security_id",
            "event_date",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    lot_id: Mapped[str] = mapped_column(
        ForeignKey("lots.id", ondelete="RESTRICT"), nullable=False
    )
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    estimated_tax_gbp: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    estimation_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )

    lot: Mapped[Lot] = relationship(lazy="select")
    security: Mapped[Security] = relationship(lazy="select")


# ---------------------------------------------------------------------------
# dividend_entries
# ---------------------------------------------------------------------------

class DividendEntry(Base):
    """
    Manual dividend record used by the dividend dashboard.

    tax_treatment:
      - TAXABLE: dividend contributes to estimated dividend tax.
      - ISA_EXEMPT: dividend is tracked but excluded from tax due.
    """

    __tablename__ = "dividend_entries"
    __table_args__ = (
        CheckConstraint(_DIVIDEND_TREATMENT_CHECK, name="ck_dividend_entries_treatment"),
        Index("ix_dividend_entries_security_date", "security_id", "dividend_date"),
        Index("ix_dividend_entries_date", "dividend_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.id", ondelete="RESTRICT"), nullable=False
    )
    dividend_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_gbp: Mapped[str] = mapped_column(String(30), nullable=False)
    amount_original_ccy: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    original_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    fx_rate_to_gbp: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    fx_rate_source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    tax_treatment: Mapped[str] = mapped_column(
        String(20), nullable=False, default="TAXABLE"
    )
    source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )

    security: Mapped[Security] = relationship(back_populates="dividend_entries")


# ---------------------------------------------------------------------------
# fx_rates
# ---------------------------------------------------------------------------

class FxRate(Base):
    """
    Historical FX rate cache.

    base_currency / quote_currency: ISO 4217 codes.
    rate: 1 unit of base_currency = rate units of quote_currency.
          e.g. base=USD, quote=GBP, rate="0.79" means 1 USD = 0.79 GBP.

    is_manual_override: True if user has manually set this rate, overriding
                        any auto-fetched value for the same date/source.
    """

    __tablename__ = "fx_rates"
    __table_args__ = (
        UniqueConstraint(
            "base_currency", "quote_currency", "rate_date", "source",
            name="uq_fx_rates_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate_date: Mapped[date] = mapped_column(Date, nullable=False)
    rate: Mapped[str] = mapped_column(String(30), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    is_manual_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )


# ---------------------------------------------------------------------------
# price_history
# ---------------------------------------------------------------------------

class PriceHistory(Base):
    """
    End-of-day closing prices for a security.

    close_price_gbp is optional (populated when FX conversion is available).
    For GBP-denominated securities, close_price_gbp == close_price_original_ccy.
    """

    __tablename__ = "price_history"
    __table_args__ = (
        UniqueConstraint(
            "security_id", "price_date", "source",
            name="uq_price_history_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    price_date: Mapped[date] = mapped_column(Date, nullable=False)
    close_price_original_ccy: Mapped[str] = mapped_column(String(30), nullable=False)
    close_price_gbp: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    is_manual_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )

    security: Mapped[Security] = relationship(back_populates="price_history")


# ---------------------------------------------------------------------------
# price_ticker_snapshots
# ---------------------------------------------------------------------------

class PriceTickerSnapshot(Base):
    """
    Per-refresh ticker snapshot used by the portfolio daily-change badge.

    Stores the displayed GBP price and the computed daily direction/percent
    at refresh time so UI freshness can be derived from DB history.
    """

    __tablename__ = "price_ticker_snapshots"
    __table_args__ = (
        Index(
            "ix_price_ticker_snapshots_security_observed",
            "security_id",
            "observed_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    price_date: Mapped[date] = mapped_column(Date, nullable=False)
    price_gbp: Mapped[str] = mapped_column(String(30), nullable=False)
    direction: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    percent_change: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )

    security: Mapped[Security] = relationship(back_populates="price_ticker_snapshots")


# ---------------------------------------------------------------------------
# corporate_actions  (schema placeholder — full logic in a later phase)
# ---------------------------------------------------------------------------

class CorporateAction(Base):
    """
    Corporate action placeholder. Schema is defined now; processing logic
    (lot quantity/price adjustments for splits, spin-offs etc.) is Phase 3+.

    ratio_numerator / ratio_denominator: for SPLIT/MERGE.
      e.g. 2-for-1 split: numerator=2, denominator=1 (new_qty = old_qty × 2/1)

    is_applied: True once lot quantities/prices have been adjusted.
    """

    __tablename__ = "corporate_actions"
    __table_args__ = (
        CheckConstraint(_CA_TYPE_CHECK, name="ck_corporate_actions_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.id", ondelete="RESTRICT"), nullable=False
    )
    action_date: Mapped[date] = mapped_column(Date, nullable=False)
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)

    ratio_numerator: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    ratio_denominator: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )

    security: Mapped[Security] = relationship(back_populates="corporate_actions")


# ---------------------------------------------------------------------------
# audit_log  (strictly append-only — no UPDATE or DELETE permitted)
# ---------------------------------------------------------------------------

class AuditLog(Base):
    """
    Append-only audit trail for all data mutations.

    Rules:
      - Only INSERT is allowed on this table.
      - The repository layer writes an audit entry for every INSERT, UPDATE
        (quantity_remaining only), and REVERSAL on other tables.
      - old_values_json / new_values_json are JSON strings of the changed fields.

    action values:
      INSERT     : new record created
      UPDATE     : quantity_remaining updated on a Lot
      CORRECTION : a reversal transaction was created
      REVERSAL   : a lot_disposal was reversed
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(_AUDIT_ACTION_CHECK, name="ck_audit_log_action"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    table_name: Mapped[str] = mapped_column(String(50), nullable=False)
    record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)

    old_values_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_values_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    changed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )


# ---------------------------------------------------------------------------
# security_catalog  (verified instrument catalogue — Phase S)
# ---------------------------------------------------------------------------

class SecurityCatalog(Base):
    """
    Verified instrument catalogue.

    Provides a validated reference list of tradeable securities from LSE,
    NYSE, and NASDAQ. Securities added via the UI must be selected from this
    catalogue unless the user explicitly enables is_manual_override.

    Populated on first run via SecurityCatalogRepository.seed_from_csv().

    symbol   : ticker symbol, uppercased (e.g. "TSCO", "AAPL")
    name     : official instrument name (e.g. "Tesco PLC", "Apple Inc.")
    exchange : exchange code, uppercased (e.g. "LSE", "NYSE", "NASDAQ")
    currency : ISO 4217 code (e.g. "GBP", "USD")
    isin     : optional ISIN identifier (12 chars)
    figi     : optional FIGI identifier (12 chars)
    """

    __tablename__ = "security_catalog"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "exchange", name="uq_security_catalog_symbol_exchange"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    isin: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    figi: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
