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

class IBKRAdapter:
    """
    Adapter per Interactive Brokers (Client Portal API / TWS).
    Specializzato su dati MIDPOINT per Forex.
    """
    def __init__(self) -> None:
        # IBKR di solito usa un gateway locale, ma lo configuriamo tramite ENV
        self.base_url = os.environ.get("IBKR_BASE_URL", "https://localhost:5000")
        self.provider_id = "IBKR"

    async def _fetch_raw_data(self, pair: str, timeframe: Timeframe) -> list[dict]:
        """
        Metodo protetto per la chiamata HTTP. 
        Da mockare nei test o implementare con aiohttp in produzione.
        """
        raise NotImplementedError("Implementare chiamata HTTP reale al Client Portal API")

    async def fetch_series(self, pair: str, timeframe: Timeframe) -> TimeframeSeries:
        raw_data = await self._fetch_raw_data(pair, timeframe)
        bars = []
        
        for item in raw_data:
            bars.append(
                MarketBar(
                    timestamp=item["timestamp"],
                    open=float(item["open"]),
                    high=float(item["high"]),
                    low=float(item["low"]),
                    close=float(item["close"]),
                    volume=None,  # MIDPOINT non ha volume
                    volume_type=VolumeType.UNAVAILABLE
                )
            )

        return TimeframeSeries(
            timeframe=timeframe,
            bars=bars,
            provenance=DataProvenance(
                kind=DataSourceKind.BROKER,
                provider_id=self.provider_id,
                received_at=datetime.now(UTC),
                price_type="MIDPOINT"
            ),
            quality=DataQuality.COMPLETE if bars else DataQuality.MISSING
        )

    async def get_snapshot(self, pair: str, timeframes: list[Timeframe]) -> MarketDataSnapshot:
        series_dict = {}
        for tf in timeframes:
            series = await self.fetch_series(pair, tf)
            series_dict[tf] = series
            
        return MarketDataSnapshot(
            pair=pair,
            as_of=datetime.now(UTC),
            series=series_dict
        )