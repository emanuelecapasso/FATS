"""Pre-trade risk controls and position sizing for FATS."""

from math import isfinite

from pydantic import Field

from backtesting.models import Order, OrderSide
from core.schemas import RiskDecision, StrictBaseModel


class RiskManager(StrictBaseModel):
    """Evaluates proposed orders against configurable risk limits."""

    max_risk_per_trade: float = Field(default=0.01, gt=0, le=1, allow_inf_nan=False)
    max_exposure_pct: float = Field(default=0.50, gt=0, allow_inf_nan=False)
    max_drawdown_pct: float = Field(default=0.20, ge=0, le=1, allow_inf_nan=False)

    def calculate_position_size(
        self,
        equity: float,
        entry_price: float,
        stop_loss: float,
        quote_to_account_rate: float = 1.0,
    ) -> float:
        """Calculate maximum units based on the permitted monetary risk."""
        values = (equity, entry_price, stop_loss, quote_to_account_rate)

        if not all(isfinite(value) for value in values):
            raise ValueError("Position sizing inputs must be finite")

        if equity <= 0 or entry_price <= 0 or stop_loss <= 0:
            raise ValueError("Equity and prices must be positive")

        if quote_to_account_rate <= 0:
            raise ValueError("Conversion rate must be positive")

        stop_distance = abs(entry_price - stop_loss)

        if stop_distance == 0:
            raise ValueError("Stop loss must differ from entry price")

        risk_amount = equity * self.max_risk_per_trade
        risk_per_unit = stop_distance * quote_to_account_rate

        return risk_amount / risk_per_unit

    def evaluate_order(
        self,
        order: Order,
        equity: float,
        entry_price: float,
        stop_loss: float,
        current_exposure: float = 0.0,
        peak_equity: float | None = None,
        quote_to_account_rate: float = 1.0,
    ) -> RiskDecision:
        """Approve or reject an order and recommend its maximum quantity.

        Monetary values and exposure must be expressed in account currency.
        current_exposure must include all existing open positions.
        peak_equity is the portfolio's historical equity high-water mark.
        """
        values = (
            equity,
            entry_price,
            stop_loss,
            current_exposure,
            quote_to_account_rate,
        )

        if not all(isfinite(value) for value in values):
            raise ValueError("Risk evaluation inputs must be finite")

        if peak_equity is not None and not isfinite(peak_equity):
            raise ValueError("Peak equity must be finite")

        if equity <= 0 or entry_price <= 0 or stop_loss <= 0:
            raise ValueError("Equity and prices must be positive")

        if current_exposure < 0:
            raise ValueError("Current exposure cannot be negative")

        if quote_to_account_rate <= 0:
            raise ValueError("Conversion rate must be positive")

        if peak_equity is not None and peak_equity < equity:
            raise ValueError("Peak equity cannot be below current equity")

        reasons: list[str] = []

        # A long position needs a stop below entry.
        # A short position needs a stop above entry.
        if order.side == OrderSide.BUY and stop_loss >= entry_price:
            reasons.append("Long position requires stop loss below entry")

        if order.side == OrderSide.SELL and stop_loss <= entry_price:
            reasons.append("Short position requires stop loss above entry")

        if peak_equity is not None:
            drawdown = (peak_equity - equity) / peak_equity

            if drawdown >= self.max_drawdown_pct:
                reasons.append("Maximum portfolio drawdown reached")

        if not reasons:
            risk_quantity = self.calculate_position_size(
                equity=equity,
                entry_price=entry_price,
                stop_loss=stop_loss,
                quote_to_account_rate=quote_to_account_rate,
            )

            max_exposure = equity * self.max_exposure_pct
            remaining_exposure = max(0.0, max_exposure - current_exposure)

            exposure_per_unit = entry_price * quote_to_account_rate
            exposure_quantity = remaining_exposure / exposure_per_unit

            recommended_quantity = min(
                risk_quantity,
                exposure_quantity,
            )

            if order.quantity > recommended_quantity:
                reasons.append("Order quantity exceeds permitted risk or exposure")
        else:
            recommended_quantity = 0.0

        return RiskDecision(
            approved=not reasons,
            reasons=reasons,
            recommended_quantity=recommended_quantity,
        )
