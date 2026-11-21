from datetime import datetime
from typing import Protocol, runtime_checkable

from core.schemas import Timeframe, TimeframeSeries


@runtime_checkable
class MarketDataAdapter(Protocol):
    """
    Protocollo comune per gli adapter di dati di mercato (broker ed esterni).

    Ogni adapter deve astrarre i dettagli dell'SDK o del trasporto sottostante,
    normalizzando l'output nei tipi canonici definiti in `core.schemas`:
    barre OHLCV chiuse in UTC, `DataProvenance` e `DataQuality`.
    """

    async def fetch_series(
        self,
        pair: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> TimeframeSeries:
        """
        Recupera una serie storica di barre normalizzate per una singola coppia e timeframe.

        Args:
            pair: Identificativo canonico dello strumento (es. "EUR_USD").
            timeframe: Timeframe richiesto (M5, M15, H1).
            start: Timestamp UTC timezone-aware di inizio intervallo.
            end: Timestamp UTC timezone-aware di fine intervallo (limite destro as_of).

        Returns:
            Un oggetto `TimeframeSeries` contenente barre canoniche, provenienza e qualità.
        """
        ...