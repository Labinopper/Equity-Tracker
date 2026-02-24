"""Add broker holding currency tracking to lots.

Revision ID: 006
Revises: 005
Create Date: 2026-02-24 00:00:00.000000

Changes:
  - Add nullable column ``lots.broker_currency`` (TEXT(3)).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    table_names = set(inspector.get_table_names())
    if "lots" not in table_names:
        return
    existing_cols = {col["name"] for col in inspector.get_columns("lots")}
    if "broker_currency" in existing_cols:
        return
    op.add_column("lots", sa.Column("broker_currency", sa.String(3), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    table_names = set(inspector.get_table_names())
    if "lots" not in table_names:
        return
    existing_cols = {col["name"] for col in inspector.get_columns("lots")}
    if "broker_currency" not in existing_cols:
        return
    with op.batch_alter_table("lots") as batch_op:
        batch_op.drop_column("broker_currency")
