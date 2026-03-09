"""Add native quote fields to price_ticker_snapshots.

Revision ID: 014
Revises: 013
Create Date: 2026-03-09 14:20:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    columns = {col["name"] for col in sa.inspect(conn).get_columns("price_ticker_snapshots")}
    if "price_native" not in columns:
        op.add_column(
            "price_ticker_snapshots",
            sa.Column("price_native", sa.String(length=30), nullable=True),
        )
    if "currency" not in columns:
        op.add_column(
            "price_ticker_snapshots",
            sa.Column("currency", sa.String(length=3), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    columns = {col["name"] for col in sa.inspect(conn).get_columns("price_ticker_snapshots")}
    if "currency" in columns:
        op.drop_column("price_ticker_snapshots", "currency")
    if "price_native" in columns:
        op.drop_column("price_ticker_snapshots", "price_native")
