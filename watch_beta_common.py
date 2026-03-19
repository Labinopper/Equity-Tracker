"""Helpers shared by local beta DB watcher scripts."""

from __future__ import annotations

import os
from pathlib import Path


def resolve_beta_db_path() -> Path:
    """Resolve the beta DB from env or common local locations."""
    repo_root = Path(__file__).resolve().parent
    env_beta = os.environ.get("EQUITY_BETA_DB_PATH", "").strip()
    env_core = os.environ.get("EQUITY_DB_PATH", "").strip()

    candidates: list[Path] = []
    if env_beta:
        candidates.append(Path(env_beta))
    if env_core:
        core_path = Path(env_core)
        candidates.append(core_path.with_name(f"{core_path.stem}.beta_research.db"))

    candidates.extend(
        [
            repo_root / "data" / "portfolio.beta_research.db",
            Path(r"C:\EquityTrackerData\portfolio.beta_research.db"),
        ]
    )

    seen: set[str] = set()
    for candidate in candidates:
        raw = str(candidate).strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "Could not locate the beta database. Set EQUITY_BETA_DB_PATH to the beta DB path."
    )
