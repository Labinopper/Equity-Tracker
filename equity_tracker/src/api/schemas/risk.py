"""
Schemas for risk endpoints.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from ...services.risk_service import (
    EmployerDependenceBreakdown,
    RiskConcentrationGuardrail,
    RiskConcentrationItem,
    RiskDeployableBreakdown,
    RiskForfeitureHeatmapRow,
    RiskLiquidityBreakdown,
    RiskOptionalityIndex,
    RiskOptionalityTimelineBand,
    RiskRebalanceFriction,
    RiskSummary,
    RiskStressPoint,
    RiskValuationBasis,
    RiskWrapperAllocation,
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


class RiskWrapperAllocationSchema(BaseModel):
    isa_market_value_gbp: str
    taxable_market_value_gbp: str
    isa_pct_of_total: str
    taxable_pct_of_total: str

    @classmethod
    def from_service(cls, value: RiskWrapperAllocation) -> "RiskWrapperAllocationSchema":
        return cls(
            isa_market_value_gbp=str(value.isa_market_value_gbp),
            taxable_market_value_gbp=str(value.taxable_market_value_gbp),
            isa_pct_of_total=str(value.isa_pct_of_total),
            taxable_pct_of_total=str(value.taxable_pct_of_total),
        )


class RiskValuationBasisSchema(BaseModel):
    total_security_count: int
    price_tracked_count: int
    price_as_of_latest: str | None
    price_as_of_earliest: str | None
    price_dates_mixed: bool
    stale_price_count: int
    missing_price_count: int
    fx_required_count: int
    fx_as_of_count: int
    fx_as_of_latest: str | None
    fx_as_of_earliest: str | None
    fx_dates_mixed: bool
    stale_fx_count: int
    missing_fx_count: int
    fx_basis_note: str | None

    @classmethod
    def from_service(cls, value: RiskValuationBasis) -> "RiskValuationBasisSchema":
        return cls(
            total_security_count=value.total_security_count,
            price_tracked_count=value.price_tracked_count,
            price_as_of_latest=value.price_as_of_latest,
            price_as_of_earliest=value.price_as_of_earliest,
            price_dates_mixed=value.price_dates_mixed,
            stale_price_count=value.stale_price_count,
            missing_price_count=value.missing_price_count,
            fx_required_count=value.fx_required_count,
            fx_as_of_count=value.fx_as_of_count,
            fx_as_of_latest=value.fx_as_of_latest,
            fx_as_of_earliest=value.fx_as_of_earliest,
            fx_dates_mixed=value.fx_dates_mixed,
            stale_fx_count=value.stale_fx_count,
            missing_fx_count=value.missing_fx_count,
            fx_basis_note=value.fx_basis_note,
        )


class RiskOptionalityTimelineBandSchema(BaseModel):
    label: str
    horizon_days: int
    as_of_date: str
    sellable_gbp: str
    locked_gbp: str
    forfeitable_gbp: str
    deployable_capital_gbp: str
    sellable_pct: str
    locked_pct: str
    forfeitable_pct: str
    deployable_pct: str

    @classmethod
    def from_service(
        cls, value: RiskOptionalityTimelineBand
    ) -> "RiskOptionalityTimelineBandSchema":
        return cls(
            label=value.label,
            horizon_days=value.horizon_days,
            as_of_date=value.as_of_date.isoformat(),
            sellable_gbp=str(value.sellable_gbp),
            locked_gbp=str(value.locked_gbp),
            forfeitable_gbp=str(value.forfeitable_gbp),
            deployable_capital_gbp=str(value.deployable_capital_gbp),
            sellable_pct=str(value.sellable_pct),
            locked_pct=str(value.locked_pct),
            forfeitable_pct=str(value.forfeitable_pct),
            deployable_pct=str(value.deployable_pct),
        )


class RiskOptionalityIndexSchema(BaseModel):
    score: str
    weights_pct: dict[str, str]
    components_pct: dict[str, str]
    notes: list[str]

    @classmethod
    def from_service(cls, value: RiskOptionalityIndex) -> "RiskOptionalityIndexSchema":
        return cls(
            score=str(value.score),
            weights_pct={key: str(v) for key, v in value.weights_pct.items()},
            components_pct={key: str(v) for key, v in value.components_pct.items()},
            notes=list(value.notes),
        )


class RiskConcentrationGuardrailSchema(BaseModel):
    guardrail_id: str
    label: str
    threshold_pct: str
    actual_pct: str
    breach_pct: str
    status: str
    message: str

    @classmethod
    def from_service(
        cls, value: RiskConcentrationGuardrail
    ) -> "RiskConcentrationGuardrailSchema":
        return cls(
            guardrail_id=value.guardrail_id,
            label=value.label,
            threshold_pct=str(value.threshold_pct),
            actual_pct=str(value.actual_pct),
            breach_pct=str(value.breach_pct),
            status=value.status,
            message=value.message,
        )


class RiskForfeitureHeatmapRowSchema(BaseModel):
    security_id: str
    ticker: str
    bucket_0_30_gbp: str
    bucket_31_90_gbp: str
    bucket_91_183_gbp: str
    bucket_over_183_gbp: str
    total_value_gbp: str
    lot_count: int

    @classmethod
    def from_service(
        cls, value: RiskForfeitureHeatmapRow
    ) -> "RiskForfeitureHeatmapRowSchema":
        return cls(
            security_id=value.security_id,
            ticker=value.ticker,
            bucket_0_30_gbp=str(value.bucket_0_30_gbp),
            bucket_31_90_gbp=str(value.bucket_31_90_gbp),
            bucket_91_183_gbp=str(value.bucket_91_183_gbp),
            bucket_over_183_gbp=str(value.bucket_over_183_gbp),
            total_value_gbp=str(value.total_value_gbp),
            lot_count=value.lot_count,
        )


class RiskRebalanceFrictionSchema(BaseModel):
    available: bool
    employer_ticker: str | None
    target_pct: str
    current_pct: str
    reduction_required_gbp: str
    reduction_possible_gbp: str
    lock_barrier_gbp: str
    estimated_employment_tax_gbp: str
    implied_tax_rate_pct: str
    post_reduction_pct: str
    note: str | None

    @classmethod
    def from_service(cls, value: RiskRebalanceFriction) -> "RiskRebalanceFrictionSchema":
        return cls(
            available=value.available,
            employer_ticker=value.employer_ticker,
            target_pct=str(value.target_pct),
            current_pct=str(value.current_pct),
            reduction_required_gbp=str(value.reduction_required_gbp),
            reduction_possible_gbp=str(value.reduction_possible_gbp),
            lock_barrier_gbp=str(value.lock_barrier_gbp),
            estimated_employment_tax_gbp=str(value.estimated_employment_tax_gbp),
            implied_tax_rate_pct=str(value.implied_tax_rate_pct),
            post_reduction_pct=str(value.post_reduction_pct),
            note=value.note,
        )


class RiskSummarySchema(BaseModel):
    generated_at_utc: str
    as_of_date: str
    total_market_value_gbp: str
    top_holding_pct: str
    top_holding_sellable_pct: str
    security_concentration: list[RiskConcentrationItemSchema]
    scheme_concentration: list[RiskConcentrationItemSchema]
    liquidity: RiskLiquidityBreakdownSchema
    deployable: RiskDeployableBreakdownSchema
    employer_dependence: EmployerDependenceBreakdownSchema
    wrapper_allocation: RiskWrapperAllocationSchema
    valuation_basis: RiskValuationBasisSchema
    stress_points: list[RiskStressPointSchema]
    optionality_timeline: list[RiskOptionalityTimelineBandSchema]
    optionality_index: RiskOptionalityIndexSchema
    concentration_guardrails: list[RiskConcentrationGuardrailSchema]
    forfeiture_heatmap_rows: list[RiskForfeitureHeatmapRowSchema]
    forfeiture_heatmap_totals: dict[str, str]
    rebalance_friction: RiskRebalanceFrictionSchema
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
        if summary.wrapper_allocation is None:
            raise ValueError("Risk summary wrapper allocation must be populated.")
        if summary.valuation_basis is None:
            raise ValueError("Risk summary valuation basis must be populated.")
        if summary.optionality_index is None:
            raise ValueError("Risk summary optionality index must be populated.")
        return cls(
            generated_at_utc=summary.generated_at_utc.isoformat(),
            as_of_date=summary.as_of_date.isoformat(),
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
            wrapper_allocation=RiskWrapperAllocationSchema.from_service(
                summary.wrapper_allocation
            ),
            valuation_basis=RiskValuationBasisSchema.from_service(
                summary.valuation_basis
            ),
            stress_points=[
                RiskStressPointSchema.from_service(point)
                for point in summary.stress_points
            ],
            optionality_timeline=[
                RiskOptionalityTimelineBandSchema.from_service(point)
                for point in summary.optionality_timeline
            ],
            optionality_index=RiskOptionalityIndexSchema.from_service(
                summary.optionality_index
            ),
            concentration_guardrails=[
                RiskConcentrationGuardrailSchema.from_service(row)
                for row in summary.concentration_guardrails
            ],
            forfeiture_heatmap_rows=[
                RiskForfeitureHeatmapRowSchema.from_service(row)
                for row in summary.forfeiture_heatmap_rows
            ],
            forfeiture_heatmap_totals={
                key: str(value) for key, value in summary.forfeiture_heatmap_totals.items()
            },
            rebalance_friction=RiskRebalanceFrictionSchema.from_service(
                summary.rebalance_friction
                if summary.rebalance_friction is not None
                else RiskRebalanceFriction(
                    available=False,
                    employer_ticker=None,
                    target_pct=Decimal("0.00"),
                    current_pct=Decimal("0.00"),
                    reduction_required_gbp=Decimal("0.00"),
                    reduction_possible_gbp=Decimal("0.00"),
                    lock_barrier_gbp=Decimal("0.00"),
                    estimated_employment_tax_gbp=Decimal("0.00"),
                    implied_tax_rate_pct=Decimal("0.00"),
                    post_reduction_pct=Decimal("0.00"),
                    note="Unavailable.",
                )
            ),
            notes=list(summary.notes),
        )
