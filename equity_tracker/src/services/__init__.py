"""
Application service layer.

Services sit between the UI/AppContext and the repository layer.
They own business-level orchestration (FIFO → persist, aggregate portfolio data,
build reports) while keeping each unit independently testable.

Usage:
    # Read
    summary = PortfolioService.get_portfolio_summary()
    report  = ReportService.cgt_summary("2024-25", tax_context=ctx)

    # Write (AppContext must be initialised first)
    security = PortfolioService.add_security("AAPL", "Apple Inc", "USD")
    lot      = PortfolioService.add_lot(security.id, ...)
    tx, disposals = PortfolioService.commit_disposal(security.id, ...)
"""

from .portfolio_service import (
    LotSummary,
    PortfolioService,
    PortfolioSummary,
    SecuritySummary,
)
from .dividend_service import DividendService
from .report_service import (
    CgtSummaryReport,
    DisposalLine,
    EconomicGainReport,
    ReportService,
)
from .validation_report_service import ValidationReportService
from .strategic_service import StrategicService

__all__ = [
    # PortfolioService
    "PortfolioService",
    "PortfolioSummary",
    "SecuritySummary",
    "LotSummary",
    "DividendService",
    # ReportService
    "ReportService",
    "CgtSummaryReport",
    "EconomicGainReport",
    "DisposalLine",
    # Validation report
    "ValidationReportService",
    "StrategicService",
]
