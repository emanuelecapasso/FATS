import random
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

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
from data.exceptions import InvalidDataError
from data.timeframes import align_timeframes


def _create_bar(dt_str, o, h, l, c, v=100.0, vt=VolumeType.TRADED):
    return MarketBar(
        timestamp=datetime.fromisoformat(dt_str).replace(tzinfo=UTC),
        open=o, high=h, low=l, close=c, volume=v, volume_type=vt
    )


@pytest.fixture
def base_provenance():
    return DataProvenance(
        kind=DataSourceKind.BROKER,
        provider_id="TEST",
        received_at=datetime.now(UTC)
    )


def test_snapshot_rejects_future_bars(base_provenance):
    """Verifica che la validazione Pydantic blocchi a monte le barre nel futuro."""
    as_of = datetime.fromisoformat("2026-11-21T10:15:00").replace(tzinfo=UTC)
    
    bars = [
        _create_bar("2026-11-21T10:15:00", 2.0, 3.0, 1.5, 2.5),
        _create_bar("2026-11-21T10:20:00", 2.5, 3.5, 2.0, 3.0),
    ]
    
    series = TimeframeSeries(
        timeframe=Timeframe.M5, bars=bars, provenance=base_provenance, quality=DataQuality.COMPLETE
    )
    
    with pytest.raises(ValidationError):
        MarketDataSnapshot(pair="EUR/USD", as_of=as_of, series={Timeframe.M5: series})


def test_align_timeframes_filters_lookahead(base_provenance):
    """Verifica il filtro look-ahead (futuro) direttamente in align_timeframes mutando l'as_of."""
    bars = [
        _create_bar("2026-11-21T10:10:00", 1.5, 2.5, 1.0, 2.0),
        _create_bar("2026-11-21T10:15:00", 2.0, 3.0, 1.5, 2.5),
    ]
    series = TimeframeSeries(
        timeframe=Timeframe.M5, bars=bars, provenance=base_provenance, quality=DataQuality.COMPLETE
    )
    
    # Snapshot valido al momento della creazione
    snapshot = MarketDataSnapshot(
        pair="EUR/USD", 
        as_of=datetime.fromisoformat("2026-11-21T10:15:00").replace(tzinfo=UTC), 
        series={Timeframe.M5: series}
    )
    
    # Mutazione manuale per simulare un as_of retrodatato o disallineato
    snapshot.as_of = datetime.fromisoformat("2026-11-21T10:10:00").replace(tzinfo=UTC)
    
    aligned = align_timeframes(snapshot, [Timeframe.M5])
    res_bars = aligned.series[Timeframe.M5].bars
    
    assert len(res_bars) == 1
    assert res_bars[0].timestamp == snapshot.as_of


def test_align_timeframes_sorts_out_of_order_bars(base_provenance):
    """Verifica l'ordinamento deterministico se l'adapter restituisce barre disordinate."""
    bars = [
        _create_bar("2026-11-21T10:15:00", 3.0, 4.0, 2.0, 3.5),
        _create_bar("2026-11-21T10:05:00", 1.0, 2.0, 0.5, 1.5),
        _create_bar("2026-11-21T10:10:00", 2.0, 3.0, 1.0, 2.5),
    ]
    random.shuffle(bars)
    
    series = TimeframeSeries(
        timeframe=Timeframe.M5, bars=bars, provenance=base_provenance, quality=DataQuality.COMPLETE
    )
    snapshot = MarketDataSnapshot(pair="EUR/USD", as_of=datetime.now(UTC), series={Timeframe.M5: series})
    
    aligned = align_timeframes(snapshot, [Timeframe.M5])
    res_bars = aligned.series[Timeframe.M5].bars
    
    assert res_bars[0].timestamp.minute == 5
    assert res_bars[1].timestamp.minute == 10
    assert res_bars[2].timestamp.minute == 15


def test_align_timeframes_raises_on_conflict(base_provenance):
    """Verifica che i duplicati con dati discordanti facciano esplodere l'allineamento."""
    bars = [
        _create_bar("2026-11-21T10:05:00", 1.0, 2.0, 0.5, 1.5),
        _create_bar("2026-11-21T10:05:00", 1.0, 2.1, 0.5, 1.5),  # Conflitto su High
    ]
    series = TimeframeSeries(
        timeframe=Timeframe.M5, bars=bars, provenance=base_provenance, quality=DataQuality.COMPLETE
    )
    snapshot = MarketDataSnapshot(pair="EUR/USD", as_of=datetime.now(UTC), series={Timeframe.M5: series})
    
    with pytest.raises(InvalidDataError):
        align_timeframes(snapshot, [Timeframe.M5])


def test_aggregation_crosses_utc_midnight(base_provenance):
    """Verifica valori, qualità e provenienza scavalcando la mezzanotte UTC."""
    start_dt = datetime.fromisoformat("2026-11-21T23:00:00").replace(tzinfo=UTC)
    bars = []
    for i in range(1, 13):
        ts = start_dt + timedelta(minutes=5 * i)
        bars.append(_create_bar(ts.isoformat(), 1.0, 1.5, 0.5, 1.2, v=10.0))
        
    # L'ultima barra ha un high e un close distintivi per testare i valori
    bars[-1].high = 2.0
    bars[-1].close = 1.8

    series = TimeframeSeries(
        timeframe=Timeframe.M5, bars=bars, provenance=base_provenance, quality=DataQuality.COMPLETE
    )
    snapshot = MarketDataSnapshot(pair="EUR/USD", as_of=bars[-1].timestamp, series={Timeframe.M5: series})
    
    aligned = align_timeframes(snapshot, [Timeframe.H1])
    h1_series = aligned.series[Timeframe.H1]
    h1_bar = h1_series.bars[0]
    
    # Asserzioni su data/ora
    assert h1_bar.timestamp.day == 22
    assert h1_bar.timestamp.hour == 0
    # Asserzioni su OHLCV
    assert h1_bar.open == 1.0
    assert h1_bar.high == 2.0
    assert h1_bar.low == 0.5
    assert h1_bar.close == 1.8
    assert h1_bar.volume == 120.0
    assert h1_bar.volume_type == VolumeType.TRADED
    # Asserzioni su metadati
    assert h1_series.quality == DataQuality.COMPLETE
    assert h1_series.provenance == base_provenance


def test_aggregation_skips_missing_intervals(base_provenance):
    """Verifica l'assenza di candele generi lacune e setti la qualità a PARTIAL."""
    bars = [
        _create_bar("2026-11-21T10:05:00", 1.0, 2.0, 0.5, 1.5),
        _create_bar("2026-11-21T10:10:00", 1.5, 2.5, 1.0, 2.0),
        # Manca 10:15 -> Non deve creare la barra M15
        _create_bar("2026-11-21T10:20:00", 1.0, 2.0, 0.5, 1.5),
        _create_bar("2026-11-21T10:25:00", 1.0, 2.0, 0.5, 1.5),
        _create_bar("2026-11-21T10:30:00", 1.0, 2.0, 0.5, 1.5),
    ]
    
    series = TimeframeSeries(
        timeframe=Timeframe.M5, bars=bars, provenance=base_provenance, quality=DataQuality.COMPLETE
    )
    snapshot = MarketDataSnapshot(pair="EUR/USD", as_of=datetime.now(UTC), series={Timeframe.M5: series})
    
    aligned = align_timeframes(snapshot, [Timeframe.M15])
    m15_series = aligned.series[Timeframe.M15]
    
    assert len(m15_series.bars) == 1
    assert m15_series.bars[0].timestamp.minute == 30
    assert m15_series.quality == DataQuality.PARTIAL


def test_propagation_of_stale_quality(base_provenance):
    """Verifica che una qualità originaria STALE o MISSING venga mantenuta nel passthrough."""
    bars = [
        _create_bar("2026-11-21T10:05:00", 1.0, 2.0, 0.5, 1.5),
    ]
    series = TimeframeSeries(
        timeframe=Timeframe.M15, bars=bars, provenance=base_provenance, quality=DataQuality.STALE
    )
    snapshot = MarketDataSnapshot(pair="EUR/USD", as_of=datetime.now(UTC), series={Timeframe.M15: series})
    
    aligned = align_timeframes(snapshot, [Timeframe.M15])
    assert aligned.series[Timeframe.M15].quality == DataQuality.STALE


def test_pure_unavailable_volumes_aggregate_correctly(base_provenance):
    """Verifica l'aggregazione corretta M5->M15 con volumi esplicitamente UNAVAILABLE (Forex)."""
    start_dt = datetime.fromisoformat("2026-11-21T10:00:00").replace(tzinfo=UTC)
    bars = []
    for i in range(1, 4): # 3 barre per fare un M15
        ts = start_dt + timedelta(minutes=5 * i)
        bars.append(_create_bar(
            ts.isoformat(), 1.0, 2.0, 0.5, 1.5, v=None, vt=VolumeType.UNAVAILABLE
        ))
        
    series = TimeframeSeries(
        timeframe=Timeframe.M5, bars=bars, provenance=base_provenance, quality=DataQuality.COMPLETE
    )
    snapshot = MarketDataSnapshot(pair="EUR/USD", as_of=datetime.now(UTC), series={Timeframe.M5: series})
    
    aligned = align_timeframes(snapshot, [Timeframe.M15])
    bar = aligned.series[Timeframe.M15].bars[0]
    
    assert bar.volume is None
    assert bar.volume_type == VolumeType.UNAVAILABLE
    assert aligned.series[Timeframe.M15].quality == DataQuality.COMPLETE


def test_mixed_volume_semantics_fallbacks_to_unavailable(base_provenance):
    """Verifica che un mix di volumi o volumi parziali invalidi il computo totale (Forex safety)."""
    bars = [
        _create_bar("2026-11-21T10:05:00", 1.0, 2.0, 0.5, 1.5, v=100.0, vt=VolumeType.TRADED),
        _create_bar("2026-11-21T10:10:00", 1.0, 2.0, 0.5, 1.5, v=None, vt=VolumeType.UNAVAILABLE),
        _create_bar("2026-11-21T10:15:00", 1.0, 2.0, 0.5, 1.5, v=100.0, vt=VolumeType.TRADED),
    ]
    series = TimeframeSeries(
        timeframe=Timeframe.M5, bars=bars, provenance=base_provenance, quality=DataQuality.COMPLETE
    )
    snapshot = MarketDataSnapshot(pair="EUR/USD", as_of=datetime.now(UTC), series={Timeframe.M5: series})
    
    aligned = align_timeframes(snapshot, [Timeframe.M15])
    bar = aligned.series[Timeframe.M15].bars[0]
    
    assert bar.volume is None
    assert bar.volume_type == VolumeType.UNAVAILABLE