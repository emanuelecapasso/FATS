
"""In-memory mock provider for controlled market data snapshots."""

from collections.abc import Mapping
from datetime import datetime

from core.schemas import MarketDataSnapshot, Timeframe


class MockMarketDataProvider:
    """Return predefined snapshots for development and automated tests."""

    def __init__(
        self,
        snapshots: Mapping[str, MarketDataSnapshot],
    ) -> None:
        self._snapshots = {
            pair.strip().upper().replace("/", ""): snapshot
            for pair, snapshot in snapshots.items()
        }

    async def fetch_snapshot(
        self,
        pair: str,
        timeframes: tuple[Timeframe, ...],
        as_of: datetime,
    ) -> MarketDataSnapshot:
        """Return the requested data from a predefined snapshot."""
        normalized_pair = pair.strip().upper().replace("/", "")

        if normalized_pair not in self._snapshots:
            raise ValueError(f"No mock snapshot configured for {normalized_pair}.")

        snapshot = self._snapshots[normalized_pair]

        if snapshot.as_of > as_of:
            raise ValueError("Mock snapshot is newer than the requested timestamp.")

        missing = set(timeframes) - set(snapshot.series)
        if missing:
            raise ValueError(f"Mock snapshot is missing timeframes: {missing}.")

        return MarketDataSnapshot(
            pair=normalized_pair,
            as_of=snapshot.as_of,
            series={
                timeframe: snapshot.series[timeframe]
                for timeframe in timeframes
            },
        )
