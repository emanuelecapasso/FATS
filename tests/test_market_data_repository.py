
import sqlite3
from datetime import UTC, datetime

import pytest

from core.schemas import Timeframe
from database.market_data_repository import MarketDataRepository


def snapshot_for_provider(snapshot, provider_id: str, close: float):
    timeframe, series = next(iter(snapshot.series.items()))

    updated_bar = series.bars[0].model_copy(
        update={"close": close}
    )

    updated_provenance = series.provenance.model_copy(
        update={"provider_id": provider_id}
    )

    updated_series = series.model_copy(
        update={
            "bars": [updated_bar],
            "provenance": updated_provenance,
        }
    )

    return snapshot.model_copy(
        update={"series": {timeframe: updated_series}}
    )


def test_same_timestamp_can_be_saved_for_different_providers(
    tmp_path,
    market_snapshot,
):
    repository = MarketDataRepository(tmp_path / "market.db")

    first = snapshot_for_provider(
        market_snapshot,
        "broker_a",
        1.1050,
    )
    second = snapshot_for_provider(
        market_snapshot,
        "broker_b",
        1.1060,
    )

    assert repository.save_snapshot(first) == 1
    assert repository.save_snapshot(second) == 1

    timeframe = next(iter(market_snapshot.series))
    bar = next(iter(market_snapshot.series.values())).bars[0]
    start = bar.timestamp
    end = datetime.fromtimestamp(
        bar.timestamp.timestamp() + 3600,
        tz=UTC,
    )

    broker_a_bars = repository.load_bars(
        market_snapshot.pair,
        timeframe,
        start,
        end,
        provider_id="broker_a",
    )
    broker_b_bars = repository.load_bars(
        market_snapshot.pair,
        timeframe,
        start,
        end,
        provider_id="broker_b",
    )

    assert len(broker_a_bars) == 1
    assert len(broker_b_bars) == 1
    assert broker_a_bars[0].close == pytest.approx(1.1050)
    assert broker_b_bars[0].close == pytest.approx(1.1060)


def test_upsert_only_updates_the_selected_provider(
    tmp_path,
    market_snapshot,
):
    repository = MarketDataRepository(tmp_path / "market.db")

    first = snapshot_for_provider(
        market_snapshot,
        "broker_a",
        1.1050,
    )
    second = snapshot_for_provider(
        market_snapshot,
        "broker_b",
        1.1060,
    )
    updated_first = snapshot_for_provider(
        market_snapshot,
        "broker_a",
        1.1070,
    )

    repository.save_snapshot(first)
    repository.save_snapshot(second)
    repository.save_snapshot(updated_first)

    timeframe = next(iter(market_snapshot.series))
    bar = next(iter(market_snapshot.series.values())).bars[0]
    start = bar.timestamp
    end = datetime.fromtimestamp(
        bar.timestamp.timestamp() + 3600,
        tz=UTC,
    )

    broker_a_bars = repository.load_bars(
        market_snapshot.pair,
        timeframe,
        start,
        end,
        provider_id="broker_a",
    )
    broker_b_bars = repository.load_bars(
        market_snapshot.pair,
        timeframe,
        start,
        end,
        provider_id="broker_b",
    )

    assert broker_a_bars[0].close == pytest.approx(1.1070)
    assert broker_b_bars[0].close == pytest.approx(1.1060)


def test_provider_is_required_when_multiple_providers_exist(
    tmp_path,
    market_snapshot,
):
    repository = MarketDataRepository(tmp_path / "market.db")

    repository.save_snapshot(
        snapshot_for_provider(market_snapshot, "broker_a", 1.1050)
    )
    repository.save_snapshot(
        snapshot_for_provider(market_snapshot, "broker_b", 1.1060)
    )

    timeframe = next(iter(market_snapshot.series))
    bar = next(iter(market_snapshot.series.values())).bars[0]
    start = bar.timestamp
    end = datetime.fromtimestamp(
        bar.timestamp.timestamp() + 3600,
        tz=UTC,
    )

    with pytest.raises(ValueError, match="provider_id is required"):
        repository.load_bars(
            market_snapshot.pair,
            timeframe,
            start,
            end,
        )


def test_provider_can_be_omitted_when_only_one_exists(
    tmp_path,
    market_snapshot,
):
    repository = MarketDataRepository(tmp_path / "market.db")
    repository.save_snapshot(market_snapshot)

    timeframe = next(iter(market_snapshot.series))
    bar = next(iter(market_snapshot.series.values())).bars[0]
    start = bar.timestamp
    end = datetime.fromtimestamp(
        bar.timestamp.timestamp() + 3600,
        tz=UTC,
    )

    result = repository.load_bars(
        market_snapshot.pair,
        timeframe,
        start,
        end,
    )

    assert len(result) == 1


def test_repository_migrates_legacy_primary_key_without_data_loss(
    tmp_path,
):
    database_path = tmp_path / "legacy.db"
    timestamp = "2026-01-01T10:00:00+00:00"

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE market_bars (
                pair TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                source_kind TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                received_at TEXT NOT NULL,
                quality TEXT NOT NULL,
                PRIMARY KEY (pair, timeframe, timestamp)
            )
            """
        )

        connection.execute(
            """
            INSERT INTO market_bars (
                pair, timeframe, timestamp,
                open, high, low, close, volume,
                source_kind, provider_id, received_at, quality
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "EURUSD",
                "H1",
                timestamp,
                1.10,
                1.11,
                1.09,
                1.105,
                100.0,
                "external",
                "legacy_broker",
                timestamp,
                "complete",
            ),
        )

    repository = MarketDataRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = connection.execute(
            "PRAGMA table_info(market_bars)"
        ).fetchall()

    primary_key = [
        column[1]
        for column in sorted(
            (column for column in columns if column[5] > 0),
            key=lambda column: column[5],
        )
    ]

    assert primary_key == [
        "pair",
        "timeframe",
        "timestamp",
        "provider_id",
    ]

    result = repository.load_bars(
        "EURUSD",
        Timeframe.H1,
        datetime(2026, 1, 1, 9, tzinfo=UTC),
        datetime(2026, 1, 1, 11, tzinfo=UTC),
        provider_id="legacy_broker",
    )

    assert len(result) == 1
    assert result[0].close == pytest.approx(1.105)
