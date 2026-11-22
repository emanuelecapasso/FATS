
from datetime import datetime
from typing import Protocol

from core.schemas import MarketBar, MarketDataSnapshot, Timeframe


class MarketDataProvider(Protocol):
    async def fetch_snapshot(
        self,
        pair: str,
        timeframes: tuple[Timeframe, ...],
        as_of: datetime,
    ) -> MarketDataSnapshot:
        ...


class MarketDataStorage(Protocol):
    def save_snapshot(self, snapshot: MarketDataSnapshot) -> int:
        ...

    def load_bars(
        self,
        pair: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        provider_id: str | None = None,
    ) -> list[MarketBar]:
        ...
