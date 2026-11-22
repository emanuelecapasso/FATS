
"""Application service for market data acquisition."""

from datetime import datetime, timedelta

from agents.ports.market_data import MarketDataProvider, MarketDataStorage
from core.schemas import MarketDataSnapshot, Timeframe


class MarketDataService:
    """Coordinate market data acquisition, validation and persistence."""

    def __init__(
        self,
        provider: MarketDataProvider,
        storage: MarketDataStorage | None = None,
    ) -> None:
        self._provider = provider
        self._storage = storage

    async def get_snapshot(
        self,
        pair: str,
        timeframes: tuple[Timeframe, ...],
        as_of: datetime,
    ) -> MarketDataSnapshot:
        """Retrieve, validate and optionally persist a market snapshot."""
        normalized_pair = pair.strip().upper().replace("/", "")

        if (
            len(normalized_pair) != 6
            or not normalized_pair.isalpha()
            or normalized_pair[:3] == normalized_pair[3:]
        ):
            raise ValueError("Invalid Forex pair.")

        if not timeframes:
            raise ValueError("At least one timeframe is required.")

        if len(set(timeframes)) != len(timeframes):
            raise ValueError("Duplicate timeframes are not allowed.")

        if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
            raise ValueError("The requested timestamp must be UTC-aware.")

        snapshot = await self._provider.fetch_snapshot(
            pair=normalized_pair,
            timeframes=timeframes,
            as_of=as_of,
        )

        if snapshot.pair != normalized_pair:
            raise ValueError("Provider returned a different Forex pair.")

        if snapshot.as_of > as_of:
            raise ValueError(
                "Provider returned data after the requested timestamp."
            )

        if set(snapshot.series) != set(timeframes):
            raise ValueError("Provider returned an incomplete timeframe set.")

        if self._storage is not None:
            self._storage.save_snapshot(snapshot)

        return snapshot
