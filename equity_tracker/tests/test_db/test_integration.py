"""
Phase 2 integration tests — full stack: DB + FIFO + Tax Engine.

Test strategy:
  a) Creates a database (encrypted or plain SQLite)
  b) Inserts a security + two lots (computed using the tax engine)
  c) Runs FIFO disposal allocation (pure core module)
  d) Persists lot_disposals atomically via DisposalRepository
  e) Verifies realised_gain_gbp and realised_gain_economic_gbp in the DB
     against expected values derived from the tax engine outputs

Scenario:
  Security  : ACME Corp (USD)
  Lot 1     : SIP Partnership, 100 shares acquired 2024-01-15
                CGT basis       = £10.00/share  (gross salary deducted = £1,000 total)
                True cost       = £4.90/share   ← from sip_partnership_true_cost()
                                                  (higher rate, 51% combined: £10 × 0.49)
  Lot 2     : RSU, 50 shares acquired 2024-03-20
                CGT basis       = £12.00/share  (FMV at vest)
                True cost       = £12.00/share  ← from rsu_true_cost()
                                                  (RSU: no pre-tax discount)

  Sale      : 120 shares at £15.00 on 2024-12-01 (FIFO: Lot 1 fully consumed, Lot 2 partially)

  Expected per-lot:
    Lot 1 (100 shares):
      proceeds          = 100 × £15.00 = £1,500.00
      CGT cost          = 100 × £10.00 = £1,000.00
      true cost         = 100 × £4.90  =   £490.00
      CGT gain          = £1,500 - £1,000 =   £500.00
      economic gain     = £1,500 - £490   = £1,010.00

    Lot 2 (20 shares):
      proceeds          =  20 × £15.00 =   £300.00
      CGT cost          =  20 × £12.00 =   £240.00
      true cost         =  20 × £12.00 =   £240.00
      CGT gain          = £300 - £240 =     £60.00
      economic gain     = £300 - £240 =     £60.00

  Aggregate:
      total proceeds    = £1,800.00
      total CGT gain    =   £560.00
      total econ. gain  = £1,070.00
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.core.lot_engine.fifo import LotForFIFO, allocate_fifo
from src.core.tax_engine.context import TaxContext
from src.core.tax_engine.marginal_rates import get_marginal_rates
from src.core.tax_engine.true_cost import rsu_true_cost, sip_partnership_true_cost
from src.db.engine import SQLCIPHER_AVAILABLE, DatabaseEngine
from src.db.models import Lot, Security, Transaction
from src.db.repository import (
    AuditRepository,
    DisposalRepository,
    LotRepository,
    SecurityRepository,
)


# ---------------------------------------------------------------------------
# Scenario constants
# ---------------------------------------------------------------------------

_TAX_YEAR = "2024-25"
_ACQ_DATE_LOT1 = date(2024, 1, 15)
_ACQ_DATE_LOT2 = date(2024, 3, 20)
_DISPOSAL_DATE = date(2024, 12, 1)
_DISPOSAL_PRICE = Decimal("15.00")

_LOT1_QUANTITY = Decimal("100")
_LOT2_QUANTITY = Decimal("50")
_SALE_QUANTITY = Decimal("120")

# SIP: 100 shares, £10 FMV each, gross salary deduction = £1,000
_LOT1_FMV = Decimal("10.00")
_LOT1_GROSS = Decimal("1000.00")

# RSU: FMV at vest = £12/share
_LOT2_FMV = Decimal("12.00")


def _make_higher_rate_rates():
    """Higher rate taxpayer with Plan 2 SL: combined = 51%."""
    ctx = TaxContext(
        tax_year=_TAX_YEAR,
        gross_employment_income=Decimal("80000"),
        student_loan_plan=2,
    )
    return get_marginal_rates(ctx)


# ---------------------------------------------------------------------------
# Core integration test
# ---------------------------------------------------------------------------

class TestFullFIFOIntegration:
    """
    Full stack test: tax engine → lot creation → FIFO → DB persistence → verification.
    """

    def test_fifo_disposal_end_to_end(self, db_engine):
        """
        Primary integration test satisfying all of the user's Phase 2 requirements:
          a) Creates a (plain) DB
          b) Inserts security + two lots (costs computed via tax engine)
          c) Runs FIFO allocation
          d) Persists lot_disposals
          e) Verifies realised gains in DB
        """
        rates = _make_higher_rate_rates()

        # ── Step 1: Compute per-share costs via tax engine ────────────────
        # Lot 1: SIP Partnership
        sip_result = sip_partnership_true_cost(
            gross_salary_deducted_gbp=_LOT1_GROSS,
            quantity=_LOT1_QUANTITY,
            fmv_at_purchase_gbp=_LOT1_FMV,
            marginal_rates=rates,
        )
        lot1_cgt_basis_per_share = sip_result.cgt_cost_basis_gbp / _LOT1_QUANTITY
        lot1_true_cost_per_share = sip_result.true_cost_per_share_gbp

        # Verify tax engine outputs before persisting (guards against regression)
        assert lot1_cgt_basis_per_share == Decimal("10.00")
        assert lot1_true_cost_per_share == Decimal("4.90")

        # Lot 2: RSU
        rsu_result = rsu_true_cost(
            fmv_at_vest_gbp=_LOT2_FMV,
            quantity=_LOT2_QUANTITY,
            marginal_rates=rates,
        )
        lot2_cgt_basis_per_share = rsu_result.cgt_cost_basis_gbp / _LOT2_QUANTITY
        lot2_true_cost_per_share = rsu_result.true_cost_per_share_gbp

        assert lot2_cgt_basis_per_share == Decimal("12.00")
        assert lot2_true_cost_per_share == Decimal("12.00")  # RSU: no discount

        # ── Step 2: Persist security + lots ───────────────────────────────
        with db_engine.session() as sess:
            sec_repo  = SecurityRepository(sess)
            lot_repo  = LotRepository(sess)
            audit     = AuditRepository(sess)

            security = Security(
                ticker="ACME",
                name="ACME Corp",
                currency="USD",
                exchange="NASDAQ",
                units_precision=0,
            )
            sec_repo.add(security)
            sess.flush()   # flush to get security.id before referencing it

            lot1 = Lot(
                security_id=security.id,
                scheme_type="SIP_PARTNERSHIP",
                tax_year=_TAX_YEAR,
                acquisition_date=_ACQ_DATE_LOT1,
                quantity=str(_LOT1_QUANTITY),
                quantity_remaining=str(_LOT1_QUANTITY),
                acquisition_price_gbp=str(lot1_cgt_basis_per_share),
                true_cost_per_share_gbp=str(lot1_true_cost_per_share),
                fmv_at_acquisition_gbp=str(_LOT1_FMV),
            )
            lot_repo.add(lot1, audit=audit)

            lot2 = Lot(
                security_id=security.id,
                scheme_type="RSU",
                tax_year=_TAX_YEAR,
                acquisition_date=_ACQ_DATE_LOT2,
                quantity=str(_LOT2_QUANTITY),
                quantity_remaining=str(_LOT2_QUANTITY),
                acquisition_price_gbp=str(lot2_cgt_basis_per_share),
                true_cost_per_share_gbp=str(lot2_true_cost_per_share),
                fmv_at_acquisition_gbp=str(_LOT2_FMV),
            )
            lot_repo.add(lot2, audit=audit)

            security_id = security.id
            lot1_id = lot1.id
            lot2_id = lot2.id
        # session auto-commits here

        # ── Step 3: FIFO allocation (pure, no DB) ─────────────────────────
        with db_engine.session() as sess:
            lot_repo = LotRepository(sess)
            active_lots = lot_repo.get_active_lots_for_security(security_id)

        assert len(active_lots) == 2
        assert active_lots[0].id == lot1_id   # Lot 1 is older → first
        assert active_lots[1].id == lot2_id

        fifo_input = [
            LotForFIFO(
                lot_id=lot.id,
                acquisition_date=lot.acquisition_date,
                quantity_remaining=Decimal(lot.quantity_remaining),
                acquisition_price_gbp=Decimal(lot.acquisition_price_gbp),
                true_cost_per_share_gbp=Decimal(lot.true_cost_per_share_gbp),
            )
            for lot in active_lots
        ]

        fifo_result = allocate_fifo(fifo_input, _SALE_QUANTITY, _DISPOSAL_PRICE)

        assert fifo_result.is_fully_allocated
        assert fifo_result.quantity_sold == _SALE_QUANTITY
        assert len(fifo_result.allocations) == 2

        # Per-lot assertions (before DB write)
        alloc1, alloc2 = fifo_result.allocations
        assert alloc1.lot_id == lot1_id
        assert alloc1.quantity_allocated == Decimal("100")
        assert alloc1.proceeds_gbp == Decimal("1500.00")
        assert alloc1.cost_basis_gbp == Decimal("1000.00")
        assert alloc1.true_cost_gbp == Decimal("490.00")
        assert alloc1.realised_gain_gbp == Decimal("500.00")
        assert alloc1.realised_gain_economic_gbp == Decimal("1010.00")

        assert alloc2.lot_id == lot2_id
        assert alloc2.quantity_allocated == Decimal("20")
        assert alloc2.proceeds_gbp == Decimal("300.00")
        assert alloc2.cost_basis_gbp == Decimal("240.00")
        assert alloc2.true_cost_gbp == Decimal("240.00")
        assert alloc2.realised_gain_gbp == Decimal("60.00")
        assert alloc2.realised_gain_economic_gbp == Decimal("60.00")

        # Aggregate
        assert fifo_result.total_proceeds_gbp == Decimal("1800.00")
        assert fifo_result.total_realised_gain_gbp == Decimal("560.00")
        assert fifo_result.total_realised_gain_economic_gbp == Decimal("1070.00")

        # ── Step 4: Persist disposal atomically ───────────────────────────
        with db_engine.session() as sess:
            lot_repo  = LotRepository(sess)
            disp_repo = DisposalRepository(sess)
            audit     = AuditRepository(sess)

            # Re-fetch lots within the persistence session
            lots_by_id = {
                lot.id: lot
                for lot in lot_repo.get_active_lots_for_security(security_id)
            }

            transaction = Transaction(
                security_id=security_id,
                transaction_type="DISPOSAL",
                transaction_date=_DISPOSAL_DATE,
                quantity=str(_SALE_QUANTITY),
                price_per_share_gbp=str(_DISPOSAL_PRICE),
                total_proceeds_gbp=str(fifo_result.total_proceeds_gbp),
                notes="Integration test disposal",
            )

            created_disposals = disp_repo.record_disposal_from_fifo(
                transaction=transaction,
                fifo_result=fifo_result,
                lots_by_id=lots_by_id,
                audit=audit,
            )
            transaction_id = transaction.id
        # auto-commit here

        # ── Step 5: Verify DB state ────────────────────────────────────────
        with db_engine.session() as sess:
            lot_repo = LotRepository(sess)

            # Lot 1 fully consumed
            lot1_db = lot_repo.require_by_id(lot1_id)
            assert Decimal(lot1_db.quantity_remaining) == Decimal("0")

            # Lot 2: 50 - 20 = 30 remaining
            lot2_db = lot_repo.require_by_id(lot2_id)
            assert Decimal(lot2_db.quantity_remaining) == Decimal("30")

            # Verify lot_disposals written correctly
            from sqlalchemy import select
            from src.db.models import LotDisposal

            disposals = list(
                sess.execute(
                    select(LotDisposal)
                    .where(LotDisposal.transaction_id == transaction_id)
                    .order_by(LotDisposal.created_at)
                ).scalars()
            )

            assert len(disposals) == 2

            # Lot 1 disposal
            d1 = next(d for d in disposals if d.lot_id == lot1_id)
            assert Decimal(d1.quantity_allocated) == Decimal("100")
            assert Decimal(d1.realised_gain_gbp) == Decimal("500.00")
            assert Decimal(d1.realised_gain_economic_gbp) == Decimal("1010.00")

            # Lot 2 disposal
            d2 = next(d for d in disposals if d.lot_id == lot2_id)
            assert Decimal(d2.quantity_allocated) == Decimal("20")
            assert Decimal(d2.realised_gain_gbp) == Decimal("60.00")
            assert Decimal(d2.realised_gain_economic_gbp) == Decimal("60.00")

            # Audit log should have entries
            from src.db.models import AuditLog
            audit_entries = list(
                sess.execute(select(AuditLog)).scalars()
            )
            assert len(audit_entries) >= 5  # 2 lot INSERTs + 1 tx INSERT + 2 lot_disposal INSERTs + 2 lot UPDATEs


# ---------------------------------------------------------------------------
# Schema integrity tests
# ---------------------------------------------------------------------------

class TestSchemaConstraints:

    def test_check_constraint_invalid_scheme_type(self, db_engine):
        """INSERT with invalid scheme_type violates CHECK constraint."""
        from sqlalchemy.exc import IntegrityError

        with pytest.raises((IntegrityError, Exception)):
            with db_engine.session() as sess:
                sec = Security(ticker="X", name="X Corp", currency="GBP", units_precision=0)
                sess.add(sec)
                sess.flush()

                bad_lot = Lot(
                    security_id=sec.id,
                    scheme_type="INVALID_SCHEME",   # not in VALID_SCHEME_TYPES
                    tax_year="2024-25",
                    acquisition_date=date(2024, 1, 1),
                    quantity="100",
                    quantity_remaining="100",
                    acquisition_price_gbp="10.00",
                    true_cost_per_share_gbp="10.00",
                )
                sess.add(bad_lot)
                # flush forces the INSERT (and the CHECK violation)
                sess.flush()

    def test_external_id_unique_constraint(self, db_engine):
        """Two lots with the same external_id are rejected (idempotency key)."""
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            with db_engine.session() as sess:
                sec = Security(ticker="DUP", name="Dup Corp", currency="GBP", units_precision=0)
                sess.add(sec)
                sess.flush()

                for _ in range(2):
                    sess.add(Lot(
                        security_id=sec.id,
                        scheme_type="RSU",
                        tax_year="2024-25",
                        acquisition_date=date(2024, 1, 1),
                        quantity="100",
                        quantity_remaining="100",
                        acquisition_price_gbp="10.00",
                        true_cost_per_share_gbp="10.00",
                        external_id="BROKER-REF-12345",  # same both times
                    ))
                sess.flush()

    def test_fk_security_not_found_raises(self, db_engine):
        """Lot references non-existent security_id → FK violation."""
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            with db_engine.session() as sess:
                bad_lot = Lot(
                    security_id="00000000-0000-0000-0000-000000000000",  # does not exist
                    scheme_type="RSU",
                    tax_year="2024-25",
                    acquisition_date=date(2024, 1, 1),
                    quantity="100",
                    quantity_remaining="100",
                    acquisition_price_gbp="10.00",
                    true_cost_per_share_gbp="10.00",
                )
                sess.add(bad_lot)
                sess.flush()


# ---------------------------------------------------------------------------
# Encrypted DB smoke test (skipped if sqlcipher3 not installed)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not SQLCIPHER_AVAILABLE,
    reason="sqlcipher3-binary not installed — skipping encrypted DB test",
)
class TestEncryptedDatabase:

    def test_create_and_reopen_encrypted_db(self, tmp_path):
        """
        Creates a new encrypted database, inserts a Security,
        disposes the engine, reopens with the same password,
        and verifies the record is readable.
        """
        db_path = tmp_path / "test_encrypted.db"
        password = "test-password-correct-horse-battery-staple"

        # Create
        engine1 = DatabaseEngine.create(db_path, password)
        with engine1.session() as sess:
            sec = Security(
                ticker="ENCTEST",
                name="Encrypted Test Corp",
                currency="GBP",
                units_precision=0,
            )
            sess.add(sec)
            sec_id = sec.id   # captured before commit (expire_on_commit=False)
        engine1.dispose()

        # Re-open with correct password
        engine2 = DatabaseEngine.open(db_path, password)
        with engine2.session() as sess:
            found = sess.get(Security, sec_id)
            assert found is not None
            assert found.ticker == "ENCTEST"
        engine2.dispose()

    def test_wrong_password_fails(self, tmp_path):
        """Opening an encrypted DB with the wrong password raises an error."""
        db_path = tmp_path / "test_wrong_pw.db"
        engine1 = DatabaseEngine.create(db_path, "correct-password")
        with engine1.session() as sess:
            sess.add(Security(ticker="T", name="T", currency="GBP", units_precision=0))
        engine1.dispose()

        engine2 = DatabaseEngine.open(db_path, "wrong-password")
        with pytest.raises(Exception):   # SQLCipher raises on first query
            with engine2.session() as sess:
                from sqlalchemy import text
                sess.execute(text("SELECT * FROM securities"))
        engine2.dispose()

    def test_salt_file_missing_raises(self, tmp_path):
        """Opening an encrypted DB without its .salt file raises FileNotFoundError."""
        db_path = tmp_path / "no_salt.db"
        # Don't call create() — no salt file will exist
        with pytest.raises(FileNotFoundError, match="Salt file not found"):
            DatabaseEngine.open(db_path, "any-password")
