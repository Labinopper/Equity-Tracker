"""
ScenarioService - additive Scenario Lab execution and retrieval.

The service provides:
- Scenario builder context for /scenario-lab.
- Multi-leg scenario execution with independent or sequential leg mode.
- Persisted scenario snapshot storage for /api/scenarios/{id}.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..app_context import AppContext
from ..core.lot_engine.fifo import ForfeitureWarning, LotForFIFO, allocate_fifo
from ..core.tax_engine import tax_year_for_date
from ..db.repository import LotRepository, ScenarioSnapshotRepository
from ..settings import AppSettings
from .portfolio_service import (
    PortfolioService,
    _build_sip_tax_estimates,
    _espp_plus_employee_lot_ids,
    _is_lot_sellable_on,
    _lot_to_fifo,
)
from .report_service import ReportService

_GBP_Q = Decimal("0.01")
_PCT_Q = Decimal("0.01")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_MAX_STORED_SCENARIOS = 100
_HIDE_REASON = "Values hidden by privacy mode."
_EXECUTION_INDEPENDENT = "INDEPENDENT"
_EXECUTION_SEQUENTIAL = "SEQUENTIAL"


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_GBP_Q, rounding=ROUND_HALF_UP)


def _q_pct(value: Decimal) -> Decimal:
    return value.quantize(_PCT_Q, rounding=ROUND_HALF_UP)


def _money_str(value: Decimal) -> str:
    return str(_q_money(value))


def _next_tax_year(tax_year: str) -> str:
    start_year = int(tax_year.split("-", 1)[0])
    next_start = start_year + 1
    return f"{next_start}-{str(next_start + 1)[2:]}"


def _apply_price_shock(base_price_gbp: Decimal, shock_pct: Decimal) -> Decimal:
    multiplier = Decimal("1") + (shock_pct / _HUNDRED)
    if multiplier < _ZERO:
        raise ValueError("price_shock_pct results in a negative per-share price.")
    return _q_money(base_price_gbp * multiplier)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_name(name: str | None, as_of_date: date) -> str:
    if name is not None and name.strip():
        return name.strip()
    return f"Scenario {as_of_date.isoformat()}"


def _empty_totals() -> dict[str, str | bool | int]:
    return {
        "quantity_requested": "0",
        "quantity_sold": "0",
        "shortfall": "0",
        "allocation_count": 0,
        "legs_count": 0,
        "has_shortfall": False,
        "total_proceeds_gbp": "0.00",
        "total_cost_basis_gbp": "0.00",
        "total_true_cost_gbp": "0.00",
        "total_realised_gain_gbp": "0.00",
        "total_realised_gain_economic_gbp": "0.00",
        "total_employment_tax_gbp": "0.00",
        "total_net_after_employment_tax_gbp": "0.00",
        "total_forfeiture_value_gbp": "0.00",
    }


def _execution_mode_label(mode: str) -> str:
    if mode == _EXECUTION_SEQUENTIAL:
        return "Sequential"
    return "Independent"


def _normalize_execution_mode(mode: str | None) -> str:
    cleaned = str(mode or "").strip().upper()
    if cleaned == _EXECUTION_SEQUENTIAL:
        return _EXECUTION_SEQUENTIAL
    return _EXECUTION_INDEPENDENT


@dataclass
class _SequentialSecurityState:
    lots_by_id: dict[str, Any]
    fifo_template_by_id: dict[str, LotForFIFO]
    remaining_by_id: dict[str, Decimal]
    lot_order: list[str]
    scheme_by_id: dict[str, str]


def _state_available_quantity(
    state: _SequentialSecurityState,
    *,
    scheme_type: str | None,
) -> Decimal:
    available = Decimal("0")
    for lot_id in state.lot_order:
        if scheme_type is not None and state.scheme_by_id.get(lot_id) != scheme_type:
            continue
        qty = state.remaining_by_id.get(lot_id, Decimal("0"))
        if qty > Decimal("0"):
            available += qty
    return available


def _build_sequential_state(
    *,
    security_id: str,
    as_of_date: date,
    settings: AppSettings | None,
) -> _SequentialSecurityState:
    with AppContext.read_session() as sess:
        lot_repo = LotRepository(sess)
        all_lots = lot_repo.get_all_lots_for_security(security_id)
        espp_plus_employee_ids = _espp_plus_employee_lot_ids(all_lots)

        active_lots = lot_repo.get_active_lots_for_security(security_id)
        active_lots = [lot for lot in active_lots if _is_lot_sellable_on(lot, as_of_date)]

    lots_by_id: dict[str, Any] = {}
    fifo_template_by_id: dict[str, LotForFIFO] = {}
    remaining_by_id: dict[str, Decimal] = {}
    lot_order: list[str] = []
    scheme_by_id: dict[str, str] = {}

    for lot in active_lots:
        fifo_lot = _lot_to_fifo(
            lot,
            settings=settings,
            espp_plus_employee_ids=espp_plus_employee_ids,
            use_live_true_cost=False,
        )
        lots_by_id[lot.id] = lot
        fifo_template_by_id[lot.id] = fifo_lot
        remaining_by_id[lot.id] = Decimal(fifo_lot.quantity_remaining)
        lot_order.append(lot.id)
        scheme_by_id[lot.id] = str(lot.scheme_type or "").upper()

    return _SequentialSecurityState(
        lots_by_id=lots_by_id,
        fifo_template_by_id=fifo_template_by_id,
        remaining_by_id=remaining_by_id,
        lot_order=lot_order,
        scheme_by_id=scheme_by_id,
    )


def _build_forfeiture_warnings_from_state(
    *,
    state: _SequentialSecurityState,
    sold_lot_ids: set[str],
    disposal_price_gbp: Decimal,
    disposal_date: date,
) -> list[ForfeitureWarning]:
    warnings: list[ForfeitureWarning] = []
    for lot_id, lot in state.lots_by_id.items():
        if str(lot.scheme_type or "").upper() != "ESPP_PLUS":
            continue
        if lot.matching_lot_id not in sold_lot_ids:
            continue
        qty = state.remaining_by_id.get(lot_id, Decimal("0"))
        if qty <= Decimal("0"):
            continue
        end = lot.forfeiture_period_end or (lot.acquisition_date + timedelta(days=183))
        if disposal_date >= end:
            continue
        warnings.append(
            ForfeitureWarning(
                lot_id=lot.id,
                acquisition_date=lot.acquisition_date,
                forfeiture_end_date=end,
                days_remaining=(end - disposal_date).days,
                quantity_at_risk=qty,
                value_at_risk_gbp=_q_money(qty * disposal_price_gbp),
                linked_partnership_lot_id=lot.matching_lot_id,
            )
        )
    return warnings


def _simulate_disposal_sequential(
    *,
    state: _SequentialSecurityState,
    quantity: Decimal,
    price_per_share_gbp: Decimal,
    scheme_type: str | None,
    as_of_date: date,
    settings: AppSettings | None,
):
    fifo_lots: list[LotForFIFO] = []
    for lot_id in state.lot_order:
        if scheme_type is not None and state.scheme_by_id.get(lot_id) != scheme_type:
            continue
        remaining = state.remaining_by_id.get(lot_id, Decimal("0"))
        if remaining <= Decimal("0"):
            continue
        template = state.fifo_template_by_id[lot_id]
        fifo_lots.append(dataclasses.replace(template, quantity_remaining=remaining))

    if not fifo_lots:
        raise ValueError("No sellable lots available for the selected leg.")

    fifo_result = allocate_fifo(
        fifo_lots,
        quantity_to_sell=quantity,
        disposal_price_gbp=price_per_share_gbp,
    )
    sold_lot_ids = {allocation.lot_id for allocation in fifo_result.allocations}
    forfeiture_warnings = _build_forfeiture_warnings_from_state(
        state=state,
        sold_lot_ids=sold_lot_ids,
        disposal_price_gbp=price_per_share_gbp,
        disposal_date=as_of_date,
    )
    sip_tax_estimates, total_employment_tax = _build_sip_tax_estimates(
        state.lots_by_id,
        fifo_result.allocations,
        price_per_share_gbp,
        as_of_date,
        settings,
    )
    total_forfeiture_value = sum(
        (warning.value_at_risk_gbp for warning in forfeiture_warnings),
        Decimal("0"),
    )
    result = dataclasses.replace(
        fifo_result,
        forfeiture_warnings=tuple(forfeiture_warnings),
        total_forfeiture_value_gbp=total_forfeiture_value,
        sip_tax_estimates=tuple(sip_tax_estimates),
        total_sip_employment_tax_gbp=total_employment_tax,
    )

    for allocation in result.allocations:
        current_remaining = state.remaining_by_id.get(allocation.lot_id, Decimal("0"))
        state.remaining_by_id[allocation.lot_id] = max(
            Decimal("0"),
            current_remaining - allocation.quantity_allocated,
        )
    return result


class ScenarioService:
    """
    Read/write scenario service with persisted snapshot storage.
    """

    @staticmethod
    def get_builder_context(
        *,
        settings: AppSettings | None = None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        """
        Build security/quantity context for Scenario Lab controls.
        """
        as_of_date = as_of or date.today()
        hide_values = bool(settings and settings.hide_values)

        summary = PortfolioService.get_portfolio_summary(
            settings=settings,
            use_live_true_cost=False,
        )

        securities: list[dict[str, Any]] = []
        for security_summary in summary.securities:
            sellable_total = Decimal("0")
            by_scheme: dict[str, Decimal] = {}

            for lot_summary in security_summary.active_lots:
                # Match /simulate availability semantics:
                # - Exclude currently forfeiture-locked ESPP+ matched lots.
                # - Exclude pre-vest RSU lots.
                if (
                    lot_summary.forfeiture_risk is not None
                    and lot_summary.forfeiture_risk.in_window
                ):
                    continue
                if (
                    lot_summary.lot.scheme_type == "RSU"
                    and lot_summary.lot.acquisition_date > as_of_date
                ):
                    continue

                qty = Decimal(lot_summary.quantity_remaining)
                if qty <= _ZERO:
                    continue
                sellable_total += qty
                scheme_key = lot_summary.lot.scheme_type
                by_scheme[scheme_key] = by_scheme.get(scheme_key, _ZERO) + qty

            if sellable_total <= _ZERO:
                continue

            latest_price = (
                _money_str(Decimal(security_summary.current_price_gbp))
                if security_summary.current_price_gbp is not None and not hide_values
                else None
            )

            securities.append(
                {
                    "id": security_summary.security.id,
                    "ticker": security_summary.security.ticker,
                    "name": security_summary.security.name,
                    "available_quantity": str(sellable_total),
                    "available_by_scheme": {
                        scheme: str(qty)
                        for scheme, qty in sorted(by_scheme.items(), key=lambda item: item[0])
                    },
                    "latest_price_gbp": latest_price,
                    "price_as_of": (
                        security_summary.price_as_of.isoformat()
                        if security_summary.price_as_of is not None
                        else None
                    ),
                }
            )

        securities.sort(key=lambda row: (row["ticker"], row["id"]))

        notes: list[str] = []
        if not securities:
            notes.append("No sellable lots are currently available for scenario planning.")
        else:
            notes.append(
                "Independent mode runs each leg against current FIFO state without consuming prior legs."
            )
            notes.append(
                "Sequential mode consumes FIFO allocations leg-by-leg to model order-sensitive outcomes."
            )
        notes.append(
            "Scenario Lab is a non-destructive planning surface; no disposals are committed."
        )
        notes.append(
            "Cross-year CGT optimization remains in Tax Plan; Scenario Lab focuses on multi-leg disposal composition."
        )
        if hide_values:
            notes = [_HIDE_REASON]

        return {
            "generated_at_utc": _now_utc_iso(),
            "as_of_date": as_of_date.isoformat(),
            "hide_values": hide_values,
            "hidden_reason": _HIDE_REASON if hide_values else None,
            "securities": securities,
            "recent_scenarios": ScenarioService.list_scenarios(settings=settings),
            "defaults": {
                "price_shock_pct": "0.00",
                "execution_mode": _EXECUTION_INDEPENDENT,
            },
            "notes": notes,
        }

    @staticmethod
    def run_scenario(
        *,
        legs: list[dict[str, Any]],
        settings: AppSettings | None = None,
        as_of: date | None = None,
        name: str | None = None,
        price_shock_pct: Decimal = Decimal("0"),
        execution_mode: str = _EXECUTION_INDEPENDENT,
    ) -> dict[str, Any]:
        """
        Execute a multi-leg scenario and store a retrievable snapshot.
        """
        if not legs:
            raise ValueError("At least one scenario leg is required.")

        as_of_date = as_of or date.today()
        shock_pct = _q_pct(price_shock_pct)
        mode = _normalize_execution_mode(execution_mode)
        scenario_id = str(uuid.uuid4())
        scenario_name = _normalize_name(name, as_of_date)
        created_at = _now_utc_iso()

        builder_context = ScenarioService.get_builder_context(
            settings=settings,
            as_of=as_of_date,
        )
        hide_values = bool(builder_context["hide_values"])
        security_context = {row["id"]: row for row in builder_context["securities"]}

        if hide_values:
            hidden_payload = {
                "scenario_id": scenario_id,
                "name": scenario_name,
                "as_of_date": as_of_date.isoformat(),
                "created_at_utc": created_at,
                "price_shock_pct": str(shock_pct),
                "execution_mode": mode,
                "hide_values": True,
                "hidden_reason": _HIDE_REASON,
                "legs": [
                    {
                        "leg_id": idx,
                        "label": leg.get("label"),
                        "security_id": leg.get("security_id"),
                        "scheme_type": leg.get("scheme_type"),
                        "quantity_requested": str(leg.get("quantity")),
                    }
                    for idx, leg in enumerate(legs, start=1)
                ],
                "totals": None,
                "tax_year_context": {
                    "active_tax_year": tax_year_for_date(as_of_date),
                    "next_tax_year": _next_tax_year(tax_year_for_date(as_of_date)),
                },
                "notes": [_HIDE_REASON],
            }
            ScenarioService._store(
                hidden_payload,
                input_snapshot={
                    "legs": legs,
                    "price_shock_pct": str(shock_pct),
                    "execution_mode": mode,
                },
            )
            return hidden_payload

        aggregate_quantity_requested = Decimal("0")
        aggregate_quantity_sold = Decimal("0")
        aggregate_shortfall = Decimal("0")
        aggregate_proceeds = Decimal("0")
        aggregate_cost_basis = Decimal("0")
        aggregate_true_cost = Decimal("0")
        aggregate_realised_gain = Decimal("0")
        aggregate_realised_gain_economic = Decimal("0")
        aggregate_employment_tax = Decimal("0")
        aggregate_forfeiture_value = Decimal("0")
        aggregate_allocation_count = 0
        has_shortfall = False
        legs_with_forfeiture_warning = 0
        sequential_state_by_security: dict[str, _SequentialSecurityState] = {}

        leg_payloads: list[dict[str, Any]] = []
        for idx, leg in enumerate(legs, start=1):
            security_id = str(leg.get("security_id", "")).strip()
            if not security_id:
                raise ValueError(f"Leg {idx} is missing security_id.")

            security_row = security_context.get(security_id)
            if security_row is None:
                raise ValueError(
                    f"Leg {idx} references a security with no sellable quantity on {as_of_date.isoformat()}."
                )

            quantity = Decimal(str(leg["quantity"]))
            scheme_type_raw = leg.get("scheme_type")
            scheme_type = (
                str(scheme_type_raw).strip().upper()
                if scheme_type_raw is not None and str(scheme_type_raw).strip()
                else None
            )

            if mode == _EXECUTION_SEQUENTIAL:
                state = sequential_state_by_security.get(security_id)
                if state is None:
                    state = _build_sequential_state(
                        security_id=security_id,
                        as_of_date=as_of_date,
                        settings=settings,
                    )
                    sequential_state_by_security[security_id] = state
                available_qty = _state_available_quantity(state, scheme_type=scheme_type)
            else:
                if scheme_type is None:
                    available_qty = Decimal(security_row["available_quantity"])
                else:
                    available_qty = Decimal(
                        security_row["available_by_scheme"].get(scheme_type, "0")
                    )

            if quantity > available_qty:
                raise ValueError(
                    f"Leg {idx} quantity ({quantity}) exceeds available ({available_qty}) "
                    f"for {security_row['ticker']} in {mode.lower()} mode."
                )

            price_override = leg.get("price_per_share_gbp")
            if price_override is None:
                latest_price = security_row.get("latest_price_gbp")
                if latest_price is None:
                    raise ValueError(
                        f"Leg {idx} requires price_per_share_gbp because "
                        f"{security_row['ticker']} has no latest price."
                    )
                input_price_gbp = Decimal(latest_price)
                input_price_source = "latest_market_price"
            else:
                input_price_gbp = Decimal(str(price_override))
                input_price_source = "manual"

            shocked_price_gbp = _apply_price_shock(input_price_gbp, shock_pct)
            if mode == _EXECUTION_SEQUENTIAL:
                simulation = _simulate_disposal_sequential(
                    state=state,
                    quantity=quantity,
                    price_per_share_gbp=shocked_price_gbp,
                    scheme_type=scheme_type,
                    as_of_date=as_of_date,
                    settings=settings,
                )
            else:
                simulation = PortfolioService.simulate_disposal(
                    security_id=security_id,
                    quantity=quantity,
                    price_per_share_gbp=shocked_price_gbp,
                    scheme_type=scheme_type,
                    as_of_date=as_of_date,
                    settings=settings,
                    use_live_true_cost=False,
                )

            allocation_tax_map = {
                estimate.lot_id: estimate.est_total_employment_tax_gbp
                for estimate in simulation.sip_tax_estimates
            }
            allocation_rows = [
                {
                    "lot_id": allocation.lot_id,
                    "acquisition_date": allocation.acquisition_date.isoformat(),
                    "quantity_allocated": str(allocation.quantity_allocated),
                    "cost_basis_gbp": _money_str(allocation.cost_basis_gbp),
                    "true_cost_gbp": _money_str(allocation.true_cost_gbp),
                    "proceeds_gbp": _money_str(allocation.proceeds_gbp),
                    "realised_gain_gbp": _money_str(allocation.realised_gain_gbp),
                    "realised_gain_economic_gbp": _money_str(
                        allocation.realised_gain_economic_gbp
                    ),
                    "employment_tax_gbp": _money_str(
                        allocation_tax_map.get(allocation.lot_id, _ZERO)
                    ),
                }
                for allocation in simulation.allocations
            ]

            forfeiture_rows = [
                {
                    "lot_id": warning.lot_id,
                    "acquisition_date": warning.acquisition_date.isoformat(),
                    "forfeiture_end_date": warning.forfeiture_end_date.isoformat(),
                    "days_remaining": warning.days_remaining,
                    "quantity_at_risk": str(warning.quantity_at_risk),
                    "value_at_risk_gbp": _money_str(warning.value_at_risk_gbp),
                    "linked_partnership_lot_id": warning.linked_partnership_lot_id,
                }
                for warning in simulation.forfeiture_warnings
            ]

            net_after_employment_tax = _q_money(
                simulation.total_proceeds_gbp - simulation.total_sip_employment_tax_gbp
            )
            if simulation.forfeiture_warnings:
                legs_with_forfeiture_warning += 1

            leg_payload = {
                "leg_id": idx,
                "label": leg.get("label") or f"Leg {idx}",
                "security_id": security_id,
                "ticker": security_row["ticker"],
                "security_name": security_row["name"],
                "scheme_type": scheme_type,
                "input_price_source": input_price_source,
                "input_price_gbp": _money_str(input_price_gbp),
                "price_after_shock_gbp": _money_str(shocked_price_gbp),
                "applied_price_shock_pct": str(shock_pct),
                "quantity_requested": str(simulation.quantity_requested),
                "quantity_sold": str(simulation.quantity_sold),
                "shortfall": str(simulation.shortfall),
                "is_fully_allocated": simulation.is_fully_allocated,
                "allocation_count": len(simulation.allocations),
                "total_proceeds_gbp": _money_str(simulation.total_proceeds_gbp),
                "total_cost_basis_gbp": _money_str(simulation.total_cost_basis_gbp),
                "total_true_cost_gbp": _money_str(simulation.total_true_cost_gbp),
                "total_realised_gain_gbp": _money_str(simulation.total_realised_gain_gbp),
                "total_realised_gain_economic_gbp": _money_str(
                    simulation.total_realised_gain_economic_gbp
                ),
                "total_employment_tax_gbp": _money_str(
                    simulation.total_sip_employment_tax_gbp
                ),
                "net_after_employment_tax_gbp": _money_str(net_after_employment_tax),
                "forfeiture_warning_count": len(simulation.forfeiture_warnings),
                "total_forfeiture_value_gbp": _money_str(
                    simulation.total_forfeiture_value_gbp
                ),
                "allocations": allocation_rows,
                "forfeiture_warnings": forfeiture_rows,
            }
            leg_payloads.append(leg_payload)

            aggregate_quantity_requested += simulation.quantity_requested
            aggregate_quantity_sold += simulation.quantity_sold
            aggregate_shortfall += simulation.shortfall
            aggregate_proceeds += simulation.total_proceeds_gbp
            aggregate_cost_basis += simulation.total_cost_basis_gbp
            aggregate_true_cost += simulation.total_true_cost_gbp
            aggregate_realised_gain += simulation.total_realised_gain_gbp
            aggregate_realised_gain_economic += simulation.total_realised_gain_economic_gbp
            aggregate_employment_tax += simulation.total_sip_employment_tax_gbp
            aggregate_forfeiture_value += simulation.total_forfeiture_value_gbp
            aggregate_allocation_count += len(simulation.allocations)
            has_shortfall = has_shortfall or (not simulation.is_fully_allocated)

        aggregate_net_after_tax = _q_money(aggregate_proceeds - aggregate_employment_tax)

        active_tax_year = tax_year_for_date(as_of_date)
        next_tax_year = _next_tax_year(active_tax_year)
        active_year_cgt = ReportService.cgt_summary(active_tax_year)
        next_year_cgt = ReportService.cgt_summary(next_tax_year)

        notes: list[str] = []
        notes.append(f"Execution mode: {_execution_mode_label(mode)}.")
        if mode == _EXECUTION_INDEPENDENT:
            notes.append(
                "Independent mode does not consume prior leg allocations."
            )
        else:
            notes.append(
                "Sequential mode consumes each leg's FIFO allocations before the next leg."
            )
        if shock_pct != _ZERO:
            notes.append(
                f"Price shock of {shock_pct}% applied uniformly to all leg prices."
            )
        if has_shortfall:
            notes.append("One or more legs are partially allocated due to quantity shortfall.")
        if legs_with_forfeiture_warning > 0:
            notes.append(
                f"{legs_with_forfeiture_warning} leg(s) include ESPP+ forfeiture warnings."
            )
        notes.append(
            "Employment tax in Scenario Lab includes IT + NI + Student Finance where settings are available."
        )
        notes.append(
            "Cross-year CGT optimization remains in Tax Plan; Scenario Lab focuses on composition and sensitivity."
        )

        payload = {
            "scenario_id": scenario_id,
            "name": scenario_name,
            "as_of_date": as_of_date.isoformat(),
            "created_at_utc": created_at,
            "price_shock_pct": str(shock_pct),
            "execution_mode": mode,
            "hide_values": False,
            "legs": leg_payloads,
            "totals": {
                "quantity_requested": str(aggregate_quantity_requested),
                "quantity_sold": str(aggregate_quantity_sold),
                "shortfall": str(aggregate_shortfall),
                "allocation_count": aggregate_allocation_count,
                "legs_count": len(leg_payloads),
                "has_shortfall": has_shortfall,
                "total_proceeds_gbp": _money_str(aggregate_proceeds),
                "total_cost_basis_gbp": _money_str(aggregate_cost_basis),
                "total_true_cost_gbp": _money_str(aggregate_true_cost),
                "total_realised_gain_gbp": _money_str(aggregate_realised_gain),
                "total_realised_gain_economic_gbp": _money_str(
                    aggregate_realised_gain_economic
                ),
                "total_employment_tax_gbp": _money_str(aggregate_employment_tax),
                "total_net_after_employment_tax_gbp": _money_str(aggregate_net_after_tax),
                "total_forfeiture_value_gbp": _money_str(aggregate_forfeiture_value),
            },
            "tax_year_context": {
                "active_tax_year": active_tax_year,
                "next_tax_year": next_tax_year,
                "active_tax_year_realised_net_gain_gbp": _money_str(
                    active_year_cgt.net_gain_gbp
                ),
                "next_tax_year_realised_net_gain_gbp": _money_str(
                    next_year_cgt.net_gain_gbp
                ),
            },
            "notes": notes,
        }

        ScenarioService._store(
            payload,
            input_snapshot={
                "legs": legs,
                "price_shock_pct": str(shock_pct),
                "execution_mode": mode,
            },
        )
        return payload

    @staticmethod
    def get_scenario(
        scenario_id: str,
        *,
        settings: AppSettings | None = None,
    ) -> dict[str, Any] | None:
        """
        Return a stored scenario payload by ID.
        """
        with AppContext.read_session() as sess:
            repo = ScenarioSnapshotRepository(sess)
            row = repo.get_by_id(scenario_id)
            if row is None:
                return None
            try:
                payload = json.loads(row.payload_json)
            except json.JSONDecodeError:
                return None
        snapshot = copy.deepcopy(payload)
        if bool(settings and settings.hide_values):
            return ScenarioService._hide_payload(snapshot)
        return snapshot

    @staticmethod
    def list_scenarios(
        *,
        settings: AppSettings | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Return recent scenario summaries, newest first.
        """
        with AppContext.read_session() as sess:
            repo = ScenarioSnapshotRepository(sess)
            rows = repo.list_recent(limit=max(limit, 0))

        hide_values = bool(settings and settings.hide_values)
        summaries: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row.payload_json)
            except json.JSONDecodeError:
                continue
            summaries.append(
                ScenarioService._summary_row(payload, hide_values=hide_values)
            )
        return summaries

    @staticmethod
    def _store(
        payload: dict[str, Any],
        *,
        input_snapshot: dict[str, Any] | None = None,
    ) -> None:
        scenario_id = str(payload["scenario_id"])
        as_of_raw = str(payload["as_of_date"])
        mode = _normalize_execution_mode(payload.get("execution_mode"))
        payload_json = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=True,
            default=str,
        )
        input_json = (
            json.dumps(
                input_snapshot,
                sort_keys=True,
                ensure_ascii=True,
                default=str,
            )
            if input_snapshot is not None
            else None
        )
        with AppContext.write_session() as sess:
            repo = ScenarioSnapshotRepository(sess)
            repo.upsert(
                snapshot_id=scenario_id,
                name=str(payload.get("name") or scenario_id),
                as_of_date=date.fromisoformat(as_of_raw),
                execution_mode=mode,
                price_shock_pct=str(payload.get("price_shock_pct", "0.00")),
                payload_json=payload_json,
                input_snapshot_json=input_json,
            )
            repo.trim(max_rows=_MAX_STORED_SCENARIOS)

    @staticmethod
    def _summary_row(
        payload: dict[str, Any],
        *,
        hide_values: bool,
    ) -> dict[str, Any]:
        totals = payload.get("totals") or _empty_totals()
        mode = _normalize_execution_mode(payload.get("execution_mode"))
        if hide_values:
            return {
                "scenario_id": payload["scenario_id"],
                "name": payload["name"],
                "as_of_date": payload["as_of_date"],
                "created_at_utc": payload["created_at_utc"],
                "price_shock_pct": payload.get("price_shock_pct", "0.00"),
                "execution_mode": mode,
                "legs_count": int(totals.get("legs_count", 0)),
                "has_shortfall": bool(totals.get("has_shortfall", False)),
                "hide_values": True,
                "hidden_reason": _HIDE_REASON,
            }

        return {
            "scenario_id": payload["scenario_id"],
            "name": payload["name"],
            "as_of_date": payload["as_of_date"],
            "created_at_utc": payload["created_at_utc"],
            "price_shock_pct": payload.get("price_shock_pct", "0.00"),
            "execution_mode": mode,
            "legs_count": int(totals.get("legs_count", 0)),
            "has_shortfall": bool(totals.get("has_shortfall", False)),
            "total_proceeds_gbp": totals.get("total_proceeds_gbp", "0.00"),
            "total_net_after_employment_tax_gbp": totals.get(
                "total_net_after_employment_tax_gbp",
                "0.00",
            ),
            "total_realised_gain_economic_gbp": totals.get(
                "total_realised_gain_economic_gbp",
                "0.00",
            ),
            "hide_values": False,
        }

    @staticmethod
    def _hide_payload(payload: dict[str, Any]) -> dict[str, Any]:
        hidden_legs = []
        for leg in payload.get("legs", []):
            hidden_legs.append(
                {
                    "leg_id": leg.get("leg_id"),
                    "label": leg.get("label"),
                    "security_id": leg.get("security_id"),
                    "ticker": leg.get("ticker"),
                    "security_name": leg.get("security_name"),
                    "scheme_type": leg.get("scheme_type"),
                    "quantity_requested": leg.get("quantity_requested"),
                    "quantity_sold": leg.get("quantity_sold"),
                    "shortfall": leg.get("shortfall"),
                    "is_fully_allocated": leg.get("is_fully_allocated"),
                }
            )

        return {
            "scenario_id": payload["scenario_id"],
            "name": payload["name"],
            "as_of_date": payload["as_of_date"],
            "created_at_utc": payload["created_at_utc"],
            "price_shock_pct": payload.get("price_shock_pct", "0.00"),
            "execution_mode": _normalize_execution_mode(payload.get("execution_mode")),
            "hide_values": True,
            "hidden_reason": _HIDE_REASON,
            "legs": hidden_legs,
            "totals": None,
            "tax_year_context": payload.get("tax_year_context"),
            "notes": [_HIDE_REASON],
        }
