import logging
import os
from datetime import UTC, datetime
from typing import Any

from core.schemas import (
    DataProvenance,
    DataQuality,
    DataSourceKind,
    MarketBar,
    Timeframe,
    TimeframeSeries,
    VolumeType,
)
from data.exceptions import (
    InvalidDataError,
    ProviderError,
)
from data.ports import MarketDataAdapter

logger = logging.getLogger(__name__)


class BrokerAdapter(MarketDataAdapter):
    """
    Adapter per il provider di broker primario.
    Gestisce l'autenticazione, la mappatura dei simboli e la normalizzazione OHLCV.
    """

    def __init__(self) -> None:
        self._api_key = os.getenv("BROKER_API_KEY")
        self._base_url = os.getenv("BROKER_BASE_URL", "https://api.fake-broker.com")
        
        if not self._api_key:
            logger.warning("BROKER_API_KEY non configurata. L'adapter fallirà al fetch.")

        self._timeframe_map = {
            Timeframe.M5: "5m",
            Timeframe.M15: "15m",
            Timeframe.H1: "1h",
        }

    def _map_pair(self, pair: str) -> str:
        return pair.replace("_", "/")

    async def fetch_series(
        self,
        pair: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> TimeframeSeries:
        broker_symbol = self._map_pair(pair)
        broker_tf = self._timeframe_map[timeframe]

        logger.debug(f"Richiesta dati broker per {broker_symbol} a {broker_tf}")
        
        raw_payload = self._mock_http_fetch(broker_symbol, broker_tf, start, end)

        bars = []
        try:
            for item in raw_payload:
                ts = datetime.fromisoformat(item["timestamp"])
                
                if not (start <= ts <= end):
                    continue

                bars.append(
                    MarketBar(
                        timestamp=ts,
                        open=float(item["o"]),
                        high=float(item["h"]),
                        low=float(item["l"]),
                        close=float(item["c"]),
                        volume=None,
                        volume_type=VolumeType.UNAVAILABLE 
                    )
                )
        except (KeyError, ValueError) as e:
            logger.exception("Dati broker malformati")
            raise InvalidDataError(f"Errore parsing barra broker: {e!s}") from e

        bars.sort(key=lambda b: b.timestamp)

        provenance = DataProvenance(
            kind=DataSourceKind.BROKER,
            provider_id="primary_broker",
            received_at=datetime.now(tz=UTC),
        )

        return TimeframeSeries(
            timeframe=timeframe,
            bars=bars,
            provenance=provenance,
            quality=DataQuality.COMPLETE, 
        )

    def _mock_http_fetch(self, sym: str, tf: str, s: datetime, e: datetime) -> list[dict[str, Any]]:
        """Stub per simulare il payload grezzo del broker."""
        if not self._api_key:
            raise ProviderError("Manca API_KEY del broker.")
        return []