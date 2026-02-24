"""
AppSettings — user preferences persisted to a JSON file alongside the database.

The settings file lives at {db_path}.settings.json.  It is NOT encrypted and
must never contain sensitive data (passwords, keys, PII).  It holds only
non-sensitive user preferences such as default income figures for tax
calculations and UI display preferences.

Design:
  - Plain class (not dataclass) to avoid dataclass field-ordering constraints
    with the internal _settings_path.
  - All monetary defaults are stored as Decimal strings in JSON, matching the
    rest of the codebase (no floats).
  - Unknown JSON keys are silently ignored (forward-compatibility).
  - A missing or corrupt file falls back to defaults without error.

Usage:
    settings = AppSettings.load(db_path)
    settings.default_gross_income = Decimal("80000")
    settings.save()
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path


class AppSettings:
    """
    User preferences for the equity-tracker application.

    Attributes mirror the Phase 3 UI architecture fields: income context
    defaults (for Add Lot and Disposal Simulator pre-population) and UI
    display preferences.
    """

    # ── Default income context ──────────────────────────────────────────────
    default_gross_income: Decimal
    default_pension_sacrifice: Decimal
    default_student_loan_plan: int | None  # None, 1, or 2
    default_other_income: Decimal

    # ── UI preferences ──────────────────────────────────────────────────────
    default_tax_year: str
    show_exhausted_lots: bool
    hide_values: bool

    def __init__(self) -> None:
        # Income defaults — all zero so the user must enter their own figures
        self.default_gross_income = Decimal("0")
        self.default_pension_sacrifice = Decimal("0")
        self.default_student_loan_plan: int | None = None
        self.default_other_income = Decimal("0")

        # UI defaults
        self.default_tax_year = "2024-25"
        self.show_exhausted_lots = False
        self.hide_values = False

        # Internal — set by load(), not serialized under this name
        self._settings_path: Path = Path("settings.json")

    # ── Factory ─────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, db_path: Path) -> "AppSettings":
        """
        Load settings from {db_path}.settings.json.

        Returns an instance with defaults if the file does not exist or cannot
        be parsed.  Never raises.
        """
        settings_path = _settings_file_for(db_path)
        obj = cls()
        obj._settings_path = settings_path

        if settings_path.exists():
            try:
                data = json.loads(settings_path.read_text(encoding="utf-8"))
                obj._apply(data)
            except (json.JSONDecodeError, OSError):
                # Corrupt or unreadable → silently use defaults
                pass

        return obj

    @classmethod
    def defaults_for(cls, db_path: Path) -> "AppSettings":
        """
        Return a fresh defaults instance bound to db_path without reading disk.

        Useful in tests and first-launch scenarios where no settings file exists
        yet and a save() call is not expected.
        """
        obj = cls()
        obj._settings_path = _settings_file_for(db_path)
        return obj

    # ── Persistence ─────────────────────────────────────────────────────────

    def save(self) -> None:
        """Write current settings to {db_path}.settings.json."""
        data = {
            "default_gross_income": str(self.default_gross_income),
            "default_pension_sacrifice": str(self.default_pension_sacrifice),
            "default_student_loan_plan": self.default_student_loan_plan,
            "default_other_income": str(self.default_other_income),
            "default_tax_year": self.default_tax_year,
            "show_exhausted_lots": self.show_exhausted_lots,
            "hide_values": self.hide_values,
        }
        self._settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @property
    def settings_path(self) -> Path:
        """Path to the JSON file on disk."""
        return self._settings_path

    # ── Internal ────────────────────────────────────────────────────────────

    def _apply(self, data: dict) -> None:
        """Overwrite attributes from a parsed JSON dict; unknown keys ignored."""
        _dec = _safe_decimal  # shorthand

        if "default_gross_income" in data:
            self.default_gross_income = _dec(data["default_gross_income"])
        if "default_pension_sacrifice" in data:
            self.default_pension_sacrifice = _dec(data["default_pension_sacrifice"])
        if "default_student_loan_plan" in data:
            raw = data["default_student_loan_plan"]
            self.default_student_loan_plan = int(raw) if raw is not None else None
        if "default_other_income" in data:
            self.default_other_income = _dec(data["default_other_income"])
        if "default_tax_year" in data:
            self.default_tax_year = str(data["default_tax_year"])
        if "show_exhausted_lots" in data:
            self.show_exhausted_lots = bool(data["show_exhausted_lots"])
        if "hide_values" in data:
            self.hide_values = bool(data["hide_values"])


# ── Module helpers ───────────────────────────────────────────────────────────

def _settings_file_for(db_path: Path) -> Path:
    return Path(str(db_path) + ".settings.json")


def _safe_decimal(value: object) -> Decimal:
    """Convert a JSON value to Decimal; return Decimal('0') on failure."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return Decimal("0")
