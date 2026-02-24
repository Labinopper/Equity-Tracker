"""
Pydantic schemas for the /api/settings endpoint.

AppSettings stores all monetary values as Decimal; responses expose them as
strings (consistent with the rest of the API).  Requests accept Decimal,
which Pydantic v2 coerces from JSON numbers or strings.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class SettingsSchema(BaseModel):
    """Current user settings — all monetary values as decimal strings."""

    default_gross_income: str        # Decimal → str
    default_pension_sacrifice: str   # Decimal → str
    default_student_loan_plan: int | None
    default_other_income: str        # Decimal → str
    default_tax_year: str
    show_exhausted_lots: bool

    @classmethod
    def from_app_settings(cls, s) -> "SettingsSchema":  # s: AppSettings
        return cls(
            default_gross_income=str(s.default_gross_income),
            default_pension_sacrifice=str(s.default_pension_sacrifice),
            default_student_loan_plan=s.default_student_loan_plan,
            default_other_income=str(s.default_other_income),
            default_tax_year=s.default_tax_year,
            show_exhausted_lots=s.show_exhausted_lots,
        )


class UpdateSettingsRequest(BaseModel):
    """
    Full settings replacement body for PUT /api/settings.

    All fields required — this is a PUT (full replacement), not a PATCH.
    Pydantic v2 coerces Decimal from JSON strings (``"80000.00"``) or
    numbers (``80000``).  Use strings for maximum precision.
    """

    default_gross_income: Decimal = Field(
        ..., ge=0, description="Gross employment income for CGT rate calculation"
    )
    default_pension_sacrifice: Decimal = Field(..., ge=0)
    default_student_loan_plan: int | None = Field(
        None, description="None, 1 (Plan 1), 2 (Plan 2), or 4 (Plan 4 / Scottish)"
    )
    default_other_income: Decimal = Field(..., ge=0)
    default_tax_year: str = Field(
        ..., description="Default tax year shown in reports, e.g. '2024-25'"
    )
    show_exhausted_lots: bool = False

    model_config = {
        "json_schema_extra": {
            "example": {
                "default_gross_income": "80000.00",
                "default_pension_sacrifice": "5000.00",
                "default_student_loan_plan": 2,
                "default_other_income": "0.00",
                "default_tax_year": "2024-25",
                "show_exhausted_lots": False,
            }
        }
    }
