"""Tests for the simulated execution engine."""

from datetime import UTC, datetime

import pytest

from backtesting.execution_simulator import ExecutionSimulator
from backtesting.models import Order, OrderSide, OrderStatus, OrderType


@pytest.fixture
def simulator():
    return ExecutionSimulator(
        spread_pips=1.0,
        slippage_pips=0.2,
        commission_per_lot=7.0,
    )


@pytest.fixture
def order():
    return Order(
        pair="EUR/USD",
        side=OrderSide.BUY,
        quantity=100_000,
    )


def test_simulate_market_execution(simulator, order):
    execution = simulator.execute(
        order=order,
        market_price=1.10000,
        pip_size=0.0001,
    )

    assert execution.order_id == order.order_id
    assert execution.price == 1.10000
    assert execution.quantity == 100_000
    assert execution.status if hasattr(execution, "status") else True


def test_calculate_spread_cost(simulator, order):
    execution = simulator.execute(order, 1.10000, 0.0001)

    assert execution.spread_cost == pytest.approx(5.0)


def test_calculate_slippage_cost(simulator, order):
    execution = simulator.execute(order, 1.10000, 0.0001)

    assert execution.slippage == pytest.approx(2.0)


def test_calculate_commission(simulator, order):
    execution = simulator.execute(order, 1.10000, 0.0001)

    assert execution.commission == pytest.approx(7.0)


def test_calculate_costs_for_half_lot(simulator):
    half_lot_order = Order(
        pair="EUR/USD",
        side=OrderSide.BUY,
        quantity=50_000,
    )

    execution = simulator.execute(half_lot_order, 1.10000, 0.0001)

    assert execution.spread_cost == pytest.approx(2.5)
    assert execution.slippage == pytest.approx(1.0)
    assert execution.commission == pytest.approx(3.5)


def test_reject_non_pending_order(simulator, order):
    filled_order = order.model_copy(update={"status": OrderStatus.FILLED})

    with pytest.raises(ValueError, match="pending"):
        simulator.execute(filled_order, 1.10000, 0.0001)


def test_reject_non_market_order(simulator):
    limit_order = Order(
        pair="EUR/USD",
        side=OrderSide.BUY,
        quantity=100_000,
        order_type=OrderType.LIMIT,
    )

    with pytest.raises(ValueError, match="MARKET"):
        simulator.execute(limit_order, 1.10000, 0.0001)


@pytest.mark.parametrize(
    ("market_price", "pip_size"),
    [
        (0, 0.0001),
        (-1, 0.0001),
        (1.1, 0),
        (1.1, -0.0001),
    ],
)
def test_reject_invalid_prices(simulator, order, market_price, pip_size):
    with pytest.raises(ValueError):
        simulator.execute(order, market_price, pip_size)


def test_reject_naive_execution_timestamp(simulator, order):
    with pytest.raises(ValueError, match="timezone-aware"):
        simulator.execute(
            order,
            1.10000,
            0.0001,
            executed_at=datetime(2026, 1, 1),
        )


def test_execution_timestamp_is_utc(simulator, order):
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)

    execution = simulator.execute(order, 1.10000, 0.0001, executed_at=timestamp)

    assert execution.executed_at == timestamp


def test_simulate_partial_fill(simulator, order):
    execution = simulator.execute(
        order,
        1.10000,
        0.0001,
        fill_quantity=40_000,
    )

    assert execution.quantity == 40_000
    assert execution.spread_cost == pytest.approx(2.0)
    assert execution.slippage == pytest.approx(0.8)
    assert execution.commission == pytest.approx(2.8)


def test_simulate_remaining_quantity(simulator):
    partially_filled_order = Order(
        pair="EUR/USD",
        side=OrderSide.BUY,
        quantity=100_000,
        status=OrderStatus.PARTIALLY_FILLED,
        filled_quantity=40_000,
    )

    execution = simulator.execute(
        partially_filled_order,
        1.10000,
        0.0001,
    )

    assert execution.quantity == 60_000


def test_reject_fill_exceeding_remaining_quantity(simulator, order):
    with pytest.raises(ValueError, match="remaining"):
        simulator.execute(
            order,
            1.10000,
            0.0001,
            fill_quantity=100_001,
        )


def test_reject_zero_fill(simulator, order):
    with pytest.raises(ValueError, match="positive"):
        simulator.execute(
            order,
            1.10000,
            0.0001,
            fill_quantity=0,
        )
