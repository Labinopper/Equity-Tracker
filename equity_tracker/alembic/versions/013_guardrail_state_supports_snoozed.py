"""Allow SNOOZED state in persisted guardrail lifecycle events.

Revision ID: 013
Revises: 012
Create Date: 2026-03-07 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE_NAME = "portfolio_guardrail_state_events"
_CONSTRAINT_NAME = "ck_portfolio_guardrail_state_events_state"
_UPGRADED_CHECK = "state IN ('ACTIVE','DISMISSED','SNOOZED')"
_DOWNGRADED_CHECK = "state IN ('ACTIVE','DISMISSED')"


def _current_check_sqltext() -> str | None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    if _TABLE_NAME not in tables:
        return None
    checks = inspector.get_check_constraints(_TABLE_NAME)
    for check in checks:
        if check.get("name") == _CONSTRAINT_NAME:
            return str(check.get("sqltext") or "")
    return None


def upgrade() -> None:
    current_sql = _current_check_sqltext()
    if current_sql is None or "SNOOZED" in current_sql:
        return

    with op.batch_alter_table(_TABLE_NAME) as batch_op:
        batch_op.drop_constraint(_CONSTRAINT_NAME, type_="check")
        batch_op.create_check_constraint(_CONSTRAINT_NAME, _UPGRADED_CHECK)


def downgrade() -> None:
    current_sql = _current_check_sqltext()
    if current_sql is None or "SNOOZED" not in current_sql:
        return

    with op.batch_alter_table(_TABLE_NAME) as batch_op:
        batch_op.drop_constraint(_CONSTRAINT_NAME, type_="check")
        batch_op.create_check_constraint(_CONSTRAINT_NAME, _DOWNGRADED_CHECK)
