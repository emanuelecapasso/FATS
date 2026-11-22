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
from strategies.trend_following import TrendFollowingStrategy

AS_OF = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
PARAMETERS = {
    "h1_fast_period": 3.0,
    "h1_slow_period": 5.0,
    "m15_ema_period": 3.0,
    "m5_entry_ema_period": 3.0,
    "breakout_lookback": 2.0,
}


def _bars(timeframe, closes, *, is_closed=True):
    interval = {
        Timeframe.M5: timedelta(minutes=5),
        Timeframe.M15: timedelta(minutes=15),
        Timeframe.H1: timedelta(hours=1),
    }[timeframe]
    first_timestamp = AS_OF - interval * (len(closes) - 1)
    bars = []
    for index, close in enumerate(closes):
        bars.append(
            MarketBar(
                timestamp=first_timestamp + interval * index,
                open=close,
                high=close + 0.001,
                low=close - 0.001,
                close=close,
                volume_type=VolumeType.UNAVAILABLE,
                is_closed=is_closed,
            )
        )
    return bars


def _snapshot(
    *,
    m5=None,
    m15=None,
    h1=None,
    quality=DataQuality.COMPLETE,
):
    provenance = DataProvenance(
        kind=DataSourceKind.BROKER,
        provider_id="test",
        received_at=AS_OF,
    )
    series_data = {
        Timeframe.M5: m5 or _bars(Timeframe.M5, [1.00, 1.01, 1.02, 1.04]),
        Timeframe.M15: m15 or _bars(Timeframe.M15, [1.00, 1.10, 1.20, 1.30, 1.40]),
        Timeframe.H1: h1 or _bars(Timeframe.H1, [1.00, 1.10, 1.20, 1.30, 1.40, 1.50]),
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
            for timeframe, bars in series_data.items()
        },
    )


def _configuration(position=SignalDirection.FLAT, parameters=None):
    return StrategyConfiguration(
        strategy_id=StrategyId.TREND_FOLLOWING,
        current_position=position,
        parameters=parameters or PARAMETERS,
    )


class _AllowRisk:
    def evaluate(self, signal, configuration):
        return RiskDecision(approved=True, reasons=[], recommended_quantity=1.0)


@pytest.fixture
def strategy():
    return TrendFollowingStrategy()


def test_entry_long_when_all_timeframes_align_and_m5_breaks_out(strategy):
    signal = strategy.evaluate(_snapshot(), _configuration())

    assert signal.direction == SignalDirection.LONG
    assert "long_entry" in signal.motivational_codes
    assert "h1_m15_aligned" in signal.motivational_codes
    assert "m5_breakout" in signal.motivational_codes


def test_entry_short_when_all_timeframes_align_and_m5_breaks_down(strategy):
    snapshot = _snapshot(
        m5=_bars(Timeframe.M5, [1.04, 1.03, 1.02, 1.00]),
        m15=_bars(Timeframe.M15, [1.50, 1.40, 1.30, 1.20, 1.10]),
        h1=_bars(Timeframe.H1, [1.50, 1.40, 1.30, 1.20, 1.10, 1.00]),
    )

    signal = strategy.evaluate(snapshot, _configuration())

    assert signal.direction == SignalDirection.SHORT
    assert "short_entry" in signal.motivational_codes


def test_maintains_existing_long_position_without_new_breakout(strategy):
    snapshot = _snapshot(m5=_bars(Timeframe.M5, [1.00, 1.01, 1.02, 1.02]))

    signal = strategy.evaluate(snapshot, _configuration(SignalDirection.LONG))

    assert signal.direction == SignalDirection.LONG
    assert "position_maintained" in signal.motivational_codes


def test_maintains_existing_short_position(strategy):
    snapshot = _snapshot(
        m5=_bars(Timeframe.M5, [1.04, 1.03, 1.02, 1.02]),
        m15=_bars(Timeframe.M15, [1.50, 1.40, 1.30, 1.20, 1.10]),
        h1=_bars(Timeframe.H1, [1.50, 1.40, 1.30, 1.20, 1.10, 1.00]),
    )

    signal = strategy.evaluate(snapshot, _configuration(SignalDirection.SHORT))

    assert signal.direction == SignalDirection.SHORT
    assert "position_maintained" in signal.motivational_codes


def test_exits_long_when_m5_close_falls_below_entry_ema(strategy):
    snapshot = _snapshot(m5=_bars(Timeframe.M5, [1.00, 1.01, 1.02, 0.99]))

    signal = strategy.evaluate(snapshot, _configuration(SignalDirection.LONG))

    assert signal.direction == SignalDirection.FLAT
    assert "long_exit" in signal.motivational_codes


def test_exits_short_when_m5_close_rises_above_entry_ema(strategy):
    snapshot = _snapshot(
        m5=_bars(Timeframe.M5, [1.04, 1.03, 1.02, 1.05]),
        m15=_bars(Timeframe.M15, [1.50, 1.40, 1.30, 1.20, 1.10]),
        h1=_bars(Timeframe.H1, [1.50, 1.40, 1.30, 1.20, 1.10, 1.00]),
    )

    signal = strategy.evaluate(snapshot, _configuration(SignalDirection.SHORT))

    assert signal.direction == SignalDirection.FLAT
    assert "short_exit" in signal.motivational_codes


def test_warmup_is_derived_from_explicit_parameters(strategy):
    assert strategy.required_history(_configuration()) == {
        Timeframe.M5: 3,
        Timeframe.M15: 4,
        Timeframe.H1: 5,
    }


def test_warmup_changes_when_indicator_parameters_change(strategy):
    configuration = _configuration(
        parameters={
            "h1_fast_period": 4.0,
            "h1_slow_period": 7.0,
            "m15_ema_period": 5.0,
            "m5_entry_ema_period": 12.0,
            "breakout_lookback": 10.0,
        }
    )

    assert strategy.required_history(configuration) == {
        Timeframe.M5: 12,
        Timeframe.M15: 6,
        Timeframe.H1: 7,
    }


def test_insufficient_history_raises_instead_of_emitting_signal(strategy):
    snapshot = _snapshot(h1=_bars(Timeframe.H1, [1.0, 1.1, 1.2, 1.3]))

    with pytest.raises(InsufficientHistoryError, match="H1"):
        strategy.evaluate(snapshot, _configuration())


def test_missing_timeframe_raises_instead_of_emitting_signal(strategy):
    snapshot = _snapshot()
    del snapshot.series[Timeframe.H1]

    with pytest.raises(InsufficientHistoryError, match="H1 is missing"):
        strategy.evaluate(snapshot, _configuration())


def test_stale_series_is_rejected(strategy):
    snapshot = _snapshot(quality=DataQuality.STALE)

    with pytest.raises(InvalidMarketDataError, match="STALE"):
        strategy.evaluate(snapshot, _configuration())


@pytest.mark.parametrize("closed_state", [False, None])
def test_rejects_incomplete_or_unknown_bars(strategy, closed_state):
    snapshot = _snapshot(
        m5=_bars(Timeframe.M5, [1.0, 1.01, 1.02, 1.04], is_closed=True)
    )
    snapshot.series[Timeframe.M5].bars[-1] = (
        snapshot.series[Timeframe.M5]
        .bars[-1]
        .model_copy(update={"is_closed": closed_state})
    )

    with pytest.raises(InvalidMarketDataError, match="Unclosed or unknown"):
        strategy.evaluate(snapshot, _configuration())


def test_rejects_future_bar_even_if_snapshot_was_constructed_unsafely(strategy):
    snapshot = _snapshot()
    future_bar = _bars(Timeframe.M5, [1.05])[0].model_copy(
        update={"timestamp": AS_OF + timedelta(minutes=5)}
    )
    snapshot.series[Timeframe.M5].bars.append(future_bar)

    with pytest.raises(InvalidMarketDataError, match="Future bar"):
        strategy.evaluate(snapshot, _configuration())


def test_missing_m5_breakout_candle_is_not_filled_or_interpolated(strategy):
    snapshot = _snapshot()
    bars = snapshot.series[Timeframe.M5].bars
    bars.pop(2)

    with pytest.raises(InsufficientHistoryError, match="contiguous M5"):
        strategy.evaluate(snapshot, _configuration())


def test_repeated_identical_input_produces_identical_signal(strategy):
    snapshot = _snapshot()
    configuration = _configuration()

    first = strategy.evaluate(snapshot, configuration)
    second = strategy.evaluate(snapshot, configuration)

    assert first == second


@pytest.mark.asyncio
async def test_strategy_integrates_with_engine_and_exposes_version(strategy):
    engine = QuantitativeEngine(strategy=strategy, risk_evaluator=_AllowRisk())
    request = EvaluationRequest(
        market_data=_snapshot(),
        configuration=_configuration(),
    )

    response = await engine.evaluate(request)

    assert response.strategy_id == StrategyId.TREND_FOLLOWING.value
    assert response.strategy_version == strategy.version
    assert response.signal.direction == SignalDirection.LONG
    assert "long_entry" in response.signal.motivational_codes


def test_partial_series_is_accepted_only_when_bars_are_closed(strategy):
    snapshot = _snapshot(quality=DataQuality.PARTIAL)

    signal = strategy.evaluate(snapshot, _configuration())

    assert signal.direction == SignalDirection.LONG


def test_rejects_unknown_or_invalid_parameters(strategy):
    with pytest.raises(ValueError, match="Unknown Trend Following parameters"):
        strategy.required_history(
            _configuration(parameters={**PARAMETERS, "hidden_period": 2.0})
        )

    with pytest.raises(ValueError, match="less than"):
        strategy.required_history(
            _configuration(
                parameters={**PARAMETERS, "h1_fast_period": 5.0, "h1_slow_period": 5.0}
            )
        )
