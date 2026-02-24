"""
Database package — encrypted SQLite (SQLCipher) persistence layer.

Public interface:
    from src.db.engine import DatabaseEngine
    from src.db.models import Base, Security, Grant, Lot, Transaction, LotDisposal, ...
    from src.db.repository import SecurityRepository, LotRepository, DisposalRepository, AuditRepository
"""
