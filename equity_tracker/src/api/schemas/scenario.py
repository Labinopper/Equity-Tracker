"""
Pydantic schemas for Scenario Lab APIs.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from ...db.models import VALID_SCHEME_TYPES


class ScenarioLegRequest(BaseModel):
    """
    One disposal leg in a multi-leg scenario run.
    """

    security_id: str
    quantity: Decimal = Field(..., gt=0)
    price_per_share_gbp: Decimal | None = Field(
        None,
        ge=0,
        description="Optional per-share override; defaults to latest market price.",
    )
    scheme_type: str | None = Field(
        None,
        description="Optional scheme filter for FIFO simulation.",
    )
    label: str | None = Field(
        None,
        max_length=120,
        description="Optional display label for this leg.",
    )

    @field_validator("scheme_type")
    @classmethod
    def validate_scheme_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        if not cleaned:
            return None
        if cleaned not in VALID_SCHEME_TYPES:
            raise ValueError(
                f"scheme_type must be one of: {list(VALID_SCHEME_TYPES)}"
            )
        return cleaned

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ScenarioRunRequest(BaseModel):
    """
    Request body for POST /api/scenarios/run.
    """

    name: str | None = Field(
        None,
        max_length=120,
        description="Optional scenario name.",
    )
    as_of_date: date | None = Field(
        None,
        description="Simulation date; defaults to today when omitted.",
    )
    price_shock_pct: Decimal = Field(
        Decimal("0"),
        ge=Decimal("-95"),
        le=Decimal("500"),
        description="Percent shock applied to each leg price.",
    )
    execution_mode: str = Field(
        "INDEPENDENT",
        description="Leg execution mode: INDEPENDENT or SEQUENTIAL.",
    )
    legs: list[ScenarioLegRequest] = Field(
        ...,
        min_length=1,
        max_length=30,
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("execution_mode")
    @classmethod
    def normalize_execution_mode(cls, value: str) -> str:
        cleaned = str(value or "").strip().upper()
        if cleaned not in {"INDEPENDENT", "SEQUENTIAL"}:
            raise ValueError("execution_mode must be INDEPENDENT or SEQUENTIAL.")
        return cleaned
