import inspect
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

import quantitative.ports as quant_ports
from core.schemas import (
    CURRENT_SCHEMA_VERSION,
    DataProvenance,
    DataQuality,
    DataSourceKind,
    EvaluationRequest,
    EvaluationResponse,
    MarketBar,
    MarketDataSnapshot,
    StrategyConfiguration,
    StrategyId,
    Timeframe,
    TimeframeSeries,
)

# --- FIXTURES DI SUPPORTO ---


@pytest.fixture
def base_utc() -> datetime:
    return datetime(2026, 11, 21, 14, 0, 0, tzinfo=UTC)


@pytest.fixture
def valid_bar(base_utc: datetime) -> MarketBar:
    return MarketBar(
        timestamp=base_utc,
        open=1.0850,
        high=1.0875,
        low=1.0840,
        close=1.0865,
        volume=1500.0,
    )


@pytest.fixture
def valid_provenance(base_utc: datetime) -> DataProvenance:
    return DataProvenance(
        kind=DataSourceKind.BROKER,
        provider_id="oanda_v20",
        received_at=base_utc,
    )


# --- TEST CONTRATTUALI ---


def test_json_serialization_and_roundtrip(
    base_utc: datetime, valid_bar: MarketBar, valid_provenance: DataProvenance
) -> None:
    """Verifica che gli oggetti serializzino in JSON e si ricostruiscano in modo deterministico."""
    series_m5 = TimeframeSeries(
        timeframe=Timeframe.M5,
        bars=[valid_bar],
        provenance=valid_provenance,
        quality=DataQuality.COMPLETE,
    )
    snapshot = MarketDataSnapshot(
        pair="EUR_USD",
        as_of=base_utc,
        series={Timeframe.M5: series_m5},
    )
    request = EvaluationRequest(
        schema_version=CURRENT_SCHEMA_VERSION,
        market_data=snapshot,
        configuration=StrategyConfiguration(
            strategy_id=StrategyId.TREND_FOLLOWING,
            parameters={"fast_period": 10.0},
        ),
    )

    json_payload = request.model_dump_json()
    reconstructed = EvaluationRequest.model_validate_json(json_payload)

    assert reconstructed == request
    assert reconstructed.market_data.as_of.tzinfo is not None


def test_accepts_all_timeframes_and_sources(
    base_utc: datetime, valid_bar: MarketBar
) -> None:
    """Verifica l'accettazione esplicita di M5, M15, H1 e delle fonti broker ed external."""
    prov_broker = DataProvenance(
        kind=DataSourceKind.BROKER, provider_id="broker_primary", received_at=base_utc
    )
    prov_external = DataProvenance(
        kind=DataSourceKind.EXTERNAL, provider_id="hist_feed", received_at=base_utc
    )

    snapshot = MarketDataSnapshot(
        pair="BTC_USD",
        as_of=base_utc,
        series={
            Timeframe.M5: TimeframeSeries(
                timeframe=Timeframe.M5,
                bars=[valid_bar],
                provenance=prov_broker,
                quality=DataQuality.COMPLETE,
            ),
            Timeframe.M15: TimeframeSeries(
                timeframe=Timeframe.M15,
                bars=[valid_bar],
                provenance=prov_broker,
                quality=DataQuality.COMPLETE,
            ),
            Timeframe.H1: TimeframeSeries(
                timeframe=Timeframe.H1,
                bars=[valid_bar],
                provenance=prov_external,
                quality=DataQuality.COMPLETE,
            ),
        },
    )

    assert set(snapshot.series.keys()) == {Timeframe.M5, Timeframe.M15, Timeframe.H1}
    assert snapshot.series[Timeframe.M5].provenance.kind == DataSourceKind.BROKER
    assert snapshot.series[Timeframe.H1].provenance.kind == DataSourceKind.EXTERNAL


@pytest.mark.parametrize(
    "quality",
    [DataQuality.COMPLETE, DataQuality.PARTIAL, DataQuality.STALE],
)
def test_explicit_handling_of_partial_and_stale_bars(
    base_utc: datetime,
    valid_bar: MarketBar,
    valid_provenance: DataProvenance,
    quality: DataQuality,
) -> None:
    """Verifica che i metadati di qualità (complete, partial, stale) siano espliciti e preservati."""
    series = TimeframeSeries(
        timeframe=Timeframe.M15,
        bars=[valid_bar],
        provenance=valid_provenance,
        quality=quality,
    )
    assert series.quality == quality
    assert series.model_dump()["quality"] == quality.value


def test_rejects_naive_timestamps() -> None:
    """Rifiuta timestamp privi di fuso orario (naive datetime)."""
    naive_dt = datetime(2026, 11, 21, 14, 0, 0)  # Nessun tzinfo

    with pytest.raises(ValidationError, match="UTC"):
        MarketBar(
            timestamp=naive_dt,
            open=100.0,
            high=105.0,
            low=99.0,
            close=102.0,
            volume=10.0,
        )

    with pytest.raises(ValidationError, match="UTC"):
        DataProvenance(
            kind=DataSourceKind.BROKER,
            provider_id="test",
            received_at=naive_dt,
        )


def test_rejects_non_utc_timezone() -> None:
    """Rifiuta timestamp timezone-aware che non siano in UTC."""
    cet_tz = timezone(timedelta(hours=1))
    cet_dt = datetime(2026, 11, 21, 15, 0, 0, tzinfo=cet_tz)

    with pytest.raises(ValidationError, match="UTC"):
        MarketBar(
            timestamp=cet_dt,
            open=100.0,
            high=105.0,
            low=99.0,
            close=102.0,
            volume=10.0,
        )


def test_rejects_future_bars(
    base_utc: datetime, valid_provenance: DataProvenance
) -> None:
    """Impedisce look-ahead bias: rifiuta barre con timestamp successivo ad as_of."""
    future_dt = base_utc + timedelta(minutes=5)
    future_bar = MarketBar(
        timestamp=future_dt,
        open=1.10,
        high=1.11,
        low=1.09,
        close=1.105,
        volume=500.0,
    )

    series = TimeframeSeries(
        timeframe=Timeframe.M5,
        bars=[future_bar],
        provenance=valid_provenance,
        quality=DataQuality.COMPLETE,
    )

    with pytest.raises(ValidationError, match="Look-ahead vietato"):
        MarketDataSnapshot(
            pair="EUR_USD",
            as_of=base_utc,
            series={Timeframe.M5: series},
        )


@pytest.mark.parametrize(
    "o,h,l,c",
    [
        (100.0, 99.0, 95.0, 98.0),  # High minore di Open
        (100.0, 105.0, 101.0, 102.0),  # Low maggiore di Open
        (100.0, 102.0, 95.0, 103.0),  # High minore di Close
        (100.0, 105.0, 99.0, 98.0),  # Low maggiore di Close
    ],
)
def test_rejects_incoherent_ohlc(
    base_utc: datetime, o: float, h: float, l: float, c: float
) -> None:
    """Rifiuta candele con geometria OHLC matematicamente impossibile."""
    with pytest.raises(ValidationError, match="OHLC incoerente"):
        MarketBar(
            timestamp=base_utc,
            open=o,
            high=h,
            low=l,
            close=c,
            volume=100.0,
        )


def test_rejects_unknown_extra_fields(base_utc: datetime) -> None:
    """Rifiuta campi non dichiarati nello schema (extra='forbid') a tutti i livelli."""
    with pytest.raises(ValidationError):
        MarketBar(
            timestamp=base_utc,
            open=1.0,
            high=1.2,
            low=0.9,
            close=1.1,
            volume=10.0,
            broker_specific_spread=0.0002,  # Campo extra vietato
        )

    with pytest.raises(ValidationError):
        StrategyConfiguration(
            strategy_id=StrategyId.MEAN_REVERSION,
            unknown_flag=True,  # Campo extra vietato
        )


def test_rejects_incompatible_schema_version(
    base_utc: datetime, valid_bar: MarketBar, valid_provenance: DataProvenance
) -> None:
    """Verifica che major version diverse (es. 2.0) vengano rifiutate."""
    snapshot = MarketDataSnapshot(
        pair="EUR_USD",
        as_of=base_utc,
        series={
            Timeframe.M5: TimeframeSeries(
                timeframe=Timeframe.M5,
                bars=[valid_bar],
                provenance=valid_provenance,
                quality=DataQuality.COMPLETE,
            )
        },
    )

    with pytest.raises(ValidationError):
        EvaluationRequest(
            schema_version="2.0",
            market_data=snapshot,
            configuration=StrategyConfiguration(strategy_id=StrategyId.TREND_FOLLOWING),
        )


def test_shared_models_across_packages_without_duplicates() -> None:
    """
    Assicura che quantitative/, backend/ e data/ importino e usino gli stessi identici
    tipi canonici definiti in core/schemas.py senza ridefinirli localmente.
    """
    import backend.routes as backend_routes

    # 1. Verifica che il tipo di annotazione dell'evaluator in quantitative.ports sia lo stesso di core.schemas
    sig = inspect.signature(quant_ports.QuantitativeEvaluator.evaluate)
    request_param = sig.parameters["request"]
    assert request_param.annotation is EvaluationRequest
    assert sig.return_annotation is EvaluationResponse

    # 2. Verifica che le route del backend riferiscano esattamente gli stessi oggetti classe di core.schemas
    assert backend_routes.EvaluationRequest is EvaluationRequest
    assert backend_routes.EvaluationResponse is EvaluationResponse
