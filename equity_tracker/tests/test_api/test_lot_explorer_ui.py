from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from src.app_context import AppContext
from src.db.repository import LotRepository
from src.db.repository.prices import PriceRepository
from src.services.portfolio_service import PortfolioService
from src.settings import AppSettings


def _add_security(client, *, ticker: str, currency: str = "GBP") -> str:
    resp = client.post(
        "/portfolio/securities",
        json={
            "ticker": ticker,
            "name": f"{ticker} Lot Explorer Plc",
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
    scheme_type: str,
    acquisition_date: str,
    quantity: str,
    price: str,
) -> None:
    resp = client.post(
        "/portfolio/lots",
        json={
            "security_id": security_id,
            "scheme_type": scheme_type,
            "acquisition_date": acquisition_date,
            "quantity": quantity,
            "acquisition_price_gbp": price,
            "true_cost_per_share_gbp": price,
        },
    )
    assert resp.status_code == 201, resp.text


def _set_price(security_id: str, close_gbp: str) -> None:
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=security_id,
            price_date=date.today(),
            close_price_original_ccy=close_gbp,
            close_price_gbp=close_gbp,
            currency="GBP",
            source="test-lot-explorer-ui",
        )


def test_lot_explorer_renders_current_lot_forensics(client):
    security_id = _add_security(client, ticker="LOTX")
    _add_lot(
        client,
        security_id=security_id,
        scheme_type="BROKERAGE",
        acquisition_date=(date.today() - timedelta(days=60)).isoformat(),
        quantity="10",
        price="10.00",
    )
    _add_lot(
        client,
        security_id=security_id,
        scheme_type="ISA",
        acquisition_date=(date.today() - timedelta(days=30)).isoformat(),
        quantity="5",
        price="11.00",
    )
    _set_price(security_id, "12.00")

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(security_id)
    assert len(lots) == 2

    resp = client.get("/lot-explorer")
    assert resp.status_code == 200
    text = resp.text

    assert "Lot Explorer" in text
    assert "Forensic lot-level inspection" in text
    assert lots[0].id in text
    assert lots[1].id in text
    assert "Current order #1" in text
    assert "Audit" in text
    assert "Reconcile" in text
    assert "Add Dividend" in text
    assert f"/dividends?lot_ids={lots[0].id}" in text or f"/dividends?lot_ids={lots[1].id}" in text


def test_lot_explorer_can_show_exhausted_lots(client, db_engine):
    _, db_path = db_engine
    settings = AppSettings.defaults_for(db_path)
    settings.save()

    security_id = _add_security(client, ticker="LOTXEX")
    _add_lot(
        client,
        security_id=security_id,
        scheme_type="BROKERAGE",
        acquisition_date=(date.today() - timedelta(days=90)).isoformat(),
        quantity="4",
        price="10.00",
    )
    _set_price(security_id, "12.00")

    with AppContext.read_session() as sess:
        lot = LotRepository(sess).get_all_lots_for_security(security_id)[0]

    PortfolioService.commit_disposal(
        security_id=security_id,
        quantity=Decimal("4"),
        price_per_share_gbp=Decimal("12.00"),
        transaction_date=date.today(),
        settings=settings,
        use_live_true_cost=False,
    )

    hidden = client.get("/lot-explorer")
    assert hidden.status_code == 200
    assert lot.id not in hidden.text

    shown = client.get("/lot-explorer?include_exhausted=true&sellability=EXHAUSTED")
    assert shown.status_code == 200
    assert lot.id in shown.text
    assert "EXHAUSTED" in shown.text
