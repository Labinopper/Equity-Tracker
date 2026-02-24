"""
Create a deterministic demo database for UI validation.

Usage:
    python scripts/seed_demo_db.py
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from src.db.engine import DatabaseEngine
from src.db.models import Base, FxRate, Lot, PriceHistory, Security
from src.settings import AppSettings


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEMO_DB_PATH = DATA_DIR / "demo.db"
DEMO_SETTINGS_PATH = Path(str(DEMO_DB_PATH) + ".settings.json")

SEED_TIMESTAMP = datetime(2026, 2, 24, 12, 0, 0)
SEED_PRICE_DATE = date(2026, 2, 24)

SECURITY_ID = "00000000-0000-0000-0000-000000000101"
LOT_BROKERAGE_ID = "00000000-0000-0000-0000-000000000201"
LOT_ESPP_ID = "00000000-0000-0000-0000-000000000202"
LOT_ESPP_EMPLOYEE_ID = "00000000-0000-0000-0000-000000000203"
LOT_ESPP_MATCHED_ID = "00000000-0000-0000-0000-000000000204"
PRICE_ID = "00000000-0000-0000-0000-000000000301"
FX_ID = "00000000-0000-0000-0000-000000000401"


def _uk_tax_year_for(d: date) -> str:
    """Return UK tax year label like '2025-26' for a calendar date."""
    if (d.month, d.day) >= (4, 6):
        start_year = d.year
    else:
        start_year = d.year - 1
    end_short = str((start_year + 1) % 100).zfill(2)
    return f"{start_year}-{end_short}"


def _reset_files() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for path in (DEMO_DB_PATH, DEMO_SETTINGS_PATH):
        if path.exists():
            path.unlink()


def _seed_demo_db() -> None:
    db_url = f"sqlite:///{DEMO_DB_PATH}"
    engine = DatabaseEngine.open_unencrypted(db_url)
    try:
        Base.metadata.create_all(engine.raw_engine)

        brokerage_date = date(2026, 2, 1)
        espp_date = date(2026, 2, 10)
        espp_pair_date = date(2026, 2, 20)
        espp_lock_end = date(2026, 8, 22)

        with engine.session() as sess:
            security = Security(
                id=SECURITY_ID,
                ticker="IBM",
                isin="US4592001014",
                name="International Business Machines Corp.",
                currency="USD",
                exchange="NYSE",
                units_precision=0,
                is_manual_override=True,
                created_at=SEED_TIMESTAMP,
                updated_at=SEED_TIMESTAMP,
            )

            lots = [
                Lot(
                    id=LOT_BROKERAGE_ID,
                    security_id=SECURITY_ID,
                    grant_id=None,
                    scheme_type="BROKERAGE",
                    tax_year=_uk_tax_year_for(brokerage_date),
                    acquisition_date=brokerage_date,
                    quantity="10",
                    quantity_remaining="10",
                    acquisition_price_gbp="100.00",
                    true_cost_per_share_gbp="100.00",
                    fmv_at_acquisition_gbp=None,
                    notes="Demo seed: brokerage lot",
                    forfeiture_period_end=None,
                    matching_lot_id=None,
                    created_at=SEED_TIMESTAMP,
                    updated_at=SEED_TIMESTAMP,
                ),
                Lot(
                    id=LOT_ESPP_ID,
                    security_id=SECURITY_ID,
                    grant_id=None,
                    scheme_type="ESPP",
                    tax_year=_uk_tax_year_for(espp_date),
                    acquisition_date=espp_date,
                    quantity="10",
                    quantity_remaining="10",
                    acquisition_price_gbp="120.00",
                    true_cost_per_share_gbp="120.00",
                    fmv_at_acquisition_gbp="130.00",
                    notes="Demo seed: ESPP lot",
                    forfeiture_period_end=None,
                    matching_lot_id=None,
                    created_at=SEED_TIMESTAMP,
                    updated_at=SEED_TIMESTAMP,
                ),
                Lot(
                    id=LOT_ESPP_EMPLOYEE_ID,
                    security_id=SECURITY_ID,
                    grant_id=None,
                    scheme_type="ESPP",
                    tax_year=_uk_tax_year_for(espp_pair_date),
                    acquisition_date=espp_pair_date,
                    quantity="7",
                    quantity_remaining="7",
                    acquisition_price_gbp="110.00",
                    true_cost_per_share_gbp="110.00",
                    fmv_at_acquisition_gbp="118.00",
                    notes="Demo seed: ESPP employee lot for ESPP+ pair",
                    forfeiture_period_end=None,
                    matching_lot_id=None,
                    created_at=SEED_TIMESTAMP,
                    updated_at=SEED_TIMESTAMP,
                ),
                Lot(
                    id=LOT_ESPP_MATCHED_ID,
                    security_id=SECURITY_ID,
                    grant_id=None,
                    scheme_type="ESPP_PLUS",
                    tax_year=_uk_tax_year_for(espp_pair_date),
                    acquisition_date=espp_pair_date,
                    quantity="1",
                    quantity_remaining="1",
                    acquisition_price_gbp="0.00",
                    true_cost_per_share_gbp="0.00",
                    fmv_at_acquisition_gbp="118.00",
                    notes="Demo seed: ESPP+ matched lot (locked)",
                    forfeiture_period_end=espp_lock_end,
                    matching_lot_id=LOT_ESPP_EMPLOYEE_ID,
                    created_at=SEED_TIMESTAMP,
                    updated_at=SEED_TIMESTAMP,
                ),
            ]

            price = PriceHistory(
                id=PRICE_ID,
                security_id=SECURITY_ID,
                price_date=SEED_PRICE_DATE,
                close_price_original_ccy="250.00",
                close_price_gbp="197.50",
                currency="USD",
                source="demo_seed:2026-02-24|fx:2026-02-24",
                is_manual_override=True,
                fetched_at=SEED_TIMESTAMP,
                created_at=SEED_TIMESTAMP,
            )

            fx_rate = FxRate(
                id=FX_ID,
                base_currency="USD",
                quote_currency="GBP",
                rate_date=SEED_PRICE_DATE,
                rate="0.79",
                source="demo_seed",
                is_manual_override=True,
                fetched_at=SEED_TIMESTAMP,
                created_at=SEED_TIMESTAMP,
            )

            sess.add(security)
            for lot in lots:
                sess.add(lot)
            sess.add(price)
            sess.add(fx_rate)

        settings = AppSettings.defaults_for(DEMO_DB_PATH)
        settings.default_gross_income = Decimal("70000")
        settings.default_pension_sacrifice = Decimal("0")
        settings.default_student_loan_plan = 2
        settings.default_other_income = Decimal("0")
        settings.default_tax_year = "2025-26"
        settings.show_exhausted_lots = False
        settings.save()
    finally:
        engine.dispose()


def main() -> None:
    _reset_files()
    _seed_demo_db()
    print(f"Seeded demo database: {DEMO_DB_PATH}")
    print(f"Seeded settings file: {DEMO_SETTINGS_PATH}")


if __name__ == "__main__":
    main()
