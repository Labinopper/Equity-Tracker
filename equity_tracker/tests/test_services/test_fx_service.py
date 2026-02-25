from __future__ import annotations

from decimal import Decimal

import pytest

from src.services.fx_service import FxService
from src.services.sheets_fx_service import FxRow


def _rates(*rows: FxRow) -> dict[str, FxRow]:
    return {row.pair.upper(): row for row in rows}


def test_get_rate_direct_pair():
    quote = FxService.get_rate(
        "USD",
        "GBP",
        rates=_rates(
            FxRow(pair="USD2GBP", rate=Decimal("0.7900"), as_of="2026-02-25 09:00:00")
        ),
    )
    assert quote.rate == Decimal("0.7900")
    assert quote.path == ("USD2GBP",)
    assert quote.as_of == "2026-02-25 09:00:00"


def test_get_rate_inverse_pair():
    quote = FxService.get_rate(
        "USD",
        "GBP",
        rates=_rates(
            FxRow(pair="GBP2USD", rate=Decimal("1.2500"), as_of="2026-02-25 09:00:00")
        ),
    )
    assert quote.rate == Decimal("0.8")
    assert quote.path == ("GBP2USD",)


def test_get_rate_multi_hop_path():
    quote = FxService.get_rate(
        "JPY",
        "GBP",
        rates=_rates(
            FxRow(pair="JPY2USD", rate=Decimal("0.0070"), as_of="2026-02-25 09:00:00"),
            FxRow(pair="USD2GBP", rate=Decimal("0.7900"), as_of="2026-02-25 09:01:00"),
        ),
    )
    assert quote.rate == Decimal("0.00553000")
    assert quote.path == ("JPY2USD", "USD2GBP")


def test_get_rate_missing_pair_raises():
    with pytest.raises(RuntimeError, match="USD2GBP"):
        FxService.get_rate("USD", "GBP", rates={})
