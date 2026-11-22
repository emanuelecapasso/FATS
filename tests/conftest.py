
from datetime import UTC, datetime

import pytest

from core.schemas import (
    DataProvenance,
    DataQuality,
    DataSourceKind,
    MarketBar,
    MarketDataSnapshot,
    Timeframe,
    TimeframeSeries,
)


@pytest.fixture
def market_snapshot():
    timestamp = datetime(2026, 1, 1, 10, tzinfo=UTC)

    return MarketDataSnapshot(
        pair="EURUSD",
        as_of=timestamp,
        series={
            Timeframe.H1: TimeframeSeries(
                timeframe=Timeframe.H1,
                bars=[
                    MarketBar(
                        timestamp=timestamp,
                        open=1.10,
                        high=1.12,
                        low=1.09,
                        close=1.11,
                        volume=100.0,
                    )
                ],
                provenance=DataProvenance(
                    kind=DataSourceKind.EXTERNAL,
                    provider_id="mock",
                    received_at=timestamp,
                ),
                quality=DataQuality.COMPLETE,
            )
        },
    )
