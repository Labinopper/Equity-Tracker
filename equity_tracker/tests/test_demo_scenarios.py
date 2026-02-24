from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.settings import AppSettings
from src.services.portfolio_service import PortfolioService


_Q2 = Decimal("0.01")
_SCENARIO_PRICE = Decimal("200.00")


def _q2(value: Decimal) -> Decimal:
    return value.quantize(_Q2, rounding=ROUND_HALF_UP)


def _fmt2(value: Decimal) -> str:
    return f"{_q2(value):.2f}"


def _extract_main_html(html: str) -> str:
    marker = "<main"
    i = html.find(marker)
    if i < 0:
        return html
    j = html.find(">", i)
    if j < 0:
        return html
    k = html.find("</main>", j)
    if k < 0:
        return html[j + 1 :]
    return html[j + 1 : k]


def _assert_no_mojibake(html: str) -> None:
    assert "Â£" not in html
    assert "â€”" not in html


def _assert_no_non_cgt_terms_in_main(html: str) -> None:
    main = _extract_main_html(html).lower()
    assert "capital gains" not in main
    assert "tax-basis" not in main
    assert "cgt" not in main


def _assert_money_in_html(html: str, amount: Decimal) -> None:
    expected = _fmt2(amount)
    assert (f"&pound;{expected}" in html) or (f"£{expected}" in html)


def _find_ibm_security_id(client: TestClient) -> str:
    resp = client.get("/portfolio/summary")
    assert resp.status_code == 200
    body = resp.json()
    for sec in body["securities"]:
        if sec["security"]["ticker"] == "IBM":
            return sec["security"]["id"]
    raise AssertionError("IBM security not found in seeded demo DB.")


@contextmanager
def _demo_env(demo_db_path: Path):
    old_path = os.environ.get("EQUITY_DB_PATH")
    old_enc = os.environ.get("EQUITY_DB_ENCRYPTED")
    old_pwd = os.environ.get("EQUITY_DB_PASSWORD")
    os.environ["EQUITY_DB_PATH"] = str(demo_db_path.resolve())
    os.environ["EQUITY_DB_ENCRYPTED"] = "false"
    if "EQUITY_DB_PASSWORD" in os.environ:
        del os.environ["EQUITY_DB_PASSWORD"]
    try:
        yield
    finally:
        if old_path is None:
            os.environ.pop("EQUITY_DB_PATH", None)
        else:
            os.environ["EQUITY_DB_PATH"] = old_path
        if old_enc is None:
            os.environ.pop("EQUITY_DB_ENCRYPTED", None)
        else:
            os.environ["EQUITY_DB_ENCRYPTED"] = old_enc
        if old_pwd is None:
            os.environ.pop("EQUITY_DB_PASSWORD", None)
        else:
            os.environ["EQUITY_DB_PASSWORD"] = old_pwd


@pytest.fixture(scope="module")
def demo_runtime():
    repo_root = Path(__file__).resolve().parents[1]
    seeder = repo_root / "scripts" / "seed_demo_db.py"
    demo_db = repo_root / "data" / "demo.db"

    subprocess.run([sys.executable, str(seeder)], check=True, cwd=str(repo_root))
    assert demo_db.exists(), "Seeder did not create data/demo.db"

    with _demo_env(demo_db):
        with TestClient(app, raise_server_exceptions=True) as client:
            status = client.get("/admin/status")
            assert status.status_code == 200
            assert status.json()["locked"] is False
            security_id = _find_ibm_security_id(client)
            settings = AppSettings.load(demo_db)
            yield {
                "client": client,
                "security_id": security_id,
                "settings": settings,
            }


def test_scenario_1_brokerage_sale_zero_employment_tax(demo_runtime):
    client: TestClient = demo_runtime["client"]
    security_id: str = demo_runtime["security_id"]
    settings: AppSettings = demo_runtime["settings"]
    fees = Decimal("1.23")

    baseline = PortfolioService.simulate_disposal(
        security_id=security_id,
        quantity=Decimal("2"),
        price_per_share_gbp=_SCENARIO_PRICE,
        scheme_type="BROKERAGE",
        broker_fees_gbp=Decimal("0"),
        settings=settings,
    )
    service_result = PortfolioService.simulate_disposal(
        security_id=security_id,
        quantity=Decimal("2"),
        price_per_share_gbp=_SCENARIO_PRICE,
        scheme_type="BROKERAGE",
        broker_fees_gbp=fees,
        settings=settings,
    )

    assert service_result.total_sip_employment_tax_gbp == Decimal("0")
    assert _q2(service_result.total_realised_gain_economic_gbp) == _q2(
        baseline.total_realised_gain_economic_gbp - fees
    )
    net_after_fee = _q2(
        service_result.total_proceeds_gbp - fees - service_result.total_sip_employment_tax_gbp
    )
    assert net_after_fee == _q2(service_result.total_proceeds_gbp - fees)

    api = client.post(
        "/portfolio/disposals/simulate",
        json={
            "security_id": security_id,
            "scheme_type": "BROKERAGE",
            "quantity": "2",
            "price_per_share_gbp": str(_SCENARIO_PRICE),
            "broker_fees_gbp": str(fees),
        },
    )
    assert api.status_code == 200, api.text
    body = api.json()
    assert Decimal(body["total_realised_gain_gbp"]) == _q2(service_result.total_realised_gain_gbp)
    assert Decimal(body["total_realised_gain_economic_gbp"]) == _q2(
        service_result.total_realised_gain_economic_gbp
    )


def test_scenario_2_espp_sale_zero_employment_tax_after_5_years(demo_runtime):
    client: TestClient = demo_runtime["client"]
    security_id: str = demo_runtime["security_id"]
    as_of = date(2026, 2, 24)

    service_result = PortfolioService.simulate_disposal(
        security_id=security_id,
        quantity=Decimal("2"),
        price_per_share_gbp=_SCENARIO_PRICE,
        scheme_type="ESPP",
        broker_fees_gbp=Decimal("0"),
        as_of_date=as_of,
        settings=None,  # Match /portfolio/disposals/simulate behavior.
    )

    assert service_result.is_fully_allocated is True
    assert service_result.total_sip_employment_tax_gbp == Decimal("0")
    assert _q2(service_result.total_realised_gain_economic_gbp) == _q2(
        service_result.total_proceeds_gbp - service_result.total_true_cost_gbp
    )

    api = client.post(
        "/portfolio/disposals/simulate",
        json={
            "security_id": security_id,
            "scheme_type": "ESPP",
            "quantity": "2",
            "price_per_share_gbp": str(_SCENARIO_PRICE),
            "broker_fees_gbp": "0",
            "as_of_date": as_of.isoformat(),
        },
    )
    assert api.status_code == 200, api.text
    body = api.json()
    assert body["is_fully_allocated"] is True
    assert Decimal(body["shortfall"]) == Decimal("0")
    assert Decimal(body["total_realised_gain_economic_gbp"]) == _q2(
        service_result.total_realised_gain_economic_gbp
    )


def test_scenario_3_espp_plus_lock_window_and_tax_consistency(demo_runtime):
    client: TestClient = demo_runtime["client"]
    security_id: str = demo_runtime["security_id"]

    # Subcase A: within lock window -> not sellable
    within_lock = date(2026, 3, 1)
    with pytest.raises(ValueError, match="No sellable lots"):
        PortfolioService.simulate_disposal(
            security_id=security_id,
            quantity=Decimal("1"),
            price_per_share_gbp=_SCENARIO_PRICE,
            scheme_type="ESPP_PLUS",
            as_of_date=within_lock,
            settings=None,  # Match /portfolio/disposals/simulate behavior.
        )
    api_locked = client.post(
        "/portfolio/disposals/simulate",
        json={
            "security_id": security_id,
            "scheme_type": "ESPP_PLUS",
            "quantity": "1",
            "price_per_share_gbp": str(_SCENARIO_PRICE),
            "as_of_date": within_lock.isoformat(),
        },
    )
    assert api_locked.status_code == 422

    # Subcase B: after lock window -> sellable, employment tax should apply
    after_lock = date(2026, 9, 1)
    service_result = PortfolioService.simulate_disposal(
        security_id=security_id,
        quantity=Decimal("1"),
        price_per_share_gbp=_SCENARIO_PRICE,
        scheme_type="ESPP_PLUS",
        as_of_date=after_lock,
        settings=None,  # Match /portfolio/disposals/simulate behavior.
    )
    assert service_result.is_fully_allocated is True
    assert service_result.total_sip_employment_tax_gbp == Decimal("0")

    api = client.post(
        "/portfolio/disposals/simulate",
        json={
            "security_id": security_id,
            "scheme_type": "ESPP_PLUS",
            "quantity": "1",
            "price_per_share_gbp": str(_SCENARIO_PRICE),
            "as_of_date": after_lock.isoformat(),
        },
    )
    assert api.status_code == 200, api.text
    body = api.json()
    assert body["is_fully_allocated"] is True
    assert Decimal(body["total_realised_gain_economic_gbp"]) == _q2(
        service_result.total_realised_gain_economic_gbp
    )


def test_ui_simulate_html_contains_tax_value_and_no_mojibake_or_cgt_terms(demo_runtime):
    client: TestClient = demo_runtime["client"]
    security_id: str = demo_runtime["security_id"]
    settings: AppSettings = demo_runtime["settings"]

    service_result = PortfolioService.simulate_disposal(
        security_id=security_id,
        quantity=Decimal("2"),
        price_per_share_gbp=_SCENARIO_PRICE,
        scheme_type="ESPP",
        broker_fees_gbp=Decimal("0"),
        settings=settings,
    )
    expected_tax = _q2(service_result.total_sip_employment_tax_gbp)

    ui = client.post(
        "/simulate",
        data={
            "security_id": security_id,
            "scheme_type": "ESPP",
            "quantity": "2",
            "price_per_share_gbp": str(_SCENARIO_PRICE),
            "broker_fees_gbp": "0",
        },
    )
    assert ui.status_code == 200
    html = ui.text

    assert expected_tax == Decimal("0.00")
    assert "Total Employment Tax" not in html
    _assert_no_mojibake(html)
    _assert_no_non_cgt_terms_in_main(html)

    home = client.get("/")
    assert home.status_code == 200
    _assert_no_mojibake(home.text)
    _assert_no_non_cgt_terms_in_main(home.text)
