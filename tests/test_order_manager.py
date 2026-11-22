import pytest

from backtesting.models import Order, OrderSide, OrderStatus
from backtesting.order_manager import OrderManager


def create_order():
    return Order(
        order_id="order-001",
        pair="EUR/USD",
        side=OrderSide.BUY,
        quantity=1000,
    )


def test_submit_order():
    manager = OrderManager()
    order = manager.submit_order(create_order())

    assert order.status == OrderStatus.PENDING
    assert len(manager.orders) == 1


def test_reject_duplicate_order():
    manager = OrderManager()
    manager.submit_order(create_order())

    with pytest.raises(ValueError):
        manager.submit_order(create_order())


def test_get_order():
    manager = OrderManager()
    manager.submit_order(create_order())

    assert manager.get_order("order-001").pair == "EUR/USD"


def test_get_unknown_order():
    manager = OrderManager()

    with pytest.raises(ValueError):
        manager.get_order("unknown")


def test_fill_order():
    manager = OrderManager()
    manager.submit_order(create_order())

    order = manager.record_fill("order-001", 1000)

    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity == 1000


def test_partial_fill_then_fill():
    manager = OrderManager()
    manager.submit_order(create_order())

    partial = manager.record_fill("order-001", 400)

    assert partial.status == OrderStatus.PARTIALLY_FILLED
    assert partial.filled_quantity == 400

    complete = manager.record_fill("order-001", 600)

    assert complete.status == OrderStatus.FILLED
    assert complete.filled_quantity == 1000


def test_cancel_order():
    manager = OrderManager()
    manager.submit_order(create_order())

    order = manager.cancel_order("order-001")

    assert order.status == OrderStatus.CANCELLED


def test_reject_order():
    manager = OrderManager()
    manager.submit_order(create_order())

    order = manager.reject_order("order-001")

    assert order.status == OrderStatus.REJECTED


def test_cannot_cancel_filled_order():
    manager = OrderManager()
    manager.submit_order(create_order())
    manager.record_fill("order-001", 1000)

    with pytest.raises(ValueError):
        manager.cancel_order("order-001")


def test_list_orders():
    manager = OrderManager()
    manager.submit_order(create_order())

    assert len(manager.list_orders()) == 1


def test_fill_cannot_exceed_order_quantity():
    manager = OrderManager()
    manager.submit_order(create_order())

    with pytest.raises(ValueError):
        manager.record_fill("order-001", 1001)


def test_partial_order_can_be_cancelled():
    manager = OrderManager()
    manager.submit_order(create_order())
    manager.record_fill("order-001", 400)

    order = manager.cancel_order("order-001")

    assert order.status == OrderStatus.CANCELLED
    assert order.filled_quantity == 400


def test_cancelled_order_cannot_receive_more_fills():
    manager = OrderManager()
    manager.submit_order(create_order())
    manager.cancel_order("order-001")

    with pytest.raises(ValueError):
        manager.record_fill("order-001", 100)
