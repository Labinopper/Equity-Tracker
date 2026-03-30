"""Helpers for loading the project .env without external dependencies."""

from __future__ import annotations

import os
from pathlib import Path


def _default_env_path() -> Path:
    return Path(__file__).resolve().parents[1] / ".env"


def load_project_dotenv(*, override: bool = False, env_path: Path | None = None) -> Path | None:
    """
    Load the project .env file into os.environ.

    This keeps direct ASGI launches (for example, uvicorn --reload from an IDE)
    aligned with the dedicated run_api.py entry point.
    """
    path = env_path or _default_env_path()
    if not path.is_file():
        return None

    with open(path, encoding="utf-8-sig") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if not key:
                continue
            value = value.strip().strip("'\"")
            if override:
                os.environ[key] = value
            else:
                os.environ.setdefault(key, value)

    return path
