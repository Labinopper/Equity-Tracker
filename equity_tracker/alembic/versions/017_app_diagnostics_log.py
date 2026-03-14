"""add app diagnostics log

Revision ID: 017
Revises: 016
Create Date: 2026-03-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_diagnostics_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("component", sa.String(length=80), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_app_diagnostics_log_created_at",
        "app_diagnostics_log",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_app_diagnostics_log_severity_component",
        "app_diagnostics_log",
        ["severity", "component", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_app_diagnostics_log_severity_component", table_name="app_diagnostics_log")
    op.drop_index("ix_app_diagnostics_log_created_at", table_name="app_diagnostics_log")
    op.drop_table("app_diagnostics_log")
