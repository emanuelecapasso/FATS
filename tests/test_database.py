
"""Tests for historical market data persistence."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from database.market_data_repository import MarketDataRepository


def test_repository_saves_and_loads_snapshot(market_snapshot, tmp_path: Path):
    repository = MarketDataRepository(tmp_path / "test.db")
    timeframe = next(iter(market_snapshot.series))
    expected_bars = market_snapshot.series[timeframe].bars

    saved = repository.save_snapshot(market_snapshot)

    loaded = repository.load_bars(
        pair=market_snapshot.pair,
        timeframe=timeframe,
        start=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        end=datetime(2026, 1, 2, 0, 0, tzinfo=UTC),
    )

    assert saved == len(expected_bars)
    assert loaded == expected_bars


def test_repository_upserts_duplicate_bars(market_snapshot, tmp_path: Path):
    repository = MarketDataRepository(tmp_path / "test.db")
    timeframe = next(iter(market_snapshot.series))

    repository.save_snapshot(market_snapshot)
    repository.save_snapshot(market_snapshot)

    loaded = repository.load_bars(
        pair=market_snapshot.pair,
        timeframe=timeframe,
        start=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        end=datetime(2026, 1, 2, 0, 0, tzinfo=UTC),
    )

    assert len(loaded) == len(market_snapshot.series[timeframe].bars)


def test_repository_rejects_non_utc_range(market_snapshot, tmp_path: Path):
    repository = MarketDataRepository(tmp_path / "test.db")
    timeframe = next(iter(market_snapshot.series))

    with pytest.raises(ValueError, match="UTC-aware"):
        repository.load_bars(
            pair=market_snapshot.pair,
            timeframe=timeframe,
            start=datetime(2026, 1, 1, 0, 0),
            end=datetime(2026, 1, 2, 0, 0, tzinfo=UTC),
        )


def test_repository_rejects_invalid_range(market_snapshot, tmp_path: Path):
    repository = MarketDataRepository(tmp_path / "test.db")
    timeframe = next(iter(market_snapshot.series))
    timestamp = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="earlier than end"):
        repository.load_bars(
            pair=market_snapshot.pair,
            timeframe=timeframe,
            start=timestamp,
            end=timestamp,
        )
