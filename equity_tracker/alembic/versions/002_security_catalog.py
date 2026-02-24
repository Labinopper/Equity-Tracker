"""Security catalogue — Phase S.

Revision ID: 002
Revises: 001
Create Date: 2026-02-23 00:00:00.000000

Changes:
  - Create table security_catalog (id, symbol, name, exchange, currency, isin, figi, created_at)
  - Add indexes on security_catalog.symbol and security_catalog.name
  - Add columns catalog_id and is_manual_override to securities

Notes:
  - UniqueConstraint on (symbol, exchange) prevents duplicate catalogue entries.
  - catalog_id FK uses ondelete=SET NULL: dropping a catalogue row does not
    break any existing security record.
  - is_manual_override defaults to False; pre-Phase-S records also get False
    (they were added manually, but retroactively marking them as overrides is
    acceptable since they predate catalogue enforcement).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── security_catalog ─────────────────────────────────────────────────
    # Guard: a previous failed run may have created the table already.
    existing_tables = sa.inspect(conn).get_table_names()
    if "security_catalog" not in existing_tables:
        op.create_table(
            "security_catalog",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("symbol", sa.String(20), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("exchange", sa.String(20), nullable=False),
            sa.Column("currency", sa.String(3), nullable=False),
            sa.Column("isin", sa.String(12), nullable=True),
            sa.Column("figi", sa.String(12), nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.UniqueConstraint("symbol", "exchange", name="uq_security_catalog_symbol_exchange"),
        )

    # Indexes are idempotent via if_not_exists.
    op.create_index("ix_security_catalog_symbol", "security_catalog", ["symbol"],
                    if_not_exists=True)
    op.create_index("ix_security_catalog_name", "security_catalog", ["name"],
                    if_not_exists=True)

    # ── securities — new columns ──────────────────────────────────────────
    # Use recreate="never" so Alembic issues plain ALTER TABLE ADD COLUMN
    # statements rather than recreating the entire table.  Table recreation
    # fails on databases with existing FK-referencing rows (lots, transactions).
    # SQLite supports ADD COLUMN natively; the catalog_id FK relationship is
    # enforced at the ORM layer.
    existing_cols = {c["name"] for c in sa.inspect(conn).get_columns("securities")}
    with op.batch_alter_table("securities", recreate="never") as batch_op:
        if "catalog_id" not in existing_cols:
            batch_op.add_column(
                sa.Column("catalog_id", sa.String(36), nullable=True)
            )
        if "is_manual_override" not in existing_cols:
            batch_op.add_column(
                sa.Column(
                    "is_manual_override",
                    sa.Boolean,
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade() -> None:
    # SQLite does not support DROP COLUMN in older versions; use batch mode
    # to recreate the table without the Phase-S columns.
    with op.batch_alter_table("securities") as batch_op:
        batch_op.drop_column("is_manual_override")
        batch_op.drop_column("catalog_id")

    op.drop_index("ix_security_catalog_name", table_name="security_catalog")
    op.drop_index("ix_security_catalog_symbol", table_name="security_catalog")
    op.drop_table("security_catalog")
