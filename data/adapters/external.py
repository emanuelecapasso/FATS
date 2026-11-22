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
from data.exceptions import InvalidDataError, ProviderError
from data.ports import MarketDataAdapter

logger = logging.getLogger(__name__)


class ExternalDataAdapter(MarketDataAdapter):
    """
    Adapter per una fonte dati esterna (es. API storiche, aggregatori crypto).
    Usato per backtesting o fallback in produzione.
    """

    def __init__(self) -> None:
        self._api_key = os.getenv("EXTERNAL_API_KEY")

        self._timeframe_map = {
            Timeframe.M5: "5Min",
            Timeframe.M15: "15Min",
            Timeframe.H1: "1Hour",
        }

    def _map_pair(self, pair: str) -> str:
        return pair.replace("_", "")

    async def fetch_series(
        self,
        pair: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> TimeframeSeries:
        ext_symbol = self._map_pair(pair)
        ext_tf = self._timeframe_map[timeframe]

        logger.debug(f"Richiesta dati esterni per {ext_symbol} a {ext_tf}")

        raw_payload = self._mock_http_fetch(ext_symbol, ext_tf, start, end)

        bars = []
        try:
            for item in raw_payload:
                ts = datetime.fromtimestamp(item["t"], tz=UTC)

                if not (start <= ts <= end):
                    continue

                bars.append(
                    MarketBar(
                        timestamp=ts,
                        open=float(item["o"]),  # (o item["open"] nell'external)
                        high=float(item["h"]),
                        low=float(item["l"]),
                        close=float(item["c"]),
                        volume=float(item["v"]),  # (o item["vol"] nell'external)
                        volume_type=VolumeType.TRADED,
                    )
                )
        except (KeyError, ValueError, TypeError) as e:
            logger.exception("Dati esterni malformati")
            raise InvalidDataError(f"Errore parsing barra esterna: {e!s}") from e

        bars.sort(key=lambda b: b.timestamp)

        provenance = DataProvenance(
            kind=DataSourceKind.EXTERNAL,
            provider_id="historical_feed_v1",
            received_at=datetime.now(tz=UTC),
        )

        return TimeframeSeries(
            timeframe=timeframe,
            bars=bars,
            provenance=provenance,
            quality=DataQuality.COMPLETE,
        )

    def _mock_http_fetch(
        self, sym: str, tf: str, s: datetime, e: datetime
    ) -> list[dict[str, Any]]:
        """Stub per simulare il payload grezzo esterno."""
        if not self._api_key:
            raise ProviderError("Manca API_KEY del provider esterno.")
        return []
