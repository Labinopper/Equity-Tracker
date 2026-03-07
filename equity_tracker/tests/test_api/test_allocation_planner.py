from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.api import _state
from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.settings import AppSettings


def _add_security(client, *, ticker: str, currency: str = "GBP") -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Allocation Plc",
            "currency": currency,
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot(
    client,
    *,
    security_id: str,
    quantity: str,
    acquisition_price_gbp: str,
) -> None:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2025-01-15",
            "quantity": quantity,
            "acquisition_price_gbp": acquisition_price_gbp,
            "true_cost_per_share_gbp": acquisition_price_gbp,
            "tax_year": "2024-25",
        },
    )
    assert resp.status_code == 201, resp.text


def _upsert_price(
    security_id: str,
    *,
    price_gbp: str,
    currency: str = "GBP",
    original_price: str | None = None,
) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=date.today(),
            close_price_original_ccy=original_price or price_gbp,
            close_price_gbp=price_gbp,
            currency=currency,
            source="test-allocation-planner",
        )


def _save_settings() -> None:
    db_path = _state.get_db_path()
    assert db_path is not None
    settings = AppSettings.load(db_path)
    settings.default_gross_income = Decimal("85000")
    settings.default_other_income = Decimal("0")
    settings.save()


def test_allocation_planner_persists_candidates_and_computes_before_after_deltas(client):
    _save_settings()

    source_security_id = _add_security(client, ticker="ALPHA")
    _add_lot(client, security_id=source_security_id, quantity="100", acquisition_price_gbp="10.00")
    _upsert_price(source_security_id, price_gbp="20.00")

    ballast_security_id = _add_security(client, ticker="BETA")
    _add_lot(client, security_id=ballast_security_id, quantity="10", acquisition_price_gbp="10.00")
    _upsert_price(ballast_security_id, price_gbp="10.00")

    settings_resp = client.post(
        "/allocation-planner/settings",
        data={
            "source_selection_mode": "TICKER",
            "source_ticker": "ALPHA",
            "target_max_pct": "25",
            "as_of": date.today().isoformat(),
        },
        follow_redirects=False,
    )
    assert settings_resp.status_code == 303

    for label, ticker, currency, wrapper, bucket, weight in (
        ("Global Core", "CORE", "GBP", "ISA", "GLOBAL_EQ", "2"),
        ("US Add", "USADD", "USD", "TAXABLE", "US_EQ", "1"),
    ):
        resp = client.post(
            "/allocation-planner/candidates",
            data={
                "label": label,
                "ticker": ticker,
                "currency": currency,
                "target_wrapper": wrapper,
                "bucket": bucket,
                "allocation_weight": weight,
                "notes": f"{label} bucket",
                "as_of": date.today().isoformat(),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    payload = client.get("/api/strategic/allocation-planner").json()

    assert payload["planner_config"]["source_selection_mode"] == "TICKER"
    assert payload["selected_source"]["ticker"] == "ALPHA"
    assert Decimal(payload["trim_plan"]["executable_quantity"]) > Decimal("0")
    assert Decimal(payload["trim_plan"]["net_redeployable_gbp"]) > Decimal("0")
    assert len(payload["candidate_rows"]) == 2
    assert len(payload["candidate_allocations"]) == 2
    assert Decimal(payload["before_after"]["source_pct_after"]) < Decimal(
        payload["before_after"]["source_pct_before"]
    )
    assert any(row["wrapper"] == "ISA" for row in payload["before_after"]["wrapper_rows"])
    assert any(row["currency"] == "USD" for row in payload["before_after"]["fx_rows"])
    assert payload["trim_plan"]["consumed_lot_rows"]

    page = client.get("/allocation-planner")
    assert page.status_code == 200
    text = page.text
    assert "Allocation Planner" in text
    assert "Candidate Universe" in text
    assert "Before vs After" in text
    assert "Trim Plan" in text

    candidate_id = payload["candidate_rows"][0]["candidate_id"]
    delete_resp = client.post(
        f"/allocation-planner/candidates/{candidate_id}/delete",
        data={"as_of": date.today().isoformat()},
        follow_redirects=False,
    )
    assert delete_resp.status_code == 303

    after_delete = client.get("/api/strategic/allocation-planner").json()
    assert len(after_delete["candidate_rows"]) == 1
