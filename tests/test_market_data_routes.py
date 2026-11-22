
"""Tests for historical market data API routes."""

from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from backend.app import create_app
from core.schemas import MarketBar, Timeframe
from data.market_calendar import ForexMarketCalendar
from database.market_data_repository import MarketDataRepository


def test_get_market_data_route(market_snapshot, tmp_path):
    repository = MarketDataRepository(tmp_path / "test.db")
    repository.save_snapshot(market_snapshot)

    client = TestClient(create_app(market_data_storage=repository))

    response = client.get(
        "/api/v1/market-data/EURUSD",
        params={
            "timeframe": "H1",
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-01-02T00:00:00+00:00",
        },
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["close"] == 1.11


def test_get_market_data_summary_route(market_snapshot, tmp_path):
    repository = MarketDataRepository(tmp_path / "test.db")
    repository.save_snapshot(market_snapshot)

    client = TestClient(create_app(market_data_storage=repository))

    response = client.get(
        "/api/v1/market-data/EURUSD/summary",
        params={
            "timeframe": "H1",
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-01-02T00:00:00+00:00",
        },
    )

    assert response.status_code == 200
    assert response.json()["pair"] == "EURUSD"
    assert response.json()["count"] == 1
    assert response.json()["first_timestamp"] is not None


def test_get_market_data_rejects_invalid_pair(tmp_path):
    repository = MarketDataRepository(tmp_path / "test.db")
    client = TestClient(create_app(market_data_storage=repository))

    response = client.get(
        "/api/v1/market-data/INVALID",
        params={
            "timeframe": "H1",
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-01-02T00:00:00+00:00",
        },
    )

    assert response.status_code == 422


def test_get_market_data_quality_route_detects_gaps(
    market_snapshot,
    tmp_path,
):
    repository = MarketDataRepository(tmp_path / "test.db")

    original_series = market_snapshot.series[Timeframe.H1]

    second_bar = MarketBar(
        timestamp=datetime(2026, 1, 1, 13, tzinfo=UTC),
        open=1.11,
        high=1.13,
        low=1.10,
        close=1.12,
        volume=120.0,
    )

    updated_series = original_series.model_copy(
        update={
            "bars": [original_series.bars[0], second_bar],
        }
    )

    updated_snapshot = market_snapshot.model_copy(
        update={
            "as_of": second_bar.timestamp,
            "series": {
                Timeframe.H1: updated_series,
            },
        }
    )

    repository.save_snapshot(updated_snapshot)

    client = TestClient(create_app(market_data_storage=repository))

    response = client.get(
        "/api/v1/market-data/EURUSD/quality",
        params={
            "timeframe": "H1",
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-01-02T00:00:00+00:00",
        },
    )

    assert response.status_code == 200

    result = response.json()

    assert result["pair"] == "EURUSD"
    assert result["timeframe"] == "H1"
    assert result["candles_checked"] == 2
    assert result["total_missing_candles"] == 2
    assert result["is_complete"] is False
    assert len(result["gaps"]) == 1
    assert result["gaps"][0]["missing_candles"] == 2


def test_get_market_data_quality_route_empty_history_is_not_complete(
    tmp_path,
):
    repository = MarketDataRepository(tmp_path / "test.db")
    client = TestClient(create_app(market_data_storage=repository))

    response = client.get(
        "/api/v1/market-data/EURUSD/quality",
        params={
            "timeframe": "H1",
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-01-02T00:00:00+00:00",
        },
    )

    assert response.status_code == 200

    result = response.json()

    assert result["candles_checked"] == 0
    assert result["total_missing_candles"] == 0
    assert result["is_complete"] is False
    assert result["gaps"] == []



def test_quality_route_uses_injected_market_calendar(
    market_snapshot,
    tmp_path,
):
    repository = MarketDataRepository(tmp_path / "test.db")

    original_series = market_snapshot.series[Timeframe.H1]

    second_bar = MarketBar(
        timestamp=datetime(2026, 1, 1, 13, tzinfo=UTC),
        open=1.11,
        high=1.13,
        low=1.10,
        close=1.12,
        volume=120.0,
    )

    updated_series = original_series.model_copy(
        update={
            "bars": [original_series.bars[0], second_bar],
        }
    )

    updated_snapshot = market_snapshot.model_copy(
        update={
            "as_of": second_bar.timestamp,
            "series": {
                Timeframe.H1: updated_series,
            },
        }
    )

    repository.save_snapshot(updated_snapshot)

    calendar = ForexMarketCalendar(
        closed_dates=frozenset({date(2026, 1, 1)}),
    )

    client = TestClient(
        create_app(
            market_data_storage=repository,
            market_calendar=calendar,
        )
    )

    response = client.get(
        "/api/v1/market-data/EURUSD/quality",
        params={
            "timeframe": "H1",
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-01-02T00:00:00+00:00",
        },
    )

    assert response.status_code == 200

    result = response.json()

    assert result["candles_checked"] == 2
    assert result["total_missing_candles"] == 0
    assert result["is_complete"] is True


def test_quality_route_uses_default_calendar_when_not_configured(
    market_snapshot,
    tmp_path,
):
    repository = MarketDataRepository(tmp_path / "test.db")

    original_series = market_snapshot.series[Timeframe.H1]

    second_bar = MarketBar(
        timestamp=datetime(2026, 1, 1, 13, tzinfo=UTC),
        open=1.11,
        high=1.13,
        low=1.10,
        close=1.12,
        volume=120.0,
    )

    updated_series = original_series.model_copy(
        update={
            "bars": [original_series.bars[0], second_bar],
        }
    )

    updated_snapshot = market_snapshot.model_copy(
        update={
            "as_of": second_bar.timestamp,
            "series": {
                Timeframe.H1: updated_series,
            },
        }
    )

    repository.save_snapshot(updated_snapshot)

    client = TestClient(
        create_app(market_data_storage=repository),
    )

    response = client.get(
        "/api/v1/market-data/EURUSD/quality",
        params={
            "timeframe": "H1",
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-01-02T00:00:00+00:00",
        },
    )

    assert response.status_code == 200

    result = response.json()

    assert result["candles_checked"] == 2
    assert result["total_missing_candles"] == 2
    assert result["is_complete"] is False
