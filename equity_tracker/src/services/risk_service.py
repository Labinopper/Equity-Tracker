"""
RiskService - read-only concentration, liquidity, and stress aggregations.

This service is intentionally additive and consumes existing portfolio summary
outputs without mutating any portfolio/tax/FIFO state.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from .alert_service import AlertService
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


def _build_valuation_basis(summary) -> "RiskValuationBasis":
    securities = list(summary.securities or [])
    total_security_count = len(securities)

    price_dates = [ss.price_as_of for ss in securities if ss.price_as_of is not None]
    price_as_of_latest = max(price_dates).isoformat() if price_dates else None
    price_as_of_earliest = min(price_dates).isoformat() if price_dates else None
    stale_price_count = sum(
        1 for ss in securities if ss.price_as_of is not None and ss.price_is_stale
    )
    missing_price_count = total_security_count - len(price_dates)

    fx_required = [
        ss
        for ss in securities
        if str(getattr(ss.security, "currency", "") or "").upper() != "GBP"
    ]
    fx_required_count = len(fx_required)
    fx_rows = [str(ss.fx_as_of) for ss in fx_required if ss.fx_as_of]
    fx_as_of_latest = max(fx_rows) if fx_rows else None
    fx_as_of_earliest = min(fx_rows) if fx_rows else None
    stale_fx_count = sum(1 for ss in fx_required if ss.fx_as_of and ss.fx_is_stale)
    missing_fx_count = fx_required_count - len(fx_rows)

    if fx_required_count <= 0:
        fx_basis_note = "GBP-only holdings (no FX conversion)"
    elif missing_fx_count > 0:
        fx_basis_note = "FX basis incomplete for part of the priced set."
    else:
        fx_basis_note = None

    return RiskValuationBasis(
        total_security_count=total_security_count,
        price_tracked_count=len(price_dates),
        price_as_of_latest=price_as_of_latest,
        price_as_of_earliest=price_as_of_earliest,
        price_dates_mixed=len(set(price_dates)) > 1,
        stale_price_count=stale_price_count,
        missing_price_count=missing_price_count,
        fx_required_count=fx_required_count,
        fx_as_of_count=len(fx_rows),
        fx_as_of_latest=fx_as_of_latest,
        fx_as_of_earliest=fx_as_of_earliest,
        fx_dates_mixed=len(set(fx_rows)) > 1,
        stale_fx_count=stale_fx_count,
        missing_fx_count=missing_fx_count,
        fx_basis_note=fx_basis_note,
    )


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
class RiskValuationBasis:
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
    fx_basis_note: str | None = None


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
class RiskConcentrationGuardrail:
    guardrail_id: str
    label: str
    threshold_pct: Decimal
    actual_pct: Decimal
    breach_pct: Decimal
    status: str
    message: str


@dataclass(frozen=True)
class RiskForfeitureHeatmapRow:
    security_id: str
    ticker: str
    bucket_0_30_gbp: Decimal
    bucket_31_90_gbp: Decimal
    bucket_91_183_gbp: Decimal
    bucket_over_183_gbp: Decimal
    total_value_gbp: Decimal
    lot_count: int


@dataclass(frozen=True)
class RiskRebalanceFriction:
    available: bool
    employer_ticker: str | None
    target_pct: Decimal
    current_pct: Decimal
    reduction_required_gbp: Decimal
    reduction_possible_gbp: Decimal
    lock_barrier_gbp: Decimal
    estimated_employment_tax_gbp: Decimal
    implied_tax_rate_pct: Decimal
    post_reduction_pct: Decimal
    note: str | None = None


@dataclass(frozen=True)
class RiskSummary:
    generated_at_utc: datetime
    as_of_date: date
    total_market_value_gbp: Decimal
    top_holding_pct: Decimal
    top_holding_sellable_pct: Decimal
    security_concentration: list[RiskConcentrationItem] = field(default_factory=list)
    scheme_concentration: list[RiskConcentrationItem] = field(default_factory=list)
    liquidity: RiskLiquidityBreakdown | None = None
    deployable: RiskDeployableBreakdown | None = None
    employer_dependence: EmployerDependenceBreakdown | None = None
    wrapper_allocation: RiskWrapperAllocation | None = None
    valuation_basis: RiskValuationBasis | None = None
    stress_points: list[RiskStressPoint] = field(default_factory=list)
    optionality_timeline: list[RiskOptionalityTimelineBand] = field(default_factory=list)
    optionality_index: RiskOptionalityIndex | None = None
    concentration_guardrails: list[RiskConcentrationGuardrail] = field(default_factory=list)
    forfeiture_heatmap_rows: list[RiskForfeitureHeatmapRow] = field(default_factory=list)
    forfeiture_heatmap_totals: dict[str, Decimal] = field(default_factory=dict)
    rebalance_friction: RiskRebalanceFriction | None = None
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
        as_of: date | None = None,
    ) -> RiskSummary:
        as_of_date = as_of or date.today()
        summary = PortfolioService.get_portfolio_summary(
            settings=settings,
            use_live_true_cost=False,
            as_of=as_of_date,
        )
        return RiskService._from_portfolio_summary(
            summary,
            settings=settings,
            db_path=db_path,
            optionality_weights=optionality_weights,
            as_of=as_of_date,
        )

    @staticmethod
    def _from_portfolio_summary(
        summary,
        *,
        settings: AppSettings | None = None,
        db_path=None,
        optionality_weights: dict[str, Decimal] | None = None,
        as_of: date | None = None,
    ) -> RiskSummary:
        as_of_date = as_of or date.today()
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
        valuation_basis = _build_valuation_basis(summary)

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
        timeline: list[RiskOptionalityTimelineBand] = []

        for label, horizon_days in _OPTIONALITY_TIMELINE_BANDS:
            band_as_of = as_of_date + timedelta(days=horizon_days)
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

                    if scheme_type == "RSU" and band_as_of < lot.acquisition_date:
                        band_locked += value
                        continue

                    if scheme_type == "ESPP_PLUS" and lot.matching_lot_id is not None:
                        end = lot.forfeiture_period_end or (lot.acquisition_date + timedelta(days=183))
                        if band_as_of < end:
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
                    as_of_date=band_as_of,
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

        alert_thresholds = AlertService.concentration_thresholds(settings)
        top_threshold_pct = _q_pct(alert_thresholds.get("top_holding_pct", Decimal("50")))
        employer_threshold_pct = _q_pct(alert_thresholds.get("employer_pct", Decimal("40")))
        employer_pct_of_gross = _q_pct(
            Decimal(str(exposure.get("employer_pct_of_gross", Decimal("0"))))
        )

        concentration_guardrails: list[RiskConcentrationGuardrail] = []

        def _guardrail_row(
            *,
            guardrail_id: str,
            label: str,
            threshold_pct: Decimal,
            actual_pct: Decimal,
        ) -> RiskConcentrationGuardrail:
            breach = _q_pct(max(Decimal("0"), actual_pct - threshold_pct))
            is_breach = actual_pct > threshold_pct
            return RiskConcentrationGuardrail(
                guardrail_id=guardrail_id,
                label=label,
                threshold_pct=_q_pct(threshold_pct),
                actual_pct=_q_pct(actual_pct),
                breach_pct=breach,
                status="BREACH" if is_breach else "OK",
                message=(
                    f"{label}: {actual_pct}% vs threshold {threshold_pct}%."
                    if is_breach
                    else f"{label}: {actual_pct}% is within threshold {threshold_pct}%."
                ),
            )

        concentration_guardrails.append(
            _guardrail_row(
                guardrail_id="top_holding_gross",
                label="Top Holding (Gross)",
                threshold_pct=top_threshold_pct,
                actual_pct=top_holding_pct,
            )
        )
        concentration_guardrails.append(
            _guardrail_row(
                guardrail_id="top_holding_sellable",
                label="Top Holding (Sellable Pool)",
                threshold_pct=top_threshold_pct,
                actual_pct=_q_pct(Decimal(str(exposure["top_holding_pct_sellable"]))),
            )
        )
        concentration_guardrails.append(
            _guardrail_row(
                guardrail_id="employer_exposure_gross",
                label=(
                    f"Employer Exposure ({exposure.get('employer_ticker')})"
                    if exposure.get("employer_ticker")
                    else "Employer Exposure (Unconfigured)"
                ),
                threshold_pct=employer_threshold_pct,
                actual_pct=employer_pct_of_gross,
            )
        )

        forfeiture_heatmap_map: dict[str, dict[str, Decimal | int | str]] = defaultdict(
            lambda: {
                "security_id": "",
                "ticker": "",
                "bucket_0_30_gbp": Decimal("0"),
                "bucket_31_90_gbp": Decimal("0"),
                "bucket_91_183_gbp": Decimal("0"),
                "bucket_over_183_gbp": Decimal("0"),
                "lot_count": 0,
            }
        )

        for security_summary in summary.securities:
            for lot_summary in security_summary.active_lots:
                risk = lot_summary.forfeiture_risk
                lot = lot_summary.lot
                if (
                    risk is None
                    or not risk.in_window
                    or (lot.scheme_type or "").upper() != "ESPP_PLUS"
                    or lot.matching_lot_id is None
                ):
                    continue
                row = forfeiture_heatmap_map[security_summary.security.id]
                row["security_id"] = security_summary.security.id
                row["ticker"] = security_summary.security.ticker
                row["lot_count"] = int(row["lot_count"]) + 1
                value = (
                    _q_money(Decimal(lot_summary.market_value_gbp))
                    if lot_summary.market_value_gbp is not None
                    else Decimal("0")
                )
                if risk.days_remaining <= 30:
                    row["bucket_0_30_gbp"] = Decimal(row["bucket_0_30_gbp"]) + value
                elif risk.days_remaining <= 90:
                    row["bucket_31_90_gbp"] = Decimal(row["bucket_31_90_gbp"]) + value
                elif risk.days_remaining <= 183:
                    row["bucket_91_183_gbp"] = Decimal(row["bucket_91_183_gbp"]) + value
                else:
                    row["bucket_over_183_gbp"] = Decimal(row["bucket_over_183_gbp"]) + value

        forfeiture_heatmap_rows: list[RiskForfeitureHeatmapRow] = []
        totals_0_30 = Decimal("0")
        totals_31_90 = Decimal("0")
        totals_91_183 = Decimal("0")
        totals_over_183 = Decimal("0")
        for item in forfeiture_heatmap_map.values():
            bucket_0_30 = _q_money(Decimal(item["bucket_0_30_gbp"]))
            bucket_31_90 = _q_money(Decimal(item["bucket_31_90_gbp"]))
            bucket_91_183 = _q_money(Decimal(item["bucket_91_183_gbp"]))
            bucket_over_183 = _q_money(Decimal(item["bucket_over_183_gbp"]))
            total_row = _q_money(bucket_0_30 + bucket_31_90 + bucket_91_183 + bucket_over_183)
            totals_0_30 += bucket_0_30
            totals_31_90 += bucket_31_90
            totals_91_183 += bucket_91_183
            totals_over_183 += bucket_over_183
            forfeiture_heatmap_rows.append(
                RiskForfeitureHeatmapRow(
                    security_id=str(item["security_id"]),
                    ticker=str(item["ticker"]),
                    bucket_0_30_gbp=bucket_0_30,
                    bucket_31_90_gbp=bucket_31_90,
                    bucket_91_183_gbp=bucket_91_183,
                    bucket_over_183_gbp=bucket_over_183,
                    total_value_gbp=total_row,
                    lot_count=int(item["lot_count"]),
                )
            )
        forfeiture_heatmap_rows.sort(
            key=lambda row: (row.total_value_gbp, row.lot_count, row.ticker),
            reverse=True,
        )
        forfeiture_heatmap_totals = {
            "bucket_0_30_gbp": _q_money(totals_0_30),
            "bucket_31_90_gbp": _q_money(totals_31_90),
            "bucket_91_183_gbp": _q_money(totals_91_183),
            "bucket_over_183_gbp": _q_money(totals_over_183),
            "total_value_gbp": _q_money(
                totals_0_30 + totals_31_90 + totals_91_183 + totals_over_183
            ),
        }

        employer_ticker = str(exposure.get("employer_ticker") or "").strip().upper() or None
        employer_market_value = _q_money(
            Decimal(str(exposure.get("employer_market_value_gbp", Decimal("0"))))
        )
        employer_sellable_market_value = _q_money(
            Decimal(str(exposure.get("employer_sellable_market_value_gbp", Decimal("0"))))
        )
        target_pct = employer_threshold_pct
        target_value = _q_money(total_market_value * (target_pct / Decimal("100")))
        reduction_required = (
            _q_money(max(Decimal("0"), employer_market_value - target_value))
            if total_market_value > Decimal("0")
            else Decimal("0")
        )
        reduction_possible = _q_money(
            min(reduction_required, employer_sellable_market_value)
        )
        lock_barrier = _q_money(max(Decimal("0"), reduction_required - reduction_possible))

        employer_sellable_tax = Decimal("0")
        employer_sellable_tax_base = Decimal("0")
        if employer_ticker is not None:
            for security_summary in summary.securities:
                if (security_summary.security.ticker or "").strip().upper() != employer_ticker:
                    continue
                for lot_summary in security_summary.active_lots:
                    if lot_summary.market_value_gbp is None:
                        continue
                    status = (lot_summary.sellability_status or "SELLABLE").upper()
                    if status == "LOCKED":
                        continue
                    if (
                        lot_summary.forfeiture_risk is not None
                        and lot_summary.forfeiture_risk.in_window
                        and lot_summary.lot.matching_lot_id is not None
                    ):
                        continue
                    employer_sellable_tax_base += _q_money(
                        Decimal(lot_summary.market_value_gbp)
                    )
                    if lot_summary.est_employment_tax_on_lot_gbp is not None:
                        employer_sellable_tax += _q_money(
                            Decimal(lot_summary.est_employment_tax_on_lot_gbp)
                        )

        implied_tax_rate_pct = _pct(
            _q_money(employer_sellable_tax),
            _q_money(employer_sellable_tax_base),
        )
        estimated_tax_on_possible_reduction = _q_money(
            reduction_possible * (implied_tax_rate_pct / Decimal("100"))
        )
        post_reduction_pct = _pct(
            _q_money(max(Decimal("0"), employer_market_value - reduction_possible)),
            total_market_value,
        )
        friction_note: str | None = None
        if employer_ticker is None:
            friction_note = "Employer ticker is not configured in Settings."
        elif reduction_required <= Decimal("0"):
            friction_note = "Current employer exposure is already within the configured threshold."
        elif reduction_possible <= Decimal("0"):
            friction_note = "No currently sellable employer lots are available for de-risking."

        rebalance_friction = RiskRebalanceFriction(
            available=bool(employer_ticker),
            employer_ticker=employer_ticker,
            target_pct=target_pct,
            current_pct=employer_pct_of_gross,
            reduction_required_gbp=reduction_required,
            reduction_possible_gbp=reduction_possible,
            lock_barrier_gbp=lock_barrier,
            estimated_employment_tax_gbp=estimated_tax_on_possible_reduction,
            implied_tax_rate_pct=implied_tax_rate_pct,
            post_reduction_pct=post_reduction_pct,
            note=friction_note,
        )

        return RiskSummary(
            generated_at_utc=datetime.now(timezone.utc),
            as_of_date=as_of_date,
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
            valuation_basis=valuation_basis,
            stress_points=stress_points,
            optionality_timeline=timeline,
            optionality_index=optionality_index,
            concentration_guardrails=concentration_guardrails,
            forfeiture_heatmap_rows=forfeiture_heatmap_rows,
            forfeiture_heatmap_totals=forfeiture_heatmap_totals,
            rebalance_friction=rebalance_friction,
            notes=notes,
        )
