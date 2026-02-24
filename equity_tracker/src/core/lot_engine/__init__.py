"""
Lot engine — pure FIFO allocation logic (no I/O, no DB calls).

Public interface:
    from src.core.lot_engine import LotForFIFO, FIFOAllocation, FIFOResult, allocate_fifo
"""

from .fifo import FIFOAllocation, FIFOResult, LotForFIFO, allocate_fifo

__all__ = [
    "LotForFIFO",
    "FIFOAllocation",
    "FIFOResult",
    "allocate_fifo",
]
