from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.services.portfolio_service import PortfolioService
from src.services.scenario_service import ScenarioService
from src.settings import AppSettings


def _add_security(ticker: str):
    return PortfolioService.add_security(
        ticker=ticker,
        name=f"{ticker} Scenario Co",
        currency="GBP",
        is_manual_override=True,
    )


def _add_lot(
    security_id: str,
    *,
    quantity: str,
    acquisition_price_gbp: str,
    acquisition_date: date = date(2025, 1, 15),
) -> None:
    PortfolioService.add_lot(
        security_id=security_id,
        scheme_type="BROKERAGE",
        acquisition_date=acquisition_date,
        quantity=Decimal(quantity),
        acquisition_price_gbp=Decimal(acquisition_price_gbp),
        true_cost_per_share_gbp=Decimal(acquisition_price_gbp),
    )


def _add_price(security_id: str, price_date: date, close_gbp: str) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=price_date,
            close_price_original_ccy=close_gbp,
            close_price_gbp=close_gbp,
            currency="GBP",
            source="test-scenario-service",
        )


def test_scenario_builder_context_empty_portfolio_has_explicit_note(app_context):
    payload = ScenarioService.get_builder_context(as_of=date(2026, 2, 25))

    assert payload["hide_values"] is False
    assert payload["securities"] == []
    assert any("No sellable lots" in note for note in payload["notes"])


def test_run_scenario_multi_leg_returns_aggregate_totals_and_retrievable_snapshot(app_context):
    sec_a = _add_security("SCNA")
    sec_b = _add_security("SCNB")
    _add_lot(sec_a.id, quantity="10", acquisition_price_gbp="10.00")
    _add_lot(sec_b.id, quantity="8", acquisition_price_gbp="5.00")
    _add_price(sec_a.id, date(2026, 2, 25), "20.00")
    _add_price(sec_b.id, date(2026, 2, 25), "8.00")

    payload = ScenarioService.run_scenario(
        name="Core scenario",
        as_of=date(2026, 2, 25),
        price_shock_pct=Decimal("0"),
        legs=[
            {
                "security_id": sec_a.id,
                "quantity": Decimal("3"),
            },
            {
                "security_id": sec_b.id,
                "quantity": Decimal("4"),
                "price_per_share_gbp": Decimal("8.00"),
            },
        ],
    )

    assert payload["hide_values"] is False
    assert payload["totals"]["legs_count"] == 2
    assert payload["totals"]["total_proceeds_gbp"] == "92.00"
    assert payload["totals"]["total_cost_basis_gbp"] == "50.00"
    assert payload["totals"]["total_realised_gain_economic_gbp"] == "42.00"
    assert payload["totals"]["total_employment_tax_gbp"] == "0.00"
    assert payload["totals"]["total_net_after_employment_tax_gbp"] == "92.00"
    assert payload["input_snapshot"]["execution_mode"] == "INDEPENDENT"
    assert len(payload["input_snapshot"]["legs"]) == 2
    assert payload["input_snapshot"]["legs"][0]["security_id"] == sec_a.id
    assert payload["legs"][0]["trace_links"]["reconcile_security_href"].startswith("/reconcile")
    assert payload["legs"][0]["trace_links"]["reconcile_audit_href"].endswith("#trace-audit-mutations")

    loaded = ScenarioService.get_scenario(payload["scenario_id"])
    assert loaded is not None
    assert loaded["scenario_id"] == payload["scenario_id"]
    assert len(loaded["legs"]) == 2
    assert loaded["input_snapshot"]["legs"][1]["security_id"] == sec_b.id


def test_run_scenario_rejects_quantity_above_available(app_context):
    sec = _add_security("SCNERR")
    _add_lot(sec.id, quantity="2", acquisition_price_gbp="10.00")
    _add_price(sec.id, date(2026, 2, 25), "12.00")

    try:
        ScenarioService.run_scenario(
            as_of=date(2026, 2, 25),
            legs=[
                {
                    "security_id": sec.id,
                    "quantity": Decimal("3"),
                }
            ],
        )
    except ValueError as exc:
        assert "exceeds available" in str(exc)
    else:
        raise AssertionError("Expected ValueError for quantity above available.")


def test_run_scenario_returns_hidden_payload_when_hide_values_enabled(app_context):
    sec = _add_security("SCNHIDE")
    _add_lot(sec.id, quantity="2", acquisition_price_gbp="10.00")
    _add_price(sec.id, date(2026, 2, 25), "12.00")

    settings = AppSettings()
    settings.hide_values = True

    payload = ScenarioService.run_scenario(
        as_of=date(2026, 2, 25),
        settings=settings,
        legs=[
            {
                "security_id": sec.id,
                "quantity": Decimal("1"),
            }
        ],
    )

    assert payload["hide_values"] is True
    assert payload["totals"] is None
    assert payload["hidden_reason"] == "Values hidden by privacy mode."
    assert payload["input_snapshot"]["legs"][0]["security_id"] == sec.id
