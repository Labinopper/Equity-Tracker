from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_ROOT.parent
DEFAULT_BETA_DB_PATH = Path(r"C:\EquityTrackerData\portfolio.beta_research.db")
OUTPUT_DIR = PROJECT_ROOT / "docs" / "paper_trading_beta" / "runtime_checks"


@dataclass
class RestartResult:
    attempted: bool
    succeeded: bool
    message: str


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_one(conn: sqlite3.Connection, query: str, params: tuple[object, ...] = ()) -> sqlite3.Row | None:
    return conn.execute(query, params).fetchone()


def _fetch_all(conn: sqlite3.Connection, query: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
    return conn.execute(query, params).fetchall()


def _latest_status(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return _fetch_one(
        conn,
        """
        SELECT supervisor_status, supervisor_pid, last_heartbeat_at, updated_at
        FROM beta_system_status
        ORDER BY updated_at DESC
        LIMIT 1
        """,
    )


def _running_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return _fetch_all(
        conn,
        """
        SELECT job_name, status, started_at, completed_at
        FROM beta_job_runs
        WHERE status = 'RUNNING'
        ORDER BY started_at DESC
        """,
    )


def _recent_jobs(conn: sqlite3.Connection, limit: int = 12) -> list[sqlite3.Row]:
    return _fetch_all(
        conn,
        """
        SELECT job_name, status, started_at, completed_at
        FROM beta_job_runs
        ORDER BY COALESCE(completed_at, started_at) DESC
        LIMIT ?
        """,
        (limit,),
    )


def _signal_stats(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return _fetch_one(
        conn,
        """
        SELECT COUNT(*) AS signal_count, MAX(created_at) AS latest_signal_at
        FROM beta_execution_signals
        """,
    )


def _latest_session_date(conn: sqlite3.Connection) -> str | None:
    row = _fetch_one(
        conn,
        """
        SELECT MAX(session_date) AS session_date
        FROM beta_intraday_feature_observations
        """,
    )
    if row is None:
        return None
    return row["session_date"]


def _outlook_stats(conn: sqlite3.Connection, session_date: str | None) -> sqlite3.Row | None:
    if not session_date:
        return None
    return _fetch_one(
        conn,
        """
        SELECT COUNT(*) AS observation_count, MAX(observed_at) AS latest_observed_at
        FROM beta_intraday_feature_observations
        WHERE session_date = ?
        """,
        (session_date,),
    )


def _sim_trade_stats(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return _fetch_all(
        conn,
        """
        SELECT
            COALESCE(simulation_source, 'LIVE_FORWARD') AS simulation_source,
            COUNT(*) AS total_trades,
            SUM(CASE WHEN status = 'OPEN' THEN 1 ELSE 0 END) AS open_trades,
            SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) AS closed_trades,
            SUM(CASE WHEN COALESCE(realized_post_cost_return_pct, realized_return_pct, 0.0) > 0 THEN 1 ELSE 0 END) AS wins,
            ROUND(AVG(COALESCE(realized_post_cost_return_pct, realized_return_pct, 0.0)), 4) AS avg_post_cost_return_pct
        FROM beta_intraday_simulated_trades
        GROUP BY COALESCE(simulation_source, 'LIVE_FORWARD')
        ORDER BY simulation_source
        """,
    )


def _restart_supervisor() -> RestartResult:
    sys.path.insert(0, str(APP_ROOT))
    try:
        import run_api
        from src.beta.runtime_manager import get_beta_db_path, initialize_beta_runtime
    except Exception as exc:  # pragma: no cover - defensive import reporting
        return RestartResult(True, False, f"import_failed: {exc}")

    try:
        run_api._load_dotenv()
        core_db_path_str = os.environ.get("EQUITY_DB_PATH", "").strip()
        core_db_path = Path(core_db_path_str) if core_db_path_str else None
        result = initialize_beta_runtime(core_db_path, allow_supervisor=True)
        active_beta_db_path = get_beta_db_path()
        if result is None or active_beta_db_path is None:
            return RestartResult(True, False, "initialize_beta_runtime_returned_none")
        return RestartResult(True, True, f"supervisor_restart_requested for {active_beta_db_path}")
    except Exception as exc:  # pragma: no cover - defensive runtime reporting
        return RestartResult(True, False, f"restart_failed: {exc}")


def _as_markdown_table(rows: list[sqlite3.Row], columns: list[str]) -> list[str]:
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        values = [str(row[column]) if row[column] is not None else "-" for column in columns]
        lines.append("| " + " | ".join(values) + " |")
    return lines


def _health_label(status_row: sqlite3.Row | None) -> str:
    if status_row is None:
        return "UNKNOWN"
    return "HEALTHY" if str(status_row["supervisor_status"] or "").lower() == "running" else "STOPPED"


def _build_report(
    *,
    db_path: Path,
    label: str,
    status_before: sqlite3.Row | None,
    status_after: sqlite3.Row | None,
    running_jobs: list[sqlite3.Row],
    recent_jobs: list[sqlite3.Row],
    signal_stats: sqlite3.Row | None,
    session_date: str | None,
    outlook_stats: sqlite3.Row | None,
    sim_trade_stats: list[sqlite3.Row],
    restart_result: RestartResult,
) -> str:
    snapshot_time = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S +00:00")
    health = _health_label(status_after)

    def _status_line(row: sqlite3.Row | None) -> list[str]:
        if row is None:
            return ["- unavailable"]
        return [
            f"- supervisor status: `{row['supervisor_status']}`",
            f"- supervisor pid: `{row['supervisor_pid']}`",
            f"- last heartbeat: `{row['last_heartbeat_at']}`",
            f"- updated at: `{row['updated_at']}`",
        ]

    lines: list[str] = [
        "# Beta Runtime Check",
        "",
        f"Snapshot time: `{snapshot_time}`",
        f"Label: `{label}`",
        f"DB path: `{db_path}`",
        f"Assessment: `{health}`",
        "",
        "## Restart Review",
        f"- attempted: `{'yes' if restart_result.attempted else 'no'}`",
        f"- succeeded: `{'yes' if restart_result.succeeded else 'no'}`",
        f"- message: `{restart_result.message}`",
        "",
        "## Status Before Review",
        *_status_line(status_before),
        "",
        "## Status After Review",
        *_status_line(status_after),
        "",
        "## Running Jobs",
    ]

    if running_jobs:
        lines.extend(_as_markdown_table(running_jobs, ["job_name", "status", "started_at", "completed_at"]))
    else:
        lines.append("- none")

    lines.extend(["", "## Recent Jobs"])
    if recent_jobs:
        lines.extend(_as_markdown_table(recent_jobs, ["job_name", "status", "started_at", "completed_at"]))
    else:
        lines.append("- none")

    lines.extend(["", "## Intraday Activity"])
    if signal_stats is None:
        lines.append("- execution signals: unavailable")
    else:
        lines.append(f"- execution signals total: `{signal_stats['signal_count']}`")
        lines.append(f"- latest execution signal created: `{signal_stats['latest_signal_at']}`")

    if session_date is None or outlook_stats is None:
        lines.append("- latest intraday session: unavailable")
    else:
        lines.append(f"- latest intraday session date: `{session_date}`")
        lines.append(f"- intraday observations on latest session: `{outlook_stats['observation_count']}`")
        lines.append(f"- latest intraday observation on latest session: `{outlook_stats['latest_observed_at']}`")

    lines.extend(["", "## Simulated Trades"])
    if sim_trade_stats:
        lines.extend(
            _as_markdown_table(
                sim_trade_stats,
                [
                    "simulation_source",
                    "total_trades",
                    "open_trades",
                    "closed_trades",
                    "wins",
                    "avg_post_cost_return_pct",
                ],
            )
        )
    else:
        lines.append("- none")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a timestamped beta runtime health review.")
    parser.add_argument("--label", default="manual", help="Short label for this review run.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_BETA_DB_PATH, help="Path to the beta SQLite DB.")
    parser.add_argument(
        "--attempt-restart",
        action="store_true",
        help="Restart the detached supervisor if the DB status is not running.",
    )
    args = parser.parse_args()

    db_path = args.db_path
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    restart_result = RestartResult(False, False, "not_needed")
    with _connect(db_path) as conn:
        status_before = _latest_status(conn)

    if args.attempt_restart and _health_label(status_before) != "HEALTHY":
        restart_result = _restart_supervisor()

    with _connect(db_path) as conn:
        status_after = _latest_status(conn)
        running_jobs = _running_jobs(conn)
        recent_jobs = _recent_jobs(conn)
        signal_stats = _signal_stats(conn)
        session_date = _latest_session_date(conn)
        outlook_stats = _outlook_stats(conn, session_date)
        sim_trade_stats = _sim_trade_stats(conn)

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%SZ")
    safe_label = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in args.label).strip("_") or "manual"
    output_path = OUTPUT_DIR / f"BETA_RUNTIME_CHECK_{timestamp}_{safe_label}.md"
    output_path.write_text(
        _build_report(
            db_path=db_path,
            label=safe_label,
            status_before=status_before,
            status_after=status_after,
            running_jobs=running_jobs,
            recent_jobs=recent_jobs,
            signal_stats=signal_stats,
            session_date=session_date,
            outlook_stats=outlook_stats,
            sim_trade_stats=sim_trade_stats,
            restart_result=restart_result,
        ),
        encoding="utf-8",
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
