
"""Tests for the market data service."""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from agents.orchestrator import FATSOrchestrator
from agents.providers.mock_market_data import MockMarketDataProvider
from agents.services import MarketDataService
from agents.services.scanner import ScannerService
from core.schemas import (
    Timeframe,
)
from database.market_data_repository import MarketDataRepository


@pytest.mark.anyio
async def test_get_snapshot_success(market_snapshot):
    provider = AsyncMock()
    provider.fetch_snapshot.return_value = market_snapshot

    service = MarketDataService(provider)
    result = await service.get_snapshot(
        "eur/usd",
        (Timeframe.H1,),
        market_snapshot.as_of,
    )

    assert result == market_snapshot
    provider.fetch_snapshot.assert_awaited_once()


@pytest.mark.anyio
async def test_rejects_invalid_pair():
    service = MarketDataService(AsyncMock())

    with pytest.raises(ValueError, match="Invalid Forex pair"):
        await service.get_snapshot(
            "INVALID",
            (Timeframe.H1,),
            datetime.now(UTC),
        )


@pytest.mark.anyio
async def test_rejects_empty_timeframes():
    service = MarketDataService(AsyncMock())

    with pytest.raises(ValueError, match="At least one timeframe"):
        await service.get_snapshot(
            "EURUSD",
            (),
            datetime.now(UTC),
        )


@pytest.mark.anyio
async def test_rejects_duplicate_timeframes():
    service = MarketDataService(AsyncMock())

    with pytest.raises(ValueError, match="Duplicate timeframes"):
        await service.get_snapshot(
            "EURUSD",
            (Timeframe.H1, Timeframe.H1),
            datetime.now(UTC),
        )


@pytest.mark.anyio
async def test_rejects_wrong_pair(market_snapshot):
    provider = AsyncMock()
    provider.fetch_snapshot.return_value = market_snapshot

    service = MarketDataService(provider)

    with pytest.raises(ValueError, match="different Forex pair"):
        await service.get_snapshot(
            "GBPUSD",
            (Timeframe.H1,),
            market_snapshot.as_of,
        )


@pytest.mark.anyio
async def test_rejects_future_snapshot(market_snapshot):
    provider = AsyncMock()
    provider.fetch_snapshot.return_value = market_snapshot

    service = MarketDataService(provider)

    with pytest.raises(ValueError, match="after the requested timestamp"):
        await service.get_snapshot(
            "EURUSD",
            (Timeframe.H1,),
            datetime(2025, 12, 31, tzinfo=UTC),
        )


@pytest.mark.anyio
async def test_rejects_missing_timeframe(market_snapshot):
    provider = AsyncMock()
    provider.fetch_snapshot.return_value = market_snapshot

    service = MarketDataService(provider)

    with pytest.raises(ValueError, match="incomplete timeframe"):
        await service.get_snapshot(
            "EURUSD",
            (Timeframe.M5,),
            market_snapshot.as_of,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "as_of",
    [
        datetime(2026, 1, 1, 10, 0),  # Timestamp senza timezone
        datetime(
            2026, 1, 1, 11, 0,
            tzinfo=timezone(timedelta(hours=1)),
        ),  # Timestamp con offset non UTC
    ],
)
async def test_rejects_non_utc_requested_timestamp(as_of):
    provider = AsyncMock()
    service = MarketDataService(provider)

    with pytest.raises(ValueError, match="must be UTC-aware"):
        await service.get_snapshot(
            pair="EURUSD",
            timeframes=(Timeframe.H1,),
            as_of=as_of,
        )

    provider.fetch_snapshot.assert_not_awaited()


@pytest.mark.anyio
async def test_mock_provider_returns_configured_snapshot(market_snapshot):
    provider = MockMarketDataProvider({"EURUSD": market_snapshot})

    result = await provider.fetch_snapshot(
        pair="EUR/USD",
        timeframes=tuple(market_snapshot.series),
        as_of=datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
    )

    assert result.pair == "EURUSD"
    assert result.series == market_snapshot.series
    assert result.as_of == market_snapshot.as_of


@pytest.mark.anyio
async def test_mock_provider_rejects_unknown_pair(market_snapshot):
    provider = MockMarketDataProvider({"EURUSD": market_snapshot})

    with pytest.raises(ValueError, match="No mock snapshot"):
        await provider.fetch_snapshot(
            pair="GBPUSD",
            timeframes=tuple(market_snapshot.series),
            as_of=datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
        )


@pytest.mark.anyio
async def test_mock_provider_rejects_future_snapshot(market_snapshot):
    provider = MockMarketDataProvider({"EURUSD": market_snapshot})

    with pytest.raises(ValueError, match="newer than"):
        await provider.fetch_snapshot(
            pair="EURUSD",
            timeframes=tuple(market_snapshot.series),
            as_of=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        )


@pytest.mark.anyio
async def test_market_data_pipeline_end_to_end(market_snapshot):
    scanner = ScannerService()
    provider = MockMarketDataProvider({"EURUSD": market_snapshot})
    market_data_service = MarketDataService(provider)

    orchestrator = FATSOrchestrator(
        evaluator=AsyncMock(),
        market_data_service=market_data_service,
    )

    pairs = scanner.scan(["EUR/USD", "INVALID", "GBP/USD"])

    assert pairs == ["EURUSD", "GBPUSD"]

    snapshot = await orchestrator.get_market_snapshot(
        pair=pairs[0],
        timeframes=tuple(market_snapshot.series),
        as_of=datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
    )

    assert snapshot.pair == "EURUSD"
    assert snapshot.series == market_snapshot.series


@pytest.mark.anyio
async def test_market_data_service_persists_snapshot(market_snapshot, tmp_path):
    provider = MockMarketDataProvider({"EURUSD": market_snapshot})
    repository = MarketDataRepository(tmp_path / "test.db")
    service = MarketDataService(provider, storage=repository)

    result = await service.get_snapshot(
        pair="EURUSD",
        timeframes=(Timeframe.H1,),
        as_of=datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
    )

    loaded = repository.load_bars(
        pair="EURUSD",
        timeframe=Timeframe.H1,
        start=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        end=datetime(2026, 1, 2, 0, 0, tzinfo=UTC),
    )

    assert result == market_snapshot
    assert loaded == market_snapshot.series[Timeframe.H1].bars
