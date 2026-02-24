"""Structured employment-tax events for non-disposal workflows.

Revision ID: 005
Revises: 004
Create Date: 2026-02-24 00:00:00.000000

Changes:
  - Add table ``employment_tax_events``:
      id TEXT(36) PK
      lot_id TEXT(36) FK(lots.id, RESTRICT)
      security_id TEXT(36) FK(securities.id, RESTRICT)
      event_type TEXT(40) NOT NULL
      event_date DATE NOT NULL
      estimated_tax_gbp TEXT(30) NULL
      estimation_notes TEXT NULL
      source TEXT(50) NULL
      created_at DATETIME NOT NULL
  - Add indexes:
      ix_employment_tax_events_lot_event_date (lot_id, event_date)
      ix_employment_tax_events_security_event_date (security_id, event_date)
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    table_names = set(sa.inspect(conn).get_table_names())
    if "employment_tax_events" in table_names:
        return

    op.create_table(
        "employment_tax_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "lot_id",
            sa.String(36),
            sa.ForeignKey("lots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "security_id",
            sa.String(36),
            sa.ForeignKey("securities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("estimated_tax_gbp", sa.String(30), nullable=True),
        sa.Column("estimation_notes", sa.Text(), nullable=True),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_employment_tax_events_lot_event_date",
        "employment_tax_events",
        ["lot_id", "event_date"],
        unique=False,
    )
    op.create_index(
        "ix_employment_tax_events_security_event_date",
        "employment_tax_events",
        ["security_id", "event_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_employment_tax_events_security_event_date",
        table_name="employment_tax_events",
    )
    op.drop_index(
        "ix_employment_tax_events_lot_event_date",
        table_name="employment_tax_events",
    )
    op.drop_table("employment_tax_events")

