"""
Settings router — user preferences persistence.

Endpoints
─────────
  GET /api/settings   Load and return current AppSettings
  PUT /api/settings   Full settings replacement; saves to {db_path}.settings.json

The settings file is stored alongside the database file (unencrypted JSON),
so the API must know the database path.  This is tracked in ``src/api/_state``
and set when the database is unlocked.

Mounted at /api/settings to avoid a URL conflict with the UI settings page
at /settings (served by the Jinja2 UI router).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...settings import AppSettings
from .. import _state
from ..dependencies import db_required, session_required
from ..schemas.settings import SettingsSchema, UpdateSettingsRequest

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(session_required)])


def _require_db_path():
    """Return the current db_path or raise 503 if not available."""
    path = _state.get_db_path()
    if path is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "database_locked",
                "message": "Database is locked. POST /admin/unlock to initialize.",
            },
        )
    return path


@router.get(
    "",
    response_model=SettingsSchema,
    summary="Get current user settings",
)
async def get_settings(
    _: None = Depends(db_required),
) -> SettingsSchema:
    """
    Load and return the current ``AppSettings`` from
    ``{db_path}.settings.json``.

    Returns defaults if the settings file does not yet exist.
    """
    db_path = _require_db_path()
    settings = AppSettings.load(db_path)
    return SettingsSchema.from_app_settings(settings)


@router.put(
    "",
    response_model=SettingsSchema,
    summary="Update user settings",
)
async def update_settings(
    req: UpdateSettingsRequest,
    _: None = Depends(db_required),
) -> SettingsSchema:
    """
    Replace all user settings and persist to
    ``{db_path}.settings.json``.

    This is a **full replacement** (PUT semantics) — all fields are
    required.  The response echoes the saved values.

    The saved settings are used by ``GET /reports/cgt?include_tax_due=true``
    to compute CGT due on the fly.
    """
    db_path = _require_db_path()
    settings = AppSettings.load(db_path)

    settings.default_gross_income = req.default_gross_income
    settings.default_pension_sacrifice = req.default_pension_sacrifice
    settings.default_student_loan_plan = req.default_student_loan_plan
    settings.default_other_income = req.default_other_income
    settings.employer_income_dependency_pct = req.employer_income_dependency_pct
    settings.employer_ticker = req.employer_ticker.strip().upper()
    settings.concentration_top_holding_alert_pct = req.concentration_top_holding_alert_pct
    settings.concentration_employer_alert_pct = req.concentration_employer_alert_pct
    settings.default_tax_year = req.default_tax_year
    settings.show_exhausted_lots = req.show_exhausted_lots
    settings.hide_values = req.hide_values
    settings.price_stale_after_days = req.price_stale_after_days
    settings.fx_stale_after_minutes = req.fx_stale_after_minutes
    settings.monthly_espp_input_reminder_enabled = req.monthly_espp_input_reminder_enabled
    settings.monthly_espp_input_reminder_day = req.monthly_espp_input_reminder_day

    settings.save()
    return SettingsSchema.from_app_settings(settings)
