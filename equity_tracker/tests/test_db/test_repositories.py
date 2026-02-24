"""
Unit tests for repository read/write extensions added in Phase 3 Step 2.

Coverage:
  - TransactionRepository : get_by_id, require_by_id, list_for_security,
                            get_by_external_id
  - AuditRepository       : list_for_record, list_all (table_name filter,
                            since filter)
  - LotRepository         : get_by_external_id
  - SecurityRepository    : update() — partial updates, no-op, audit trail
  - DisposalRepository    : list_for_security

All tests use the in-memory SQLite engine from conftest.py (fresh DB per test,
no SQLCipher required).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.db.models import Lot, Security, Transaction
from src.db.repository import (
    AuditRepository,
    DisposalRepository,
    EmploymentTaxEventRepository,
    LotRepository,
    SecurityRepository,
    TransactionRepository,
)
from src.core.lot_engine.fifo import LotForFIFO, allocate_fifo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _security(ticker: str = "ACME") -> Security:
    return Security(
        ticker=ticker,
        name=f"{ticker} Corp",
        currency="GBP",
        units_precision=0,
    )


def _lot(security_id: str, qty: str = "100", ext_id: str | None = None) -> Lot:
    return Lot(
        security_id=security_id,
        scheme_type="RSU",
        tax_year="2024-25",
        acquisition_date=date(2024, 1, 15),
        quantity=qty,
        quantity_remaining=qty,
        acquisition_price_gbp="10.00",
        true_cost_per_share_gbp="10.00",
        external_id=ext_id,
    )


def _disposal_tx(security_id: str, qty: str = "50",
                 tx_date: date = date(2024, 6, 1)) -> Transaction:
    return Transaction(
        security_id=security_id,
        transaction_type="DISPOSAL",
        transaction_date=tx_date,
        quantity=qty,
        price_per_share_gbp="15.00",
        total_proceeds_gbp=str(Decimal(qty) * Decimal("15.00")),
    )


# ---------------------------------------------------------------------------
# TransactionRepository
# ---------------------------------------------------------------------------

class TestTransactionRepository:

    def test_get_by_id_returns_transaction(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        tx = _disposal_tx(sec.id)
        session.add(tx)
        session.flush()

        repo = TransactionRepository(session)
        found = repo.get_by_id(tx.id)
        assert found is not None
        assert found.id == tx.id
        assert found.transaction_type == "DISPOSAL"

    def test_get_by_id_returns_none_for_unknown(self, session):
        repo = TransactionRepository(session)
        assert repo.get_by_id("00000000-0000-0000-0000-000000000000") is None

    def test_require_by_id_raises_key_error_for_unknown(self, session):
        repo = TransactionRepository(session)
        with pytest.raises(KeyError, match="Transaction not found"):
            repo.require_by_id("00000000-0000-0000-0000-000000000000")

    def test_list_for_security_returns_all_transactions(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        tx1 = _disposal_tx(sec.id, "50", date(2024, 3, 1))
        tx2 = _disposal_tx(sec.id, "30", date(2024, 6, 1))
        session.add_all([tx1, tx2])
        session.flush()

        repo = TransactionRepository(session)
        results = repo.list_for_security(sec.id)
        assert len(results) == 2

    def test_list_for_security_ordered_newest_first(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        tx_early = _disposal_tx(sec.id, "50", date(2024, 3, 1))
        tx_late  = _disposal_tx(sec.id, "30", date(2024, 9, 1))
        session.add_all([tx_early, tx_late])
        session.flush()

        repo = TransactionRepository(session)
        results = repo.list_for_security(sec.id)
        # Newest first
        assert results[0].id == tx_late.id
        assert results[1].id == tx_early.id

    def test_list_for_security_filtered_by_type(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        disposal = _disposal_tx(sec.id)
        dividend = Transaction(
            security_id=sec.id,
            transaction_type="DIVIDEND",
            transaction_date=date(2024, 7, 1),
            quantity="0",
            price_per_share_gbp="0",
            total_proceeds_gbp="25.00",
        )
        session.add_all([disposal, dividend])
        session.flush()

        repo = TransactionRepository(session)
        disposals_only = repo.list_for_security(sec.id, transaction_type="DISPOSAL")
        assert len(disposals_only) == 1
        assert disposals_only[0].transaction_type == "DISPOSAL"

    def test_list_for_security_returns_empty_for_unknown_security(self, session):
        repo = TransactionRepository(session)
        results = repo.list_for_security("00000000-0000-0000-0000-000000000000")
        assert results == []

    def test_list_for_security_excludes_other_securities(self, session):
        sec_a = _security("AAAA")
        sec_b = _security("BBBB")
        session.add_all([sec_a, sec_b])
        session.flush()

        session.add(_disposal_tx(sec_a.id))
        session.add(_disposal_tx(sec_b.id))
        session.flush()

        repo = TransactionRepository(session)
        assert len(repo.list_for_security(sec_a.id)) == 1
        assert len(repo.list_for_security(sec_b.id)) == 1

    def test_get_by_external_id_returns_match(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        tx = Transaction(
            security_id=sec.id,
            transaction_type="DISPOSAL",
            transaction_date=date(2024, 6, 1),
            quantity="50",
            price_per_share_gbp="15.00",
            total_proceeds_gbp="750.00",
            external_id="BROKER-TX-99",
        )
        session.add(tx)
        session.flush()

        repo = TransactionRepository(session)
        found = repo.get_by_external_id("BROKER-TX-99")
        assert found is not None
        assert found.id == tx.id

    def test_get_by_external_id_returns_none_for_unknown(self, session):
        repo = TransactionRepository(session)
        assert repo.get_by_external_id("NONEXISTENT") is None


# ---------------------------------------------------------------------------
# AuditRepository — read methods
# ---------------------------------------------------------------------------

class TestAuditRepositoryReads:

    def _setup_security_with_audit(self, session):
        """Helper: create a security with two audit entries."""
        sec = _security()
        session.add(sec)
        session.flush()
        audit = AuditRepository(session)
        audit.log_insert("securities", sec.id, {"ticker": sec.ticker})
        audit.log_update(
            "securities", sec.id,
            old_values={"name": "Old Corp"},
            new_values={"name": "ACME Corp"},
        )
        session.flush()
        return sec, audit

    def test_list_for_record_returns_entries_oldest_first(self, session):
        sec, audit = self._setup_security_with_audit(session)

        results = audit.list_for_record("securities", sec.id)
        assert len(results) == 2
        assert results[0].action == "INSERT"
        assert results[1].action == "UPDATE"

    def test_list_for_record_returns_empty_for_unknown(self, session):
        audit = AuditRepository(session)
        results = audit.list_for_record("securities", "00000000-0000-0000-0000-000000000000")
        assert results == []

    def test_list_for_record_excludes_other_records(self, session):
        sec_a = _security("AAAA")
        sec_b = _security("BBBB")
        session.add_all([sec_a, sec_b])
        session.flush()

        audit = AuditRepository(session)
        audit.log_insert("securities", sec_a.id, {"ticker": "AAAA"})
        audit.log_insert("securities", sec_b.id, {"ticker": "BBBB"})
        session.flush()

        results = audit.list_for_record("securities", sec_a.id)
        assert len(results) == 1
        assert results[0].record_id == sec_a.id

    def test_list_all_returns_newest_first(self, session):
        sec, audit = self._setup_security_with_audit(session)

        results = audit.list_all()
        assert len(results) >= 2
        # Should be newest first
        assert results[0].changed_at >= results[-1].changed_at

    def test_list_all_filtered_by_table_name(self, session):
        sec = _security()
        session.add(sec)
        session.flush()
        audit = AuditRepository(session)
        audit.log_insert("securities", sec.id, {"ticker": sec.ticker})
        audit.log_insert("lots", "fake-lot-id", {"qty": "100"})
        session.flush()

        results = audit.list_all(table_name="securities")
        assert all(r.table_name == "securities" for r in results)

        results_lots = audit.list_all(table_name="lots")
        assert all(r.table_name == "lots" for r in results_lots)

    def test_list_all_filtered_by_since(self, session):
        sec = _security()
        session.add(sec)
        session.flush()
        audit = AuditRepository(session)
        audit.log_insert("securities", sec.id, {"ticker": sec.ticker})
        session.flush()

        # Any entry "since" a future timestamp should return empty.
        # Use timezone-aware now() then strip tzinfo — audit_log stores naive UTC datetimes.
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        future = now_utc + timedelta(hours=1)
        results = audit.list_all(since=future)
        assert results == []

        # Past timestamp should include all entries
        past = now_utc - timedelta(hours=1)
        results = audit.list_all(since=past)
        assert len(results) >= 1

    def test_list_all_no_filters_returns_all(self, session):
        sec = _security()
        session.add(sec)
        session.flush()
        audit = AuditRepository(session)
        audit.log_insert("securities", sec.id, {"ticker": sec.ticker})
        audit.log_correction("lots", "fake-lot")
        session.flush()

        results = audit.list_all()
        assert len(results) >= 2


# ---------------------------------------------------------------------------
# LotRepository — get_by_external_id
# ---------------------------------------------------------------------------

class TestLotRepositoryExternalId:

    def test_get_by_external_id_returns_match(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        lot = _lot(sec.id, ext_id="ETRADE-2024-001")
        lot_repo = LotRepository(session)
        lot_repo.add(lot)
        session.flush()

        found = lot_repo.get_by_external_id("ETRADE-2024-001")
        assert found is not None
        assert found.id == lot.id

    def test_get_by_external_id_returns_none_for_unknown(self, session):
        lot_repo = LotRepository(session)
        assert lot_repo.get_by_external_id("NONEXISTENT-ID") is None

    def test_get_by_external_id_returns_none_for_null(self, session):
        """Lots with no external_id do not match any external_id query."""
        sec = _security()
        session.add(sec)
        session.flush()
        # Lot with external_id=None
        lot_repo = LotRepository(session)
        lot_repo.add(_lot(sec.id, ext_id=None))
        session.flush()

        # Should not find a lot that has no external_id
        assert lot_repo.get_by_external_id("anything") is None


# ---------------------------------------------------------------------------
# EmploymentTaxEventRepository
# ---------------------------------------------------------------------------

class TestEmploymentTaxEventRepository:

    def test_add_and_list_for_lot(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        lot = _lot(sec.id)
        LotRepository(session).add(lot)
        session.flush()

        repo = EmploymentTaxEventRepository(session)
        created = repo.add(
            lot_id=lot.id,
            security_id=sec.id,
            event_type="ESPP_PLUS_TRANSFER",
            event_date=date(2024, 6, 1),
            estimated_tax_gbp=Decimal("12.34"),
            source="ui_transfer_to_brokerage",
        )
        session.flush()

        rows = repo.list_for_lot(lot.id)
        assert len(rows) == 1
        assert rows[0].id == created.id
        assert rows[0].event_type == "ESPP_PLUS_TRANSFER"
        assert rows[0].estimated_tax_gbp == "12.34"
        assert rows[0].source == "ui_transfer_to_brokerage"

    def test_list_for_security_orders_newest_first(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        lot = _lot(sec.id)
        LotRepository(session).add(lot)
        session.flush()

        repo = EmploymentTaxEventRepository(session)
        repo.add(
            lot_id=lot.id,
            security_id=sec.id,
            event_type="ESPP_PLUS_TRANSFER",
            event_date=date(2024, 5, 1),
            estimated_tax_gbp=Decimal("1.00"),
        )
        repo.add(
            lot_id=lot.id,
            security_id=sec.id,
            event_type="ESPP_PLUS_TRANSFER",
            event_date=date(2024, 8, 1),
            estimated_tax_gbp=None,
            estimation_notes="Estimate unavailable; configure Settings.",
        )
        session.flush()

        rows = repo.list_for_security(sec.id)
        assert len(rows) == 2
        assert rows[0].event_date == date(2024, 8, 1)
        assert rows[1].event_date == date(2024, 5, 1)

# ---------------------------------------------------------------------------
# SecurityRepository — update()
# ---------------------------------------------------------------------------

class TestSecurityRepositoryUpdate:

    def test_update_name(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        repo = SecurityRepository(session)
        repo.update(sec, name="New Name Ltd")
        session.flush()

        assert sec.name == "New Name Ltd"

    def test_update_isin(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        repo = SecurityRepository(session)
        repo.update(sec, isin="GB00B16GWD56")
        assert sec.isin == "GB00B16GWD56"

    def test_update_exchange(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        repo = SecurityRepository(session)
        repo.update(sec, exchange="LSE")
        assert sec.exchange == "LSE"

    def test_update_units_precision(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        repo = SecurityRepository(session)
        repo.update(sec, units_precision=4)
        assert sec.units_precision == 4

    def test_update_multiple_fields_at_once(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        repo = SecurityRepository(session)
        repo.update(sec, name="Renamed Corp", exchange="NYSE", units_precision=2)
        assert sec.name == "Renamed Corp"
        assert sec.exchange == "NYSE"
        assert sec.units_precision == 2

    def test_update_noop_when_no_fields_change(self, session):
        sec = _security()
        session.add(sec)
        session.flush()
        # Capture AFTER flush so the INSERT default has fired.
        original_updated_at = sec.updated_at

        repo = SecurityRepository(session)
        repo.update(sec, name=sec.name)  # same name → no change
        # updated_at must remain unchanged for a no-op
        assert sec.updated_at == original_updated_at

    def test_update_writes_audit_entry(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        audit = AuditRepository(session)
        repo = SecurityRepository(session)
        repo.update(sec, name="Audited Corp", audit=audit)
        session.flush()

        entries = audit.list_for_record("securities", sec.id)
        assert any(e.action == "UPDATE" for e in entries)
        update_entry = next(e for e in entries if e.action == "UPDATE")
        import json
        new_vals = json.loads(update_entry.new_values_json)
        assert new_vals["name"] == "Audited Corp"

    def test_update_noop_does_not_write_audit_entry(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        audit = AuditRepository(session)
        repo = SecurityRepository(session)
        # Pass same name → nothing changes → no audit entry
        repo.update(sec, name=sec.name, audit=audit)
        session.flush()

        entries = audit.list_for_record("securities", sec.id)
        assert not any(e.action == "UPDATE" for e in entries)


# ---------------------------------------------------------------------------
# DisposalRepository — list_for_security
# ---------------------------------------------------------------------------

class TestDisposalRepositoryListForSecurity:

    def _make_disposal(self, session, security_id: str,
                       qty: str = "50", tx_date: date = date(2024, 6, 1)):
        """
        Create a lot, run a FIFO disposal through it, and persist.
        Returns (lot, transaction, [lot_disposal]).
        """
        lot_repo  = LotRepository(session)
        disp_repo = DisposalRepository(session)

        lot = _lot(security_id, qty=qty)
        lot_repo.add(lot)
        session.flush()

        fifo_input = [LotForFIFO(
            lot_id=lot.id,
            acquisition_date=lot.acquisition_date,
            quantity_remaining=Decimal(lot.quantity_remaining),
            acquisition_price_gbp=Decimal(lot.acquisition_price_gbp),
            true_cost_per_share_gbp=Decimal(lot.true_cost_per_share_gbp),
        )]
        fifo_result = allocate_fifo(fifo_input, Decimal(qty), Decimal("15.00"))

        lots_by_id = {lot.id: lot}
        tx = _disposal_tx(security_id, qty, tx_date)
        disposals = disp_repo.record_disposal_from_fifo(tx, fifo_result, lots_by_id)
        session.flush()
        return lot, tx, disposals

    def test_list_returns_disposals_for_security(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        _, _, disposals = self._make_disposal(session, sec.id)

        repo = DisposalRepository(session)
        results = repo.list_for_security(sec.id)
        assert len(results) == len(disposals)
        assert results[0].id == disposals[0].id

    def test_list_returns_empty_for_security_with_no_disposals(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        repo = DisposalRepository(session)
        assert repo.list_for_security(sec.id) == []

    def test_list_excludes_other_security_disposals(self, session):
        sec_a = _security("AAAA")
        sec_b = _security("BBBB")
        session.add_all([sec_a, sec_b])
        session.flush()

        self._make_disposal(session, sec_a.id, qty="50")
        self._make_disposal(session, sec_b.id, qty="60")

        repo = DisposalRepository(session)
        a_results = repo.list_for_security(sec_a.id)
        b_results = repo.list_for_security(sec_b.id)

        assert len(a_results) == 1
        assert len(b_results) == 1
        # Ensure no cross-contamination
        a_lot_ids = {d.lot_id for d in a_results}
        b_lot_ids = {d.lot_id for d in b_results}
        assert a_lot_ids.isdisjoint(b_lot_ids)

    def test_list_multiple_disposals_ordered_newest_first(self, session):
        sec = _security()
        session.add(sec)
        session.flush()

        # Two separate disposals on different dates (need separate lots each time)
        self._make_disposal(session, sec.id, qty="30", tx_date=date(2024, 3, 1))
        self._make_disposal(session, sec.id, qty="20", tx_date=date(2024, 9, 1))

        repo = DisposalRepository(session)
        results = repo.list_for_security(sec.id)
        assert len(results) == 2

        # Get the associated transactions to check ordering
        tx_repo = TransactionRepository(session)
        txs = tx_repo.list_for_security(sec.id)
        # Transactions newest first: Sep > Mar
        assert txs[0].transaction_date == date(2024, 9, 1)
        assert txs[1].transaction_date == date(2024, 3, 1)
