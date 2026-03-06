"""Add persisted scenario snapshot table.

Revision ID: 010
Revises: 009
Create Date: 2026-03-06 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    if "scenario_snapshots" in tables:
        return

    op.create_table(
        "scenario_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("execution_mode", sa.String(length=20), nullable=False),
        sa.Column("price_shock_pct", sa.String(length=30), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("input_snapshot_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "execution_mode IN ('INDEPENDENT','SEQUENTIAL')",
            name="ck_scenario_snapshots_execution_mode",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_scenario_snapshots_created_at",
        "scenario_snapshots",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    if "scenario_snapshots" not in tables:
        return

    with op.batch_alter_table("scenario_snapshots") as batch_op:
        batch_op.drop_index("ix_scenario_snapshots_created_at")
    op.drop_table("scenario_snapshots")
