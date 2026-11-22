from datetime import UTC, datetime

import pytest

from core.schemas import (
    DataProvenance,
    DataQuality,
    DataSourceKind,
    EvaluationRequest,
    EvaluationResponse,
    MarketBar,
    MarketDataSnapshot,
    RiskDecision,
    Signal,
    SignalDirection,
    StrategyConfiguration,
    StrategyId,
    Timeframe,
    TimeframeSeries,
    VolumeType,
)
from quantitative.engine import QuantitativeEngine
from quantitative.exceptions import InsufficientHistoryError, InvalidMarketDataError


class MockStrategy:
    strategy_id = StrategyId.TREND_FOLLOWING.value
    version = "1.0"

    def required_history(self, configuration):
        return {
            Timeframe.M5: 2,
            Timeframe.M15: 2,
            Timeframe.H1: 1,
        }

    def evaluate(self, snapshot, configuration):
        return Signal(
            direction=SignalDirection.LONG,
            confidence=0.9,
            motivational_codes=[timeframe.value for timeframe in snapshot.series],
        )


class MockRiskEvaluator:
    def evaluate(
        self, signal, snapshot, configuration, risk_configuration, account_state
    ):
        return RiskDecision(
            approved=True,
            reasons=["Test Passed"],
            recommended_quantity=1.0,
        )


@pytest.fixture
def engine():
    return QuantitativeEngine(
        strategy=MockStrategy(),
        risk_evaluator=MockRiskEvaluator(),
    )


@pytest.fixture
def base_dt():
    return datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


@pytest.fixture
def provenance_mock(base_dt):
    return DataProvenance(
        kind=DataSourceKind.BROKER,
        provider_id="test_prov",
        received_at=base_dt,
    )


def make_bar(timestamp, *, is_closed=True):
    return MarketBar(
        timestamp=datetime.fromisoformat(timestamp).replace(tzinfo=UTC),
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume_type=VolumeType.UNAVAILABLE,
        is_closed=is_closed,
    )


def create_eval_request(
    base_dt,
    provenance,
    *,
    quality_by_timeframe=None,
    bars_by_timeframe=None,
    include_timeframes=None,
):
    quality_by_timeframe = quality_by_timeframe or {}
    bars_by_timeframe = bars_by_timeframe or {}

    default_bars = {
        Timeframe.M5: [
            make_bar("2026-11-22T11:45:00"),
            make_bar("2026-11-22T11:50:00"),
            make_bar("2026-11-22T11:55:00"),
        ],
        Timeframe.M15: [
            make_bar("2026-11-22T11:15:00"),
            make_bar("2026-11-22T11:30:00"),
            make_bar("2026-11-22T11:45:00"),
        ],
        Timeframe.H1: [
            make_bar("2026-11-22T09:00:00"),
            make_bar("2026-11-22T10:00:00"),
            make_bar("2026-11-22T11:00:00"),
        ],
    }

    series = {}
    for timeframe, bars in default_bars.items():
        if include_timeframes is not None and timeframe not in include_timeframes:
            continue
        series[timeframe] = TimeframeSeries(
            timeframe=timeframe,
            bars=bars_by_timeframe.get(timeframe, bars),
            provenance=provenance,
            quality=quality_by_timeframe.get(timeframe, DataQuality.COMPLETE),
        )

    snapshot = MarketDataSnapshot(
        pair="EURUSD",
        as_of=base_dt,
        series=series,
    )
    configuration = StrategyConfiguration(
        strategy_id=StrategyId.TREND_FOLLOWING,
        parameters={},
    )
    return EvaluationRequest(
        market_data=snapshot,
        configuration=configuration,
    )


@pytest.mark.asyncio
async def test_engine_returns_canonical_evaluation_response(
    engine, base_dt, provenance_mock
):
    request = create_eval_request(base_dt, provenance_mock)

    response = await engine.evaluate(request)

    assert isinstance(response, EvaluationResponse)
    assert response.pair == "EURUSD"
    assert response.timestamp == base_dt
    assert response.signal.direction == SignalDirection.LONG
    assert response.risk_decision.approved is True
    assert response.risk_decision.recommended_quantity == 1.0
    assert response.strategy_id == StrategyId.TREND_FOLLOWING.value
    assert response.strategy_version == "1.0"
    assert response.engine_version == "1.0.0"
    assert response.signal.motivational_codes == ["M5", "M15", "H1"]


@pytest.mark.asyncio
async def test_engine_requires_all_mandatory_timeframes(
    engine, base_dt, provenance_mock
):
    request = create_eval_request(
        base_dt,
        provenance_mock,
        include_timeframes={Timeframe.M5, Timeframe.M15},
    )

    with pytest.raises(InvalidMarketDataError, match="H1"):
        await engine.evaluate(request)


@pytest.mark.asyncio
async def test_engine_rejects_insufficient_warmup_history(
    engine, base_dt, provenance_mock
):
    request = create_eval_request(
        base_dt,
        provenance_mock,
        bars_by_timeframe={
            Timeframe.M15: [make_bar("2026-11-22T11:45:00")],
        },
    )

    with pytest.raises(InsufficientHistoryError, match="M15.*required 2, available 1"):
        await engine.evaluate(request)


@pytest.mark.asyncio
async def test_engine_is_deterministic_for_different_series_mapping_order(
    engine, base_dt, provenance_mock
):
    request = create_eval_request(base_dt, provenance_mock)
    reversed_series = dict(reversed(list(request.market_data.series.items())))
    reordered_snapshot = MarketDataSnapshot(
        pair=request.market_data.pair,
        as_of=request.market_data.as_of,
        series=reversed_series,
    )
    reordered_request = request.model_copy(update={"market_data": reordered_snapshot})

    first_response = await engine.evaluate(request)
    second_response = await engine.evaluate(reordered_request)

    assert first_response == second_response


@pytest.mark.asyncio
async def test_engine_rejects_out_of_order_bars(engine, base_dt, provenance_mock):
    request = create_eval_request(
        base_dt,
        provenance_mock,
        bars_by_timeframe={
            Timeframe.H1: [
                make_bar("2026-11-22T11:00:00"),
                make_bar("2026-11-22T10:00:00"),
            ]
        },
    )

    with pytest.raises(InvalidMarketDataError, match="out of order"):
        await engine.evaluate(request)


@pytest.mark.asyncio
async def test_engine_rejects_duplicate_bars(engine, base_dt, provenance_mock):
    duplicate = make_bar("2026-11-22T11:00:00")
    request = create_eval_request(
        base_dt,
        provenance_mock,
        bars_by_timeframe={
            Timeframe.H1: [
                make_bar("2026-11-22T10:00:00"),
                duplicate,
                duplicate,
            ]
        },
    )

    with pytest.raises(InvalidMarketDataError, match="duplicated|out of order"):
        await engine.evaluate(request)


@pytest.mark.asyncio
async def test_engine_rejects_stale_data(engine, base_dt, provenance_mock):
    request = create_eval_request(
        base_dt,
        provenance_mock,
        quality_by_timeframe={Timeframe.H1: DataQuality.STALE},
    )

    with pytest.raises(InvalidMarketDataError, match="STALE"):
        await engine.evaluate(request)


@pytest.mark.asyncio
async def test_engine_rejects_unclosed_partial_bar(engine, base_dt, provenance_mock):
    request = create_eval_request(
        base_dt,
        provenance_mock,
        quality_by_timeframe={Timeframe.H1: DataQuality.PARTIAL},
        bars_by_timeframe={
            Timeframe.H1: [
                make_bar("2026-11-22T11:00:00", is_closed=False),
            ]
        },
    )

    with pytest.raises(InvalidMarketDataError, match="is_closed=False"):
        await engine.evaluate(request)


@pytest.mark.asyncio
async def test_engine_rejects_partial_bar_with_unknown_closed_state(
    engine, base_dt, provenance_mock
):
    request = create_eval_request(
        base_dt,
        provenance_mock,
        quality_by_timeframe={Timeframe.H1: DataQuality.PARTIAL},
        bars_by_timeframe={
            Timeframe.H1: [
                make_bar("2026-11-22T11:00:00", is_closed=None),
            ]
        },
    )

    with pytest.raises(InvalidMarketDataError, match="closed|closure|chiusa"):
        await engine.evaluate(request)


@pytest.mark.asyncio
async def test_engine_accepts_partial_series_with_closed_bars(
    engine, base_dt, provenance_mock
):
    request = create_eval_request(
        base_dt,
        provenance_mock,
        quality_by_timeframe={Timeframe.H1: DataQuality.PARTIAL},
        bars_by_timeframe={
            Timeframe.H1: [
                make_bar("2026-11-22T11:00:00", is_closed=True),
            ]
        },
    )

    response = await engine.evaluate(request)

    assert isinstance(response, EvaluationResponse)
