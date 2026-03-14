from __future__ import annotations

import os
from pathlib import Path


def resolve_beta_db_path(core_db_path: Path | None) -> Path | None:
    """Resolve the beta research DB path from env override or the core DB path."""
    env_path = os.environ.get("EQUITY_BETA_DB_PATH", "").strip()
    if env_path:
        return Path(env_path)
    if core_db_path is None:
        return None
    core = Path(core_db_path)
    return core.with_name(f"{core.stem}.beta_research.db")


def resolve_beta_settings_path(beta_db_path: Path) -> Path:
    """Return the JSON settings path stored alongside the beta DB."""
    return Path(str(beta_db_path) + ".settings.json")


def resolve_beta_artifacts_dir(beta_db_path: Path) -> Path:
    """Return the artifacts directory that sits beside the beta DB."""
    return beta_db_path.with_name(f"{beta_db_path.stem}.beta_artifacts")

