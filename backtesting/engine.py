"""Historical backtesting engine."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from backtesting.execution_simulator import ExecutionSimulator
from backtesting.models import (
    Execution,
    Order,
    OrderSide,
    OrderStatus,
)
from backtesting.order_manager import OrderManager
from backtesting.portfolio import Portfolio
from backtesting.risk_manager import RiskManager
from backtesting.strategy import BacktestStrategy
from core.schemas import MarketBar, SignalDirection
from data.market_calendar import ForexMarketCalendar


@dataclass(frozen=True)
class BacktestResult:
    """Immutable summary of a completed backtest."""

    pair: str
    initial_balance: float
    final_balance: float
    realized_pnl: float
    equity_curve: tuple[tuple[datetime, float], ...]
    orders: tuple[Order, ...]
    executions: tuple[Execution, ...]
    rejected_orders: int


class BacktestingEngine:
    """Runs historical simulations using injected trading components."""

    def __init__(
        self,
        initial_balance: float,
        candle_duration: timedelta,
        pair: str = "EURUSD",
        pip_size: float = 0.0001,
        quote_to_account_rate: float = 1.0,
        calendar: ForexMarketCalendar | None = None,
        risk_manager: RiskManager | None = None,
        execution_simulator: ExecutionSimulator | None = None,
    ) -> None:
        if initial_balance <= 0:
            raise ValueError("Initial balance must be positive")

        if candle_duration <= timedelta(0):
            raise ValueError("Candle duration must be positive")

        if pip_size <= 0:
            raise ValueError("Pip size must be positive")

        if quote_to_account_rate <= 0:
            raise ValueError("Conversion rate must be positive")

        normalized_pair = pair.upper().replace("/", "")

        if len(normalized_pair) != 6 or not normalized_pair.isalpha():
            raise ValueError("Pair must be a valid six-letter Forex pair")

        if normalized_pair[3:] != "USD":
            raise ValueError("Version 1 supports USD-quoted pairs only")

        self.initial_balance = initial_balance
        self.candle_duration = candle_duration
        self.pair = normalized_pair
        self.pip_size = pip_size
        self.quote_to_account_rate = quote_to_account_rate

        self.calendar = calendar or ForexMarketCalendar()
        self.risk_manager = risk_manager or RiskManager()
        self.execution_simulator = execution_simulator or ExecutionSimulator()

    @staticmethod
    def _validate_bars(bars: Sequence[MarketBar]) -> None:
        if len(bars) < 2:
            raise ValueError("At least two historical bars are required")

        previous_timestamp = None

        for bar in bars:
            timestamp = bar.timestamp

            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("Bar timestamps must be timezone-aware")

            if timestamp.utcoffset() != timedelta(0):
                raise ValueError("Bar timestamps must be in UTC")

            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise ValueError("Bars must have unique, ascending timestamps")

            previous_timestamp = timestamp

    @staticmethod
    def _validate_signal_prices(
        signal,
        entry_price: float,
    ) -> None:
        if signal.direction == SignalDirection.LONG:
            if signal.stop_loss >= entry_price:
                raise ValueError("LONG stop loss must be below entry")

            if signal.take_profit is not None and signal.take_profit <= entry_price:
                raise ValueError("LONG take profit must be above entry")

        elif signal.direction == SignalDirection.SHORT:
            if signal.stop_loss <= entry_price:
                raise ValueError("SHORT stop loss must be above entry")

            if signal.take_profit is not None and signal.take_profit >= entry_price:
                raise ValueError("SHORT take profit must be below entry")

    def run(
        self,
        bars: Sequence[MarketBar],
        strategy: BacktestStrategy,
    ) -> BacktestResult:
        """Run a complete historical simulation."""

        self._validate_bars(bars)

        portfolio = Portfolio(initial_balance=self.initial_balance)
        order_manager = OrderManager()

        executions: list[Execution] = []
        equity_curve: list[tuple[datetime, float]] = []

        # Position-specific protective levels.
        protective_levels: dict[str, tuple[float, float | None]] = {}

        pending_signal = None
        peak_equity = self.initial_balance

        def execute_order(
            order: Order,
            price: float,
            timestamp: datetime,
        ) -> Execution:
            execution = self.execution_simulator.execute(
                order=order,
                market_price=price,
                pip_size=self.pip_size,
                executed_at=timestamp,
            )

            order_manager.record_fill(order.order_id, execution.quantity)

            executions.append(execution)
            return execution

        def close_position(
            position_id: str,
            price: float,
            timestamp: datetime,
        ) -> None:
            position = portfolio.positions[position_id]

            close_side = (
                OrderSide.SELL if position.side == OrderSide.BUY else OrderSide.BUY
            )

            close_order = Order(
                pair=self.pair,
                side=close_side,
                quantity=position.quantity,
                created_at=timestamp,
            )

            order_manager.submit_order(close_order)

            execution = execute_order(
                close_order,
                price,
                timestamp,
            )

            portfolio.close_position(position_id, execution)
            protective_levels.pop(position_id, None)

        for index, bar in enumerate(bars):
            timestamp = bar.timestamp

            if not self.calendar.is_candle_expected(
                timestamp,
                self.candle_duration,
            ):
                continue

            # 1. Execute the signal generated at the previous candle close.
            if pending_signal is not None and not portfolio.positions:
                signal = pending_signal
                pending_signal = None

                side = (
                    OrderSide.BUY
                    if signal.direction == SignalDirection.LONG
                    else OrderSide.SELL
                )

                order = Order(
                    pair=self.pair,
                    side=side,
                    quantity=signal.quantity,
                    created_at=timestamp,
                )

                order_manager.submit_order(order)

                try:
                    self._validate_signal_prices(signal, bar.open)

                    decision = self.risk_manager.evaluate_order(
                        order=order,
                        equity=portfolio.equity,
                        entry_price=bar.open,
                        stop_loss=signal.stop_loss,
                        current_exposure=portfolio.gross_exposure,
                        peak_equity=peak_equity,
                        quote_to_account_rate=self.quote_to_account_rate,
                    )

                    if not decision.approved:
                        order_manager.reject_order(order.order_id)
                    else:
                        execution = execute_order(
                            order,
                            bar.open,
                            timestamp,
                        )

                        position = portfolio.open_position(execution)

                        protective_levels[position.position_id] = (
                            signal.stop_loss,
                            signal.take_profit,
                        )

                except ValueError:
                    current_order = order_manager.get_order(order.order_id)

                    if current_order.status == OrderStatus.PENDING:
                        order_manager.reject_order(order.order_id)

            # 2. Check stop-loss and take-profit against the current OHLC bar.
            for position_id, position in list(portfolio.positions.items()):
                stop_loss, take_profit = protective_levels[position_id]

                exit_price = None

                if position.side == OrderSide.BUY:
                    if (
                        bar.open <= stop_loss
                        or take_profit is not None
                        and bar.open >= take_profit
                    ):
                        exit_price = bar.open
                    elif bar.low <= stop_loss:
                        exit_price = stop_loss
                    elif take_profit is not None and bar.high >= take_profit:
                        exit_price = take_profit

                else:
                    if (
                        bar.open >= stop_loss
                        or take_profit is not None
                        and bar.open <= take_profit
                    ):
                        exit_price = bar.open
                    elif bar.high >= stop_loss:
                        exit_price = stop_loss
                    elif take_profit is not None and bar.low <= take_profit:
                        exit_price = take_profit

                if exit_price is not None:
                    close_position(
                        position_id,
                        exit_price,
                        timestamp,
                    )

            # 3. Mark open positions to market at candle close.
            portfolio.update_market_prices({self.pair: bar.close})

            current_equity = portfolio.equity
            peak_equity = max(peak_equity, current_equity)

            equity_curve.append((timestamp, current_equity))

            # 4. Generate a signal using only candles already closed.
            if not portfolio.positions and index < len(bars) - 1:
                signal = strategy.generate_signal(bars[: index + 1])

                if signal.direction != SignalDirection.FLAT:
                    pending_signal = signal

        # 5. Liquidate any remaining position at the last valid candle close.
        valid_bars = [
            bar
            for bar in bars
            if self.calendar.is_candle_expected(
                bar.timestamp,
                self.candle_duration,
            )
        ]

        if portfolio.positions and valid_bars:
            last_bar = valid_bars[-1]
            position_ids = list(portfolio.positions)

            for position_id in position_ids:
                close_position(
                    position_id,
                    last_bar.close,
                    last_bar.timestamp + self.candle_duration,
                )

            equity_curve.append(
                (
                    last_bar.timestamp + self.candle_duration,
                    portfolio.equity,
                )
            )

        return BacktestResult(
            pair=self.pair,
            initial_balance=self.initial_balance,
            final_balance=portfolio.balance,
            realized_pnl=portfolio.realized_pnl,
            equity_curve=tuple(equity_curve),
            orders=tuple(order_manager.list_orders()),
            executions=tuple(executions),
            rejected_orders=sum(
                order.status == OrderStatus.REJECTED
                for order in order_manager.list_orders()
            ),
        )
