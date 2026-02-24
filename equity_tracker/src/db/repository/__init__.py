"""
Repository layer — session-scoped data access objects.

Each repository is constructed with a SQLAlchemy Session.
Transaction management (commit/rollback) belongs to the caller (service layer or test).

Usage:
    with engine.session() as sess:
        sec_repo  = SecurityRepository(sess)
        lot_repo  = LotRepository(sess)
        tx_repo   = TransactionRepository(sess)
        disp_repo = DisposalRepository(sess)
        audit     = AuditRepository(sess)

        security = sec_repo.add(Security(...))
        # session auto-commits on context-manager exit
"""

from .audit import AuditRepository
from .catalog import SecurityCatalogRepository
from .disposals import DisposalRepository
from .employment_tax_events import EmploymentTaxEventRepository
from .lots import LotRepository
from .prices import PriceRepository
from .securities import SecurityRepository
from .transactions import TransactionRepository

__all__ = [
    "AuditRepository",
    "SecurityCatalogRepository",
    "DisposalRepository",
    "EmploymentTaxEventRepository",
    "LotRepository",
    "PriceRepository",
    "SecurityRepository",
    "TransactionRepository",
]
