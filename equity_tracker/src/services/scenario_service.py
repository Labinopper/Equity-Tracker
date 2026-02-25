"""
ScenarioService - additive Scenario Lab execution and retrieval.

The service provides:
- Scenario builder context for /scenario-lab.
- Multi-leg scenario execution via PortfolioService.simulate_disposal().
- In-memory scenario snapshot storage for /api/scenarios/{id}.
"""

from __future__ import annotations

import copy
import uuid
from collections import OrderedDict
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from threading import Lock
from typing import Any

from ..core.tax_engine import tax_year_for_date
from ..settings import AppSettings
from .portfolio_service import PortfolioService
from .report_service import ReportService

_GBP_Q = Decimal("0.01")
_PCT_Q = Decimal("0.01")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_MAX_STORED_SCENARIOS = 100
_HIDE_REASON = "Values hidden by privacy mode."

_SCENARIO_STORE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_SCENARIO_STORE_LOCK = Lock()


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


def _empty_totals() -> dict[str, str | bool]:
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


class ScenarioService:
    """
    Read/write scenario service with in-process storage.
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
                "Scenario legs run independently using current FIFO state and are aggregated."
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
    ) -> dict[str, Any]:
        """
        Execute a multi-leg scenario and store a retrievable snapshot.
        """
        if not legs:
            raise ValueError("At least one scenario leg is required.")

        as_of_date = as_of or date.today()
        shock_pct = _q_pct(price_shock_pct)
        scenario_id = str(uuid.uuid4())
        scenario_name = _normalize_name(name, as_of_date)
        created_at = _now_utc_iso()

        builder_context = ScenarioService.get_builder_context(
            settings=settings,
            as_of=as_of_date,
        )
        hide_values = bool(builder_context["hide_values"])
        security_context = {
            row["id"]: row for row in builder_context["securities"]
        }

        if hide_values:
            hidden_payload = {
                "scenario_id": scenario_id,
                "name": scenario_name,
                "as_of_date": as_of_date.isoformat(),
                "created_at_utc": created_at,
                "price_shock_pct": str(shock_pct),
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
            ScenarioService._store(hidden_payload)
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

            if scheme_type is None:
                available_qty = Decimal(security_row["available_quantity"])
            else:
                available_qty = Decimal(
                    security_row["available_by_scheme"].get(scheme_type, "0")
                )
            if quantity > available_qty:
                raise ValueError(
                    f"Leg {idx} quantity ({quantity}) exceeds available ({available_qty}) "
                    f"for {security_row['ticker']}."
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

        ScenarioService._store(payload)
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
        with _SCENARIO_STORE_LOCK:
            payload = _SCENARIO_STORE.get(scenario_id)
            if payload is None:
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
        with _SCENARIO_STORE_LOCK:
            values = list(_SCENARIO_STORE.values())[-max(limit, 0):]
        values.reverse()

        hide_values = bool(settings and settings.hide_values)
        return [
            ScenarioService._summary_row(payload, hide_values=hide_values)
            for payload in values
        ]

    @staticmethod
    def _store(payload: dict[str, Any]) -> None:
        scenario_id = payload["scenario_id"]
        with _SCENARIO_STORE_LOCK:
            _SCENARIO_STORE[scenario_id] = copy.deepcopy(payload)
            _SCENARIO_STORE.move_to_end(scenario_id)
            while len(_SCENARIO_STORE) > _MAX_STORED_SCENARIOS:
                _SCENARIO_STORE.popitem(last=False)

    @staticmethod
    def _summary_row(
        payload: dict[str, Any],
        *,
        hide_values: bool,
    ) -> dict[str, Any]:
        totals = payload.get("totals") or _empty_totals()
        if hide_values:
            return {
                "scenario_id": payload["scenario_id"],
                "name": payload["name"],
                "as_of_date": payload["as_of_date"],
                "created_at_utc": payload["created_at_utc"],
                "price_shock_pct": payload.get("price_shock_pct", "0.00"),
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
            "hide_values": True,
            "hidden_reason": _HIDE_REASON,
            "legs": hidden_legs,
            "totals": None,
            "tax_year_context": payload.get("tax_year_context"),
            "notes": [_HIDE_REASON],
        }
