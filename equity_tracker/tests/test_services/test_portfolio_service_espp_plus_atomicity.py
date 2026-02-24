from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from src.app_context import AppContext
from src.db.repository import AuditRepository
from src.db.repository.lots import LotRepository
from src.services import PortfolioService


def _add_basic_security(ticker: str = "EPLAT", name: str = "ESPP+ Atomic") -> object:
    return PortfolioService.add_security(ticker, name, "GBP", is_manual_override=True)


def test_add_espp_plus_lot_pair_creates_employee_and_matched_lots(app_context):
    sec = _add_basic_security("EPLATOM")
    acq = date(2025, 1, 15)

    employee, matched = PortfolioService.add_espp_plus_lot_pair(
        security_id=sec.id,
        acquisition_date=acq,
        employee_quantity=Decimal("10"),
        employee_acquisition_price_gbp=Decimal("5.00"),
        employee_true_cost_per_share_gbp=Decimal("5.00"),
        employee_fmv_at_acquisition_gbp=Decimal("7.00"),
        matched_quantity=Decimal("2"),
        original_currency="GBP",
        employee_import_source="ui_espp_plus_employee",
        notes="atomic test",
        forfeiture_period_end=acq + timedelta(days=183),
    )

    assert matched is not None
    assert matched.matching_lot_id == employee.id

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(sec.id)

    assert len(lots) == 2
    by_id = {lot.id: lot for lot in lots}
    assert by_id[employee.id].scheme_type == "ESPP_PLUS"
    assert by_id[matched.id].scheme_type == "ESPP_PLUS"
    assert by_id[matched.id].matching_lot_id == employee.id


def test_add_espp_plus_lot_pair_rolls_back_when_matched_insert_fails(app_context, monkeypatch):
    sec = _add_basic_security("EPLRB")
    acq = date(2025, 1, 15)

    original_add = LotRepository.add

    def _failing_add(self, lot, audit=None):
        if lot.matching_lot_id is not None:
            raise IntegrityError("forced matched lot failure", None, Exception("forced"))
        return original_add(self, lot, audit=audit)

    monkeypatch.setattr(LotRepository, "add", _failing_add)

    with pytest.raises(IntegrityError):
        PortfolioService.add_espp_plus_lot_pair(
            security_id=sec.id,
            acquisition_date=acq,
            employee_quantity=Decimal("10"),
            employee_acquisition_price_gbp=Decimal("5.00"),
            employee_true_cost_per_share_gbp=Decimal("5.00"),
            employee_fmv_at_acquisition_gbp=Decimal("7.00"),
            matched_quantity=Decimal("2"),
            original_currency="GBP",
            employee_import_source="ui_espp_plus_employee",
            notes="atomic test rollback",
            forfeiture_period_end=acq + timedelta(days=183),
        )

    with AppContext.read_session() as sess:
        lots = LotRepository(sess).get_all_lots_for_security(sec.id)
        audit_rows = AuditRepository(sess).list_all(table_name="lots")

    assert lots == []
    assert audit_rows == []
