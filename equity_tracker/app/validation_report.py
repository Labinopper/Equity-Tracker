"""Compatibility wrapper for `python -m app.validation_report`."""

from __future__ import annotations

from src.validation_report import main


if __name__ == "__main__":
    raise SystemExit(main())
