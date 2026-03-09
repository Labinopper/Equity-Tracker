from __future__ import annotations

from datetime import date

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.services.pension_service import ENTRY_TYPE_GROWTH


def _add_security(client, *, ticker: str, currency: str = "GBP") -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Pension Context Plc",
            "currency": currency,
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot(client, *, security_id: str, quantity: str = "10", price: str = "100.00") -> None:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2025-01-15",
            "quantity": quantity,
            "acquisition_price_gbp": price,
            "true_cost_per_share_gbp": price,
            "tax_year": "2024-25",
        },
    )
    assert resp.status_code == 201, resp.text


def _upsert_price(security_id: str, *, price_gbp: str = "150.00") -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=date.today(),
            close_price_original_ccy=price_gbp,
            close_price_gbp=price_gbp,
            currency="GBP",
            source="test-pension-page",
        )


def test_pension_page_persists_assumptions_and_contributions(client):
    security_id = _add_security(client, ticker="PENT83")
    _add_lot(client, security_id=security_id)
    _upsert_price(security_id)

    assumptions_resp = client.post(
        "/pension/assumptions",
        data={
            "current_pension_value_gbp": "120000",
            "monthly_employee_contribution_gbp": "500",
            "monthly_employer_contribution_gbp": "250",
            "retirement_date": "2045-03-31",
            "target_annual_income_gbp": "50000",
            "target_withdrawal_rate_pct": "4",
            "conservative_annual_return_pct": "3",
            "base_annual_return_pct": "5",
            "aggressive_annual_return_pct": "7",
        },
        follow_redirects=False,
    )
    assert assumptions_resp.status_code == 303
    assert assumptions_resp.headers["location"] == "/pension?msg=Pension+assumptions+saved."

    for entry_type, amount, source in (
        ("EMPLOYEE", "1000", "payroll"),
        ("EMPLOYER", "500", "employer-match"),
        ("ADJUSTMENT", "250", "opening-balance"),
    ):
        resp = client.post(
            "/pension/contributions",
            data={
                "entry_date": date.today().isoformat(),
                "entry_type": entry_type,
                "amount_gbp": amount,
                "source": source,
                "notes": f"{entry_type.lower()} contribution",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/pension?msg=Pension+contribution+saved."

    payload = client.get("/api/strategic/pension").json()
    assert payload["current_pension_value_gbp"] == "120000.00"
    assert payload["employee_contributions_gbp"] == "1000.00"
    assert payload["employer_contributions_gbp"] == "500.00"
    assert payload["adjustments_gbp"] == "250.00"
    assert payload["recorded_inputs_gbp"] == "1750.00"
    assert payload["growth_attribution_gbp"] == "118250.00"
    assert payload["target_pot_gbp"] == "1250000.00"
    assert payload["portfolio_gross_market_value_gbp"] == "1500.00"
    assert payload["total_tracked_wealth_gbp"] == "121500.00"
    assert payload["timeline_chart"]["points"]
    assert len(payload["ledger_rows"]) == 3
    assert len(payload["scenario_rows"]) == 4

    row_5y = next(row for row in payload["scenario_rows"] if row["label"] == "5y")
    assert row_5y["future_employee_contributions_gbp"] == "30000.00"
    assert row_5y["future_employer_contributions_gbp"] == "15000.00"
    assert row_5y["base_projected_pot_gbp"] != payload["current_pension_value_gbp"]

    page = client.get("/pension")
    assert page.status_code == 200
    text = page.text
    assert "Pension" in text
    assert "Tracked Wealth Context" in text
    assert "Pension Assumptions" in text
    assert "Pension Timeline" in text
    assert "Validate Current Value" in text
    assert "Contribution Ledger" in text
    assert 'id="chart-pension-timeline"' in text
    assert 'id="pension-assumptions"' in text
    assert 'id="pension-validation"' in text
    assert 'id="pension-ledger"' in text
    assert "payroll" in text
    assert "employer-match" in text
    assert "opening-balance" in text


def test_pension_page_validation_rejects_invalid_inputs(client):
    invalid_assumptions = client.post(
        "/pension/assumptions",
        data={
            "current_pension_value_gbp": "1000",
            "monthly_employee_contribution_gbp": "100",
            "monthly_employer_contribution_gbp": "100",
            "retirement_date": "2040-01-01",
            "target_annual_income_gbp": "40000",
            "target_withdrawal_rate_pct": "4",
            "conservative_annual_return_pct": "6",
            "base_annual_return_pct": "5",
            "aggressive_annual_return_pct": "7",
        },
        follow_redirects=False,
    )
    assert invalid_assumptions.status_code == 422
    assert "Pension assumptions not saved" in invalid_assumptions.text

    invalid_contribution = client.post(
        "/pension/contributions",
        data={
            "entry_date": date.today().isoformat(),
            "entry_type": "EMPLOYEE",
            "amount_gbp": "-10",
            "source": "manual",
            "notes": "bad row",
        },
        follow_redirects=False,
    )
    assert invalid_contribution.status_code == 422
    assert "Pension contribution not saved" in invalid_contribution.text


def test_pension_value_validation_records_growth_separately(client):
    baseline_resp = client.post(
        "/pension/valuation",
        data={
            "valuation_date": "2026-02-06",
            "current_value_gbp": "100000",
            "source": "portal",
            "notes": "baseline",
        },
        follow_redirects=False,
    )
    assert baseline_resp.status_code == 303
    assert baseline_resp.headers["location"] == "/pension?msg=Pension+value+validated."

    contribution_resp = client.post(
        "/pension/contributions",
        data={
            "entry_date": "2026-03-06",
            "entry_type": "EMPLOYEE",
            "amount_gbp": "1000",
            "source": "payroll",
            "notes": "march contribution",
        },
        follow_redirects=False,
    )
    assert contribution_resp.status_code == 303

    second_resp = client.post(
        "/pension/valuation",
        data={
            "valuation_date": "2026-03-06",
            "current_value_gbp": "101750",
            "source": "portal",
            "notes": "march value check",
        },
        follow_redirects=False,
    )
    assert second_resp.status_code == 303
    assert (
        second_resp.headers["location"]
        == "/pension?msg=Pension+value+validated.+Growth+recorded%3A+GBP+750.00."
    )

    payload = client.get("/api/strategic/pension").json()
    assert payload["current_pension_value_gbp"] == "101750.00"
    assert payload["last_valuation_date"] == "2026-03-06"
    assert payload["employee_contributions_gbp"] == "1000.00"
    assert payload["recorded_growth_gbp"] == "750.00"
    assert payload["growth_attribution_gbp"] == "100750.00"
    assert payload["ledger_rows"][0]["entry_type"] == ENTRY_TYPE_GROWTH
    assert payload["ledger_rows"][0]["amount_gbp"] == "750.00"
