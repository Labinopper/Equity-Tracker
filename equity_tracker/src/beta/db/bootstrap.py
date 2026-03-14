"""Schema bootstrap for the separate beta database."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from shutil import move

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, inspect, select, text

from .engine import BetaDatabaseEngine
from .models import (
    BetaBase,
    BetaLedgerState,
    BetaRiskControlState,
    BetaSchemaMeta,
    BetaSystemStatus,
)

_SCHEMA_VERSION = "v5"


def _missing_beta_columns(engine: BetaDatabaseEngine) -> list[tuple[str, object]]:
    inspector = inspect(engine.raw_engine)
    existing_tables = set(inspector.get_table_names())
    if not existing_tables:
        return []

    missing: list[tuple[str, object]] = []
    for table in BetaBase.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_columns = {row["name"] for row in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in existing_columns:
                missing.append((table.name, column))
    return missing


def beta_schema_requires_reset(engine: BetaDatabaseEngine) -> tuple[bool, list[str]]:
    """Return whether the existing beta DB is missing required columns."""
    missing = _missing_beta_columns(engine)
    if not missing:
        return False, []

    grouped: dict[str, list[str]] = defaultdict(list)
    for table_name, column in missing:
        grouped[table_name].append(column.name)
    reasons = [
        f"{table_name}: missing {', '.join(sorted(column_names))}"
        for table_name, column_names in sorted(grouped.items())
    ]
    return True, reasons


def _column_type_sql(engine: BetaDatabaseEngine, column) -> str:
    return column.type.compile(dialect=engine.raw_engine.dialect)


def _column_fill_value(column):
    default = getattr(column, "default", None)
    if default is not None:
        arg = default.arg
        if callable(arg):
            try:
                return arg()
            except TypeError:
                return None
        return arg

    if column.nullable:
        return None

    if isinstance(column.type, Boolean):
        return False
    if isinstance(column.type, Integer):
        return 0
    if isinstance(column.type, Float):
        return 0.0
    if isinstance(column.type, DateTime):
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if isinstance(column.type, Date):
        return date.today()
    return ""


def _ensure_beta_indexes(engine: BetaDatabaseEngine) -> list[str]:
    inspector = inspect(engine.raw_engine)
    existing_index_names = {
        index["name"]
        for table_name in inspector.get_table_names()
        for index in inspector.get_indexes(table_name)
        if index.get("name")
    }

    created: list[str] = []
    for table in BetaBase.metadata.sorted_tables:
        for index in table.indexes:
            if not index.name or index.name in existing_index_names:
                continue
            index.create(bind=engine.raw_engine, checkfirst=True)
            created.append(index.name)
            existing_index_names.add(index.name)
    return created


def apply_beta_schema_migrations(engine: BetaDatabaseEngine) -> list[str]:
    """Apply additive in-place schema migrations for existing beta DBs."""
    BetaBase.metadata.create_all(engine.raw_engine)
    missing = _missing_beta_columns(engine)
    if not missing:
        _ensure_beta_indexes(engine)
        return []

    preparer = engine.raw_engine.dialect.identifier_preparer
    applied: list[str] = []
    with engine.raw_engine.begin() as conn:
        for table_name, column in missing:
            quoted_table = preparer.quote(table_name)
            quoted_column = preparer.quote(column.name)
            type_sql = _column_type_sql(engine, column)
            conn.execute(text(f"ALTER TABLE {quoted_table} ADD COLUMN {quoted_column} {type_sql}"))
            fill_value = _column_fill_value(column)
            if fill_value is not None:
                conn.execute(
                    text(f"UPDATE {quoted_table} SET {quoted_column} = :value WHERE {quoted_column} IS NULL"),
                    {"value": fill_value},
                )
            applied.append(f"{table_name}.{column.name}")

    applied.extend(f"index:{index_name}" for index_name in _ensure_beta_indexes(engine))
    return applied


def archive_incompatible_beta_db(beta_db_path: Path) -> Path:
    """Move an incompatible beta DB aside so a fresh schema can be created."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup_path = beta_db_path.with_name(f"{beta_db_path.stem}.schema_backup_{stamp}{beta_db_path.suffix}")
    for suffix in ("", "-wal", "-shm"):
        source = Path(f"{beta_db_path}{suffix}") if suffix else beta_db_path
        if not source.exists():
            continue
        destination = Path(f"{backup_path}{suffix}") if suffix else backup_path
        move(str(source), str(destination))
    return backup_path


def ensure_beta_schema(engine: BetaDatabaseEngine, *, beta_db_path: Path | None = None) -> None:
    """Create the beta schema and seed singleton rows if missing."""
    BetaBase.metadata.create_all(engine.raw_engine)
    with engine.session() as sess:
        schema_meta = sess.scalar(select(BetaSchemaMeta).where(BetaSchemaMeta.id == 1))
        if schema_meta is None:
            schema_meta = BetaSchemaMeta(id=1, schema_version=_SCHEMA_VERSION)
            sess.add(schema_meta)
        else:
            schema_meta.schema_version = _SCHEMA_VERSION

        system_status = sess.scalar(select(BetaSystemStatus).where(BetaSystemStatus.id == 1))
        if system_status is None:
            sess.add(
                BetaSystemStatus(
                    id=1,
                    beta_db_path=str(beta_db_path or ""),
                    runtime_mode="FULL_INTERNAL_BETA",
                    enabled=True,
                    web_ui_enabled=True,
                    observation_enabled=True,
                    learning_enabled=True,
                    shadow_scoring_enabled=True,
                    demo_execution_enabled=True,
                    filings_enabled=True,
                    supervisor_status="stopped",
                )
            )
        elif beta_db_path is not None:
            system_status.beta_db_path = str(beta_db_path)

        ledger_state = sess.scalar(select(BetaLedgerState).where(BetaLedgerState.id == 1))
        if ledger_state is None:
            sess.add(
                BetaLedgerState(
                    id=1,
                    base_currency="GBP",
                    starting_capital_gbp="10000.00",
                    available_cash_gbp="10000.00",
                    deployed_capital_gbp="0.00",
                    realized_pnl_gbp="0.00",
                    unrealized_pnl_gbp="0.00",
                    total_equity_gbp="10000.00",
                )
            )

        risk_state = sess.scalar(select(BetaRiskControlState).where(BetaRiskControlState.id == 1))
        if risk_state is None:
            sess.add(
                BetaRiskControlState(
                    id=1,
                    demo_entries_paused=False,
                    degradation_status="NORMAL",
                    recent_closed_trades=0,
                    recent_win_rate_pct=0.0,
                    recent_avg_pnl_pct=0.0,
                )
            )
