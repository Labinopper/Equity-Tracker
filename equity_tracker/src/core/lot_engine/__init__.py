"""
Lot engine — pure allocation logic (no I/O, no DB calls).

Public interface:
    from src.core.lot_engine import (
        LotForFIFO,
        FIFOAllocation,
        FIFOResult,
        allocate_fifo,
        allocate_uk_share_matching,
    )
"""

from .fifo import FIFOAllocation, FIFOResult, LotForFIFO, allocate_fifo
from .uk_matching import allocate_uk_share_matching

__all__ = [
    "LotForFIFO",
    "FIFOAllocation",
    "FIFOResult",
    "allocate_fifo",
    "allocate_uk_share_matching",
]
