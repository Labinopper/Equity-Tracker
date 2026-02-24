from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.app_context import AppContext
from src.db.repository.prices import PriceRepository
from src.services.portfolio_service import PortfolioService


def test_validation_report_cli_text_output(app_context, capsys):
    sec = PortfolioService.add_security("CLIBM", "CLI IBM", "GBP", is_manual_override=True)
    PortfolioService.add_lot(
        security_id=sec.id,
        scheme_type="BROKERAGE",
        acquisition_date=date(2025, 1, 15),
        quantity=Decimal("2"),
        acquisition_price_gbp=Decimal("10.00"),
        true_cost_per_share_gbp=Decimal("10.00"),
    )
    with AppContext.write_session() as sess:
        PriceRepository(sess).upsert(
            security_id=sec.id,
            price_date=date(2026, 2, 24),
            close_price_original_ccy="12.00",
            close_price_gbp="12.00",
            currency="GBP",
            source="google_sheets:2026-02-24 10:00:00",
        )

    from app.validation_report import main

    rc = main(
        [
            "--format",
            "text",
            "--security",
            "CLIBM",
            "--as-of",
            "2026-02-24T23:59:59Z",
        ]
    )
    assert rc == 0

    out = capsys.readouterr().out
    assert "A) Report Metadata" in out
    assert "F) Per-Lot Deep Breakdown" in out
