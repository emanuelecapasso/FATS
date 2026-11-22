"""Core domain models for the FATS trading engine."""

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from core.schemas import StrictBaseModel


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class OrderStatus(str, Enum):
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class Order(StrictBaseModel):
    """Represents an order submitted to the trading engine."""

    order_id: str = Field(default_factory=lambda: str(uuid4()))
    pair: str = Field(..., min_length=1)
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    quantity: float = Field(..., gt=0, allow_inf_nan=False)
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    limit_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if value.utcoffset().total_seconds() != 0:
            raise ValueError("created_at must be in UTC")
        return value

    @model_validator(mode="after")
    def validate_filled_quantity(self):
        if self.filled_quantity > self.quantity:
            raise ValueError("Filled quantity cannot exceed order quantity")

        if self.status == OrderStatus.PENDING and self.filled_quantity != 0:
            raise ValueError("Pending orders cannot have filled quantity")

        if (
            self.status == OrderStatus.PARTIALLY_FILLED
            and not 0 < self.filled_quantity < self.quantity
        ):
            raise ValueError("Partially filled orders require a partial quantity")

        if self.status == OrderStatus.FILLED and self.filled_quantity != self.quantity:
            raise ValueError("Filled orders must have their full quantity executed")

        if self.status == OrderStatus.REJECTED and self.filled_quantity != 0:
            raise ValueError("Rejected orders cannot have filled quantity")

        return self


class Position(StrictBaseModel):
    """Represents an open trading position."""

    position_id: str = Field(default_factory=lambda: str(uuid4()))
    pair: str = Field(..., min_length=1)
    side: OrderSide
    quantity: float = Field(..., gt=0, allow_inf_nan=False)
    entry_price: float = Field(..., gt=0, allow_inf_nan=False)
    current_price: float = Field(..., gt=0, allow_inf_nan=False)
    opened_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def unrealized_pnl(self) -> float:
        """Calculate unrealized P&L in quote currency, excluding costs."""
        price_difference = self.current_price - self.entry_price

        if self.side == OrderSide.SELL:
            price_difference = -price_difference

        return price_difference * self.quantity


class Execution(StrictBaseModel):
    """Represents a simulated or real order execution."""

    execution_id: str = Field(default_factory=lambda: str(uuid4()))
    order_id: str = Field(..., min_length=1)
    pair: str = Field(..., min_length=1)
    side: OrderSide
    quantity: float = Field(..., gt=0, allow_inf_nan=False)
    price: float = Field(..., gt=0, allow_inf_nan=False)
    spread_cost: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    commission: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    slippage: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
