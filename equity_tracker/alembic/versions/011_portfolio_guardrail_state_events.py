"""Add persisted portfolio guardrail lifecycle table.

Revision ID: 011
Revises: 010
Create Date: 2026-03-06 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    if "portfolio_guardrail_state_events" in tables:
        return

    op.create_table(
        "portfolio_guardrail_state_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("guardrail_id", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("condition_hash", sa.String(length=64), nullable=True),
        sa.Column("dismiss_until", sa.DateTime(), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('ACTIVE','DISMISSED')",
            name="ck_portfolio_guardrail_state_events_state",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_portfolio_guardrail_state_events_guardrail_changed",
        "portfolio_guardrail_state_events",
        ["guardrail_id", "changed_at"],
        unique=False,
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    if "portfolio_guardrail_state_events" not in tables:
        return

    with op.batch_alter_table("portfolio_guardrail_state_events") as batch_op:
        batch_op.drop_index("ix_portfolio_guardrail_state_events_guardrail_changed")
    op.drop_table("portfolio_guardrail_state_events")
