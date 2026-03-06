"""Add native-currency and FX provenance columns to dividend_entries.

Revision ID: 009
Revises: 008
Create Date: 2026-03-06 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_column_names(table_name: str) -> set[str]:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "dividend_entries" not in set(inspector.get_table_names()):
        return

    existing = _existing_column_names("dividend_entries")
    if "amount_original_ccy" not in existing:
        op.add_column(
            "dividend_entries",
            sa.Column("amount_original_ccy", sa.String(length=30), nullable=True),
        )
    if "original_currency" not in existing:
        op.add_column(
            "dividend_entries",
            sa.Column("original_currency", sa.String(length=3), nullable=True),
        )
    if "fx_rate_to_gbp" not in existing:
        op.add_column(
            "dividend_entries",
            sa.Column("fx_rate_to_gbp", sa.String(length=30), nullable=True),
        )
    if "fx_rate_source" not in existing:
        op.add_column(
            "dividend_entries",
            sa.Column("fx_rate_source", sa.String(length=50), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "dividend_entries" not in set(inspector.get_table_names()):
        return

    existing = _existing_column_names("dividend_entries")
    with op.batch_alter_table("dividend_entries") as batch_op:
        if "fx_rate_source" in existing:
            batch_op.drop_column("fx_rate_source")
        if "fx_rate_to_gbp" in existing:
            batch_op.drop_column("fx_rate_to_gbp")
        if "original_currency" in existing:
            batch_op.drop_column("original_currency")
        if "amount_original_ccy" in existing:
            batch_op.drop_column("amount_original_ccy")
