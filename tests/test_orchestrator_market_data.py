
"""Tests for market data orchestration."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from agents.orchestrator import FATSOrchestrator
from agents.services.market_data import MarketDataService
from core.schemas import Timeframe


@pytest.mark.anyio
async def test_orchestrator_delegates_market_data_request():
    evaluator = AsyncMock()
    market_data_service = AsyncMock(spec=MarketDataService)
    expected_snapshot = object()
    market_data_service.get_snapshot.return_value = expected_snapshot

    orchestrator = FATSOrchestrator(
        evaluator=evaluator,
        market_data_service=market_data_service,
    )

    as_of = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    result = await orchestrator.get_market_snapshot(
        pair="EUR/USD",
        timeframes=(Timeframe.H1,),
        as_of=as_of,
    )

    assert result is expected_snapshot
    market_data_service.get_snapshot.assert_awaited_once_with(
        pair="EUR/USD",
        timeframes=(Timeframe.H1,),
        as_of=as_of,
    )


@pytest.mark.anyio
async def test_orchestrator_rejects_missing_market_data_service():
    orchestrator = FATSOrchestrator(evaluator=AsyncMock())

    with pytest.raises(RuntimeError, match="not configured"):
        await orchestrator.get_market_snapshot(
            pair="EURUSD",
            timeframes=(Timeframe.H1,),
            as_of=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        )
