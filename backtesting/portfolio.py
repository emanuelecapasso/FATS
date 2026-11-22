"""Portfolio management for the FATS trading engine."""

from math import isfinite

from pydantic import Field, model_validator

from backtesting.models import Execution, OrderSide, Position
from core.schemas import StrictBaseModel


class Portfolio(StrictBaseModel):
    """Manages account balance, positions and portfolio performance."""

    initial_balance: float = Field(..., gt=0, allow_inf_nan=False)
    balance: float = Field(default=0.0, allow_inf_nan=False)
    realized_pnl: float = Field(default=0.0, allow_inf_nan=False)
    positions: dict[str, Position] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def initialize_balance(cls, values):
        """Use initial balance when no current balance is provided."""
        if isinstance(values, dict) and "balance" not in values:
            values = {**values, "balance": values.get("initial_balance")}
        return values

    @property
    def unrealized_pnl(self) -> float:
        """Calculate total unrealized P&L."""
        return sum(position.unrealized_pnl for position in self.positions.values())

    @property
    def equity(self) -> float:
        """Calculate account equity."""
        return self.balance + self.unrealized_pnl

    @property
    def available_balance(self) -> float:
        """Return balance without margin reservations."""
        return self.balance

    @property
    def gross_exposure(self) -> float:
        """Calculate total gross position exposure."""
        return sum(
            position.quantity * position.current_price
            for position in self.positions.values()
        )

    def open_position(self, execution: Execution) -> Position:
        """Open a position using an executed order."""
        position = Position(
            pair=execution.pair,
            side=execution.side,
            quantity=execution.quantity,
            entry_price=execution.price,
            current_price=execution.price,
            opened_at=execution.executed_at,
        )

        costs = execution.spread_cost + execution.commission + execution.slippage

        self.positions[position.position_id] = position
        self.balance -= costs
        self.realized_pnl -= costs

        return position

    def close_position(
        self,
        position_id: str,
        execution: Execution,
    ) -> float:
        """Close all or part of a position and return realized trade P&L."""
        position = self.positions.get(position_id)

        if position is None:
            raise ValueError(f"Position not found: {position_id}")

        if execution.pair != position.pair:
            raise ValueError("Execution pair does not match position")

        if execution.side == position.side:
            raise ValueError("Closing execution must have opposite side")

        if execution.quantity > position.quantity:
            raise ValueError("Closing quantity exceeds open position")

        if position.side == OrderSide.BUY:
            gross_pnl = (execution.price - position.entry_price) * execution.quantity
        else:
            gross_pnl = (position.entry_price - execution.price) * execution.quantity

        costs = execution.spread_cost + execution.commission + execution.slippage

        net_pnl = gross_pnl - costs

        self.balance += net_pnl
        self.realized_pnl += net_pnl

        remaining_quantity = position.quantity - execution.quantity

        if remaining_quantity == 0:
            del self.positions[position_id]
        else:
            self.positions[position_id] = position.model_copy(
                update={
                    "quantity": remaining_quantity,
                    "current_price": execution.price,
                }
            )

        return net_pnl

    def update_market_prices(self, prices: dict[str, float]) -> None:
        """Update market prices for all matching open positions."""
        for pair, price in prices.items():
            if not isfinite(price) or price <= 0:
                raise ValueError(f"Invalid market price for {pair}")

        for position_id, position in list(self.positions.items()):
            if position.pair in prices:
                self.positions[position_id] = position.model_copy(
                    update={"current_price": prices[position.pair]}
                )
