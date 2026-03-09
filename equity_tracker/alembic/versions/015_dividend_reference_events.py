"""Add dividend reference events table.

Revision ID: 015
Revises: 014
Create Date: 2026-03-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in set(inspector.get_table_names())


def upgrade() -> None:
    if _table_exists("dividend_reference_events"):
        return

    op.create_table(
        "dividend_reference_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("security_id", sa.String(length=36), nullable=False),
        sa.Column("ex_dividend_date", sa.Date(), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("amount_original_ccy", sa.String(length=30), nullable=False),
        sa.Column("original_currency", sa.String(length=3), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("provider_event_key", sa.String(length=200), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["security_id"], ["securities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_event_key", name="uq_dividend_reference_provider_key"),
    )
    op.create_index(
        "ix_dividend_reference_security_ex_date",
        "dividend_reference_events",
        ["security_id", "ex_dividend_date"],
        unique=False,
    )


def downgrade() -> None:
    if not _table_exists("dividend_reference_events"):
        return
    op.drop_index("ix_dividend_reference_security_ex_date", table_name="dividend_reference_events")
    op.drop_table("dividend_reference_events")
