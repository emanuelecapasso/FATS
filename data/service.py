import asyncio
import logging
from datetime import datetime, timedelta

from core.schemas import MarketDataSnapshot, Timeframe, TimeframeSeries
from data.exceptions import DataError
from data.ports import MarketDataAdapter

logger = logging.getLogger(__name__)


class MarketDataService:
    """
    Servizio di composizione che orchestra il recupero dei dati storici.
    Costruisce un MarketDataSnapshot richiedendo le serie necessarie agli adapter.
    Implementa una logica di fallback esplicita, senza mai mescolare dati di 
    provider diversi all'interno dello stesso timeframe.
    """

    def __init__(
        self, 
        primary_adapter: MarketDataAdapter, 
        fallback_adapter: MarketDataAdapter | None = None
    ) -> None:
        self.primary = primary_adapter
        self.fallback = fallback_adapter

    async def build_snapshot(
        self,
        pair: str,
        as_of: datetime,
        timeframes: list[Timeframe],
        lookback_bars: int = 100,
    ) -> MarketDataSnapshot:
        """
        Recupera le serie per i timeframe richiesti e compone uno snapshot allineato.

        Args:
            pair: Coppia di mercato (es. EUR_USD).
            as_of: Limite destro del recupero (timestamp UTC).
            timeframes: Elenco di timeframe necessari (es. M5, M15, H1).
            lookback_bars: Stima del numero di barre necessarie per calcolare 
                           una data di inizio sufficiente.

        Returns:
            MarketDataSnapshot completo e validato.

        Raises:
            DataError: Se anche l'adapter di fallback fallisce nel recupero di una serie.
        """
        series_dict: dict[Timeframe, TimeframeSeries] = {}

        # Recupero parallelo per tutti i timeframe richiesti
        tasks = [
            self._fetch_single_timeframe(pair, tf, as_of, lookback_bars)
            for tf in timeframes
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for tf, result in zip(timeframes, results):
            if isinstance(result, Exception):
                logger.error(f"Impossibile recuperare la serie {tf.value} per {pair}")
                raise result  # Rilancia l'eccezione (es. DataError) fermando il processo
            series_dict[tf] = result

        return MarketDataSnapshot(
            pair=pair,
            as_of=as_of,
            series=series_dict,
        )

    async def _fetch_single_timeframe(
        self, pair: str, tf: Timeframe, as_of: datetime, lookback: int
    ) -> TimeframeSeries:
        """Recupera la singola serie applicando la logica di fallback se necessario."""
        start_time = self._calculate_start_time(as_of, tf, lookback)

        try:
            return await self.primary.fetch_series(pair, tf, start_time, as_of)
        except DataError as primary_error:
            if self.fallback is None:
                logger.error(f"Errore adapter primario su {tf.value} e nessun fallback configurato.")
                raise

            logger.warning(
                f"Errore adapter primario su {tf.value} ({primary_error}). "
                "Tentativo con adapter di fallback."
            )
            
            # Se anche il fallback fallisce, l'eccezione sale e fa fallire il compose
            return await self.fallback.fetch_series(pair, tf, start_time, as_of)

    def _calculate_start_time(self, as_of: datetime, tf: Timeframe, lookback: int) -> datetime:
        """Calcola un start_time sicuro aggiungendo un buffer temporale per i weekend/chiusure."""
        minutes_map = {
            Timeframe.M5: 5,
            Timeframe.M15: 15,
            Timeframe.H1: 60,
        }
        minutes = minutes_map[tf]
        
        # Moltiplicatore 2.0 come buffer per buchi fisiologici del mercato (es. weekend)
        total_minutes = int(minutes * lookback * 2.0)
        return as_of - timedelta(minutes=total_minutes)