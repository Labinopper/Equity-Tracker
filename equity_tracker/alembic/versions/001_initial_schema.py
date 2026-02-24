"""Initial schema — all Phase 2 tables.

Revision ID: 001
Revises:
Create Date: 2025-01-01 00:00:00.000000

Tables created:
  securities, grants, lots, transactions, lot_disposals,
  fx_rates, price_history, corporate_actions, audit_log

Notes:
  - All monetary columns are TEXT (Decimal-as-string). No NUMERIC/REAL.
  - Enum values enforced via CHECK constraints (portable with SQLite/SQLCipher).
  - render_as_batch=True in env.py handles SQLite's lack of ALTER TABLE.
  - SQLite enforces FK constraints only when PRAGMA foreign_keys = ON, which
    the DatabaseEngine sets on every connection.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCHEME_CHECK = (
    "scheme_type IN ('RSU','ESPP','ESPP_PLUS',"
    "'SIP_PARTNERSHIP','SIP_MATCHING','SIP_DIVIDEND','BROKERAGE')"
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


def upgrade() -> None:
    # ── securities ───────────────────────────────────────────────────────
    op.create_table(
        "securities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("isin", sa.String(12), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=True),
        sa.Column("units_precision", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.CheckConstraint("length(currency) = 3", name="ck_securities_currency_iso"),
        sa.CheckConstraint(
            "units_precision >= 0 AND units_precision <= 10",
            name="ck_securities_units_precision",
        ),
    )

    # ── grants ───────────────────────────────────────────────────────────
    op.create_table(
        "grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("security_id", sa.String(36), sa.ForeignKey("securities.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("scheme_type", sa.String(20), nullable=False),
        sa.Column("grant_date", sa.Date, nullable=True),
        sa.Column("grant_reference", sa.String(100), nullable=True),
        sa.Column("vest_schedule_json", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.CheckConstraint(_SCHEME_CHECK, name="ck_grants_scheme_type"),
    )

    # ── lots ─────────────────────────────────────────────────────────────
    op.create_table(
        "lots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("security_id", sa.String(36), sa.ForeignKey("securities.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("grant_id", sa.String(36), sa.ForeignKey("grants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("scheme_type", sa.String(20), nullable=False),
        sa.Column("tax_year", sa.String(7), nullable=False),
        sa.Column("acquisition_date", sa.Date, nullable=False),
        sa.Column("quantity", sa.String(30), nullable=False),
        sa.Column("quantity_remaining", sa.String(30), nullable=False),
        sa.Column("acquisition_price_gbp", sa.String(30), nullable=False),
        sa.Column("true_cost_per_share_gbp", sa.String(30), nullable=False),
        sa.Column("fmv_at_acquisition_gbp", sa.String(30), nullable=True),
        sa.Column("acquisition_price_original_ccy", sa.String(30), nullable=True),
        sa.Column("original_currency", sa.String(3), nullable=True),
        sa.Column("fx_rate_at_acquisition", sa.String(30), nullable=True),
        sa.Column("fx_rate_source", sa.String(50), nullable=True),
        sa.Column("broker_reference", sa.String(100), nullable=True),
        sa.Column("import_source", sa.String(50), nullable=True),
        sa.Column("external_id", sa.String(200), nullable=True, unique=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.CheckConstraint(_SCHEME_CHECK, name="ck_lots_scheme_type"),
    )

    # ── transactions ─────────────────────────────────────────────────────
    op.create_table(
        "transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("security_id", sa.String(36), sa.ForeignKey("securities.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("transaction_type", sa.String(20), nullable=False),
        sa.Column("transaction_date", sa.Date, nullable=False),
        sa.Column("quantity", sa.String(30), nullable=False),
        sa.Column("price_per_share_gbp", sa.String(30), nullable=False),
        sa.Column("total_proceeds_gbp", sa.String(30), nullable=False),
        sa.Column("price_per_share_original_ccy", sa.String(30), nullable=True),
        sa.Column("original_currency", sa.String(3), nullable=True),
        sa.Column("fx_rate", sa.String(30), nullable=True),
        sa.Column("broker_fees_gbp", sa.String(30), nullable=True),
        sa.Column("broker_reference", sa.String(100), nullable=True),
        sa.Column("import_source", sa.String(50), nullable=True),
        sa.Column("external_id", sa.String(200), nullable=True, unique=True),
        sa.Column("is_reversal", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("reverses_transaction_id", sa.String(36), sa.ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.CheckConstraint(_TX_TYPE_CHECK, name="ck_transactions_type"),
    )

    # ── lot_disposals ────────────────────────────────────────────────────
    op.create_table(
        "lot_disposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("transaction_id", sa.String(36), sa.ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("lot_id", sa.String(36), sa.ForeignKey("lots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity_allocated", sa.String(30), nullable=False),
        sa.Column("cost_basis_gbp", sa.String(30), nullable=False),
        sa.Column("true_cost_gbp", sa.String(30), nullable=False),
        sa.Column("proceeds_gbp", sa.String(30), nullable=False),
        sa.Column("realised_gain_gbp", sa.String(30), nullable=False),
        sa.Column("realised_gain_economic_gbp", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )

    # ── fx_rates ─────────────────────────────────────────────────────────
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("rate_date", sa.Date, nullable=False),
        sa.Column("rate", sa.String(30), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("is_manual_override", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("fetched_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("base_currency", "quote_currency", "rate_date", "source", name="uq_fx_rates_key"),
    )

    # ── price_history ────────────────────────────────────────────────────
    op.create_table(
        "price_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("security_id", sa.String(36), sa.ForeignKey("securities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("price_date", sa.Date, nullable=False),
        sa.Column("close_price_original_ccy", sa.String(30), nullable=False),
        sa.Column("close_price_gbp", sa.String(30), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("is_manual_override", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("fetched_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("security_id", "price_date", "source", name="uq_price_history_key"),
    )

    # ── corporate_actions ────────────────────────────────────────────────
    op.create_table(
        "corporate_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("security_id", sa.String(36), sa.ForeignKey("securities.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action_date", sa.Date, nullable=False),
        sa.Column("action_type", sa.String(20), nullable=False),
        sa.Column("ratio_numerator", sa.String(20), nullable=True),
        sa.Column("ratio_denominator", sa.String(20), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("is_applied", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.CheckConstraint(_CA_TYPE_CHECK, name="ck_corporate_actions_type"),
    )

    # ── audit_log ────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("table_name", sa.String(50), nullable=False),
        sa.Column("record_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("old_values_json", sa.Text, nullable=True),
        sa.Column("new_values_json", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("changed_at", sa.DateTime, nullable=False),
        sa.CheckConstraint(_AUDIT_ACTION_CHECK, name="ck_audit_log_action"),
    )


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("audit_log")
    op.drop_table("corporate_actions")
    op.drop_table("price_history")
    op.drop_table("fx_rates")
    op.drop_table("lot_disposals")
    op.drop_table("transactions")
    op.drop_table("lots")
    op.drop_table("grants")
    op.drop_table("securities")
