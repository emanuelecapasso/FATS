from datetime import UTC, datetime, timedelta

import pytest

from core.schemas import (
    DataProvenance,
    DataQuality,
    DataSourceKind,
    EvaluationRequest,
    MarketBar,
    MarketDataSnapshot,
    RiskDecision,
    SignalDirection,
    StrategyConfiguration,
    StrategyId,
    Timeframe,
    TimeframeSeries,
    VolumeType,
)
from quantitative.engine import QuantitativeEngine
from quantitative.exceptions import InsufficientHistoryError, InvalidMarketDataError
from strategies.mean_reversion import MeanReversionStrategy

AS_OF = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
PARAMETERS = {
    "h1_fast_period": 2.0,
    "h1_slow_period": 4.0,
    "max_h1_trend_spread": 0.01,
    "m15_bollinger_period": 5.0,
    "entry_zscore": 1.5,
    "exit_zscore": 0.2,
    "stop_zscore": 2.5,
    "m5_mean_period": 3.0,
}
OVERSOLD_M15 = [100.0, 101.0, 100.0, 99.0, 90.0]
OVERBOUGHT_M15 = [90.0, 89.0, 90.0, 91.0, 100.0]
NEUTRAL_M15 = [100.0] * 5
NEUTRAL_H1 = [100.0] * 5


def _bars(timeframe, closes, *, is_closed=True):
    interval = {
        Timeframe.M5: timedelta(minutes=5),
        Timeframe.M15: timedelta(minutes=15),
        Timeframe.H1: timedelta(hours=1),
    }[timeframe]
    first_timestamp = AS_OF - interval * (len(closes) - 1)
    return [
        MarketBar(
            timestamp=first_timestamp + interval * index,
            open=close,
            high=close + 0.1,
            low=close - 0.1,
            close=close,
            volume_type=VolumeType.UNAVAILABLE,
            is_closed=is_closed,
        )
        for index, close in enumerate(closes)
    ]


def _snapshot(
    *,
    m5_closes=None,
    m15_closes=None,
    h1_closes=None,
    quality=DataQuality.COMPLETE,
):
    provenance = DataProvenance(
        kind=DataSourceKind.BROKER,
        provider_id="test",
        received_at=AS_OF,
    )
    bars_by_timeframe = {
        Timeframe.M5: _bars(
            Timeframe.M5,
            m5_closes or [90.0, 89.0, 88.0, 87.0, 89.0],
        ),
        Timeframe.M15: _bars(
            Timeframe.M15,
            m15_closes or OVERSOLD_M15,
        ),
        Timeframe.H1: _bars(
            Timeframe.H1,
            h1_closes or NEUTRAL_H1,
        ),
    }
    return MarketDataSnapshot(
        pair="EURUSD",
        as_of=AS_OF,
        series={
            timeframe: TimeframeSeries(
                timeframe=timeframe,
                bars=bars,
                provenance=provenance,
                quality=quality,
            )
            for timeframe, bars in bars_by_timeframe.items()
        },
    )


def _configuration(position=SignalDirection.FLAT, parameters=None):
    return StrategyConfiguration(
        strategy_id=StrategyId.MEAN_REVERSION,
        current_position=position,
        parameters=parameters or PARAMETERS,
    )


class _AllowRisk:
    def evaluate(self, signal, configuration):
        return RiskDecision(approved=True, reasons=[], recommended_quantity=1.0)


@pytest.fixture
def strategy():
    return MeanReversionStrategy()


def test_enters_long_on_oversold_m15_and_m5_reversion_cross(strategy):
    signal = strategy.evaluate(_snapshot(), _configuration())

    assert signal.direction == SignalDirection.LONG
    assert "long_entry" in signal.motivational_codes
    assert "m15_oversold" in signal.motivational_codes
    assert "m5_crossed_above_mean" in signal.motivational_codes


def test_enters_short_on_overbought_m15_and_m5_reversion_cross(strategy):
    snapshot = _snapshot(
        m5_closes=[100.0, 101.0, 102.0, 103.0, 101.0],
        m15_closes=OVERBOUGHT_M15,
    )

    signal = strategy.evaluate(snapshot, _configuration())

    assert signal.direction == SignalDirection.SHORT
    assert "short_entry" in signal.motivational_codes
    assert "m15_overbought" in signal.motivational_codes
    assert "m5_crossed_below_mean" in signal.motivational_codes


def test_maintains_long_position_until_mean_reversion_completes(strategy):
    signal = strategy.evaluate(_snapshot(), _configuration(SignalDirection.LONG))

    assert signal.direction == SignalDirection.LONG
    assert "position_maintained" in signal.motivational_codes


def test_maintains_short_position_until_mean_reversion_completes(strategy):
    snapshot = _snapshot(m15_closes=OVERBOUGHT_M15)

    signal = strategy.evaluate(snapshot, _configuration(SignalDirection.SHORT))

    assert signal.direction == SignalDirection.SHORT
    assert "position_maintained" in signal.motivational_codes


@pytest.mark.parametrize("position", [SignalDirection.LONG, SignalDirection.SHORT])
def test_exits_position_when_m15_returns_to_mean(strategy, position):
    snapshot = _snapshot(m15_closes=NEUTRAL_M15)

    signal = strategy.evaluate(snapshot, _configuration(position))

    assert signal.direction == SignalDirection.FLAT
    assert "mean_reached" in signal.motivational_codes


def test_h1_strong_trend_blocks_mean_reversion_entry(strategy):
    snapshot = _snapshot(h1_closes=[100.0, 102.0, 104.0, 106.0, 108.0])

    signal = strategy.evaluate(snapshot, _configuration())

    assert signal.direction == SignalDirection.FLAT
    assert "h1_trend_filter" in signal.motivational_codes


def test_warmup_is_parameterized_for_all_timeframes(strategy):
    assert strategy.required_history(_configuration()) == {
        Timeframe.M5: 4,
        Timeframe.M15: 5,
        Timeframe.H1: 4,
    }

    larger = _configuration(
        parameters={**PARAMETERS, "m5_mean_period": 8.0, "h1_slow_period": 10.0}
    )
    assert strategy.required_history(larger)[Timeframe.M5] == 9
    assert strategy.required_history(larger)[Timeframe.H1] == 10


def test_insufficient_history_does_not_emit_signal(strategy):
    snapshot = _snapshot(h1_closes=[100.0, 100.1, 100.2])

    with pytest.raises(InsufficientHistoryError, match="H1"):
        strategy.evaluate(snapshot, _configuration())


def test_missing_timeframe_is_rejected(strategy):
    snapshot = _snapshot()
    del snapshot.series[Timeframe.M15]

    with pytest.raises(InsufficientHistoryError, match="M15 is missing"):
        strategy.evaluate(snapshot, _configuration())


def test_stale_series_is_rejected(strategy):
    snapshot = _snapshot(quality=DataQuality.STALE)

    with pytest.raises(InvalidMarketDataError, match="STALE"):
        strategy.evaluate(snapshot, _configuration())


@pytest.mark.parametrize("closed_state", [False, None])
def test_incomplete_or_unknown_bar_is_rejected(strategy, closed_state):
    snapshot = _snapshot()
    bar = snapshot.series[Timeframe.M5].bars[-1]
    snapshot.series[Timeframe.M5].bars[-1] = bar.model_copy(
        update={"is_closed": closed_state}
    )

    with pytest.raises(InvalidMarketDataError, match="Unclosed or unknown"):
        strategy.evaluate(snapshot, _configuration())


def test_future_bar_is_rejected(strategy):
    snapshot = _snapshot()
    future_bar = _bars(Timeframe.M5, [90.0])[0].model_copy(
        update={"timestamp": AS_OF + timedelta(minutes=5)}
    )
    snapshot.series[Timeframe.M5].bars.append(future_bar)

    with pytest.raises(InvalidMarketDataError, match="Future bar"):
        strategy.evaluate(snapshot, _configuration())


def test_m5_confirmation_gap_is_not_filled(strategy):
    snapshot = _snapshot()
    snapshot.series[Timeframe.M5].bars.pop(3)

    with pytest.raises(InsufficientHistoryError, match="contiguous M5"):
        strategy.evaluate(snapshot, _configuration())


def test_same_input_and_configuration_produce_identical_signal(strategy):
    snapshot = _snapshot()
    configuration = _configuration()

    first = strategy.evaluate(snapshot, configuration)
    second = strategy.evaluate(snapshot, configuration)

    assert first == second


@pytest.mark.asyncio
async def test_integrates_with_engine_and_exposes_strategy_metadata(strategy):
    engine = QuantitativeEngine(strategy=strategy, risk_evaluator=_AllowRisk())
    request = EvaluationRequest(
        market_data=_snapshot(),
        configuration=_configuration(),
    )

    response = await engine.evaluate(request)

    assert response.strategy_id == StrategyId.MEAN_REVERSION.value
    assert response.strategy_version == strategy.version
    assert response.signal.direction == SignalDirection.LONG
    assert "long_entry" in response.signal.motivational_codes


def test_rejects_unknown_and_inconsistent_parameters(strategy):
    with pytest.raises(ValueError, match="Unknown Mean Reversion parameters"):
        strategy.required_history(
            _configuration(parameters={**PARAMETERS, "hidden_threshold": 0.3})
        )

    with pytest.raises(ValueError, match="entry_zscore must exceed"):
        strategy.required_history(
            _configuration(parameters={**PARAMETERS, "exit_zscore": 2.0})
        )
