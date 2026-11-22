
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from backend.app import create_app
from core.schemas import Timeframe
from data.broker_config import BrokerConfig
from data.csv_importer import CSVMarketDataImporter
from database.broker_repository import BrokerRepository
from database.market_data_repository import MarketDataRepository


def make_provider_snapshot(snapshot, provider_id: str, close: float):
    timeframe, series = next(iter(snapshot.series.items()))

    bar = series.bars[0].model_copy(update={"close": close})
    provenance = series.provenance.model_copy(
        update={"provider_id": provider_id}
    )
    updated_series = series.model_copy(
        update={
            "bars": [bar],
            "provenance": provenance,
        }
    )

    return snapshot.model_copy(
        update={"series": {timeframe: updated_series}}
    )


def test_csv_import_supports_custom_provider(tmp_path):
    database_path = tmp_path / "market.db"
    csv_path = tmp_path / "history.csv"

    csv_path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-01T10:00:00Z,1.10,1.12,1.09,1.11,100\n",
        encoding="utf-8",
    )

    repository = MarketDataRepository(database_path)
    importer = CSVMarketDataImporter(repository)

    result = importer.import_file(
        file_path=csv_path,
        pair="EUR/USD",
        timeframe=Timeframe.H1,
        provider_id="broker_a",
    )

    bars = repository.load_bars(
        pair="EURUSD",
        timeframe=Timeframe.H1,
        start=datetime(2026, 1, 1, 9, tzinfo=UTC),
        end=datetime(2026, 1, 1, 11, tzinfo=UTC),
        provider_id="broker_a",
    )

    assert result.bars_imported == 1
    assert len(bars) == 1
    assert bars[0].close == 1.11


def test_market_data_api_filters_by_provider(
    tmp_path,
    market_snapshot,
):
    database_path = tmp_path / "market.db"
    storage = MarketDataRepository(database_path)

    storage.save_snapshot(
        make_provider_snapshot(market_snapshot, "broker_a", 1.1050)
    )
    storage.save_snapshot(
        make_provider_snapshot(market_snapshot, "broker_b", 1.1060)
    )

    app = create_app(market_data_storage=storage)
    client = TestClient(app)

    bar = next(iter(market_snapshot.series.values())).bars[0]
    start = bar.timestamp.isoformat()
    end = (bar.timestamp + timedelta(hours=1)).isoformat()

    response_a = client.get(
        f"/api/v1/market-data/{market_snapshot.pair}",
        params={
            "timeframe": "H1",
            "start": start,
            "end": end,
            "provider_id": "broker_a",
        },
    )

    response_b = client.get(
        f"/api/v1/market-data/{market_snapshot.pair}",
        params={
            "timeframe": "H1",
            "start": start,
            "end": end,
            "provider_id": "broker_b",
        },
    )

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert response_a.json()[0]["close"] == 1.1050
    assert response_b.json()[0]["close"] == 1.1060


def test_market_data_summary_filters_by_provider(
    tmp_path,
    market_snapshot,
):
    storage = MarketDataRepository(tmp_path / "market.db")

    storage.save_snapshot(
        make_provider_snapshot(market_snapshot, "broker_a", 1.1050)
    )
    storage.save_snapshot(
        make_provider_snapshot(market_snapshot, "broker_b", 1.1060)
    )

    client = TestClient(create_app(market_data_storage=storage))

    bar = next(iter(market_snapshot.series.values())).bars[0]

    response = client.get(
        f"/api/v1/market-data/{market_snapshot.pair}/summary",
        params={
            "timeframe": "H1",
            "start": bar.timestamp.isoformat(),
            "end": (bar.timestamp + timedelta(hours=1)).isoformat(),
            "provider_id": "broker_a",
        },
    )

    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_quality_api_uses_registered_broker_calendar(
    tmp_path,
    market_snapshot,
):
    database_path = tmp_path / "market.db"

    storage = MarketDataRepository(database_path)
    brokers = BrokerRepository(database_path)

    storage.save_snapshot(
        make_provider_snapshot(market_snapshot, "broker_a", 1.1050)
    )

    brokers.save(
        BrokerConfig(
            provider_id="broker_a",
            name="Broker A",
            timezone="Europe/London",
        )
    )

    client = TestClient(
        create_app(
            market_data_storage=storage,
            broker_repository=brokers,
        )
    )

    bar = next(iter(market_snapshot.series.values())).bars[0]

    params = {
        "timeframe": "H1",
        "start": bar.timestamp.isoformat(),
        "end": (bar.timestamp + timedelta(hours=1)).isoformat(),
        "provider_id": "broker_a",
    }

    response = client.get(
        f"/api/v1/market-data/{market_snapshot.pair}/quality",
        params=params,
    )

    assert response.status_code == 200
    assert response.json()["candles_checked"] == 1


def test_quality_api_rejects_unknown_broker(
    tmp_path,
    market_snapshot,
):
    storage = MarketDataRepository(tmp_path / "market.db")
    client = TestClient(create_app(market_data_storage=storage))

    bar = next(iter(market_snapshot.series.values())).bars[0]

    response = client.get(
        f"/api/v1/market-data/{market_snapshot.pair}/quality",
        params={
            "timeframe": "H1",
            "start": bar.timestamp.isoformat(),
            "end": (bar.timestamp + timedelta(hours=1)).isoformat(),
            "provider_id": "unknown_broker",
        },
    )

    assert response.status_code == 404
