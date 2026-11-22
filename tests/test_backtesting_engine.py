"""Tests for the historical backtesting engine."""

from datetime import UTC, datetime, timedelta

import pytest

from backtesting.engine import BacktestingEngine
from backtesting.models import OrderStatus
from backtesting.strategy import BacktestSignal
from core.schemas import MarketBar, SignalDirection


class FixedStrategy:
    """Returns a fixed signal on the first evaluation only."""

    def __init__(self, signal):
        self.signal = signal
        self.calls = 0

    def generate_signal(self, bars):
        self.calls += 1

        if self.calls == 1:
            return self.signal

        return BacktestSignal(direction=SignalDirection.FLAT)


def make_bar(timestamp, open_price, high, low, close):
    return MarketBar(
        timestamp=timestamp,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=1000,
    )


@pytest.fixture
def historical_bars():
    start = datetime(2026, 11, 16, 14, 0, tzinfo=UTC)

    return [
        make_bar(start, 1.1000, 1.1010, 1.0990, 1.1005),
        make_bar(start + timedelta(hours=1), 1.1005, 1.1020, 1.1000, 1.1015),
        make_bar(start + timedelta(hours=2), 1.1015, 1.1030, 1.1010, 1.1025),
        make_bar(start + timedelta(hours=3), 1.1025, 1.1030, 1.1010, 1.1020),
    ]


@pytest.fixture
def engine():
    return BacktestingEngine(
        initial_balance=10_000,
        candle_duration=timedelta(hours=1),
        pair="EURUSD",
        pip_size=0.0001,
    )


def test_engine_requires_at_least_two_bars(engine):
    with pytest.raises(ValueError, match="At least two"):
        engine.run([], FixedStrategy(BacktestSignal(direction=SignalDirection.FLAT)))


def test_engine_rejects_unsorted_bars(engine, historical_bars):
    bars = [historical_bars[1], historical_bars[0]]

    with pytest.raises(ValueError, match="ascending"):
        engine.run(
            bars,
            FixedStrategy(BacktestSignal(direction=SignalDirection.FLAT)),
        )


def test_engine_generates_signal_after_candle_close(
    engine,
    historical_bars,
):
    strategy = FixedStrategy(
        BacktestSignal(
            direction=SignalDirection.LONG,
            quantity=4_000,
            stop_loss=1.0950,
            take_profit=1.1100,
        )
    )

    result = engine.run(historical_bars, strategy)

    assert strategy.calls > 0
    assert len(result.executions) >= 2

    # Entry must occur on the candle after the first signal.
    assert result.executions[0].executed_at == historical_bars[1].timestamp


def test_engine_closes_position_at_end_of_backtest(
    engine,
    historical_bars,
):
    strategy = FixedStrategy(
        BacktestSignal(
            direction=SignalDirection.LONG,
            quantity=4_000,
            stop_loss=1.0950,
        )
    )

    result = engine.run(historical_bars, strategy)

    assert len(result.executions) == 2
    assert result.executions[-1].executed_at == (
        historical_bars[-1].timestamp + timedelta(hours=1)
    )


def test_engine_rejects_invalid_long_stop_loss(
    engine,
    historical_bars,
):
    strategy = FixedStrategy(
        BacktestSignal(
            direction=SignalDirection.LONG,
            quantity=10_000,
            stop_loss=1.2000,
        )
    )

    result = engine.run(historical_bars, strategy)

    assert result.rejected_orders == 1
    assert all(order.status != OrderStatus.FILLED for order in result.orders)


def test_engine_returns_equity_curve(
    engine,
    historical_bars,
):
    strategy = FixedStrategy(BacktestSignal(direction=SignalDirection.FLAT))

    result = engine.run(historical_bars, strategy)

    assert len(result.equity_curve) == len(historical_bars)
    assert result.equity_curve[0][1] == 10_000
    assert result.initial_balance == 10_000


def test_stop_loss_closes_position(engine, historical_bars):
    strategy = FixedStrategy(
        BacktestSignal(
            direction=SignalDirection.LONG,
            quantity=4000,
            stop_loss=1.1000,
            take_profit=1.2000,
        )
    )

    result = engine.run(historical_bars, strategy)

    assert len(result.executions) == 2
    assert result.executions[-1].price == 1.1000


def test_take_profit_closes_position(engine, historical_bars):
    strategy = FixedStrategy(
        BacktestSignal(
            direction=SignalDirection.LONG,
            quantity=4000,
            stop_loss=1.0900,
            take_profit=1.1020,
        )
    )

    result = engine.run(historical_bars, strategy)

    assert len(result.executions) == 2
    assert result.executions[-1].price == 1.1020


def test_stop_loss_has_priority_when_both_levels_are_hit(engine, historical_bars):
    strategy = FixedStrategy(
        BacktestSignal(
            direction=SignalDirection.LONG,
            quantity=4000,
            stop_loss=1.1000,
            take_profit=1.1020,
        )
    )

    result = engine.run(historical_bars, strategy)

    assert len(result.executions) == 2
    assert result.executions[-1].price == 1.1000


def test_execution_costs_are_applied(engine, historical_bars):
    from backtesting.execution_simulator import ExecutionSimulator

    engine.execution_simulator = ExecutionSimulator(
        spread_pips=1.0,
        slippage_pips=0.2,
        commission_per_lot=7.0,
    )

    strategy = FixedStrategy(
        BacktestSignal(
            direction=SignalDirection.LONG,
            quantity=4000,
            stop_loss=1.0900,
            take_profit=1.2000,
        )
    )

    result = engine.run(historical_bars, strategy)

    assert len(result.executions) == 2
    assert all(execution.spread_cost > 0 for execution in result.executions)
    assert all(execution.slippage > 0 for execution in result.executions)
    assert all(execution.commission > 0 for execution in result.executions)
    total_costs = sum(
        execution.spread_cost + execution.slippage + execution.commission
        for execution in result.executions
    )
    assert total_costs > 0
    assert result.final_balance == pytest.approx(10_000 + 6 - total_costs)
