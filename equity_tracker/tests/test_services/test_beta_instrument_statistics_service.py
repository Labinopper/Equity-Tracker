from __future__ import annotations

import pytest
from sqlalchemy import select

from src.beta.context import BetaContext
from src.beta.db.bootstrap import ensure_beta_schema
from src.beta.db.engine import BetaDatabaseEngine
from src.beta.db.models import BetaInstrument
from src.beta.services.instrument_statistics_service import BetaInstrumentStatisticsService


@pytest.fixture()
def beta_context():
    engine = BetaDatabaseEngine.open_in_memory()
    BetaContext.initialize(engine)
    ensure_beta_schema(engine)
    yield
    BetaContext.lock()


def test_refresh_stale_statistics_caps_failed_fetch_attempts(beta_context, monkeypatch):
    with BetaContext.write_session() as sess:
        sess.add_all(
            [
                BetaInstrument(symbol="AAA", name="AAA", market="US", exchange="NASDAQ", currency="USD"),
                BetaInstrument(symbol="BBB", name="BBB", market="US", exchange="NASDAQ", currency="USD"),
                BetaInstrument(symbol="CCC", name="CCC", market="US", exchange="NASDAQ", currency="USD"),
            ]
        )

    calls: list[str] = []

    monkeypatch.setattr(
        "src.beta.services.instrument_statistics_service._api_key",
        lambda: "test-key",
    )

    def _fail_fetch(symbol: str, api_key: str) -> dict:
        calls.append(symbol)
        raise RuntimeError("boom")

    monkeypatch.setattr(BetaInstrumentStatisticsService, "_fetch", staticmethod(_fail_fetch))

    result = BetaInstrumentStatisticsService.refresh_stale_statistics(credits_budget=2)

    with BetaContext.read_session() as sess:
        instrument_count = len(list(sess.scalars(select(BetaInstrument)).all()))

    assert instrument_count == 3
    assert len(calls) == 2
    assert result["refreshed"] == 0
    assert result["credits_used"] == 0
    assert result["fetch_attempts"] == 2
    assert result["fetch_failures"] == 2
