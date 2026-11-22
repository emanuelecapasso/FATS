"""Order lifecycle management for the FATS trading engine."""

from math import isfinite

from pydantic import Field

from backtesting.models import Order, OrderStatus
from core.schemas import StrictBaseModel


class OrderManager(StrictBaseModel):
    """Manages order registration, validation and lifecycle."""

    orders: dict[str, Order] = Field(default_factory=dict)

    def submit_order(self, order: Order) -> Order:
        if order.order_id in self.orders:
            raise ValueError(f"Duplicate order ID: {order.order_id}")

        if order.status != OrderStatus.PENDING:
            raise ValueError("New orders must have PENDING status")

        self.orders[order.order_id] = order
        return order

    def get_order(self, order_id: str) -> Order:
        order = self.orders.get(order_id)

        if order is None:
            raise ValueError(f"Order not found: {order_id}")

        return order

    def record_fill(self, order_id: str, quantity: float) -> Order:
        """Record an execution and derive the resulting order status."""
        order = self.get_order(order_id)

        if not isfinite(quantity) or quantity <= 0:
            raise ValueError("Fill quantity must be finite and positive")

        if order.status not in {
            OrderStatus.PENDING,
            OrderStatus.PARTIALLY_FILLED,
        }:
            raise ValueError("Order cannot receive additional fills")

        filled_quantity = order.filled_quantity + quantity

        if filled_quantity > order.quantity:
            raise ValueError("Fill exceeds remaining order quantity")

        status = (
            OrderStatus.FILLED
            if filled_quantity == order.quantity
            else OrderStatus.PARTIALLY_FILLED
        )

        updated = Order.model_validate(
            {
                **order.model_dump(),
                "filled_quantity": filled_quantity,
                "status": status,
            }
        )

        self.orders[order_id] = updated
        return updated

    def cancel_order(self, order_id: str) -> Order:
        order = self.get_order(order_id)

        if order.status not in {
            OrderStatus.PENDING,
            OrderStatus.PARTIALLY_FILLED,
        }:
            raise ValueError("Only active orders can be cancelled")

        updated = Order.model_validate(
            {**order.model_dump(), "status": OrderStatus.CANCELLED}
        )
        self.orders[order_id] = updated
        return updated

    def reject_order(self, order_id: str) -> Order:
        order = self.get_order(order_id)

        if order.status != OrderStatus.PENDING:
            raise ValueError("Only pending orders can be rejected")

        updated = Order.model_validate(
            {**order.model_dump(), "status": OrderStatus.REJECTED}
        )
        self.orders[order_id] = updated
        return updated

    def list_orders(self) -> list[Order]:
        return list(self.orders.values())
