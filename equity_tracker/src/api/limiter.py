"""
Shared slowapi rate-limiter instance.

Imported by app.py (middleware wiring) and any router that needs
per-endpoint rate limiting (e.g. auth_router, admin).

Key function used for identifying callers: remote IP address.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
