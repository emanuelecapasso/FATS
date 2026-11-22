
from datetime import datetime

import pytest
from pydantic import ValidationError

from backtesting.models import (
    Execution,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)


def test_order_creation():
    order = Order(
        pair="EUR/USD",
        side=OrderSide.BUY,
        quantity=1000,
    )

    assert order.pair == "EUR/USD"
    assert order.side == OrderSide.BUY
    assert order.order_type == OrderType.MARKET
    assert order.status == OrderStatus.PENDING
    assert order.quantity == 1000
    assert order.order_id


def test_order_rejects_invalid_quantity():
    with pytest.raises(ValidationError):
        Order(
            pair="EUR/USD",
            side=OrderSide.BUY,
            quantity=0,
        )


def test_order_rejects_non_utc_timestamp():
    with pytest.raises(ValidationError):
        Order(
            pair="EUR/USD",
            side=OrderSide.BUY,
            quantity=1000,
            created_at=datetime(2026, 1, 1),
        )


def test_position_long_unrealized_pnl():
    position = Position(
        pair="EUR/USD",
        side=OrderSide.BUY,
        quantity=10000,
        entry_price=1.1000,
        current_price=1.1050,
    )

    assert position.unrealized_pnl == pytest.approx(50.0)


def test_position_short_unrealized_pnl():
    position = Position(
        pair="EUR/USD",
        side=OrderSide.SELL,
        quantity=10000,
        entry_price=1.1000,
        current_price=1.0950,
    )

    assert position.unrealized_pnl == pytest.approx(50.0)


def test_execution_creation():
    execution = Execution(
        order_id="order-001",
        pair="EUR/USD",
        side=OrderSide.BUY,
        quantity=1000,
        price=1.1000,
        spread_cost=0.5,
        commission=0.2,
        slippage=0.1,
    )

    assert execution.order_id == "order-001"
    assert execution.price == 1.1000
    assert execution.spread_cost == 0.5
    assert execution.commission == 0.2
    assert execution.slippage == 0.1
