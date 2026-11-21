import asyncio
import logging
from datetime import datetime, timedelta

from core.schemas import MarketDataSnapshot, Timeframe, TimeframeSeries
from data.exceptions import DataError
from data.ports import MarketDataAdapter
from data.timeframes import align_timeframes

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
        """
        series_dict: dict[Timeframe, TimeframeSeries] = {}

        # Per abilitare il fallback di aggregazione definito in align_timeframes, 
        # assicuriamoci di recuperare sempre M5 se servono timeframe superiori.
        fetch_timeframes = set(timeframes)
        if Timeframe.M15 in fetch_timeframes or Timeframe.H1 in fetch_timeframes:
            fetch_timeframes.add(Timeframe.M5)

        # Recupero parallelo
        tasks = [
            self._fetch_single_timeframe(pair, tf, as_of, lookback_bars, fetch_timeframes)
            for tf in fetch_timeframes
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for tf, result in zip(fetch_timeframes, results):
            if isinstance(result, Exception):
                logger.error(f"Impossibile recuperare la serie {tf.value} per {pair}")
                raise result
            series_dict[tf] = result

        # Costruisce lo snapshot grezzo contenente tutto ciò che è stato scaricato
        raw_snapshot = MarketDataSnapshot(
            pair=pair,
            as_of=as_of,
            series=series_dict,
        )

        # Applica l'allineamento puro e la logica di fallback / aggregazione
        return align_timeframes(raw_snapshot, timeframes)

    async def _fetch_single_timeframe(
        self, pair: str, tf: Timeframe, as_of: datetime, lookback: int, all_requested: set[Timeframe]
    ) -> TimeframeSeries:
        """Recupera la singola serie applicando la logica di fallback se necessario."""
        start_time = self._calculate_start_time(as_of, tf, lookback, all_requested)

        try:
            return await self.primary.fetch_series(pair, tf, start_time, as_of)
        except DataError as primary_error:
            if self.fallback is None:
                logger.error(f"Errore adapter primario su {tf.value} e nessun fallback configurato.")
                raise

            logger.warning(
                f"Errore adapter primario su {tf.value} ({primary_error}). Tentativo di fallback."
            )
            return await self.fallback.fetch_series(pair, tf, start_time, as_of)

    def _calculate_start_time(
        self, as_of: datetime, tf: Timeframe, lookback: int, all_requested: set[Timeframe]
    ) -> datetime:
        """Calcola uno start_time sicuro aggiungendo buffer e allineando i lookback per l'aggregazione."""
        minutes_map = {
            Timeframe.M5: 5,
            Timeframe.M15: 15,
            Timeframe.H1: 60,
        }
        
        # Se stiamo scaricando M5 ma abbiamo richiesto timeframe più ampi, 
        # calcoliamo i minuti basandoci sul timeframe massimo richiesto 
        # in modo da avere abbastanza candele M5 per aggregarle.
        if tf == Timeframe.M5:
            max_requested_tf = max(all_requested, key=lambda t: minutes_map[t])
            base_minutes = minutes_map[max_requested_tf]
        else:
            base_minutes = minutes_map[tf]
        
        # Moltiplicatore 2.0 per buchi fisiologici (es. weekend)
        total_minutes = int(base_minutes * lookback * 2.0)
        return as_of - timedelta(minutes=total_minutes)