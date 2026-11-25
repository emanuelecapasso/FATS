"""Offline deterministic multi-instrument portfolio replay."""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal

from backtesting.runner import M5_DURATION
from backtesting.schemas import (
    BacktestCostModel,
    InstrumentBacktestMetrics,
    InstrumentEquityPoint,
    PortfolioBacktestRequest,
    PortfolioBacktestResult,
    PortfolioEquityPoint,
    PortfolioInstrument,
    PortfolioInstrumentResult,
    PortfolioInstrumentStatus,
    PortfolioTrade,
    canonical_json_payload,
)
from core.schemas import (
    DataQuality,
    MarketBar,
    MarketDataSnapshot,
    PortfolioPositionState,
    RiskAccountState,
    Signal,
    SignalDirection,
    Timeframe,
    TimeframeSeries,
    VolumeType,
)
from quantitative.engine import ENGINE_VERSION
from quantitative.exceptions import InsufficientHistoryError, InvalidMarketDataError
from quantitative.ports import RiskEvaluator, Strategy

PORTFOLIO_BACKTEST_VERSION = "1.0.0"
_REQUIRED_TIMEFRAMES = (Timeframe.M5, Timeframe.M15, Timeframe.H1)


@dataclass
class _Position:
    side: SignalDirection
    quantity: float
    entry_timestamp: datetime
    entry_price: float
    entry_conversion_rate: float
    entry_commission: float
    entry_slippage: float
    entry_reasons: list[str]


@dataclass
class _PendingTarget:
    direction: SignalDirection
    signal: Signal
    reasons: list[str]


@dataclass
class _InstrumentState:
    spec: PortfolioInstrument
    position: _Position | None = None
    pending: _PendingTarget | None = None
    latest_bar: MarketBar | None = None
    previous_closed_timestamp: datetime | None = None
    gap_timestamps: set[datetime] = field(default_factory=set)
    temporal_gaps: int = 0
    observed_m5_events: int = 0
    skipped_decisions: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    trades: list[PortfolioTrade] = field(default_factory=list)
    equity_curve: list[InstrumentEquityPoint] = field(default_factory=list)


class PortfolioBacktestRunner:
    """Replays a shared account over multiple instruments in stable ID order.

    Strategy signals at a common timestamp are calculated against one shared
    pre-allocation account snapshot. Risk sizing then reserves exposure in
    ascending instrument_id order. Pending targets fill at the next observed
    M5 open and risk is checked again at that fill using only data available at
    the fill timestamp.
    """

    def __init__(self, strategy: Strategy, risk_evaluator: RiskEvaluator) -> None:
        self.strategy = strategy
        self.risk_evaluator = risk_evaluator

    async def run(self, request: PortfolioBacktestRequest) -> PortfolioBacktestResult:
        if any(
            item.configuration.strategy_id.value != self.strategy.strategy_id
            for item in request.instruments
        ):
            raise ValueError("An instrument configuration does not match the strategy.")

        states = {
            item.instrument_id: _InstrumentState(item)
            for item in sorted(request.instruments, key=lambda spec: spec.instrument_id)
        }
        events: set[datetime] = set()
        portfolio_equity_curve: list[PortfolioEquityPoint] = []
        bars_by_time: dict[str, dict[datetime, MarketBar]] = {}
        for instrument_id, state in states.items():
            series = state.spec.market_data.series.get(Timeframe.M5)
            bars = series.bars if series else []
            bars_by_time[instrument_id] = {}
            previous = None
            for bar in bars:
                if (
                    request.start <= bar.timestamp <= request.end
                    and bar.is_closed is True
                ):
                    events.add(bar.timestamp)
                    state.observed_m5_events += 1
                    bars_by_time[instrument_id][bar.timestamp] = bar
                    if previous is not None and bar.timestamp - previous > M5_DURATION:
                        state.temporal_gaps += 1
                        state.gap_timestamps.add(bar.timestamp)
                    previous = bar.timestamp
                elif request.start <= bar.timestamp <= request.end:
                    self._skip(state, "incomplete_or_unknown_m5_bar")
            for rate in state.spec.conversion_rates:
                if request.start <= rate.timestamp <= request.end:
                    events.add(rate.timestamp)

        daily_start = request.account_state.daily_start_equity
        current_day = request.start.date()
        peak_equity = request.account_state.peak_equity
        for timestamp in sorted(events):
            if timestamp < request.start or timestamp > request.end:
                continue
            event_bars = {
                instrument_id: bars_by_time[instrument_id][timestamp]
                for instrument_id in states
                if timestamp in bars_by_time[instrument_id]
            }

            await self._fill_pending(
                request,
                states,
                event_bars,
                timestamp,
                daily_start,
                peak_equity,
            )

            # Make all same-time closes visible together before any strategy call.
            for instrument_id in sorted(event_bars):
                state = states[instrument_id]
                bar = event_bars[instrument_id]
                previous = state.previous_closed_timestamp
                if previous is not None and bar.timestamp - previous > M5_DURATION:
                    state.gap_timestamps.add(bar.timestamp)
                state.previous_closed_timestamp = bar.timestamp
                state.latest_bar = bar

            valuation = self._value_account(request, states, timestamp)
            if timestamp.date() != current_day:
                current_day = timestamp.date()
                daily_start = valuation["equity"]
            peak_equity = max(peak_equity, valuation["equity"])
            portfolio_equity_curve.append(
                self._append_marks(states, timestamp, valuation)
            )
            if event_bars:
                for instrument_id in sorted(event_bars):
                    state = states[instrument_id]
                    if timestamp in state.gap_timestamps:
                        self._skip(state, "temporal_gap_decision_skipped")
                        continue
                    signal_data = self._decision_snapshot(state.spec, timestamp)
                    if any(
                        timeframe not in signal_data.series
                        or signal_data.series[timeframe].quality == DataQuality.STALE
                        for timeframe in _REQUIRED_TIMEFRAMES
                    ):
                        self._skip(state, "required_timeframe_missing_or_stale")
                        continue
                    conversion = self._rate_at(
                        state.spec,
                        timestamp,
                        request.risk_configuration.max_price_age_seconds,
                    )
                    if conversion is None:
                        self._skip(state, "conversion_missing_or_stale")
                        continue
                    try:
                        signal = self.strategy.evaluate(
                            signal_data,
                            state.spec.configuration.model_copy(
                                update={
                                    "current_position": (
                                        state.position.side
                                        if state.position
                                        else SignalDirection.FLAT
                                    )
                                }
                            ),
                        )
                    except InsufficientHistoryError:
                        self._skip(state, "insufficient_history")
                        continue
                    if not isinstance(signal, Signal):
                        raise TypeError("Strategy must return a canonical Signal.")
                    state.pending = _PendingTarget(
                        direction=signal.direction,
                        signal=signal,
                        reasons=list(signal.motivational_codes),
                    )

                self._allocate_simultaneous_targets(
                    request,
                    states,
                    event_bars,
                    valuation,
                    daily_start,
                    peak_equity,
                    timestamp,
                )

        final_timestamp = max(events, default=None)
        if final_timestamp is not None:
            await self._liquidate(request, states, final_timestamp)
            final_valuation = self._value_account(request, states, final_timestamp)
            final_point = self._replace_final_marks(
                states, final_timestamp, final_valuation
            )
            if (
                portfolio_equity_curve
                and portfolio_equity_curve[-1].timestamp == final_timestamp
            ):
                portfolio_equity_curve[-1] = final_point
            else:
                portfolio_equity_curve.append(final_point)

        return self._build_result(request, states, portfolio_equity_curve)

    def _allocate_simultaneous_targets(
        self,
        request: PortfolioBacktestRequest,
        states: dict[str, _InstrumentState],
        event_bars: dict[str, MarketBar],
        valuation: dict,
        daily_start: float,
        peak_equity: float,
        timestamp: datetime,
    ) -> None:
        reserved_delta = 0.0
        for instrument_id in sorted(event_bars):
            state = states[instrument_id]
            pending = state.pending
            if pending is None:
                continue
            conversion = self._rate_at(
                state.spec,
                timestamp,
                request.risk_configuration.max_price_age_seconds,
            )
            if conversion is None:
                self._skip(state, "conversion_missing_or_stale")
                state.pending = None
                continue
            current_position = state.position
            current_direction = (
                current_position.side if current_position else SignalDirection.FLAT
            )
            configuration = state.spec.configuration.model_copy(
                update={"current_position": current_direction}
            )
            risk_account = self._risk_account(
                request,
                states,
                instrument_id,
                valuation,
                daily_start,
                peak_equity,
                conversion,
                reserved_delta,
            )
            risk = self.risk_evaluator.evaluate(
                pending.signal,
                self._decision_snapshot(state.spec, timestamp),
                configuration,
                request.risk_configuration,
                risk_account,
            )
            if not risk.approved:
                pending.reasons.extend(risk.reasons)
                self._skip(state, "fill_risk_rejected")
                if current_position is not None:
                    pending.direction = current_position.side
                    target_quantity = current_position.quantity
                else:
                    pending.direction = SignalDirection.FLAT
                    target_quantity = 0.0
            elif pending.direction == SignalDirection.FLAT:
                target_quantity = 0.0
            elif risk.recommended_quantity > 0:
                target_quantity = (
                    current_position.quantity
                    if current_position and pending.direction == current_position.side
                    else risk.recommended_quantity
                )
            else:
                self._skip(state, "risk_returned_non_positive_quantity")
                pending.direction = SignalDirection.FLAT
                target_quantity = 0.0

            mark_price = event_bars[instrument_id].close
            current_notional = (
                current_position.quantity * mark_price * conversion
                if current_position
                else 0.0
            )
            target_notional = (
                target_quantity * mark_price * conversion
                if pending.direction != SignalDirection.FLAT
                else 0.0
            )
            if target_quantity > 0 and not (
                not risk.approved
                and current_position is not None
                and pending.direction == current_position.side
            ):
                target_quantity = self._cap_quantity(
                    request,
                    target_quantity,
                    valuation["equity"],
                    valuation["gross_exposure"],
                    reserved_delta,
                    current_notional,
                    mark_price,
                    conversion,
                )
                target_notional = target_quantity * mark_price * conversion
                if (
                    current_position
                    and pending.direction == current_position.side
                    and target_quantity < current_position.quantity
                ):
                    pending.direction = SignalDirection.FLAT
                    target_quantity = 0.0
                    target_notional = 0.0
            reserved_delta += target_notional - current_notional
            pending.direction = (
                pending.direction if target_quantity > 0 else SignalDirection.FLAT
            )

    async def _fill_pending(
        self,
        request: PortfolioBacktestRequest,
        states: dict[str, _InstrumentState],
        event_bars: dict[str, MarketBar],
        timestamp: datetime,
        daily_start: float,
        peak_equity: float,
    ) -> None:
        fill_timestamp = timestamp - M5_DURATION
        for instrument_id in sorted(event_bars):
            state = states[instrument_id]
            pending = state.pending
            if pending is None:
                continue
            bar = event_bars[instrument_id]
            m5_series = state.spec.market_data.series.get(Timeframe.M5)
            if m5_series is None or m5_series.quality == DataQuality.STALE:
                if state.position is not None:
                    raise InvalidMarketDataError(
                        f"STALE M5 data cannot execute a pending close for {instrument_id}."
                    )
                self._skip(state, "m5_missing_or_stale_at_fill")
                state.pending = None
                continue
            conversion = self._rate_at(
                state.spec,
                fill_timestamp,
                request.risk_configuration.max_price_age_seconds,
            )
            if conversion is None:
                if state.position is not None:
                    raise InvalidMarketDataError(
                        f"Missing or stale conversion rate to close {instrument_id} at {fill_timestamp}."
                    )
                self._skip(state, "fill_conversion_missing_or_stale")
                state.pending = None
                continue

            fill_valuation = self._value_account(
                request,
                states,
                fill_timestamp,
                price_overrides={
                    event_instrument_id: event_bar.open
                    for event_instrument_id, event_bar in event_bars.items()
                },
            )
            configuration = state.spec.configuration.model_copy(
                update={
                    "current_position": (
                        state.position.side if state.position else SignalDirection.FLAT
                    )
                }
            )
            risk_account = self._risk_account(
                request,
                states,
                instrument_id,
                fill_valuation,
                daily_start,
                max(peak_equity, fill_valuation["equity"]),
                conversion,
                0.0,
            )
            fill_snapshot = self._fill_snapshot(state.spec, fill_timestamp, bar.open)
            risk = self.risk_evaluator.evaluate(
                pending.signal,
                fill_snapshot,
                configuration,
                request.risk_configuration,
                risk_account,
            )
            if not risk.approved:
                pending.reasons.extend(risk.reasons)
                if state.position is not None:
                    pending.direction = state.position.side
                else:
                    pending.direction = SignalDirection.FLAT

            current_notional = (
                state.position.quantity * bar.open * conversion
                if state.position
                else 0.0
            )
            if not risk.approved and state.position is not None:
                fill_quantity = state.position.quantity
            else:
                fill_quantity = self._cap_quantity(
                    request,
                    risk.recommended_quantity if risk.approved else 0.0,
                    fill_valuation["equity"],
                    fill_valuation["gross_exposure"],
                    0.0,
                    current_notional,
                    bar.open,
                    conversion,
                )
            if (
                state.position is not None
                and pending.direction == state.position.side
                and fill_quantity < state.position.quantity
            ):
                pending.direction = SignalDirection.FLAT
                pending.reasons.append("risk:aggregate_exposure_reduction")

            if state.position is not None and (
                pending.direction == SignalDirection.FLAT
                or pending.direction != state.position.side
            ):
                self._close_position(
                    request,
                    state,
                    bar.open,
                    conversion,
                    fill_timestamp,
                    pending.reasons,
                )
            if pending.direction != SignalDirection.FLAT and state.position is None:
                quantity = fill_quantity
                if not risk.approved or quantity <= 0:
                    self._skip(state, "fill_risk_rejected")
                else:
                    commission, slippage = self._entry_costs(
                        state.spec.costs, quantity, bar.open, conversion
                    )
                    state.position = _Position(
                        side=pending.direction,
                        quantity=quantity,
                        entry_timestamp=fill_timestamp,
                        entry_price=bar.open,
                        entry_conversion_rate=conversion,
                        entry_commission=commission,
                        entry_slippage=slippage,
                        entry_reasons=pending.reasons,
                    )
            state.pending = None

    def _close_position(
        self,
        request: PortfolioBacktestRequest,
        state: _InstrumentState,
        price: float,
        conversion: float,
        timestamp: datetime,
        reasons: list[str],
    ) -> None:
        position = state.position
        if position is None:
            return
        if price <= 0:
            raise InvalidMarketDataError("Portfolio fill prices must be positive.")
        commission, slippage = self._entry_costs(
            state.spec.costs, position.quantity, price, conversion
        )
        sign = 1.0 if position.side == SignalDirection.LONG else -1.0
        gross = sign * (price - position.entry_price) * position.quantity * conversion
        total_commission = position.entry_commission + commission
        total_slippage = position.entry_slippage + slippage
        state.trades.append(
            PortfolioTrade(
                instrument_id=state.spec.instrument_id,
                side=position.side,
                quantity=position.quantity,
                entry_timestamp=position.entry_timestamp,
                exit_timestamp=timestamp,
                entry_market_price=position.entry_price,
                exit_market_price=price,
                entry_conversion_rate=position.entry_conversion_rate,
                exit_conversion_rate=conversion,
                gross_pnl=gross,
                commission=total_commission,
                slippage_cost=total_slippage,
                net_pnl=gross - total_commission - total_slippage,
                entry_reasons=position.entry_reasons,
                exit_reasons=reasons,
            )
        )
        state.position = None

    @staticmethod
    def _entry_costs(
        costs: BacktestCostModel, quantity: float, price: float, conversion: float
    ) -> tuple[float, float]:
        notional = quantity * price * conversion
        return (
            notional * costs.commission_bps / 10_000.0,
            notional * costs.slippage_bps / 10_000.0,
        )

    @staticmethod
    def _cap_quantity(
        request: PortfolioBacktestRequest,
        requested_quantity: float,
        equity: float,
        gross_exposure: float,
        reserved_delta: float,
        current_notional: float,
        price: float,
        conversion: float,
    ) -> float:
        if requested_quantity <= 0 or price <= 0 or conversion <= 0 or equity <= 0:
            return 0.0
        exposure_limit = equity * request.risk_configuration.max_aggregate_exposure_pct
        exposure_without_current = max(
            0.0, gross_exposure + reserved_delta - current_notional
        )
        remaining = max(0.0, exposure_limit - exposure_without_current)
        max_quantity = remaining / (price * conversion)
        sized = min(requested_quantity, max_quantity)
        step = Decimal(str(request.risk_configuration.quantity_step))
        return float(
            (Decimal(str(sized)) / step).to_integral_value(rounding=ROUND_FLOOR) * step
        )

    def _value_account(
        self,
        request: PortfolioBacktestRequest,
        states: dict[str, _InstrumentState],
        timestamp: datetime,
        *,
        price_overrides: dict[str, float] | None = None,
    ) -> dict:
        price_overrides = price_overrides or {}
        positions = {}
        unrealized = 0.0
        gross_exposure = 0.0
        cumulative_commission = sum(
            trade.commission for state in states.values() for trade in state.trades
        )
        cumulative_slippage = sum(
            trade.slippage_cost for state in states.values() for trade in state.trades
        )
        realized = sum(
            trade.net_pnl for state in states.values() for trade in state.trades
        )
        for instrument_id, state in states.items():
            position = state.position
            if position is None:
                continue
            m5_series = state.spec.market_data.series.get(Timeframe.M5)
            if m5_series is None or m5_series.quality == DataQuality.STALE:
                raise InvalidMarketDataError(
                    f"STALE M5 data cannot mark open position {instrument_id}."
                )
            price = price_overrides.get(instrument_id)
            if price is None:
                if state.latest_bar is None:
                    raise InvalidMarketDataError(
                        f"No current M5 mark is available for open position {instrument_id}."
                    )
                price = state.latest_bar.close
                age = timestamp - state.latest_bar.timestamp
                if (
                    age.total_seconds() < 0
                    or age.total_seconds()
                    > request.risk_configuration.max_price_age_seconds
                ):
                    raise InvalidMarketDataError(
                        f"Stale M5 mark for open position {instrument_id} at {timestamp}."
                    )
            rate = self._rate_at(
                state.spec,
                timestamp,
                request.risk_configuration.max_price_age_seconds,
            )
            if rate is None:
                raise InvalidMarketDataError(
                    f"Missing or stale conversion rate for open position {instrument_id} at {timestamp}."
                )
            sign = 1.0 if position.side == SignalDirection.LONG else -1.0
            position_unrealized = (
                sign * (price - position.entry_price) * position.quantity * rate
                - position.entry_commission
                - position.entry_slippage
            )
            exposure = price * position.quantity * rate
            unrealized += position_unrealized
            gross_exposure += exposure
            cumulative_commission += position.entry_commission
            cumulative_slippage += position.entry_slippage
            positions[instrument_id] = PortfolioPositionState(
                instrument_id=instrument_id,
                direction=position.side,
                quantity=position.quantity,
                mark_price=price,
                price_to_account_currency_rate=rate,
                gross_exposure=exposure,
                unrealized_pnl=position_unrealized,
                valued_at=timestamp,
            )
        equity = request.initial_capital + realized + unrealized
        return {
            "equity": equity,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "gross_exposure": gross_exposure,
            "positions": positions,
            "cumulative_commission": cumulative_commission,
            "cumulative_slippage": cumulative_slippage,
        }

    def _risk_account(
        self,
        request: PortfolioBacktestRequest,
        states: dict[str, _InstrumentState],
        instrument_id: str,
        valuation: dict,
        daily_start: float,
        peak_equity: float,
        conversion: float,
        reserved_delta: float,
    ) -> RiskAccountState:
        if valuation["equity"] <= 0:
            raise InvalidMarketDataError(
                "Portfolio equity is non-positive; risk evaluation stopped."
            )
        state = states[instrument_id]
        position = state.position
        closed_trades = sorted(
            (trade for instrument_state in states.values() for trade in instrument_state.trades),
            key=lambda trade: trade.exit_timestamp,
        )
        consecutive_losses = 0
        for trade in reversed(closed_trades):
            if trade.net_pnl >= 0.0:
                break
            consecutive_losses += 1
        return RiskAccountState(
            equity=valuation["equity"],
            daily_start_equity=daily_start,
            peak_equity=max(peak_equity, valuation["equity"], daily_start),
            gross_exposure=valuation["gross_exposure"],
            reserved_exposure_delta=reserved_delta,
            price_to_account_currency_rate=conversion,
            position_direction=position.side if position else SignalDirection.FLAT,
            position_quantity=position.quantity if position else 0.0,
            consecutive_losses=consecutive_losses,
            instrument_id=instrument_id,
            portfolio_positions=valuation["positions"],
        )

    def _append_marks(
        self,
        states: dict[str, _InstrumentState],
        timestamp: datetime,
        valuation: dict,
    ) -> PortfolioEquityPoint:
        for instrument_id, state in states.items():
            position = state.position
            position_mark = valuation["positions"].get(instrument_id)
            realized = sum(trade.net_pnl for trade in state.trades)
            unrealized = position_mark.unrealized_pnl if position_mark else 0.0
            exposure = position_mark.gross_exposure if position_mark else 0.0
            state.equity_curve.append(
                InstrumentEquityPoint(
                    timestamp=timestamp,
                    net_pnl=realized + unrealized,
                    unrealized_pnl=unrealized,
                    gross_exposure=exposure,
                    position_direction=position.side
                    if position
                    else SignalDirection.FLAT,
                    position_quantity=position.quantity if position else 0.0,
                )
            )

        return PortfolioEquityPoint(
            timestamp=timestamp,
            equity=valuation["equity"],
            realized_pnl=valuation["realized_pnl"],
            unrealized_pnl=valuation["unrealized_pnl"],
            cumulative_commission=valuation["cumulative_commission"],
            cumulative_slippage_cost=valuation["cumulative_slippage"],
            gross_exposure=valuation["gross_exposure"],
            positions=valuation["positions"],
        )

    def _replace_final_marks(
        self,
        states: dict[str, _InstrumentState],
        timestamp: datetime,
        valuation: dict,
    ) -> PortfolioEquityPoint:
        for instrument_id, state in states.items():
            position = state.position
            position_mark = valuation["positions"].get(instrument_id)
            state_point = InstrumentEquityPoint(
                timestamp=timestamp,
                net_pnl=sum(trade.net_pnl for trade in state.trades)
                + (position_mark.unrealized_pnl if position_mark else 0.0),
                unrealized_pnl=position_mark.unrealized_pnl if position_mark else 0.0,
                gross_exposure=position_mark.gross_exposure if position_mark else 0.0,
                position_direction=position.side if position else SignalDirection.FLAT,
                position_quantity=position.quantity if position else 0.0,
            )
            if state.equity_curve and state.equity_curve[-1].timestamp == timestamp:
                state.equity_curve[-1] = state_point
            else:
                state.equity_curve.append(state_point)
        return PortfolioEquityPoint(
            timestamp=timestamp,
            equity=valuation["equity"],
            realized_pnl=valuation["realized_pnl"],
            unrealized_pnl=valuation["unrealized_pnl"],
            cumulative_commission=valuation["cumulative_commission"],
            cumulative_slippage_cost=valuation["cumulative_slippage"],
            gross_exposure=valuation["gross_exposure"],
            positions=valuation["positions"],
        )

    async def _liquidate(
        self,
        request: PortfolioBacktestRequest,
        states: dict[str, _InstrumentState],
        timestamp: datetime,
    ) -> None:
        for instrument_id in sorted(states):
            state = states[instrument_id]
            if state.position is None:
                continue
            if state.latest_bar is None:
                raise InvalidMarketDataError(f"No final price for {instrument_id}.")
            if timestamp - state.latest_bar.timestamp > timedelta(
                seconds=request.risk_configuration.max_price_age_seconds
            ):
                raise InvalidMarketDataError(
                    f"Stale final M5 price for {instrument_id}."
                )
            conversion = self._rate_at(
                state.spec,
                timestamp,
                request.risk_configuration.max_price_age_seconds,
            )
            if conversion is None:
                raise InvalidMarketDataError(
                    f"Missing or stale final conversion rate for {instrument_id}."
                )
            self._close_position(
                request,
                state,
                state.latest_bar.close,
                conversion,
                timestamp,
                ["end_of_period_liquidation"],
            )

    def _build_result(
        self,
        request: PortfolioBacktestRequest,
        states: dict[str, _InstrumentState],
        equity_curve: list[PortfolioEquityPoint],
    ) -> PortfolioBacktestResult:
        trades = [trade for state in states.values() for trade in state.trades]
        trades.sort(
            key=lambda item: (
                item.exit_timestamp,
                item.instrument_id,
                item.entry_timestamp,
            )
        )
        net_pnl = sum(trade.net_pnl for trade in trades)
        gross_pnl = sum(trade.gross_pnl for trade in trades)
        commission = sum(trade.commission for trade in trades)
        slippage = sum(trade.slippage_cost for trade in trades)
        winners = sum(trade.net_pnl > 0 for trade in trades)
        losers = sum(trade.net_pnl < 0 for trade in trades)
        equity_values = [
            request.initial_capital,
            *(point.equity for point in equity_curve),
        ]
        peak = request.initial_capital
        max_drawdown = 0.0
        max_drawdown_pct = 0.0
        for value in equity_values[1:]:
            peak = max(peak, value)
            drawdown = peak - value
            max_drawdown = max(max_drawdown, drawdown)
            max_drawdown_pct = max(max_drawdown_pct, drawdown / peak)
        profit_factor = (
            sum(trade.net_pnl for trade in trades if trade.net_pnl > 0)
            / abs(sum(trade.net_pnl for trade in trades if trade.net_pnl < 0))
            if losers
            else None
        )
        metrics = {
            "trade_count": len(trades),
            "winning_trades": winners,
            "losing_trades": losers,
            "win_rate": winners / len(trades) if trades else 0.0,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "total_commission": commission,
            "total_slippage_cost": slippage,
            "return_pct": net_pnl / request.initial_capital * 100.0,
            "max_drawdown_abs": max_drawdown,
            "max_drawdown_pct": max_drawdown_pct,
            "average_gross_exposure": (
                sum(point.gross_exposure for point in equity_curve) / len(equity_curve)
                if equity_curve
                else 0.0
            ),
            "max_gross_exposure": max(
                (point.gross_exposure for point in equity_curve), default=0.0
            ),
            "profit_factor": profit_factor,
            "expectancy_per_trade": net_pnl / len(trades) if trades else None,
            "time_in_market_pct": (
                sum(point.gross_exposure > 0 for point in equity_curve)
                / len(equity_curve)
                if equity_curve
                else None
            ),
            "skipped_decisions": sum(
                state.skipped_decisions for state in states.values()
            ),
            "temporal_gaps": sum(state.temporal_gaps for state in states.values()),
        }
        dataset = {
            item.instrument_id: {
                "market_data": item.market_data.model_dump(mode="json"),
                "conversion_rates": [
                    rate.model_dump(mode="json") for rate in item.conversion_rates
                ],
            }
            for item in request.instruments
        }
        input_fingerprint = hashlib.sha256(
            canonical_json_payload(dataset).encode("utf-8")
        ).hexdigest()
        request_payload = request.model_dump(mode="json")
        request_payload["instruments"] = sorted(
            request_payload["instruments"], key=lambda item: item["instrument_id"]
        )
        run_payload = {
            "request": request_payload,
            "input_fingerprint": input_fingerprint,
            "strategy_id": self.strategy.strategy_id,
            "strategy_version": self.strategy.version,
            "engine_version": ENGINE_VERSION,
            "portfolio_backtest_version": PORTFOLIO_BACKTEST_VERSION,
        }
        run_id = hashlib.sha256(
            canonical_json_payload(run_payload).encode("utf-8")
        ).hexdigest()
        instrument_results = {}
        for instrument_id, state in states.items():
            own_trades = state.trades
            instrument_results[instrument_id] = PortfolioInstrumentResult(
                instrument_id=instrument_id,
                status=(
                    PortfolioInstrumentStatus.COMPLETED
                    if own_trades
                    else PortfolioInstrumentStatus.NO_TRADES
                    if state.observed_m5_events
                    else PortfolioInstrumentStatus.NO_DATA
                ),
                configuration=state.spec.configuration,
                costs=state.spec.costs,
                conversion_rates=state.spec.conversion_rates,
                metrics=InstrumentBacktestMetrics(
                    trade_count=len(own_trades),
                    winning_trades=sum(trade.net_pnl > 0 for trade in own_trades),
                    losing_trades=sum(trade.net_pnl < 0 for trade in own_trades),
                    gross_pnl=sum(trade.gross_pnl for trade in own_trades),
                    net_pnl=sum(trade.net_pnl for trade in own_trades),
                    total_commission=sum(trade.commission for trade in own_trades),
                    total_slippage_cost=sum(
                        trade.slippage_cost for trade in own_trades
                    ),
                    temporal_gaps=state.temporal_gaps,
                    skipped_decisions=state.skipped_decisions,
                ),
                trades=own_trades,
                equity_curve=state.equity_curve,
                skipped_reasons=state.skipped_reasons,
                unfilled_final_signal=state.pending is not None
                and state.pending.direction != SignalDirection.FLAT,
            )
        return PortfolioBacktestResult(
            run_id=run_id,
            input_fingerprint=input_fingerprint,
            portfolio_backtest_version=PORTFOLIO_BACKTEST_VERSION,
            strategy_id=self.strategy.strategy_id,
            strategy_version=self.strategy.version,
            engine_version=ENGINE_VERSION,
            account_currency=request.account_currency,
            start=request.start,
            end=request.end,
            initial_capital=request.initial_capital,
            final_equity=request.initial_capital + net_pnl,
            initial_account_state=request.account_state,
            risk_configuration=request.risk_configuration,
            configurations={
                key: state.spec.configuration for key, state in states.items()
            },
            costs={key: state.spec.costs for key, state in states.items()},
            metrics=metrics,
            equity_curve=equity_curve,
            instruments=instrument_results,
        )

    @staticmethod
    def _decision_snapshot(
        instrument: PortfolioInstrument, as_of: datetime
    ) -> MarketDataSnapshot:
        series = {
            timeframe: TimeframeSeries(
                timeframe=timeframe,
                bars=[
                    bar
                    for bar in item.bars
                    if bar.timestamp <= as_of and bar.is_closed is True
                ],
                provenance=item.provenance,
                quality=item.quality,
            )
            for timeframe, item in instrument.market_data.series.items()
        }
        return MarketDataSnapshot(
            pair=instrument.instrument_id, as_of=as_of, series=series
        )

    @staticmethod
    def _fill_snapshot(
        instrument: PortfolioInstrument, timestamp: datetime, price: float
    ) -> MarketDataSnapshot:
        source = instrument.market_data
        series = {}
        for timeframe, item in source.series.items():
            bars = [
                bar
                for bar in item.bars
                if bar.timestamp <= timestamp and bar.is_closed is True
            ]
            if timeframe == Timeframe.M5:
                bars = [bar for bar in bars if bar.timestamp < timestamp]
                bars.append(
                    MarketBar(
                        timestamp=timestamp,
                        open=price,
                        high=price,
                        low=price,
                        close=price,
                        volume_type=(
                            item.bars[-1].volume_type
                            if item.bars
                            else VolumeType.UNAVAILABLE
                        ),
                        is_closed=True,
                    )
                )
            series[timeframe] = TimeframeSeries(
                timeframe=timeframe,
                bars=bars,
                provenance=item.provenance,
                quality=item.quality,
            )
        return MarketDataSnapshot(
            pair=instrument.instrument_id, as_of=timestamp, series=series
        )

    @staticmethod
    def _rate_at(
        instrument: PortfolioInstrument,
        timestamp: datetime,
        max_age_seconds: int,
    ) -> float | None:
        for item in reversed(instrument.conversion_rates):
            if item.timestamp <= timestamp:
                age = timestamp - item.timestamp
                if age.total_seconds() < 0 or age.total_seconds() > max_age_seconds:
                    return None
                return item.rate
        return None

    @staticmethod
    def _skip(state: _InstrumentState, reason: str) -> None:
        state.skipped_decisions += 1
        state.skipped_reasons[reason] = state.skipped_reasons.get(reason, 0) + 1
