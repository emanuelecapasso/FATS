"""Strategy interface used by the backtesting engine."""

from collections.abc import Sequence
from typing import Protocol

from pydantic import Field, model_validator

from core.schemas import MarketBar, SignalDirection, StrictBaseModel


class BacktestSignal(StrictBaseModel):
    """Trading decision enriched with execution and risk parameters."""

    direction: SignalDirection
    quantity: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
    )
    stop_loss: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
    )
    take_profit: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
    )

    @model_validator(mode="after")
    def validate_trade_parameters(self):
        if self.direction == SignalDirection.FLAT:
            if any(
                value is not None
                for value in (self.quantity, self.stop_loss, self.take_profit)
            ):
                raise ValueError("FLAT signals cannot contain trade parameters.")
            return self

        if self.quantity is None or self.stop_loss is None:
            raise ValueError("LONG and SHORT signals require quantity and stop_loss.")

        return self


class BacktestStrategy(Protocol):
    """Contract implemented by the quantitative strategy adapter."""

    def generate_signal(
        self,
        bars: Sequence[MarketBar],
    ) -> BacktestSignal:
        """Evaluate closed historical bars and return a trading decision."""
        ...
