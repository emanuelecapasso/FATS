import logging
import os
from datetime import UTC, datetime

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

logger = logging.getLogger(__name__)


class TwelveDataAdapter:
    """
    Adapter per Twelve Data API.
    Fornisce pricing di mercato (assenza di volume per il mercato valutario).
    """

    def __init__(self) -> None:
        self.api_key = os.environ.get("TWELVE_DATA_API_KEY")
        if not self.api_key:
            logger.warning("TWELVE_DATA_API_KEY non impostata nell'ambiente.")

        self.base_url = "https://api.twelvedata.com"
        self.provider_id = "TwelveData"

    async def _fetch_raw_data(self, pair: str, timeframe: Timeframe) -> dict:
        """
        Ritorna un dizionario con i metadati (incluso price_type) e la lista delle barre.
        Da mockare nei test.
        """
        raise NotImplementedError(
            "Implementare chiamata HTTP reale verso api.twelvedata.com"
        )

    async def fetch_series(self, pair: str, timeframe: Timeframe) -> TimeframeSeries:
        payload = await self._fetch_raw_data(pair, timeframe)

        # Estrarre la tipologia di prezzo dichiarata dal provider
        price_type = payload.get("meta", {}).get("price_type", "TRADED")
        raw_bars = payload.get("values", [])

        bars = []
        for item in raw_bars:
            bars.append(
                MarketBar(
                    timestamp=item["timestamp"],
                    open=float(item["open"]),
                    high=float(item["high"]),
                    low=float(item["low"]),
                    close=float(item["close"]),
                    volume=None,
                    volume_type=VolumeType.UNAVAILABLE,
                )
            )

        return TimeframeSeries(
            timeframe=timeframe,
            bars=bars,
            provenance=DataProvenance(
                kind=DataSourceKind.EXTERNAL,
                provider_id=self.provider_id,
                received_at=datetime.now(UTC),
                price_type=price_type,
            ),
            quality=DataQuality.COMPLETE if bars else DataQuality.MISSING,
        )

    async def get_snapshot(
        self, pair: str, timeframes: list[Timeframe]
    ) -> MarketDataSnapshot:
        series_dict = {}
        for tf in timeframes:
            series = await self.fetch_series(pair, tf)
            series_dict[tf] = series

        return MarketDataSnapshot(
            pair=pair, as_of=datetime.now(UTC), series=series_dict
        )
