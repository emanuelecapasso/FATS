import sqlite3
from datetime import UTC, datetime, timedelta

from core.schemas import (
    DataProvenance,
    DataQuality,
    DataSourceKind,
    MarketBar,
    MarketDataSnapshot,
    Timeframe,
    TimeframeSeries,
    VolumeType,
)
from database.market_data_repository import MarketDataRepository

T0 = datetime(2026, 1, 12, 10, 0, tzinfo=UTC)
END = T0 + timedelta(hours=1)


def _snapshot(bars: list[MarketBar]) -> MarketDataSnapshot:
    return MarketDataSnapshot(
        pair="EURUSD",
        as_of=T0 + timedelta(minutes=10),
        series={
            Timeframe.M5: TimeframeSeries(
                timeframe=Timeframe.M5,
                bars=bars,
                provenance=DataProvenance(
                    kind=DataSourceKind.BROKER,
                    provider_id="test",
                    received_at=T0 + timedelta(minutes=10),
                ),
                quality=DataQuality.COMPLETE,
            )
        },
    )


def test_roundtrip_preserves_volume_type_and_closed_state(tmp_path):
    repository = MarketDataRepository(tmp_path / "fats.db")
    bars = [
        MarketBar(
            timestamp=T0,
            open=1.1,
            high=1.2,
            low=1.0,
            close=1.15,
            volume=None,
            is_closed=True,
        ),
        MarketBar(
            timestamp=T0 + timedelta(minutes=5),
            open=1.15,
            high=1.2,
            low=1.1,
            close=1.18,
            volume=42.0,
            volume_type=VolumeType.TICK_COUNT,
            is_closed=False,
        ),
    ]

    repository.save_snapshot(_snapshot(bars))
    loaded = repository.load_bars("EURUSD", Timeframe.M5, T0, END, "test")

    assert loaded == bars


def test_legacy_table_is_migrated_without_losing_rows(tmp_path):
    db_path = tmp_path / "fats.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE market_bars (
            pair TEXT NOT NULL, timeframe TEXT NOT NULL,
            timestamp TEXT NOT NULL, open REAL NOT NULL,
            high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
            volume REAL NOT NULL, source_kind TEXT NOT NULL,
            provider_id TEXT NOT NULL, received_at TEXT NOT NULL,
            quality TEXT NOT NULL,
            PRIMARY KEY (pair, timeframe, timestamp, provider_id)
        )
        """
    )
    connection.execute(
        "INSERT INTO market_bars VALUES "
        "('EURUSD','M5',?,1.1,1.2,1.0,1.15,7.0,'broker','test',?,'complete')",
        (T0.isoformat(), T0.isoformat()),
    )
    connection.commit()
    connection.close()

    repository = MarketDataRepository(db_path)
    loaded = repository.load_bars("EURUSD", Timeframe.M5, T0, END, "test")

    assert len(loaded) == 1
    assert loaded[0].volume == 7.0
    assert loaded[0].volume_type == VolumeType.TRADED
    assert loaded[0].is_closed is None


def test_initialization_is_idempotent(tmp_path):
    db_path = tmp_path / "fats.db"
    MarketDataRepository(db_path)
    MarketDataRepository(db_path)
