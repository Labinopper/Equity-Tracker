from __future__ import annotations

from datetime import date


def test_cash_page_renders(client):
    resp = client.get("/cash")
    assert resp.status_code == 200
    assert "Cash Ledger" in resp.text
    assert "GBP-Only ISA Transfer Workflow" in resp.text
    assert "Deterministic Transfer Preview" in resp.text
    assert "Provenance confidence" in resp.text


def test_cash_manual_entry_updates_balance(client):
    create = client.post(
        "/cash/entry",
        data={
            "entry_date": date.today().isoformat(),
            "container": "BROKER",
            "currency": "GBP",
            "amount": "250.00",
            "entry_type": "MANUAL_ADJUSTMENT",
            "source": "test",
        },
        follow_redirects=False,
    )
    assert create.status_code == 303

    page = client.get("/cash")
    assert page.status_code == 200
    assert "BROKER" in page.text
    assert "&pound;250.00" in page.text


def test_cash_isa_transfer_gbp_moves_balance(client):
    seed = client.post(
        "/cash/entry",
        data={
            "entry_date": date.today().isoformat(),
            "container": "BROKER",
            "currency": "GBP",
            "amount": "500.00",
        },
        follow_redirects=False,
    )
    assert seed.status_code == 303

    transfer = client.post(
        "/cash/isa-transfer",
        data={
            "entry_date": date.today().isoformat(),
            "source_container": "BROKER",
            "source_currency": "GBP",
            "source_amount": "200.00",
            "fx_rate": "",
            "fx_fee_gbp": "0",
            "fx_source": "",
        },
        follow_redirects=False,
    )
    assert transfer.status_code == 303

    page = client.get("/cash")
    assert page.status_code == 200
    assert "&pound;300.00" in page.text
    assert "&pound;200.00" in page.text
    assert "ISA_TRANSFER_IN" in page.text


def test_cash_isa_transfer_non_gbp_requires_fx_metadata(client):
    seed = client.post(
        "/cash/entry",
        data={
            "entry_date": date.today().isoformat(),
            "container": "BROKER",
            "currency": "USD",
            "amount": "100.00",
        },
        follow_redirects=False,
    )
    assert seed.status_code == 303

    transfer = client.post(
        "/cash/isa-transfer",
        data={
            "entry_date": date.today().isoformat(),
            "source_container": "BROKER",
            "source_currency": "USD",
            "source_amount": "50.00",
            "fx_rate": "",
            "fx_fee_gbp": "0",
            "fx_source": "",
        },
    )
    assert transfer.status_code == 422
    assert "requires a positive FX rate" in transfer.text


def test_cash_isa_transfer_non_gbp_converts_then_transfers(client):
    seed = client.post(
        "/cash/entry",
        data={
            "entry_date": date.today().isoformat(),
            "container": "BROKER",
            "currency": "USD",
            "amount": "100.00",
        },
        follow_redirects=False,
    )
    assert seed.status_code == 303

    transfer = client.post(
        "/cash/isa-transfer",
        data={
            "entry_date": date.today().isoformat(),
            "source_container": "BROKER",
            "source_currency": "USD",
            "source_amount": "50.00",
            "fx_rate": "0.80",
            "fx_fee_gbp": "1.00",
            "fx_source": "test-rate",
        },
        follow_redirects=False,
    )
    assert transfer.status_code == 303

    page = client.get("/cash")
    assert page.status_code == 200
    assert "USD 50.00" in page.text
    assert "&pound;39.00" in page.text
    assert "FX_CONVERSION_IN" in page.text
    assert "ISA_TRANSFER_IN" in page.text
    assert "High (explicit FX source)" in page.text
