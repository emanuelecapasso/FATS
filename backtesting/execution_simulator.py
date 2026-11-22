"""Simulated order execution with configurable transaction costs."""

from datetime import UTC, datetime
from math import isfinite

from pydantic import Field

from backtesting.models import Execution, Order, OrderStatus, OrderType
from core.schemas import StrictBaseModel


class ExecutionSimulator(StrictBaseModel):
    """Simulates market order execution and transaction costs."""

    spread_pips: float = Field(default=1.0, ge=0, allow_inf_nan=False)
    slippage_pips: float = Field(default=0.2, ge=0, allow_inf_nan=False)
    commission_per_lot: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    lot_size: float = Field(default=100_000, gt=0, allow_inf_nan=False)

    def execute(
        self,
        order: Order,
        market_price: float,
        pip_size: float,
        executed_at: datetime | None = None,
        fill_quantity: float | None = None,
    ) -> Execution:
        """Execute a market order, optionally partially."""

        if order.status not in {
            OrderStatus.PENDING,
            OrderStatus.PARTIALLY_FILLED,
        }:
            raise ValueError("Only pending or partially filled orders can be executed")

        if order.order_type != OrderType.MARKET:
            raise ValueError("Only MARKET orders are supported")

        if not isfinite(market_price) or market_price <= 0:
            raise ValueError("Market price must be finite and positive")

        if not isfinite(pip_size) or pip_size <= 0:
            raise ValueError("Pip size must be finite and positive")

        remaining_quantity = order.quantity - order.filled_quantity
        quantity = remaining_quantity if fill_quantity is None else fill_quantity

        if not isfinite(quantity) or quantity <= 0:
            raise ValueError("Fill quantity must be finite and positive")

        if quantity > remaining_quantity:
            raise ValueError("Fill quantity exceeds remaining order quantity")

        timestamp = executed_at or datetime.now(UTC)

        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Execution timestamp must be timezone-aware")

        if timestamp.utcoffset().total_seconds() != 0:
            raise ValueError("Execution timestamp must be in UTC")

        spread_cost = quantity * pip_size * self.spread_pips / 2
        slippage_cost = quantity * pip_size * self.slippage_pips
        commission = quantity / self.lot_size * self.commission_per_lot

        return Execution(
            order_id=order.order_id,
            pair=order.pair,
            side=order.side,
            quantity=quantity,
            price=market_price,
            spread_cost=spread_cost,
            commission=commission,
            slippage=slippage_cost,
            executed_at=timestamp,
        )
