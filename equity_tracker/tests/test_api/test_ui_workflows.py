from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from src.api.routers.ui import (
    _build_per_scheme_reports,
    _build_portfolio_position_rows,
    _compute_exit_summary,
    _portfolio_blocked_restricted_value,
    _portfolio_est_net_liquidity,
)
from src.api import _state
from src.app_context import AppContext
from src.core.tax_engine import TaxContext, get_marginal_rates, tax_year_for_date
from src.db.repository import SecurityCatalogRepository
from src.db.repository.lots import LotRepository
from src.db.repository.prices import PriceRepository
from src.services.portfolio_service import PortfolioService
from src.services.sheets_fx_service import FxRow
from src.settings import AppSettings


def _add_security(client, ticker: str = "UISEC", currency: str = "GBP") -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Inc",
            "currency": currency,
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_security_with_exchange(
    client,
    *,
    ticker: str,
    exchange: str,
    currency: str = "USD",
) -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Inc",
            "currency": currency,
            "exchange": exchange,
            "is_manual_override": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_lot_via_api(client, security_id: str, quantity: str = "5", price: str = "10.00") -> None:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2025-01-15",
            "quantity": quantity,
            "acquisition_price_gbp": price,
            "true_cost_per_share_gbp": price,
        },
    )
    assert resp.status_code == 201, resp.text


def _post_add_lot_ui(client, data: dict[str, str]):
    return client.post("/portfolio/add-lot", data=data, follow_redirects=False)


def test_exit_summary_cash_vs_economic_formula():
    # Invariant: Gain = Net Cash Received – True Economic Cost.
    # Forfeiture is handled via quantity (excluded from proceeds), not as
    # a separate deduction.
    summary = _compute_exit_summary(
        proceeds_cash_gbp=Decimal("840"),
        true_cost_gbp=Decimal("487.20"),
        employment_tax_due_gbp=Decimal("352.80"),
        broker_fees_gbp=Decimal("0"),
    )
    assert summary["net_cash_received_gbp"] == Decimal("487.20")
    assert summary["net_economic_result_gbp"] == Decimal("0.00")


def test_add_lot_form_defaults_acquisition_date_today(client):
    _add_security(client, ticker="UIDATE")
    resp = client.get("/portfolio/add-lot")
    assert resp.status_code == 200
    assert f'value="{date.today().isoformat()}"' in resp.text


def test_add_lot_form_shows_only_supported_scheme_types(client):
    _add_security(client, ticker="UISCHEME")
    resp = client.get("/portfolio/add-lot")
    assert resp.status_code == 200
    assert "RSU" in resp.text
    assert "ESPP" in resp.text
    assert "ESPP+" in resp.text
    assert "BROKERAGE" in resp.text
    assert "ISA" in resp.text
    assert "SIP_PARTNERSHIP" not in resp.text
    assert "SIP_MATCHING" not in resp.text
    assert "SIP_DIVIDEND" not in resp.text


def test_add_lot_template_has_per_scheme_field_mappings(client):
    _add_security(client, ticker="UIMAP")
    resp = client.get("/portfolio/add-lot")
    assert resp.status_code == 200
    html = resp.text
    assert 'data-schemes="RSU"' in html
    assert 'data-required-schemes="RSU"' in html
    assert 'name="rsu_vesting_date"' in html
    assert 'name="rsu_fmv_at_vest_gbp"' in html
    assert 'id="rsu_fmv_at_vest_gbp"' in html
    assert 'type="hidden"' in html
    assert "rsuLivePrices" in html
    assert "rsuTaxRate" in html
    assert 'data-schemes="ESPP,ESPP_PLUS,BROKERAGE,ISA"' in html
    assert 'data-required-schemes="ESPP,ESPP_PLUS,BROKERAGE,ISA"' in html
    assert 'name="price_input_currency"' in html
    assert 'value="GBP"' in html
    assert 'value="USD"' in html
    assert "Currency Workflow" in html
    assert "currency-workflow-rate" in html
    # FMV-at-purchase row is shown for ESPP only; ESPP_PLUS no longer uses FMV.
    assert 'data-schemes="ESPP"' in html
    assert 'data-schemes="ESPP,ESPP_PLUS"' not in html
    assert 'data-schemes="ESPP_PLUS"' in html
    assert 'name="espp_plus_net_price_per_share_gbp"' in html
    assert 'name="espp_plus_net_price_overridden"' in html
    assert 'name="matched_shares_overridden"' in html
    assert "computeDefaultMatchedShares" in html
    assert "qty / 7" in html
    assert "function applySchemeVisibility()" in html


def test_add_lot_rsu_requires_latest_price_estimate(client):
    sec_id = _add_security(client, ticker="UIRSUERR")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "RSU",
            "acquisition_date": "2025-03-01",
            "rsu_vesting_date": "2025-03-01",
            "quantity": "10",
            "rsu_fmv_at_vest_gbp": "999.99",
        },
    )
    assert resp.status_code == 422
    assert "pending latest price" in resp.text


def test_add_lot_espp_requires_positive_purchase_price(client):
    sec_id = _add_security(client, ticker="UIESPPERR")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-03-01",
            "quantity": "10",
            "purchase_price_per_share_gbp": "0",
        },
    )
    assert resp.status_code == 422
    assert b"Purchase price per share must be greater than zero." in resp.content


def test_add_lot_espp_plus_requires_positive_purchase_price(client):
    sec_id = _add_security(client, ticker="UIEPLERR")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP_PLUS",
            "acquisition_date": "2025-03-01",
            "quantity": "10",
            "purchase_price_per_share_gbp": "0",
            "matched_shares_quantity": "5",
        },
    )
    assert resp.status_code == 422
    assert b"Purchase price per share must be greater than zero." in resp.content


def test_add_lot_brokerage_requires_positive_purchase_price(client):
    sec_id = _add_security(client, ticker="UIBROKERERR")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2025-03-01",
            "quantity": "10",
            "purchase_price_per_share_gbp": "0",
        },
    )
    assert resp.status_code == 422
    assert b"Purchase price per share must be greater than zero." in resp.content


def test_add_lot_isa_requires_positive_purchase_price(client):
    sec_id = _add_security(client, ticker="UIISAERR")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ISA",
            "acquisition_date": "2025-03-01",
            "quantity": "10",
            "purchase_price_per_share_gbp": "0",
        },
    )
    assert resp.status_code == 422
    assert b"Purchase price per share must be greater than zero." in resp.content


def test_add_lot_isa_success(client):
    sec_id = _add_security(client, ticker="UIISAOK")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ISA",
            "acquisition_date": "2025-03-01",
            "quantity": "10",
            "purchase_price_per_share_gbp": "8.50",
        },
    )
    assert resp.status_code == 303

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(sec_id)
    assert len(lots) == 1
    lot = lots[0]
    assert lot.scheme_type == "ISA"
    assert Decimal(lot.acquisition_price_gbp) == Decimal("8.50")
    assert Decimal(lot.true_cost_per_share_gbp) == Decimal("8.50")
    assert lot.broker_currency == "GBP"


def test_add_lot_brokerage_usd_converts_to_gbp_and_persists_fx_metadata(client, monkeypatch):
    sec_id = _add_security(client, ticker="UIUSDOK", currency="USD")

    def _fx() -> dict[str, FxRow]:
        return {
            "USD2GBP": FxRow(
                pair="USD2GBP",
                rate=Decimal("0.8000"),
                as_of="2026-02-24 12:00:00",
            )
        }

    monkeypatch.setattr(
        "src.api.routers.ui.FxService.read_rates",
        staticmethod(_fx),
    )

    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2025-03-01",
            "quantity": "2.5",
            "price_input_currency": "USD",
            "purchase_price_per_share_gbp": "100.00",
        },
    )
    assert resp.status_code == 303

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(sec_id)
    assert len(lots) == 1
    lot = lots[0]
    assert Decimal(lot.acquisition_price_gbp) == Decimal("80.0000")
    assert Decimal(lot.true_cost_per_share_gbp) == Decimal("80.0000")
    assert Decimal(lot.acquisition_price_original_ccy) == Decimal("100.00")
    assert lot.original_currency == "USD"
    assert lot.broker_currency == "USD"
    assert Decimal(lot.fx_rate_at_acquisition) == Decimal("0.8000")
    assert lot.fx_rate_source == "google_sheets_fx_tab"


def test_add_lot_usd_rejects_when_usd2gbp_rate_missing(client, monkeypatch):
    sec_id = _add_security(client, ticker="UIUSDMISS", currency="USD")

    monkeypatch.setattr(
        "src.api.routers.ui.FxService.read_rates",
        staticmethod(lambda: {}),
    )

    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2025-03-01",
            "quantity": "1",
            "price_input_currency": "USD",
            "purchase_price_per_share_gbp": "100.00",
        },
    )
    assert resp.status_code == 422
    assert b"USD-&gt;GBP conversion rate (USD2GBP) was not found" in resp.content


def test_add_lot_brokerage_eur_converts_to_gbp_and_persists_fx_metadata(client, monkeypatch):
    sec_id = _add_security(client, ticker="UIEUROK", currency="EUR")

    def _fx() -> dict[str, FxRow]:
        return {
            "EUR2GBP": FxRow(
                pair="EUR2GBP",
                rate=Decimal("0.8500"),
                as_of="2026-02-24 12:30:00",
            )
        }

    monkeypatch.setattr(
        "src.api.routers.ui.FxService.read_rates",
        staticmethod(_fx),
    )

    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2025-03-01",
            "quantity": "2.5",
            "price_input_currency": "EUR",
            "purchase_price_per_share_gbp": "100.00",
        },
    )
    assert resp.status_code == 303

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(sec_id)
    assert len(lots) == 1
    lot = lots[0]
    assert Decimal(lot.acquisition_price_gbp) == Decimal("85.0000")
    assert Decimal(lot.true_cost_per_share_gbp) == Decimal("85.0000")
    assert Decimal(lot.acquisition_price_original_ccy) == Decimal("100.00")
    assert lot.original_currency == "EUR"
    assert lot.broker_currency == "EUR"
    assert Decimal(lot.fx_rate_at_acquisition) == Decimal("0.8500")
    assert lot.fx_rate_source == "google_sheets_fx_tab"


def test_add_lot_rsu_success(client, db_engine):
    _, db_path = db_engine
    settings = AppSettings.defaults_for(db_path)
    settings.default_gross_income = Decimal("60000")
    settings.default_pension_sacrifice = Decimal("0")
    settings.default_other_income = Decimal("0")
    settings.default_student_loan_plan = 2
    settings.save()

    sec_id = _add_security(client, ticker="UIRSUOK")
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="12.34",
            close_price_gbp="12.34",
            currency="GBP",
            source="test-ui",
        )

    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "RSU",
            "acquisition_date": "2025-03-01",
            "rsu_vesting_date": "2025-03-01",
            "quantity": "10",
            # Server must ignore client FMV and use latest tracked price.
            "rsu_fmv_at_vest_gbp": "999.99",
        },
    )
    assert resp.status_code == 303

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(sec_id)
    assert len(lots) == 1
    lot = lots[0]
    assert lot.scheme_type == "RSU"
    assert Decimal(lot.acquisition_price_gbp) == Decimal("12.34")
    rates = get_marginal_rates(
        TaxContext(
            tax_year=tax_year_for_date(date(2025, 3, 1)),
            gross_employment_income=Decimal("60000"),
            pension_sacrifice=Decimal("0"),
            other_income=Decimal("0"),
            student_loan_plan=2,
        )
    )
    expected = (Decimal("12.34") * (rates.income_tax + rates.national_insurance)).quantize(Decimal("0.0001"))
    assert Decimal(lot.true_cost_per_share_gbp) == expected
    assert lot.tax_year == tax_year_for_date(date(2025, 3, 1))


def test_add_lot_rsu_success_without_acquisition_date_field(client, db_engine):
    _, db_path = db_engine
    settings = AppSettings.defaults_for(db_path)
    settings.default_gross_income = Decimal("60000")
    settings.default_pension_sacrifice = Decimal("0")
    settings.default_other_income = Decimal("0")
    settings.default_student_loan_plan = 2
    settings.save()

    sec_id = _add_security(client, ticker="UIRSUNOACQ")
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="12.34",
            close_price_gbp="12.34",
            currency="GBP",
            source="test-ui",
        )

    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "RSU",
            "rsu_vesting_date": "2025-03-01",
            "quantity": "10",
            "rsu_fmv_at_vest_gbp": "12.34",
        },
    )
    assert resp.status_code == 303, resp.text

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(sec_id)
    assert len(lots) == 1
    lot = lots[0]
    assert lot.scheme_type == "RSU"
    assert lot.acquisition_date == date(2025, 3, 1)


def test_add_lot_espp_success(client):
    sec_id = _add_security(client, ticker="UIESPPOK")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-03-01",
            "quantity": "10",
            "purchase_price_per_share_gbp": "8.50",
            "espp_fmv_at_purchase_gbp": "10.00",
        },
    )
    assert resp.status_code == 303

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(sec_id)
    assert len(lots) == 1
    lot = lots[0]
    assert lot.scheme_type == "ESPP"
    assert Decimal(lot.acquisition_price_gbp) == Decimal("8.50")
    assert Decimal(lot.quantity) == Decimal("10")


def test_add_lot_espp_plus_matched_optional(client):
    sec_id = _add_security(client, ticker="UIEPLOPT")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP_PLUS",
            "acquisition_date": "2025-02-01",
            "quantity": "10",
            "purchase_price_per_share_gbp": "8.50",
            "matched_shares_quantity": "",
        },
    )
    assert resp.status_code == 303

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(sec_id)
    assert len(lots) == 1
    assert lots[0].scheme_type == "ESPP_PLUS"
    assert lots[0].matching_lot_id is None


def test_add_lot_espp_plus_creates_internal_matched_locked_lot(client):
    sec_id = _add_security(client, ticker="UIESPPPLUS")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP_PLUS",
            "acquisition_date": "2025-02-01",
            "quantity": "10",
            "purchase_price_per_share_gbp": "8.50",
            "espp_fmv_at_purchase_gbp": "10.00",
            "matched_shares_quantity": "5",
            "notes": "ui test",
        },
    )
    assert resp.status_code == 303

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(sec_id)

    assert len(lots) == 2
    employee = next(l for l in lots if l.scheme_type == "ESPP_PLUS" and l.matching_lot_id is None)
    matched = next(l for l in lots if l.scheme_type == "ESPP_PLUS" and l.matching_lot_id is not None)

    assert Decimal(employee.quantity) == Decimal("10")
    assert Decimal(employee.acquisition_price_gbp) == Decimal("8.50")
    assert Decimal(employee.fmv_at_acquisition_gbp) == Decimal("10.00")
    assert employee.tax_year == tax_year_for_date(date(2025, 2, 1))

    assert Decimal(matched.quantity) == Decimal("5")
    assert Decimal(matched.acquisition_price_gbp) == Decimal("0")
    assert Decimal(matched.true_cost_per_share_gbp) == Decimal("0")
    assert Decimal(matched.fmv_at_acquisition_gbp) == Decimal("10.00")
    assert matched.matching_lot_id == employee.id
    assert matched.forfeiture_period_end == date(2025, 2, 1) + timedelta(days=183)


def test_add_lot_espp_plus_defaults_award_fmv_to_purchase_price_for_both_legs(client):
    sec_id = _add_security(client, ticker="UIESPPFMVFB")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP_PLUS",
            "acquisition_date": "2025-03-01",
            "quantity": "7",
            "purchase_price_per_share_gbp": "9.25",
            "matched_shares_quantity": "1",
        },
    )
    assert resp.status_code == 303

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(sec_id)

    assert len(lots) == 2
    employee = next(l for l in lots if l.scheme_type == "ESPP_PLUS" and l.matching_lot_id is None)
    matched = next(l for l in lots if l.scheme_type == "ESPP_PLUS" and l.matching_lot_id is not None)
    assert Decimal(employee.fmv_at_acquisition_gbp) == Decimal("9.25")
    assert Decimal(matched.fmv_at_acquisition_gbp) == Decimal("9.25")


def test_add_lot_espp_plus_net_price_override_persists_employee_true_cost(client):
    sec_id = _add_security(client, ticker="UIEPLNETOVR")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP_PLUS",
            "acquisition_date": "2026-02-24",
            "quantity": "10",
            "purchase_price_per_share_gbp": "100.00",
            "espp_plus_net_price_per_share_gbp": "55.4321",
            "espp_plus_net_price_overridden": "true",
            "matched_shares_quantity": "0",
        },
    )
    assert resp.status_code == 303

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(sec_id)
    assert len(lots) == 1
    employee = lots[0]
    assert employee.scheme_type == "ESPP_PLUS"
    assert employee.matching_lot_id is None
    assert Decimal(employee.true_cost_per_share_gbp) == Decimal("55.4321")
    assert employee.import_source == "ui_espp_plus_employee_override"


def test_portfolio_shows_espp_plus_and_matched_labels(client):
    sec_id = _add_security(client, ticker="UIEPLLBL")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP_PLUS",
            "acquisition_date": "2026-02-24",
            "quantity": "10",
            "purchase_price_per_share_gbp": "50.00",
            "matched_shares_quantity": "2",
        },
    )
    assert resp.status_code == 303

    home = client.get("/")
    assert home.status_code == 200
    assert "ESPP+" in home.text
    assert "ESPP+ Matched" not in home.text


def test_portfolio_shows_est_net_proceeds_reason_when_price_missing(client):
    sec_id = _add_security(client, ticker="UINOPRICE")
    _add_lot_via_api(client, sec_id, quantity="5", price="10.00")

    home = client.get("/")
    assert home.status_code == 200
    assert "Est. Net Proceeds unavailable: no live price available." in home.text


def test_portfolio_page_renders_qol_view_controls(client):
    sec_id = _add_security(client, ticker="UIQOLCTRL")
    _add_lot_via_api(client, sec_id, quantity="5", price="10.00")

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date.today(),
            close_price_original_ccy="12.00",
            close_price_gbp="12.00",
            currency="GBP",
            source="test-ui",
        )

    home = client.get("/")
    assert home.status_code == 200
    assert "Portfolio View Controls" in home.text
    assert "Quick Filters" in home.text
    assert "Sort Decision Rows" in home.text
    assert "Focus Mode (compact decision-first view)" in home.text
    assert "portfolio.view_prefs.v1" in home.text
    assert "Formula" in home.text


def test_portfolio_shows_locked_est_net_reason_for_pre_vest_rsu(client):
    sec_id = _add_security(client, ticker="UILOCKEDNET")
    vest_date = date.today() + timedelta(days=30)
    add = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "RSU",
            "acquisition_date": vest_date.isoformat(),
            "quantity": "5",
            "acquisition_price_gbp": "100.00",
            "true_cost_per_share_gbp": "40.00",
        },
    )
    assert add.status_code == 201, add.text

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="120.00",
            close_price_gbp="120.00",
            currency="GBP",
            source="test-ui",
        )

    home = client.get("/")
    assert home.status_code == 200
    assert f"Locked until {vest_date.isoformat()}" in home.text


def test_portfolio_refresh_diagnostics_rendered(client):
    home = client.get("/")
    assert home.status_code == 200
    assert "Last success:" in home.text
    assert "Last error:" in home.text
    assert "Next refresh:" in home.text
    assert "refresh-state" in home.text


def test_prices_refresh_updates_success_diagnostics(client, monkeypatch):
    def _ok():
        return {"fetched": 2, "failed": 0, "errors": []}

    monkeypatch.setattr("src.api.routers.prices.PriceService.fetch_all", staticmethod(_ok))
    resp = client.post("/prices/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["fetched"] == 2
    assert body["failed"] == 0

    diag = _state.get_refresh_diagnostics()
    assert diag["last_success_at"] is not None
    assert diag["last_error"] is None
    assert diag["next_due_at"] is not None


def test_prices_refresh_updates_error_diagnostics(client, monkeypatch):
    def _partial():
        return {
            "fetched": 1,
            "failed": 1,
            "errors": [{"security_id": "x", "ticker": "X", "error": "sheet stale"}],
        }

    monkeypatch.setattr("src.api.routers.prices.PriceService.fetch_all", staticmethod(_partial))
    resp = client.post("/prices/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["failed"] == 1

    diag = _state.get_refresh_diagnostics()
    assert diag["last_success_at"] is not None
    assert diag["last_error"] is not None
    assert "1 failed" in diag["last_error"]


def test_portfolio_refresh_script_has_retry_safe_guard(client):
    home = client.get("/")
    assert home.status_code == 200
    html = home.text
    assert "var inFlight = false;" in html
    assert "function resetCountdown()" in html
    assert "function doRefresh()" in html


def test_portfolio_badges_rsu_pre_vest_lock_state(client):
    sec_id = _add_security(client, ticker="UIRSUBADGE")
    vest_date = date.today() + timedelta(days=30)
    add = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "RSU",
            "acquisition_date": vest_date.isoformat(),
            "quantity": "5",
            "acquisition_price_gbp": "100.00",
            "true_cost_per_share_gbp": "40.00",
        },
    )
    assert add.status_code == 201, add.text

    home = client.get("/")
    assert home.status_code == 200
    assert "Locked until" in home.text


def test_portfolio_badges_espp_does_not_show_tax_impact_window(client):
    sec_id = _add_security(client, ticker="UIESPPTAXBADGE")
    add = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": date.today().isoformat(),
            "quantity": "5",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert add.status_code == 201, add.text

    home = client.get("/")
    assert home.status_code == 200
    assert "Tax Window" not in home.text
    assert "SIP Qualifying" not in home.text


def test_portfolio_badges_espp_plus_shows_tax_impact_window(client):
    sec_id = _add_security(client, ticker="UIEPLTAXBADGE")
    add = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "ESPP_PLUS",
            "acquisition_date": date.today().replace(year=date.today().year - 1).isoformat(),
            "quantity": "5",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert add.status_code == 201, add.text

    home = client.get("/")
    assert home.status_code == 200
    assert "Tax Window" in home.text


def test_edit_lot_form_renders_with_confirmation_summary(client):
    sec_id = _add_security(client, ticker="UIEDITFORM")
    _add_lot_via_api(client, sec_id, quantity="5", price="10.00")

    with AppContext.read_session() as sess:
        lot = LotRepository(sess).get_all_lots_for_security(sec_id)[0]

    resp = client.get(f"/portfolio/edit-lot?lot_id={lot.id}")
    assert resp.status_code == 200
    assert "Lot Correction" in resp.text
    assert "Confirmation Summary" in resp.text
    assert "confirm_changes" in resp.text


def test_edit_lot_submit_updates_lot_and_redirects_with_audit_reference(client):
    sec_id = _add_security(client, ticker="UIEDITSUBMIT")
    _add_lot_via_api(client, sec_id, quantity="5", price="10.00")

    with AppContext.read_session() as sess:
        lot = LotRepository(sess).get_all_lots_for_security(sec_id)[0]

    resp = client.post(
        "/portfolio/edit-lot",
        data={
            "lot_id": lot.id,
            "acquisition_date": "2025-02-01",
            "quantity": "6",
            "acquisition_price_gbp": "11.00",
            "true_cost_per_share_gbp": "9.50",
            "tax_year": "2024-25",
            "fmv_at_acquisition_gbp": "12.00",
            "notes": "fix",
            "confirm_changes": "yes",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "Lot+updated+(audit+" in resp.headers["location"]

    with AppContext.read_session() as sess:
        updated = LotRepository(sess).require_by_id(lot.id)
    assert updated.quantity == "6"
    assert updated.quantity_remaining == "6"
    assert updated.acquisition_price_gbp == "11.00"


def test_edit_lot_submit_updates_broker_currency_for_brokerage_lot(client):
    sec_id = _add_security(client, ticker="UIEDITCCY")
    add = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2025-01-15",
            "quantity": "5",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
            "broker_currency": "GBP",
        },
    )
    assert add.status_code == 201, add.text
    lot_id = add.json()["id"]

    resp = client.post(
        "/portfolio/edit-lot",
        data={
            "lot_id": lot_id,
            "acquisition_date": "2025-02-01",
            "quantity": "5",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
            "tax_year": "2024-25",
            "fmv_at_acquisition_gbp": "",
            "broker_currency": "USD",
            "notes": "ccy fix",
            "confirm_changes": "yes",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    with AppContext.read_session() as sess:
        updated = LotRepository(sess).require_by_id(lot_id)
    assert updated.broker_currency == "USD"


def test_edit_lot_submit_requires_confirmation(client):
    sec_id = _add_security(client, ticker="UIEDITCONF")
    _add_lot_via_api(client, sec_id, quantity="5", price="10.00")

    with AppContext.read_session() as sess:
        lot = LotRepository(sess).get_all_lots_for_security(sec_id)[0]

    resp = client.post(
        "/portfolio/edit-lot",
        data={
            "lot_id": lot.id,
            "acquisition_date": "2025-02-01",
            "quantity": "6",
            "acquisition_price_gbp": "11.00",
            "true_cost_per_share_gbp": "9.50",
            "tax_year": "2024-25",
            "fmv_at_acquisition_gbp": "12.00",
            "notes": "fix",
        },
    )
    assert resp.status_code == 422
    assert "Confirm the change summary before saving." in resp.text


def test_transfer_lot_form_renders_candidates_and_warning(client):
    sec_id = _add_security(client, ticker="UITRFORM")
    add = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-01-15",
            "quantity": "5",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert add.status_code == 201, add.text

    resp = client.get("/portfolio/transfer-lot")
    assert resp.status_code == 200
    assert "Transfer to Brokerage" in resp.text
    assert "Transfer rules:" in resp.text
    assert "Quantity to Transfer" in resp.text
    assert "Destination Broker Currency" in resp.text
    assert "Transfers into ISA are not supported. Use dispose then Add Lot in ISA." in resp.text


def test_transfer_lot_form_groups_espp_as_fifo_pool_with_whole_share_default(client):
    sec_id = _add_security(client, ticker="UITRPOOL")
    first = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-01-15",
            "quantity": "0.6",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-02-15",
            "quantity": "0.6",
            "acquisition_price_gbp": "11.00",
            "true_cost_per_share_gbp": "11.00",
        },
    )
    assert second.status_code == 201, second.text

    resp = client.get("/portfolio/transfer-lot")
    assert resp.status_code == 200
    assert "ESPP (FIFO pool)" in resp.text
    assert resp.text.count("ESPP (FIFO pool)") == 1
    assert 'data-default-qty="1"' in resp.text
    assert 'data-whole-qty="1"' in resp.text


def test_transfer_lot_form_excludes_pre_vest_rsu_and_matched_espp_plus(client):
    sec_id = _add_security(client, ticker="UITRFILT")
    future_vest = (date.today() + timedelta(days=10)).isoformat()
    pre_vest = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "RSU",
            "acquisition_date": future_vest,
            "quantity": "5",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "4.00",
        },
    )
    assert pre_vest.status_code == 201, pre_vest.text

    add_espp_plus = client.post(
        "/portfolio/add-lot",
        data={
            "security_id": sec_id,
            "scheme_type": "ESPP_PLUS",
            "acquisition_date": date.today().isoformat(),
            "quantity": "7",
            "purchase_price_per_share_gbp": "10.00",
            "matched_shares_quantity": "1",
        },
        follow_redirects=False,
    )
    assert add_espp_plus.status_code == 303, add_espp_plus.text

    resp = client.get("/portfolio/transfer-lot")
    assert resp.status_code == 200
    assert "UITRFILT | RSU" not in resp.text
    assert "ESPP_PLUS" in resp.text
    assert "qty 1" not in resp.text


def test_transfer_lot_submit_updates_scheme_and_redirects_with_audit(client):
    sec_id = _add_security(client, ticker="UITRSUB")
    add = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-01-15",
            "quantity": "5",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert add.status_code == 201, add.text
    lot_id = add.json()["id"]

    resp = client.post(
        "/portfolio/transfer-lot",
        data={
            "lot_id": lot_id,
            "quantity": "5",
            "notes": "move",
            "confirm_transfer": "yes",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "Lot+transferred+(audit+" in resp.headers["location"]

    with AppContext.read_session() as sess:
        source = LotRepository(sess).require_by_id(lot_id)
        all_lots = LotRepository(sess).get_all_lots_for_security(sec_id)
    assert source.scheme_type == "ESPP"
    assert Decimal(source.quantity_remaining) == Decimal("0")
    broker_lots = [
        l
        for l in all_lots
        if l.scheme_type == "BROKERAGE" and Decimal(l.quantity_remaining) > Decimal("0")
    ]
    assert len(broker_lots) == 1
    assert Decimal(broker_lots[0].quantity_remaining) == Decimal("5")
    assert broker_lots[0].broker_currency == "GBP"


def test_transfer_lot_submit_respects_selected_destination_broker_currency(client):
    sec_id = _add_security(client, ticker="UITRCCY", currency="USD")
    add = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "RSU",
            "acquisition_date": "2025-01-15",
            "quantity": "5",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "4.00",
        },
    )
    assert add.status_code == 201, add.text
    lot_id = add.json()["id"]

    resp = client.post(
        "/portfolio/transfer-lot",
        data={
            "lot_id": lot_id,
            "quantity": "5",
            "broker_currency": "USD",
            "notes": "move with ccy",
            "confirm_transfer": "yes",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    with AppContext.read_session() as sess:
        transferred = LotRepository(sess).require_by_id(lot_id)
    assert transferred.scheme_type == "BROKERAGE"
    assert transferred.broker_currency == "USD"


def test_transfer_lot_submit_requires_confirmation(client):
    sec_id = _add_security(client, ticker="UITRCONF")
    add = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-01-15",
            "quantity": "5",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert add.status_code == 201, add.text
    lot_id = add.json()["id"]

    resp = client.post(
        "/portfolio/transfer-lot",
        data={"lot_id": lot_id, "quantity": "5", "notes": "move"},
    )
    assert resp.status_code == 422
    assert "Confirm the transfer summary before continuing." in resp.text


def test_transfer_lot_submit_rejects_fractional_espp_quantity(client):
    sec_id = _add_security(client, ticker="UITRFRAC")
    add = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-01-15",
            "quantity": "5",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert add.status_code == 201, add.text
    lot_id = add.json()["id"]

    resp = client.post(
        "/portfolio/transfer-lot",
        data={
            "lot_id": lot_id,
            "quantity": "1.5",
            "notes": "fractional move",
            "confirm_transfer": "yes",
        },
    )
    assert resp.status_code == 422
    assert "whole shares" in resp.text


def test_transfer_lot_submit_allows_whole_quantity_from_fractional_espp_remainder(client):
    sec_id = _add_security(client, ticker="UITRWHOLE")
    add = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-01-15",
            "quantity": "2.3",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert add.status_code == 201, add.text
    lot_id = add.json()["id"]

    resp = client.post(
        "/portfolio/transfer-lot",
        data={
            "lot_id": lot_id,
            "quantity": "2",
            "notes": "whole from fractional remainder",
            "confirm_transfer": "yes",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    with AppContext.read_session() as sess:
        source = LotRepository(sess).require_by_id(lot_id)
    assert Decimal(source.quantity_remaining) == Decimal("0.3")


def test_transfer_lot_submit_consumes_fractional_fifo_head_before_newer_lot(client):
    sec_id = _add_security(client, ticker="UITRFIFOFRAC")
    first = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-01-15",
            "quantity": "0.3",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-02-15",
            "quantity": "2",
            "acquisition_price_gbp": "11.00",
            "true_cost_per_share_gbp": "11.00",
        },
    )
    assert second.status_code == 201, second.text
    first_lot_id = first.json()["id"]
    second_lot_id = second.json()["id"]

    resp = client.post(
        "/portfolio/transfer-lot",
        data={
            "lot_id": first_lot_id,
            "quantity": "2",
            "notes": "fifo fractional head",
            "confirm_transfer": "yes",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    with AppContext.read_session() as sess:
        first_after = LotRepository(sess).require_by_id(first_lot_id)
        second_after = LotRepository(sess).require_by_id(second_lot_id)
        all_lots = LotRepository(sess).get_all_lots_for_security(sec_id)

    assert Decimal(first_after.quantity_remaining) == Decimal("0")
    assert Decimal(second_after.quantity_remaining) == Decimal("0.3")
    broker_lots = sorted(
        [
            lot
            for lot in all_lots
            if lot.scheme_type == "BROKERAGE" and Decimal(lot.quantity_remaining) > Decimal("0")
        ],
        key=lambda lot: (lot.acquisition_date, lot.id),
    )
    assert len(broker_lots) == 2
    assert Decimal(broker_lots[0].quantity_remaining) == Decimal("0.3")
    assert Decimal(broker_lots[1].quantity_remaining) == Decimal("1.7")


def test_transfer_lot_submit_rejects_non_fifo_espp_selection(client):
    sec_id = _add_security(client, ticker="UITRFIFO")
    first = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-01-15",
            "quantity": "5",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-02-15",
            "quantity": "5",
            "acquisition_price_gbp": "11.00",
            "true_cost_per_share_gbp": "11.00",
        },
    )
    assert second.status_code == 201, second.text

    later_lot_id = second.json()["id"]
    resp = client.post(
        "/portfolio/transfer-lot",
        data={
            "lot_id": later_lot_id,
            "quantity": "1",
            "notes": "skip fifo",
            "confirm_transfer": "yes",
        },
    )
    assert resp.status_code == 422
    assert "FIFO order" in resp.text


def test_portfolio_shows_decision_surface_columns(client):
    sec_id = _add_security(client, ticker="UISELLCOL")
    _add_lot_via_api(client, sec_id, quantity="5", price="10.00")

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="12.00",
            close_price_gbp="12.00",
            currency="GBP",
            source="test-ui",
        )

    home = client.get("/")
    assert home.status_code == 200
    assert "Status" in home.text
    assert "Net If Sold Today" in home.text
    assert "Gain If Sold Today" in home.text
    assert "Net If Held (Next Milestone)" in home.text
    assert "Gain If Held" in home.text
    assert "Net If Long-Term (5+ Years)" in home.text
    assert "Gain If Long-Term" in home.text
    assert "Notes" in home.text
    assert "Net Cash If Sold" not in home.text
    assert "Economic Result If Sold" not in home.text
    assert "Net Gain If Sold Today" in home.text
    assert "Sellable" in home.text


def test_portfolio_shows_per_ticker_daily_change_badge(client):
    sec_id = _add_security(client, ticker="UIDAILY")
    _add_lot_via_api(client, sec_id, quantity="5", price="10.00")

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 23),
            close_price_original_ccy="10.00",
            close_price_gbp="10.00",
            currency="GBP",
            source="yfinance_history",
        )
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="11.00",
            close_price_gbp="11.00",
            currency="GBP",
            source="test-ui",
        )

    home = client.get("/")
    assert home.status_code == 200
    assert "daily-change--up" in home.text
    assert "10.00% (&pound;5.00)" in home.text


def test_portfolio_daily_change_flags_no_change_when_market_open(client, monkeypatch):
    sec_id = _add_security_with_exchange(
        client,
        ticker="UISTALOPEN",
        exchange="NASDAQ",
        currency="USD",
    )
    _add_lot_via_api(client, sec_id, quantity="5", price="10.00")

    with AppContext.write_session() as sess:
        repo = PriceRepository(sess)
        repo.upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 23),
            close_price_original_ccy="10.00",
            close_price_gbp="10.00",
            currency="USD",
            source="yfinance_history",
        )
        repo.upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="11.00",
            close_price_gbp="11.00",
            currency="USD",
            source="test-ui",
        )
        repo.add_ticker_snapshot(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            price_gbp="11.0000",
            observed_at=datetime(2026, 2, 24, 14, 0, 0, tzinfo=timezone.utc),
        )
        repo.add_ticker_snapshot(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            price_gbp="11.0000",
            observed_at=datetime(2026, 2, 24, 14, 30, 0, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(
        "src.api.routers.ui._utc_now",
        lambda: datetime(2026, 2, 24, 15, 0, 0, tzinfo=timezone.utc),
    )

    home = client.get("/")
    assert home.status_code == 200
    assert "No change" in home.text
    assert "(market open)" in home.text


def test_portfolio_daily_change_shows_market_closed_opening_countdown(client, monkeypatch):
    sec_id = _add_security_with_exchange(
        client,
        ticker="UISTALCLOSE",
        exchange="NASDAQ",
        currency="USD",
    )
    _add_lot_via_api(client, sec_id, quantity="5", price="10.00")

    with AppContext.write_session() as sess:
        repo = PriceRepository(sess)
        repo.upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 23),
            close_price_original_ccy="10.00",
            close_price_gbp="10.00",
            currency="USD",
            source="yfinance_history",
        )
        repo.upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="11.00",
            close_price_gbp="11.00",
            currency="USD",
            source="test-ui",
        )
        repo.add_ticker_snapshot(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            price_gbp="11.0000",
            observed_at=datetime(2026, 2, 24, 14, 0, 0, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(
        "src.api.routers.ui._utc_now",
        lambda: datetime(2026, 2, 28, 15, 0, 0, tzinfo=timezone.utc),
    )

    home = client.get("/")
    assert home.status_code == 200
    assert "closed (opening in" in home.text
    assert "No change" not in home.text


def test_portfolio_cards_are_collapsible_and_net_panel_shows_top_level_fields(client):
    sec_id = _add_security(client, ticker="UICOLLAPSE")
    _add_lot_via_api(client, sec_id, quantity="5", price="10.00")

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 23),
            close_price_original_ccy="11.00",
            close_price_gbp="11.00",
            currency="GBP",
            source="test-ui",
        )
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="12.00",
            close_price_gbp="12.00",
            currency="GBP",
            source="test-ui",
        )

    home = client.get("/")
    assert home.status_code == 200
    assert '<div class="stat__label">Securities</div>' not in home.text
    assert "security-lots-toggle" in home.text
    assert "data-security-id=" in home.text
    assert "active lot" in home.text
    assert "Total Quantity" in home.text
    assert "P&amp;L (Economic)" in home.text


def test_portfolio_persists_security_hidden_show_state_on_refresh_contract(client):
    sec_id = _add_security(client, ticker="UIVISSTATE")
    _add_lot_via_api(client, sec_id, quantity="2", price="10.00")

    home = client.get("/")
    assert home.status_code == 200
    assert "SECURITY_VISIBILITY_KEY" in home.text
    assert "portfolio.security_visibility.v1" in home.text
    assert "wireSecurityVisibilityPersistence" in home.text
    assert ".security-lots-toggle[data-security-id]" in home.text
    assert "addEventListener(\"toggle\"" in home.text


def test_portfolio_net_gain_if_sold_today_sums_economic_result_column(client):
    sec_gain = _add_security(client, ticker="UINETGAIN1")
    sec_loss = _add_security(client, ticker="UINETGAIN2")
    _add_lot_via_api(client, sec_gain, quantity="5", price="10.00")
    _add_lot_via_api(client, sec_loss, quantity="2", price="20.00")

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_gain,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="12.00",
            close_price_gbp="12.00",
            currency="GBP",
            source="test-ui",
        )
        PriceRepository(sess).upsert(
            security_id=sec_loss,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="12.00",
            close_price_gbp="12.00",
            currency="GBP",
            source="test-ui",
        )

    home = client.get("/")
    assert home.status_code == 200
    assert "Net Gain If Sold Today" in home.text
    assert "&pound;-6.00" in home.text


def test_portfolio_est_net_liquidity_excludes_locked_and_tracks_blocked_value(client):
    sellable_sec = _add_security(client, ticker="UILIQSELL")
    locked_sec = _add_security(client, ticker="UILIQLOCK")
    vest_date = date.today() + timedelta(days=30)

    _add_lot_via_api(client, sellable_sec, quantity="2", price="10.00")
    add_locked = client.post(
        "/portfolio/lots",
        json={
            "security_id": locked_sec,
            "scheme_type": "RSU",
            "acquisition_date": vest_date.isoformat(),
            "quantity": "3",
            "acquisition_price_gbp": "100.00",
            "true_cost_per_share_gbp": "40.00",
        },
    )
    assert add_locked.status_code == 201, add_locked.text

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sellable_sec,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="12.00",
            close_price_gbp="12.00",
            currency="GBP",
            source="test-ui",
        )
        PriceRepository(sess).upsert(
            security_id=locked_sec,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="120.00",
            close_price_gbp="120.00",
            currency="GBP",
            source="test-ui",
        )

    settings = AppSettings.load(_state.get_db_path())
    summary = PortfolioService.get_portfolio_summary(
        settings=settings,
        use_live_true_cost=False,
    )
    rows_by_security = _build_portfolio_position_rows(summary, settings=settings)

    assert _portfolio_est_net_liquidity(rows_by_security) == Decimal("24.00")
    assert _portfolio_blocked_restricted_value(rows_by_security) == Decimal("360.00")

    home = client.get("/")
    assert home.status_code == 200
    assert "Est. Net Liquidity (Sellable)" in home.text
    assert "Blocked/Restricted Value" in home.text
    assert "&pound;24.00" in home.text
    assert "&pound;360.00" in home.text
    assert "Est. Net Liquidation" not in home.text


def test_portfolio_and_net_value_show_isa_tax_sheltered_labels(client):
    sec_id = _add_security(client, ticker="UIISALBL")
    add = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ISA",
            "acquisition_date": date.today().isoformat(),
            "quantity": "5",
            "purchase_price_per_share_gbp": "10.00",
        },
    )
    assert add.status_code == 303

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="12.00",
            close_price_gbp="12.00",
            currency="GBP",
            source="test-ui",
        )

    home = client.get("/")
    assert home.status_code == 200
    assert "ISA" in home.text
    assert "Tax-sheltered" in home.text
    assert "Set income profile in Settings to estimate employment tax." not in home.text

    net = client.get("/net-value")
    assert net.status_code == 200
    assert "Tax-sheltered" in net.text


def test_portfolio_shows_at_risk_status_for_espp_plus_employee_lot(client):
    sec_id = _add_security(client, ticker="UIATRISK")
    add = client.post(
        "/portfolio/add-lot",
        data={
            "security_id": sec_id,
            "scheme_type": "ESPP_PLUS",
            "acquisition_date": date.today().isoformat(),
            "quantity": "10",
            "purchase_price_per_share_gbp": "8.50",
            "matched_shares_quantity": "2",
        },
        follow_redirects=False,
    )
    assert add.status_code == 303

    home = client.get("/")
    assert home.status_code == 200
    assert "Forfeiture Risk" in home.text
    assert "Locked until" not in home.text


def test_portfolio_view_model_groups_espp_plus_paid_and_match_rows(client, db_engine):
    _, db_path = db_engine
    settings = AppSettings.defaults_for(db_path)
    settings.default_gross_income = Decimal("0")
    settings.default_pension_sacrifice = Decimal("0")
    settings.default_other_income = Decimal("0")
    settings.default_student_loan_plan = None
    settings.save()

    sec_id = _add_security(client, ticker="UIGROUPED")
    add = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP_PLUS",
            "acquisition_date": date.today().isoformat(),
            "quantity": "10",
            "purchase_price_per_share_gbp": "8.50",
            "matched_shares_quantity": "2",
        },
    )
    assert add.status_code == 303

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="12.00",
            close_price_gbp="12.00",
            currency="GBP",
            source="test-ui",
        )

    summary = PortfolioService.get_portfolio_summary(
        settings=AppSettings.load(db_path),
        use_live_true_cost=False,
    )
    rows = _build_portfolio_position_rows(summary)[sec_id]
    assert len(rows) == 1
    row = rows[0]
    assert row.row_kind == "GROUPED_ESPP_PLUS"
    assert row.scheme_display == "ESPP+"
    assert row.paid_qty == Decimal("10")
    assert row.match_qty == Decimal("2")
    assert row.total_qty == Decimal("12")


def test_portfolio_view_model_keeps_non_espp_rows_separate(client, db_engine):
    _, db_path = db_engine
    settings = AppSettings.defaults_for(db_path)
    settings.save()

    sec_id = _add_security(client, ticker="UISEPARATE")
    add_espp = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-01-15",
            "quantity": "5",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert add_espp.status_code == 201, add_espp.text
    _add_lot_via_api(client, sec_id, quantity="3", price="12.00")

    summary = PortfolioService.get_portfolio_summary(
        settings=AppSettings.load(db_path),
        use_live_true_cost=False,
    )
    rows = _build_portfolio_position_rows(summary)[sec_id]
    assert len(rows) == 2
    assert all(row.row_kind == "SINGLE_LOT" for row in rows)


def test_portfolio_view_model_forfeiture_cash_and_economic_logic(client, db_engine):
    _, db_path = db_engine
    settings = AppSettings.defaults_for(db_path)
    settings.default_gross_income = Decimal("0")
    settings.default_pension_sacrifice = Decimal("0")
    settings.default_other_income = Decimal("0")
    settings.default_student_loan_plan = None
    settings.save()

    sec_id = _add_security(client, ticker="UIFORFEITROW")
    add = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP_PLUS",
            "acquisition_date": date.today().isoformat(),
            "quantity": "10",
            "purchase_price_per_share_gbp": "10.00",
            "matched_shares_quantity": "2",
        },
    )
    assert add.status_code == 303

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="12.00",
            close_price_gbp="12.00",
            currency="GBP",
            source="test-ui",
        )

    summary = PortfolioService.get_portfolio_summary(
        settings=AppSettings.load(db_path),
        use_live_true_cost=False,
    )
    row = _build_portfolio_position_rows(summary)[sec_id][0]

    assert row.sell_now_match_effect == "FORFEITED"
    assert row.sell_now_forfeited_match_value > Decimal("0")
    # Cash outcome must not include locked/forfeited matched-share value.
    assert row.net_cash_if_sold == row.sell_now_cash_paid
    expected_economic = (
        row.net_cash_if_sold - row.paid_true_cost - row.sell_now_forfeited_match_value
    ).quantize(Decimal("0.01"))
    assert row.sell_now_economic_result == expected_economic


def test_portfolio_decision_table_money_cells_render_two_decimals(client):
    sec_id = _add_security(client, ticker="UIFMT2DP")
    _add_lot_via_api(client, sec_id, quantity="5", price="10.00")

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="12.00",
            close_price_gbp="12.00",
            currency="GBP",
            source="test-ui",
        )

    home = client.get("/")
    assert home.status_code == 200
    # Decision table values should render as 2dp currency values.
    assert "&pound;60.00" in home.text
    assert "&pound;50.00" in home.text


def test_simulate_rejects_quantity_above_available(client):
    sec_id = _add_security(client, ticker="UIMAX")
    _add_lot_via_api(client, sec_id, quantity="5", price="10.00")

    resp = client.post(
        "/simulate",
        data={
            "security_id": sec_id,
            "quantity": "10",
            "price_per_share_gbp": "12.00",
            "scheme_type": "",
        },
    )

    assert resp.status_code == 422
    assert "cannot exceed available quantity" in resp.text


def test_simulate_available_quantity_excludes_locked_espp_plus_matched_lots(client):
    sec_id = _add_security(client, ticker="UIMAXLOCK")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP_PLUS",
            "acquisition_date": date.today().isoformat(),
            "quantity": "10",
            "purchase_price_per_share_gbp": "10.00",
            "matched_shares_quantity": "5",
        },
    )
    assert resp.status_code == 303

    # Only the employee ESPP+ shares are sellable now; matched shares are locked.
    sim = client.post(
        "/simulate",
        data={
            "security_id": sec_id,
            "quantity": "11",
            "price_per_share_gbp": "12.00",
            "scheme_type": "ESPP_PLUS",
        },
    )
    assert sim.status_code == 422
    assert "cannot exceed available quantity (10)" in sim.text


def test_simulate_available_quantity_excludes_pre_vest_rsu_lots(client):
    sec_id = _add_security(client, ticker="UIRSULOCK")
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "RSU",
            "acquisition_date": "2099-01-01",
            "quantity": "5",
            "acquisition_price_gbp": "100.00",
            "true_cost_per_share_gbp": "40.00",
        },
    )
    assert resp.status_code == 201, resp.text

    # Pre-vest RSU is unsellable, so UI availability should be 0.
    sim = client.post(
        "/simulate",
        data={
            "security_id": sec_id,
            "quantity": "1",
            "price_per_share_gbp": "120.00",
            "scheme_type": "RSU",
        },
    )
    assert sim.status_code == 422
    assert "cannot exceed available quantity (0)" in sim.text


def test_simulate_prefills_latest_market_price(client):
    sec_id = _add_security(client, ticker="UIPRICE")
    _add_lot_via_api(client, sec_id, quantity="10", price="10.00")

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="123.45",
            close_price_gbp="123.45",
            currency="GBP",
            source="test-ui",
        )

    resp = client.get(f"/simulate?security_id={sec_id}")
    assert resp.status_code == 200
    assert 'name="price_per_share_gbp"' in resp.text
    assert 'value="123.45"' in resp.text


# ---------------------------------------------------------------------------
# ESPP_PLUS semantics: FMV exclusion + net-factor true cost
# ---------------------------------------------------------------------------

def test_add_lot_espp_plus_fmv_field_excluded_from_scheme():
    """
    The Market-Price-at-Purchase (FMV) input must not appear for ESPP_PLUS.
    After the ESPP_PLUS semantics fix the row carries data-schemes="ESPP" only.
    """
    # This is a template-structure test; no DB needed â€” reuse any client.
    # We verify the HTML attribute directly without JS execution.
    from fastapi.testclient import TestClient
    from src.api.app import app as _app
    from src.api import _state as _st
    from src.app_context import AppContext as _AC
    from src.db.engine import DatabaseEngine
    from src.db.models import Base
    import tempfile, pathlib

    import os as _os
    _os.environ.setdefault("EQUITY_SECRET_KEY", "test-secret-key-equity-tracker-testing-only-xx")
    _os.environ.setdefault("EQUITY_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    _os.environ.setdefault("EQUITY_DEV_MODE", "true")
    from src.api.auth import SESSION_COOKIE_NAME as _COOKIE, make_session_token as _tok

    with tempfile.TemporaryDirectory() as tmp:
        db_file = pathlib.Path(tmp) / "t.db"
        engine = DatabaseEngine.open_unencrypted(f"sqlite:///{db_file}")
        Base.metadata.create_all(engine.raw_engine)
        _AC.initialize(engine)
        _st.set_db_path(db_file)
        try:
            tc = TestClient(_app, raise_server_exceptions=True, cookies={_COOKIE: _tok()})
            # Add a security so the form renders
            tc.post("/portfolio/securities", json={
                "ticker": "FMVCHK", "name": "FMV Check", "currency": "GBP",
                "is_manual_override": True,
            })
            resp = tc.get("/portfolio/add-lot")
            assert resp.status_code == 200
            html = resp.text
            # FMV row must NOT span ESPP_PLUS
            assert 'data-schemes="ESPP,ESPP_PLUS"' not in html
            # FMV row still exists for plain ESPP
            assert 'data-schemes="ESPP"' in html
        finally:
            _st.set_db_path(None)
            _AC.lock()
            engine.dispose()


def test_add_lot_espp_plus_simulate_uses_persisted_true_cost(client, db_engine):
    """
    ESPP+ employee true cost is derived at add time and persisted.
    Simulation uses the stored acquisition-time value.
    """
    _, db_path = db_engine

    # Configure deterministic settings
    settings = AppSettings.defaults_for(db_path)
    settings.default_gross_income = Decimal("80000")
    settings.default_pension_sacrifice = Decimal("0")
    settings.default_student_loan_plan = 2
    settings.default_other_income = Decimal("0")
    settings.save()

    sec_id = _add_security(client, ticker="UIEPLRATE")
    purchase_price = Decimal("10.00")
    quantity = Decimal("10")
    acquisition_date = date.today()
    acquisition_date_str = acquisition_date.isoformat()

    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP_PLUS",
            "acquisition_date": acquisition_date_str,
            "quantity": str(quantity),
            "purchase_price_per_share_gbp": str(purchase_price),
            "matched_shares_quantity": "5",
        },
    )
    assert resp.status_code == 303, resp.text

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(sec_id)

    # Both lots are ESPP_PLUS; employee lot has matching_lot_id=None.
    assert len(lots) == 2
    employee = next(l for l in lots if l.scheme_type == "ESPP_PLUS" and l.matching_lot_id is None)
    assert employee.scheme_type == "ESPP_PLUS"
    rates = get_marginal_rates(
        TaxContext(
            tax_year=tax_year_for_date(acquisition_date),
            gross_employment_income=settings.default_gross_income,
            pension_sacrifice=settings.default_pension_sacrifice,
            other_income=settings.default_other_income,
            student_loan_plan=settings.default_student_loan_plan,
        )
    )
    expected_true_cost_per_share = (
        purchase_price * rates.pence_kept_per_pound
    ).quantize(Decimal("0.0001"))
    assert Decimal(employee.true_cost_per_share_gbp) == expected_true_cost_per_share

    simulated = client.post(
        "/simulate",
        data={
            "security_id": sec_id,
            "quantity": str(quantity),
            "price_per_share_gbp": "15.00",
            "scheme_type": "ESPP_PLUS",
        },
    )
    assert simulated.status_code == 200
    expected_total_true_cost = (
        expected_true_cost_per_share * quantity
    ).quantize(Decimal("0.01"))
    assert f"&pound;{expected_total_true_cost:.2f}" in simulated.text


def test_simulate_espp_plus_true_cost_does_not_change_after_income_change(client, db_engine):
    _, db_path = db_engine

    settings = AppSettings.defaults_for(db_path)
    settings.default_gross_income = Decimal("0")
    settings.default_pension_sacrifice = Decimal("0")
    settings.default_student_loan_plan = 2
    settings.default_other_income = Decimal("0")
    settings.save()

    sec_id = _add_security(client, ticker="UIEPLLIVE")
    purchase_price = Decimal("100.00")

    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP_PLUS",
            "acquisition_date": date.today().isoformat(),
            "quantity": "10",
            "purchase_price_per_share_gbp": str(purchase_price),
            "matched_shares_quantity": "1",
        },
    )
    assert resp.status_code == 303, resp.text

    sim_low = client.post(
        "/simulate",
        data={
            "security_id": sec_id,
            "quantity": "10",
            "price_per_share_gbp": "120.00",
            "scheme_type": "ESPP_PLUS",
        },
    )
    assert sim_low.status_code == 200
    assert "&pound;1000.00" in sim_low.text

    settings.default_gross_income = Decimal("100000")
    settings.save()

    sim_high = client.post(
        "/simulate",
        data={
            "security_id": sec_id,
            "quantity": "10",
            "price_per_share_gbp": "120.00",
            "scheme_type": "ESPP_PLUS",
        },
    )
    assert sim_high.status_code == 200
    assert "&pound;1000.00" in sim_high.text
    assert "&pound;490.00" not in sim_high.text


def test_add_lot_espp_non_plus_still_accepts_fmv(client):
    """
    Plain ESPP must still accept an optional Market-Price-at-Purchase value.
    FMV is stored for reference only; true cost always equals purchase price.
    """
    sec_id = _add_security(client, ticker="UIESPPFMV")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-03-01",
            "quantity": "10",
            "purchase_price_per_share_gbp": "8.50",
            "espp_fmv_at_purchase_gbp": "10.00",
        },
    )
    assert resp.status_code == 303, resp.text

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(sec_id)
    assert len(lots) == 1
    lot = lots[0]
    assert lot.scheme_type == "ESPP"
    assert Decimal(lot.acquisition_price_gbp) == Decimal("8.50")


# ---------------------------------------------------------------------------
# ESPP true-cost semantics: net-pay scheme, no discount-benefit tax
# ---------------------------------------------------------------------------


def test_add_lot_espp_true_cost_equals_purchase_price_with_fmv(client):
    """
    IBM UK ESPP: contributions from net pay; no income tax on the discount
    at acquisition. True economic cost must equal purchase price even when
    an FMV value is provided.
    """
    sec_id = _add_security(client, ticker="UIESPPFMV2")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-03-01",
            "quantity": "10",
            "purchase_price_per_share_gbp": "200.00",
            "espp_fmv_at_purchase_gbp": "230.00",
        },
    )
    assert resp.status_code == 303, resp.text

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(sec_id)
    assert len(lots) == 1
    lot = lots[0]
    assert lot.scheme_type == "ESPP"
    assert Decimal(lot.true_cost_per_share_gbp) == Decimal("200.00")


def test_add_lot_espp_true_cost_equals_purchase_price_without_fmv(client):
    """
    IBM UK ESPP: true economic cost must equal purchase price even when no
    FMV is provided. The old logic returned None when FMV was absent.
    """
    sec_id = _add_security(client, ticker="UIESPPTCNOFMV")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP",
            "acquisition_date": "2025-03-01",
            "quantity": "10",
            "purchase_price_per_share_gbp": "200.00",
        },
    )
    assert resp.status_code == 303, resp.text

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(sec_id)
    assert len(lots) == 1
    lot = lots[0]
    assert lot.scheme_type == "ESPP"
    assert Decimal(lot.true_cost_per_share_gbp) == Decimal("200.00")


def test_homepage_tax_tile_uses_employment_tax_label(client):
    sec_id = _add_security(client, ticker="UIHOMEEMP")
    _add_lot_via_api(client, sec_id, quantity="5", price="10.00")

    resp = client.get("/")
    assert resp.status_code == 200
    assert "Estimated Employment Tax" in resp.text
    assert "Est. Tax Liability" not in resp.text


def test_homepage_true_cost_uses_stored_db_value_not_live_income_recompute(client, db_engine):
    _, db_path = db_engine
    settings = AppSettings.defaults_for(db_path)
    settings.default_gross_income = Decimal("100000")
    settings.default_pension_sacrifice = Decimal("0")
    settings.default_other_income = Decimal("0")
    settings.default_student_loan_plan = 2
    settings.save()

    sec_id = _add_security(client, ticker="UIHOMESTORED")
    resp = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP_PLUS",
            "acquisition_date": date.today().isoformat(),
            "quantity": "10",
            "purchase_price_per_share_gbp": "100.00",
            "matched_shares_quantity": "0",
        },
    )
    assert resp.status_code == 303

    rates = get_marginal_rates(
        TaxContext(
            tax_year=tax_year_for_date(date.today()),
            gross_employment_income=settings.default_gross_income,
            pension_sacrifice=settings.default_pension_sacrifice,
            other_income=settings.default_other_income,
            student_loan_plan=settings.default_student_loan_plan,
        )
    )
    expected_total_true_cost = (
        Decimal("100.00") * rates.pence_kept_per_pound * Decimal("10")
    ).quantize(Decimal("0.01"))

    home = client.get("/")
    assert home.status_code == 200
    assert "Total True Cost" in home.text
    assert (
        f"{expected_total_true_cost:,.2f}" in home.text
        or f"{expected_total_true_cost:.2f}" in home.text
    )

    settings.default_gross_income = Decimal("0")
    settings.save()

    home_after_income_change = client.get("/")
    assert home_after_income_change.status_code == 200
    assert (
        f"{expected_total_true_cost:,.2f}" in home_after_income_change.text
        or f"{expected_total_true_cost:.2f}" in home_after_income_change.text
    )


def test_simulate_page_shows_employment_tax_wording_only(client, db_engine):
    _, db_path = db_engine
    settings = AppSettings.defaults_for(db_path)
    settings.default_gross_income = Decimal("60000")
    settings.default_pension_sacrifice = Decimal("0")
    settings.default_other_income = Decimal("0")
    settings.default_student_loan_plan = 2
    settings.save()

    sec_id = _add_security(client, ticker="UISIMEMP")
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "SIP_PARTNERSHIP",
            "acquisition_date": (date.today() - timedelta(days=90)).isoformat(),
            "quantity": "10",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert resp.status_code == 201, resp.text

    sim = client.post(
        "/simulate",
        data={
            "security_id": sec_id,
            "quantity": "10",
            "price_per_share_gbp": "12.00",
            "scheme_type": "",
        },
    )
    assert sim.status_code == 200
    assert "Total Employment Tax" in sim.text
    assert "Net Proceeds After Employment Tax" in sim.text
    assert "Tax-Basis Gain" not in sim.text
    assert "Cash Flow" in sim.text
    assert "Investment Performance (Paid Shares Only)" in sim.text
    assert "Scheme Incentive Impact" in sim.text
    assert "Total Economic Outcome" in sim.text
    assert "Net Cash Received" in sim.text
    assert "Net Economic Outcome" in sim.text
    assert "Net proceeds after employment tax" not in sim.text


def test_settings_nuke_db_requires_confirmation_text(client):
    resp = client.post(
        "/settings/nuke-db",
        data={"confirm_text": "nope"},
    )
    assert resp.status_code == 422
    assert "confirm database reset" in resp.text


def test_settings_nuke_db_drops_and_recreates_schema(client):
    sec_id = _add_security(client, ticker="NUKEUI")
    _add_lot_via_api(client, sec_id, quantity="2", price="10.00")

    summary_before = client.get("/portfolio/summary")
    assert summary_before.status_code == 200
    assert len(summary_before.json()["securities"]) == 1

    reset = client.post(
        "/settings/nuke-db",
        data={"confirm_text": "NUKE"},
        follow_redirects=False,
    )
    assert reset.status_code == 303
    assert reset.headers["location"] == "/settings?msg=Database+reset+complete."

    summary_after = client.get("/portfolio/summary")
    assert summary_after.status_code == 200
    assert summary_after.json()["securities"] == []

    with AppContext.read_session() as sess:
        catalog_count = SecurityCatalogRepository(sess).count()
    assert catalog_count > 0


def test_hide_values_masks_monetary_values_in_ui(client, db_engine):
    _, db_path = db_engine
    settings = AppSettings.defaults_for(db_path)
    settings.hide_values = True
    settings.save()

    sec_id = _add_security(client, ticker="UIHIDE")
    _add_lot_via_api(client, sec_id, quantity="5", price="10.00")

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="12.00",
            close_price_gbp="12.00",
            currency="GBP",
            source="test-hide-ui",
        )

    home = client.get("/")
    assert home.status_code == 200
    assert "Values Hidden" in home.text
    assert "••••" in home.text
    assert "&pound;12.00" not in home.text

    risk = client.get("/risk")
    assert risk.status_code == 200
    assert "100.00%" in risk.text
    assert "&pound;••••" in risk.text


def test_per_scheme_page_renders_current_and_historic_rows(client):
    sec_id = _add_security(client, ticker="UIPERSCHEME")
    add = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "BROKERAGE",
            "acquisition_date": "2024-01-15",
            "quantity": "10",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert add.status_code == 201, add.text

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date.today(),
            close_price_original_ccy="12.00",
            close_price_gbp="12.00",
            currency="GBP",
            source="test-ui",
        )

    commit = client.post(
        "/portfolio/disposals/commit",
        json={
            "security_id": sec_id,
            "quantity": "4",
            "price_per_share_gbp": "12.00",
            "transaction_date": "2024-06-01",
        },
    )
    assert commit.status_code == 201, commit.text

    page = client.get("/per-scheme")
    assert page.status_code == 200
    assert "Per Scheme" in page.text
    assert "Brokerage" in page.text
    assert "Current Lots" in page.text
    assert "Total Prev Lots" in page.text
    assert "Unrealised P&amp;L If Sold Now (Post-Tax)" in page.text
    assert "Realised P&amp;L (Economic)" in page.text
    assert "Scheme Visibility" in page.text
    assert "per_scheme.visibility.v1" in page.text


def test_per_scheme_page_shows_espp_plus_potential_forfeiture(client):
    sec_id = _add_security(client, ticker="UIPERSCHEMEPLUS")
    add = _post_add_lot_ui(
        client,
        {
            "security_id": sec_id,
            "scheme_type": "ESPP_PLUS",
            "acquisition_date": date.today().isoformat(),
            "quantity": "10",
            "purchase_price_per_share_gbp": "8.50",
            "matched_shares_quantity": "5",
        },
    )
    assert add.status_code == 303, add.text

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date.today(),
            close_price_original_ccy="15.00",
            close_price_gbp="15.00",
            currency="GBP",
            source="test-ui",
        )

    page = client.get("/per-scheme")
    assert page.status_code == 200
    assert "ESPP+" in page.text
    assert "Potential Match Forfeiture if Sold Now" in page.text


def test_per_scheme_est_net_liquidation_uses_economic_post_tax_pnl(client):
    sec_id = _add_security(client, ticker="UIPERSCHPNL")
    add = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "RSU",
            "acquisition_date": "2024-01-15",
            "quantity": "10",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert add.status_code == 201, add.text

    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec_id,
            price_date=date.today(),
            close_price_original_ccy="12.00",
            close_price_gbp="12.00",
            currency="GBP",
            source="test-ui",
        )

    db_path = _state.get_db_path()
    settings = AppSettings.load(db_path) if db_path else None
    summary = PortfolioService.get_portfolio_summary(
        settings=settings,
        use_live_true_cost=False,
    )
    rows_by_security = _build_portfolio_position_rows(summary)
    scheme_reports = _build_per_scheme_reports(rows_by_security)
    rsu_report = next(sr for sr in scheme_reports if sr.scheme_type == "RSU")

    assert rsu_report.current.market_value_gbp == Decimal("120.00")
    assert rsu_report.current.post_tax_economic_pnl_gbp == Decimal("20.00")
    assert rsu_report.current.est_net_liquidation_gbp == Decimal("20.00")
    assert rsu_report.current.est_net_liquidation_gbp != rsu_report.current.market_value_gbp


def test_cgt_page_shows_isa_exempt_notice(client):
    sec_id = _add_security(client, ticker="UICGTISA")
    add = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "ISA",
            "acquisition_date": "2024-01-15",
            "quantity": "10",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert add.status_code == 201, add.text

    tx_date = date(2024, 6, 1)
    commit = client.post(
        "/portfolio/disposals/commit",
        json={
            "security_id": sec_id,
            "quantity": "10",
            "price_per_share_gbp": "12.00",
            "transaction_date": tx_date.isoformat(),
        },
    )
    assert commit.status_code == 201, commit.text

    tax_year = tax_year_for_date(tx_date)
    page = client.get(f"/cgt?tax_year={tax_year}")
    assert page.status_code == 200
    assert "ISA disposals are tax-sheltered and excluded from CGT totals." in page.text
    assert f"No taxable disposals in {tax_year}." in page.text


def test_cgt_page_uses_tax_year_selector_controls(client):
    tax_years = client.get("/reports/tax-years").json()
    target = tax_years[min(1, len(tax_years) - 1)]
    page = client.get(f"/cgt?tax_year={target}")
    assert page.status_code == 200
    assert 'class="tax-year-selector"' in page.text
    assert 'id="cgt-tax-year"' in page.text
    assert "Prev" in page.text
    assert "Next" in page.text


def test_economic_gain_page_shows_isa_exempt_notice(client):
    sec_id = _add_security(client, ticker="UIECOISA")
    add = client.post(
        "/portfolio/lots",
        json={
            "security_id": sec_id,
            "scheme_type": "ISA",
            "acquisition_date": "2024-01-15",
            "quantity": "10",
            "acquisition_price_gbp": "10.00",
            "true_cost_per_share_gbp": "10.00",
        },
    )
    assert add.status_code == 201, add.text

    tx_date = date(2024, 6, 1)
    commit = client.post(
        "/portfolio/disposals/commit",
        json={
            "security_id": sec_id,
            "quantity": "10",
            "price_per_share_gbp": "12.00",
            "transaction_date": tx_date.isoformat(),
        },
    )
    assert commit.status_code == 201, commit.text

    tax_year = tax_year_for_date(tx_date)
    page = client.get(f"/economic-gain?tax_year={tax_year}")
    assert page.status_code == 200
    assert "ISA disposals are tax-sheltered and excluded from this report total." in page.text
    assert f"No taxable disposals in {tax_year}." in page.text


def test_economic_gain_page_uses_tax_year_selector_controls(client):
    tax_years = client.get("/reports/tax-years").json()
    target = tax_years[min(1, len(tax_years) - 1)]
    page = client.get(f"/economic-gain?tax_year={target}")
    assert page.status_code == 200
    assert 'class="tax-year-selector"' in page.text
    assert 'id="economic-tax-year"' in page.text
    assert "Prev" in page.text
    assert "Next" in page.text

