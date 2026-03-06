"""
RiskService - read-only concentration, liquidity, and stress aggregations.

This service is intentionally additive and consumes existing portfolio summary
outputs without mutating any portfolio/tax/FIFO state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from .exposure_service import ExposureService
from .portfolio_service import PortfolioService
from ..settings import AppSettings

_GBP_Q = Decimal("0.01")
_PCT_Q = Decimal("0.01")
_HUNDRED = Decimal("100")

_SCHEME_LABELS: dict[str, str] = {
    "RSU": "RSU",
    "ESPP": "ESPP",
    "ESPP_PLUS": "ESPP+",
    "SIP_PARTNERSHIP": "SIP Partnership",
    "SIP_MATCHING": "SIP Matching",
    "SIP_DIVIDEND": "SIP Dividend",
    "BROKERAGE": "Brokerage",
    "ISA": "ISA",
}

_STRESS_SHOCKS: tuple[Decimal, ...] = (
    Decimal("-30"),
    Decimal("-20"),
    Decimal("-10"),
    Decimal("0"),
    Decimal("10"),
    Decimal("20"),
)

_OPTIONALITY_TIMELINE_BANDS: tuple[tuple[str, int], ...] = (
    ("Now", 0),
    ("6m", 183),
    ("1y", 365),
    ("3y", 1095),
    ("5y", 1825),
)

_OPTIONALITY_WEIGHT_DEFAULTS: dict[str, Decimal] = {
    "sellability": Decimal("35"),
    "forfeiture": Decimal("20"),
    "concentration": Decimal("20"),
    "isa_ratio": Decimal("15"),
    "config": Decimal("10"),
}


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_GBP_Q, rounding=ROUND_HALF_UP)


def _q_pct(value: Decimal) -> Decimal:
    return value.quantize(_PCT_Q, rounding=ROUND_HALF_UP)


def _pct(part: Decimal, whole: Decimal) -> Decimal:
    if whole <= Decimal("0"):
        return Decimal("0.00")
    return _q_pct((part / whole) * _HUNDRED)


def _normalize_optionality_weights(
    weights: dict[str, Decimal] | None,
) -> dict[str, Decimal]:
    normalized = dict(_OPTIONALITY_WEIGHT_DEFAULTS)
    if weights:
        for key, value in weights.items():
            if key not in normalized:
                continue
            if value < Decimal("0"):
                continue
            normalized[key] = _q_pct(value)
    total = sum(normalized.values(), Decimal("0"))
    if total <= Decimal("0"):
        return dict(_OPTIONALITY_WEIGHT_DEFAULTS)
    if total == Decimal("100"):
        return normalized

    scaled: dict[str, Decimal] = {}
    running_total = Decimal("0")
    ranked_keys = sorted(normalized.keys())
    for key in ranked_keys:
        scaled_value = _q_pct((normalized[key] / total) * Decimal("100"))
        scaled[key] = scaled_value
        running_total += scaled_value
    remainder = _q_pct(Decimal("100") - running_total)
    if remainder != Decimal("0"):
        top_key = max(ranked_keys, key=lambda k: normalized[k])
        scaled[top_key] = _q_pct(scaled[top_key] + remainder)
    return scaled


def _optionality_config_score(settings: AppSettings | None) -> Decimal:
    if settings is None:
        return Decimal("0.00")

    checks = [
        bool(str(getattr(settings, "employer_ticker", "") or "").strip()),
        not (
            getattr(settings, "default_gross_income", Decimal("0")) <= Decimal("0")
            and getattr(settings, "default_other_income", Decimal("0")) <= Decimal("0")
        ),
        getattr(settings, "default_student_loan_plan", None) is not None,
        getattr(settings, "employer_income_dependency_pct", None) is not None,
    ]
    passed = sum(1 for ok in checks if ok)
    return _q_pct((Decimal(passed) / Decimal(len(checks))) * Decimal("100"))


@dataclass(frozen=True)
class RiskConcentrationItem:
    key: str
    label: str
    value_gbp: Decimal
    pct_of_total: Decimal


@dataclass(frozen=True)
class RiskLiquidityBreakdown:
    sellable_gbp: Decimal
    locked_gbp: Decimal
    at_risk_gbp: Decimal
    classified_total_gbp: Decimal
    sellable_pct: Decimal
    locked_pct: Decimal
    at_risk_pct: Decimal
    unpriced_lot_count: int


@dataclass(frozen=True)
class RiskDeployableBreakdown:
    sellable_holdings_gbp: Decimal
    deployable_cash_gbp: Decimal
    deployable_capital_gbp: Decimal
    employer_sellable_market_value_gbp: Decimal
    employer_share_of_deployable_pct: Decimal


@dataclass(frozen=True)
class EmployerDependenceBreakdown:
    employer_ticker: str | None
    employer_equity_gbp: Decimal
    income_dependency_proxy_gbp: Decimal
    income_dependency_pct: Decimal
    denominator_gbp: Decimal
    ratio_pct: Decimal


@dataclass(frozen=True)
class RiskStressPoint:
    shock_pct: Decimal
    shock_label: str
    stressed_market_value_gbp: Decimal


@dataclass(frozen=True)
class RiskWrapperAllocation:
    isa_market_value_gbp: Decimal
    taxable_market_value_gbp: Decimal
    isa_pct_of_total: Decimal
    taxable_pct_of_total: Decimal


@dataclass(frozen=True)
class RiskOptionalityTimelineBand:
    label: str
    horizon_days: int
    as_of_date: date
    sellable_gbp: Decimal
    locked_gbp: Decimal
    forfeitable_gbp: Decimal
    deployable_capital_gbp: Decimal
    sellable_pct: Decimal
    locked_pct: Decimal
    forfeitable_pct: Decimal
    deployable_pct: Decimal


@dataclass(frozen=True)
class RiskOptionalityIndex:
    score: Decimal
    weights_pct: dict[str, Decimal]
    components_pct: dict[str, Decimal]
    notes: list[str]


@dataclass(frozen=True)
class RiskSummary:
    generated_at_utc: datetime
    total_market_value_gbp: Decimal
    top_holding_pct: Decimal
    top_holding_sellable_pct: Decimal
    security_concentration: list[RiskConcentrationItem] = field(default_factory=list)
    scheme_concentration: list[RiskConcentrationItem] = field(default_factory=list)
    liquidity: RiskLiquidityBreakdown | None = None
    deployable: RiskDeployableBreakdown | None = None
    employer_dependence: EmployerDependenceBreakdown | None = None
    wrapper_allocation: RiskWrapperAllocation | None = None
    stress_points: list[RiskStressPoint] = field(default_factory=list)
    optionality_timeline: list[RiskOptionalityTimelineBand] = field(default_factory=list)
    optionality_index: RiskOptionalityIndex | None = None
    notes: list[str] = field(default_factory=list)


class RiskService:
    """
    Build portfolio risk views from current summary data.
    """

    @staticmethod
    def get_risk_summary(
        settings: AppSettings | None = None,
        db_path=None,
        optionality_weights: dict[str, Decimal] | None = None,
    ) -> RiskSummary:
        summary = PortfolioService.get_portfolio_summary(
            settings=settings,
            use_live_true_cost=False,
        )
        return RiskService._from_portfolio_summary(
            summary,
            settings=settings,
            db_path=db_path,
            optionality_weights=optionality_weights,
        )

    @staticmethod
    def _from_portfolio_summary(
        summary,
        *,
        settings: AppSettings | None = None,
        db_path=None,
        optionality_weights: dict[str, Decimal] | None = None,
    ) -> RiskSummary:
        security_values: list[tuple[str, str, Decimal]] = []
        scheme_values: dict[str, Decimal] = {}
        sellable = Decimal("0")
        locked = Decimal("0")
        at_risk = Decimal("0")
        isa_market_value = Decimal("0")
        taxable_market_value = Decimal("0")
        unpriced_lot_count = 0
        unpriced_security_count = 0

        for security_summary in summary.securities:
            if security_summary.market_value_gbp is not None:
                security_values.append(
                    (
                        security_summary.security.id,
                        security_summary.security.ticker,
                        _q_money(security_summary.market_value_gbp),
                    )
                )
            elif security_summary.active_lots:
                unpriced_security_count += 1

            for lot_summary in security_summary.active_lots:
                lot_mv = lot_summary.market_value_gbp
                if lot_mv is None:
                    unpriced_lot_count += 1
                    continue

                lot_mv_q = _q_money(lot_mv)
                scheme_key = lot_summary.lot.scheme_type
                scheme_values[scheme_key] = scheme_values.get(
                    scheme_key, Decimal("0")
                ) + lot_mv_q
                if scheme_key == "ISA":
                    isa_market_value += lot_mv_q
                else:
                    taxable_market_value += lot_mv_q

                status = (lot_summary.sellability_status or "SELLABLE").upper()
                if status == "LOCKED":
                    locked += lot_mv_q
                elif status == "AT_RISK":
                    at_risk += lot_mv_q
                else:
                    sellable += lot_mv_q

        total_market_value = _q_money(
            sum((value for _, _, value in security_values), Decimal("0"))
        )
        security_sorted = sorted(security_values, key=lambda row: row[2], reverse=True)
        security_concentration = [
            RiskConcentrationItem(
                key=security_id,
                label=ticker,
                value_gbp=value,
                pct_of_total=_pct(value, total_market_value),
            )
            for security_id, ticker, value in security_sorted
        ]

        scheme_sorted = sorted(scheme_values.items(), key=lambda item: item[1], reverse=True)
        scheme_concentration = [
            RiskConcentrationItem(
                key=scheme_type,
                label=_SCHEME_LABELS.get(scheme_type, scheme_type),
                value_gbp=_q_money(value),
                pct_of_total=_pct(value, total_market_value),
            )
            for scheme_type, value in scheme_sorted
        ]

        classified_total = _q_money(sellable + locked + at_risk)
        liquidity = RiskLiquidityBreakdown(
            sellable_gbp=_q_money(sellable),
            locked_gbp=_q_money(locked),
            at_risk_gbp=_q_money(at_risk),
            classified_total_gbp=classified_total,
            sellable_pct=_pct(sellable, classified_total),
            locked_pct=_pct(locked, classified_total),
            at_risk_pct=_pct(at_risk, classified_total),
            unpriced_lot_count=unpriced_lot_count,
        )
        wrapper_total = _q_money(isa_market_value + taxable_market_value)
        wrapper_allocation = RiskWrapperAllocation(
            isa_market_value_gbp=_q_money(isa_market_value),
            taxable_market_value_gbp=_q_money(taxable_market_value),
            isa_pct_of_total=_pct(isa_market_value, wrapper_total),
            taxable_pct_of_total=_pct(taxable_market_value, wrapper_total),
        )

        stress_points = [
            RiskStressPoint(
                shock_pct=shock,
                shock_label=f"{shock:+.0f}%",
                stressed_market_value_gbp=_q_money(
                    total_market_value * ((_HUNDRED + shock) / _HUNDRED)
                ),
            )
            for shock in _STRESS_SHOCKS
        ]

        notes: list[str] = []
        if total_market_value <= Decimal("0"):
            notes.append(
                "No priced holdings available. Concentration and stress values are zeroed."
            )
        if unpriced_lot_count > 0:
            notes.append(
                f"{unpriced_lot_count} lot(s) excluded due to missing live prices."
            )
        if unpriced_security_count > 0:
            notes.append(
                f"{unpriced_security_count} security(ies) have active lots but no current market value."
            )

        top_holding_pct = (
            security_concentration[0].pct_of_total
            if security_concentration
            else Decimal("0.00")
        )
        exposure = ExposureService.get_snapshot(
            settings=settings,
            db_path=db_path,
            summary=summary,
        )
        deployable = RiskDeployableBreakdown(
            sellable_holdings_gbp=_q_money(
                Decimal(str(exposure["total_sellable_market_value_gbp"]))
            ),
            deployable_cash_gbp=_q_money(
                Decimal(str(exposure["deployable_cash_gbp"]))
            ),
            deployable_capital_gbp=_q_money(
                Decimal(str(exposure["deployable_capital_gbp"]))
            ),
            employer_sellable_market_value_gbp=_q_money(
                Decimal(str(exposure["employer_sellable_market_value_gbp"]))
            ),
            employer_share_of_deployable_pct=_q_pct(
                Decimal(str(exposure["employer_share_of_deployable_pct"]))
            ),
        )
        employer_dependence = EmployerDependenceBreakdown(
            employer_ticker=exposure.get("employer_ticker"),
            employer_equity_gbp=_q_money(
                Decimal(str(exposure["employer_market_value_gbp"]))
            ),
            income_dependency_proxy_gbp=_q_money(
                Decimal(str(exposure["employer_income_dependency_proxy_gbp"]))
            ),
            income_dependency_pct=_q_pct(
                Decimal(str(exposure["employer_income_dependency_pct"]))
            ),
            denominator_gbp=_q_money(
                Decimal(str(exposure["employer_dependence_denominator_gbp"]))
            ),
            ratio_pct=_q_pct(
                Decimal(str(exposure["employer_dependence_ratio_pct"]))
            ),
        )
        notes.extend(list(exposure.get("notes", [])))

        deployable_cash = _q_money(Decimal(str(exposure["deployable_cash_gbp"])))
        today = date.today()
        timeline: list[RiskOptionalityTimelineBand] = []

        for label, horizon_days in _OPTIONALITY_TIMELINE_BANDS:
            as_of = today + timedelta(days=horizon_days)
            band_sellable = Decimal("0")
            band_locked = Decimal("0")
            band_forfeitable = Decimal("0")

            for security_summary in summary.securities:
                for lot_summary in security_summary.active_lots:
                    mv = lot_summary.market_value_gbp
                    if mv is None:
                        continue
                    value = _q_money(Decimal(mv))
                    lot = lot_summary.lot
                    scheme_type = (lot.scheme_type or "").upper()

                    if scheme_type == "RSU" and as_of < lot.acquisition_date:
                        band_locked += value
                        continue

                    if scheme_type == "ESPP_PLUS" and lot.matching_lot_id is not None:
                        end = lot.forfeiture_period_end or (lot.acquisition_date + timedelta(days=183))
                        if as_of < end:
                            band_forfeitable += value
                            continue

                    band_sellable += value

            classified_band_total = _q_money(band_sellable + band_locked + band_forfeitable)
            deployable_capital_band = _q_money(band_sellable + deployable_cash)
            deployable_base_band = _q_money(classified_band_total + deployable_cash)
            timeline.append(
                RiskOptionalityTimelineBand(
                    label=label,
                    horizon_days=horizon_days,
                    as_of_date=as_of,
                    sellable_gbp=_q_money(band_sellable),
                    locked_gbp=_q_money(band_locked),
                    forfeitable_gbp=_q_money(band_forfeitable),
                    deployable_capital_gbp=deployable_capital_band,
                    sellable_pct=_pct(band_sellable, classified_band_total),
                    locked_pct=_pct(band_locked, classified_band_total),
                    forfeitable_pct=_pct(band_forfeitable, classified_band_total),
                    deployable_pct=_pct(deployable_capital_band, deployable_base_band),
                )
            )

        now_band = timeline[0] if timeline else None
        weights = _normalize_optionality_weights(optionality_weights)
        isa_ratio_pct = wrapper_allocation.isa_pct_of_total
        config_score_pct = _optionality_config_score(settings)
        components = {
            "sellability": now_band.sellable_pct if now_band is not None else Decimal("0.00"),
            "forfeiture": (
                _q_pct(Decimal("100") - now_band.forfeitable_pct)
                if now_band is not None
                else Decimal("100.00")
            ),
            "concentration": _q_pct(Decimal("100") - top_holding_pct),
            "isa_ratio": isa_ratio_pct,
            "config": config_score_pct,
        }
        weighted_score_sum = Decimal("0")
        for key, component_pct in components.items():
            weighted_score_sum += component_pct * weights[key]
        optionality_score = _q_pct(weighted_score_sum / Decimal("100"))
        optionality_notes = [
            "Optionality score is deterministic and uses current holdings, lock dates, and configuration completeness only.",
            "No market return, volatility, or timing prediction is included.",
        ]
        optionality_index = RiskOptionalityIndex(
            score=optionality_score,
            weights_pct=weights,
            components_pct=components,
            notes=optionality_notes,
        )

        return RiskSummary(
            generated_at_utc=datetime.now(timezone.utc),
            total_market_value_gbp=total_market_value,
            top_holding_pct=top_holding_pct,
            top_holding_sellable_pct=_q_pct(
                Decimal(str(exposure["top_holding_pct_sellable"]))
            ),
            security_concentration=security_concentration,
            scheme_concentration=scheme_concentration,
            liquidity=liquidity,
            deployable=deployable,
            employer_dependence=employer_dependence,
            wrapper_allocation=wrapper_allocation,
            stress_points=stress_points,
            optionality_timeline=timeline,
            optionality_index=optionality_index,
            notes=notes,
        )
