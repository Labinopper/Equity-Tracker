"""ESPP+ forfeiture tracking — Phase E.

Revision ID: 003
Revises: 002
Create Date: 2026-02-23 00:00:00.000000

Changes:
  - lots.forfeiture_period_end DATE NULL — exact end of 6-month matching-share
    forfeiture window. Set on ESPP_PLUS lots at creation. NULL for all other
    scheme types and for legacy ESPP_PLUS lots (pre-Phase E).
    _forfeiture_risk_for_lot() falls back to acq_date + 183 days when NULL.
  - lots.matching_lot_id TEXT(36) NULL FK(lots.id, SET NULL) — for ESPP_PLUS
    lots, the lot_id of the linked ESPP partnership lot. Selling the partnership
    lot within 6 months forfeits this matching lot.

Notes:
  - Both columns are nullable; existing lots are unaffected.
  - matching_lot_id FK uses ondelete=SET NULL: deleting a partnership lot does
    not cascade-delete the ESPP_PLUS lot (it only clears the link).
  - SQLite requires batch_alter_table for idempotent ADD COLUMN.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    existing_cols = {c["name"] for c in sa.inspect(conn).get_columns("lots")}

    with op.batch_alter_table("lots", recreate="never") as batch_op:
        if "forfeiture_period_end" not in existing_cols:
            batch_op.add_column(
                sa.Column("forfeiture_period_end", sa.Date(), nullable=True)
            )
        if "matching_lot_id" not in existing_cols:
            batch_op.add_column(
                sa.Column("matching_lot_id", sa.String(36), nullable=True)
            )


def downgrade() -> None:
    # SQLite does not support DROP COLUMN in older versions; use batch mode
    # to recreate the table without the Phase-E columns.
    with op.batch_alter_table("lots") as batch_op:
        batch_op.drop_column("matching_lot_id")
        batch_op.drop_column("forfeiture_period_end")
