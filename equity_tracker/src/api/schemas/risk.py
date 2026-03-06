"""
Schemas for risk endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel

from ...services.risk_service import (
    EmployerDependenceBreakdown,
    RiskConcentrationItem,
    RiskDeployableBreakdown,
    RiskLiquidityBreakdown,
    RiskSummary,
    RiskStressPoint,
)


class RiskConcentrationItemSchema(BaseModel):
    key: str
    label: str
    value_gbp: str
    pct_of_total: str

    @classmethod
    def from_service(cls, item: RiskConcentrationItem) -> "RiskConcentrationItemSchema":
        return cls(
            key=item.key,
            label=item.label,
            value_gbp=str(item.value_gbp),
            pct_of_total=str(item.pct_of_total),
        )


class RiskLiquidityBreakdownSchema(BaseModel):
    sellable_gbp: str
    locked_gbp: str
    at_risk_gbp: str
    classified_total_gbp: str
    sellable_pct: str
    locked_pct: str
    at_risk_pct: str
    unpriced_lot_count: int

    @classmethod
    def from_service(cls, value: RiskLiquidityBreakdown) -> "RiskLiquidityBreakdownSchema":
        return cls(
            sellable_gbp=str(value.sellable_gbp),
            locked_gbp=str(value.locked_gbp),
            at_risk_gbp=str(value.at_risk_gbp),
            classified_total_gbp=str(value.classified_total_gbp),
            sellable_pct=str(value.sellable_pct),
            locked_pct=str(value.locked_pct),
            at_risk_pct=str(value.at_risk_pct),
            unpriced_lot_count=value.unpriced_lot_count,
        )


class RiskStressPointSchema(BaseModel):
    shock_pct: str
    shock_label: str
    stressed_market_value_gbp: str

    @classmethod
    def from_service(cls, point: RiskStressPoint) -> "RiskStressPointSchema":
        return cls(
            shock_pct=str(point.shock_pct),
            shock_label=point.shock_label,
            stressed_market_value_gbp=str(point.stressed_market_value_gbp),
        )


class RiskDeployableBreakdownSchema(BaseModel):
    sellable_holdings_gbp: str
    deployable_cash_gbp: str
    deployable_capital_gbp: str
    employer_sellable_market_value_gbp: str
    employer_share_of_deployable_pct: str

    @classmethod
    def from_service(cls, value: RiskDeployableBreakdown) -> "RiskDeployableBreakdownSchema":
        return cls(
            sellable_holdings_gbp=str(value.sellable_holdings_gbp),
            deployable_cash_gbp=str(value.deployable_cash_gbp),
            deployable_capital_gbp=str(value.deployable_capital_gbp),
            employer_sellable_market_value_gbp=str(value.employer_sellable_market_value_gbp),
            employer_share_of_deployable_pct=str(value.employer_share_of_deployable_pct),
        )


class EmployerDependenceBreakdownSchema(BaseModel):
    employer_ticker: str | None
    employer_equity_gbp: str
    income_dependency_proxy_gbp: str
    income_dependency_pct: str
    denominator_gbp: str
    ratio_pct: str

    @classmethod
    def from_service(cls, value: EmployerDependenceBreakdown) -> "EmployerDependenceBreakdownSchema":
        return cls(
            employer_ticker=value.employer_ticker,
            employer_equity_gbp=str(value.employer_equity_gbp),
            income_dependency_proxy_gbp=str(value.income_dependency_proxy_gbp),
            income_dependency_pct=str(value.income_dependency_pct),
            denominator_gbp=str(value.denominator_gbp),
            ratio_pct=str(value.ratio_pct),
        )


class RiskSummarySchema(BaseModel):
    generated_at_utc: str
    total_market_value_gbp: str
    top_holding_pct: str
    top_holding_sellable_pct: str
    security_concentration: list[RiskConcentrationItemSchema]
    scheme_concentration: list[RiskConcentrationItemSchema]
    liquidity: RiskLiquidityBreakdownSchema
    deployable: RiskDeployableBreakdownSchema
    employer_dependence: EmployerDependenceBreakdownSchema
    stress_points: list[RiskStressPointSchema]
    notes: list[str]

    @classmethod
    def from_service(cls, summary: RiskSummary) -> "RiskSummarySchema":
        liquidity = summary.liquidity
        if liquidity is None:
            raise ValueError("Risk summary liquidity breakdown must be populated.")
        if summary.deployable is None:
            raise ValueError("Risk summary deployable breakdown must be populated.")
        if summary.employer_dependence is None:
            raise ValueError("Risk summary employer dependence breakdown must be populated.")
        return cls(
            generated_at_utc=summary.generated_at_utc.isoformat(),
            total_market_value_gbp=str(summary.total_market_value_gbp),
            top_holding_pct=str(summary.top_holding_pct),
            top_holding_sellable_pct=str(summary.top_holding_sellable_pct),
            security_concentration=[
                RiskConcentrationItemSchema.from_service(item)
                for item in summary.security_concentration
            ],
            scheme_concentration=[
                RiskConcentrationItemSchema.from_service(item)
                for item in summary.scheme_concentration
            ],
            liquidity=RiskLiquidityBreakdownSchema.from_service(liquidity),
            deployable=RiskDeployableBreakdownSchema.from_service(summary.deployable),
            employer_dependence=EmployerDependenceBreakdownSchema.from_service(
                summary.employer_dependence
            ),
            stress_points=[
                RiskStressPointSchema.from_service(point)
                for point in summary.stress_points
            ],
            notes=list(summary.notes),
        )
