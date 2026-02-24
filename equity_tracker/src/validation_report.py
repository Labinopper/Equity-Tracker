"""
CLI entrypoint for validation report generation.

Usage example:
    python -m app.validation_report --format text --security IBM --as-of 2026-02-24T14:39:04Z
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .api import _state
from .app_context import AppContext
from .db.engine import DatabaseEngine
from .services.validation_report_service import ValidationReportService


def _parse_as_of(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _parse_encrypted(raw: str | None) -> bool:
    if raw is None:
        env = os.environ.get("EQUITY_DB_ENCRYPTED", "true").strip().lower()
        return env != "false"
    return raw.strip().lower() not in {"0", "false", "no"}


def _resolve_db_inputs(args: argparse.Namespace) -> tuple[Path | None, str, bool]:
    db_path_raw = args.db_path or os.environ.get("EQUITY_DB_PATH", "")
    db_password = args.db_password or os.environ.get("EQUITY_DB_PASSWORD", "")
    encrypted = _parse_encrypted(args.encrypted)
    db_path = Path(db_path_raw).resolve() if db_path_raw else None
    return db_path, db_password, encrypted


def _ensure_app_context(args: argparse.Namespace) -> tuple[bool, Path | None]:
    """
    Ensure AppContext is ready for report generation.

    Returns:
        (opened_here, db_path_used)
    """
    if AppContext.is_initialized():
        return False, _state.get_db_path()

    db_path, db_password, encrypted = _resolve_db_inputs(args)
    if db_path is None:
        raise RuntimeError(
            "No active DB context and no db path provided. "
            "Set EQUITY_DB_PATH or pass --db-path."
        )
    if encrypted and not db_password:
        raise RuntimeError(
            "Encrypted DB requires a password. Set EQUITY_DB_PASSWORD or pass --db-password."
        )

    if encrypted:
        engine = DatabaseEngine.open(db_path, db_password)
    else:
        engine = DatabaseEngine.open_unencrypted(f"sqlite:///{db_path}")

    AppContext.initialize(engine)
    _state.set_db_path(db_path)
    return True, db_path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate live validation report (text/json).")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument(
        "--security",
        default=None,
        help="Optional security filter (ticker or security id).",
    )
    p.add_argument("--security-id", default=None, help=argparse.SUPPRESS)
    p.add_argument(
        "--as-of",
        default=None,
        help="Optional ISO datetime cutoff (e.g. 2026-02-24T14:39:04Z).",
    )
    p.add_argument(
        "--limit-lots",
        type=int,
        default=None,
        help="Optional lot cap; if exceeded, report includes top N lots by value.",
    )
    p.add_argument("--db-path", default=None, help="Database path override.")
    p.add_argument("--db-password", default=None, help="Database password override.")
    p.add_argument(
        "--encrypted",
        default=None,
        help="Set true/false. Defaults to EQUITY_DB_ENCRYPTED or true.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    opened_here = False
    db_path_used: Path | None = None
    try:
        opened_here, db_path_used = _ensure_app_context(args)
        security_filter = args.security or args.security_id
        report = ValidationReportService.generate_report(
            security_filter=security_filter,
            as_of=_parse_as_of(args.as_of),
            limit_lots=args.limit_lots,
            db_path=db_path_used or _state.get_db_path(),
        )
        if args.format == "json":
            print(json.dumps(report, indent=2))
        else:
            print(ValidationReportService.render_text(report))
        return 0
    except Exception as exc:
        print(f"validation_report error: {exc}", file=sys.stderr)
        return 2
    finally:
        if opened_here:
            _state.set_db_path(None)
            AppContext.lock()


if __name__ == "__main__":
    raise SystemExit(main())
