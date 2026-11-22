from collections import defaultdict
from datetime import timedelta

from core.schemas import (
    DataQuality,
    MarketBar,
    MarketDataSnapshot,
    Timeframe,
    TimeframeSeries,
    VolumeType,
)
from data.exceptions import InvalidDataError


def _clean_bars(bars: list[MarketBar], as_of) -> list[MarketBar]:
    unique_bars = {}
    for b in bars:
        if b.timestamp > as_of:
            continue

        if b.timestamp in unique_bars:
            existing = unique_bars[b.timestamp]
            if (
                existing.open != b.open
                or existing.high != b.high
                or existing.low != b.low
                or existing.close != b.close
                or existing.volume != b.volume
                or existing.volume_type != b.volume_type
            ):
                raise InvalidDataError(
                    f"Dati conflittuali per il timestamp {b.timestamp}"
                )
        else:
            unique_bars[b.timestamp] = b

    return sorted(unique_bars.values(), key=lambda x: x.timestamp)


def _get_target_timestamp(ts, target_tf: Timeframe):
    """Calcola il timestamp di chiusura del timeframe target partendo dalla chiusura della barra sorgente."""
    shifted = ts - timedelta(minutes=1)
    if target_tf == Timeframe.M15:
        base_minute = (shifted.minute // 15) * 15
        base = shifted.replace(minute=base_minute, second=0, microsecond=0)
        return base + timedelta(minutes=15)
    elif target_tf == Timeframe.H1:
        base = shifted.replace(minute=0, second=0, microsecond=0)
        return base + timedelta(hours=1)
    return ts


def _aggregate(
    bars: list[MarketBar], target_tf: Timeframe
) -> tuple[list[MarketBar], DataQuality]:
    if not bars:
        return [], DataQuality.MISSING

    required_count = 3 if target_tf == Timeframe.M15 else 12
    chunks = defaultdict(list)

    for b in bars:
        target_ts = _get_target_timestamp(b.timestamp, target_tf)
        chunks[target_ts].append(b)

    agg_bars = []
    quality = DataQuality.COMPLETE

    for target_ts in sorted(chunks.keys()):
        chunk = chunks[target_ts]
        if len(chunk) < required_count:
            quality = DataQuality.PARTIAL
            continue

        chunk_sorted = sorted(chunk, key=lambda x: x.timestamp)

        vol_types = {b.volume_type for b in chunk_sorted}
        volumes = [b.volume for b in chunk_sorted]

        if (
            len(vol_types) == 1
            and VolumeType.UNAVAILABLE not in vol_types
            and all(v is not None for v in volumes)
        ):
            agg_volume = sum(volumes)
            agg_vol_type = next(iter(vol_types))
        else:
            agg_volume = None
            agg_vol_type = VolumeType.UNAVAILABLE

        agg_bars.append(
            MarketBar(
                timestamp=target_ts,
                open=chunk_sorted[0].open,
                high=max(b.high for b in chunk_sorted),
                low=min(b.low for b in chunk_sorted),
                close=chunk_sorted[-1].close,
                volume=agg_volume,
                volume_type=agg_vol_type,
            )
        )

    if not agg_bars:
        return [], DataQuality.MISSING

    return agg_bars, quality


def align_timeframes(
    snapshot: MarketDataSnapshot, requested_timeframes: list[Timeframe]
) -> MarketDataSnapshot:
    """Allinea temporalmente, pulisce e, se necessario, aggrega le serie richieste."""
    aligned_series = {}

    # Prima passata: pulizia di tutte le serie ricevute nel raw snapshot
    for tf, series in snapshot.series.items():
        cleaned_bars = _clean_bars(series.bars, snapshot.as_of)
        quality = series.quality if cleaned_bars else DataQuality.MISSING

        aligned_series[tf] = TimeframeSeries(
            timeframe=tf,
            bars=cleaned_bars,
            provenance=series.provenance,
            quality=quality,
        )

    final_series = {}

    # Seconda passata: generazione dell'output e calcolo fallback
    for tf in requested_timeframes:
        if tf in aligned_series and aligned_series[tf].quality == DataQuality.COMPLETE:
            final_series[tf] = aligned_series[tf]
        else:
            # Se la serie target è mancante o incompleta, tenta l'aggregazione da M5
            if tf in [Timeframe.M15, Timeframe.H1] and Timeframe.M5 in aligned_series:
                m5_series = aligned_series[Timeframe.M5]
                agg_bars, agg_quality = _aggregate(m5_series.bars, tf)

                final_series[tf] = TimeframeSeries(
                    timeframe=tf,
                    bars=agg_bars,
                    provenance=m5_series.provenance,  # Eredita la provenienza della fonte usata
                    quality=agg_quality,
                )
            elif tf in aligned_series:
                # Se non è aggregabile, mantieni la serie parziale/mancante originale
                final_series[tf] = aligned_series[tf]

    return MarketDataSnapshot(
        pair=snapshot.pair,
        as_of=snapshot.as_of,
        series=final_series,
    )
