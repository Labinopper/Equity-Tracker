"""Add optional per-security dividend reminder date.

Revision ID: 012
Revises: 011
Create Date: 2026-03-07 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    if "securities" not in tables:
        return

    cols = {col["name"] for col in inspector.get_columns("securities")}
    if "dividend_reminder_date" in cols:
        return

    with op.batch_alter_table("securities") as batch_op:
        batch_op.add_column(sa.Column("dividend_reminder_date", sa.Date(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    if "securities" not in tables:
        return

    cols = {col["name"] for col in inspector.get_columns("securities")}
    if "dividend_reminder_date" not in cols:
        return

    with op.batch_alter_table("securities") as batch_op:
        batch_op.drop_column("dividend_reminder_date")
