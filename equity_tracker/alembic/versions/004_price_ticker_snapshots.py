"""Per-refresh ticker snapshot history for market-freshness UI.

Revision ID: 004
Revises: 003
Create Date: 2026-02-24 00:00:00.000000

Changes:
  - Add table ``price_ticker_snapshots``:
      id TEXT(36) PK
      security_id TEXT(36) FK(securities.id, CASCADE)
      price_date DATE NOT NULL
      price_gbp TEXT(30) NOT NULL
      direction TEXT(20) NULL
      percent_change TEXT(30) NULL
      source TEXT(50) NULL
      observed_at DATETIME NOT NULL
  - Add index ``ix_price_ticker_snapshots_security_observed`` on
    (security_id, observed_at) for recent-history lookups.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    table_names = set(sa.inspect(conn).get_table_names())
    if "price_ticker_snapshots" in table_names:
        return

    op.create_table(
        "price_ticker_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "security_id",
            sa.String(36),
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("price_date", sa.Date(), nullable=False),
        sa.Column("price_gbp", sa.String(30), nullable=False),
        sa.Column("direction", sa.String(20), nullable=True),
        sa.Column("percent_change", sa.String(30), nullable=True),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_price_ticker_snapshots_security_observed",
        "price_ticker_snapshots",
        ["security_id", "observed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_price_ticker_snapshots_security_observed",
        table_name="price_ticker_snapshots",
    )
    op.drop_table("price_ticker_snapshots")

