"""Add lot transfer events table.

Revision ID: 016
Revises: 015
Create Date: 2026-03-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in set(inspector.get_table_names())


def upgrade() -> None:
    if _table_exists("lot_transfer_events"):
        return

    op.create_table(
        "lot_transfer_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("security_id", sa.String(length=36), nullable=False),
        sa.Column("source_lot_id", sa.String(length=36), nullable=False),
        sa.Column("destination_lot_id", sa.String(length=36), nullable=True),
        sa.Column("source_scheme", sa.String(length=20), nullable=False),
        sa.Column("destination_scheme", sa.String(length=20), nullable=False),
        sa.Column("transfer_date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.String(length=30), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "source_scheme IN ('RSU','ESPP','ESPP_PLUS','SIP_PARTNERSHIP','SIP_MATCHING','SIP_DIVIDEND','BROKERAGE','ISA')",
            name="ck_lot_transfer_events_source_scheme",
        ),
        sa.CheckConstraint(
            "destination_scheme IN ('RSU','ESPP','ESPP_PLUS','SIP_PARTNERSHIP','SIP_MATCHING','SIP_DIVIDEND','BROKERAGE','ISA')",
            name="ck_lot_transfer_events_destination_scheme",
        ),
        sa.ForeignKeyConstraint(["security_id"], ["securities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_lot_id"], ["lots.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["destination_lot_id"], ["lots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id", name="uq_lot_transfer_events_external_id"),
    )
    op.create_index(
        "ix_lot_transfer_events_security_date",
        "lot_transfer_events",
        ["security_id", "transfer_date"],
        unique=False,
    )
    op.create_index(
        "ix_lot_transfer_events_source_lot_date",
        "lot_transfer_events",
        ["source_lot_id", "transfer_date"],
        unique=False,
    )
    op.create_index(
        "ix_lot_transfer_events_destination_lot_date",
        "lot_transfer_events",
        ["destination_lot_id", "transfer_date"],
        unique=False,
    )


def downgrade() -> None:
    if not _table_exists("lot_transfer_events"):
        return
    op.drop_index("ix_lot_transfer_events_destination_lot_date", table_name="lot_transfer_events")
    op.drop_index("ix_lot_transfer_events_source_lot_date", table_name="lot_transfer_events")
    op.drop_index("ix_lot_transfer_events_security_date", table_name="lot_transfer_events")
    op.drop_table("lot_transfer_events")
