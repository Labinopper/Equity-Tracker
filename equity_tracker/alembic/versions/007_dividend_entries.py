"""Add dividend_entries table for dividend dashboard inputs.

Revision ID: 007
Revises: 006
Create Date: 2026-02-24 00:00:00.000000

Changes:
  - Add table ``dividend_entries``:
      id TEXT(36) PK
      security_id TEXT(36) FK(securities.id, RESTRICT)
      dividend_date DATE NOT NULL
      amount_gbp TEXT(30) NOT NULL
      tax_treatment TEXT(20) NOT NULL (TAXABLE | ISA_EXEMPT)
      source TEXT(50) NULL
      notes TEXT NULL
      created_at DATETIME NOT NULL
  - Add indexes:
      ix_dividend_entries_security_date (security_id, dividend_date)
      ix_dividend_entries_date (dividend_date)
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    table_names = set(inspector.get_table_names())
    if "dividend_entries" in table_names:
        return

    op.create_table(
        "dividend_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "security_id",
            sa.String(36),
            sa.ForeignKey("securities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("dividend_date", sa.Date(), nullable=False),
        sa.Column("amount_gbp", sa.String(30), nullable=False),
        sa.Column("tax_treatment", sa.String(20), nullable=False),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "tax_treatment IN ('TAXABLE','ISA_EXEMPT')",
            name="ck_dividend_entries_treatment",
        ),
    )
    op.create_index(
        "ix_dividend_entries_security_date",
        "dividend_entries",
        ["security_id", "dividend_date"],
        unique=False,
    )
    op.create_index(
        "ix_dividend_entries_date",
        "dividend_entries",
        ["dividend_date"],
        unique=False,
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    table_names = set(inspector.get_table_names())
    if "dividend_entries" not in table_names:
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("dividend_entries")}
    if "ix_dividend_entries_date" in existing_indexes:
        op.drop_index("ix_dividend_entries_date", table_name="dividend_entries")
    if "ix_dividend_entries_security_date" in existing_indexes:
        op.drop_index("ix_dividend_entries_security_date", table_name="dividend_entries")
    op.drop_table("dividend_entries")
