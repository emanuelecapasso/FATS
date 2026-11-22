"""Chronological, offline simulation around the deterministic quant engine."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from backtesting.schemas import (
    BacktestMetrics,
    BacktestRequest,
    BacktestResult,
    BacktestTrade,
    canonical_json_payload,
)
from core.schemas import (
    DataQuality,
    EvaluationRequest,
    MarketDataSnapshot,
    SignalDirection,
    Timeframe,
    TimeframeSeries,
)
from quantitative.engine import ENGINE_VERSION, QuantitativeEngine
from quantitative.exceptions import InsufficientHistoryError, InvalidMarketDataError
from quantitative.ports import RiskEvaluator, Strategy

REQUIRED_TIMEFRAMES = (Timeframe.M5, Timeframe.M15, Timeframe.H1)
M5_DURATION = timedelta(minutes=5)


@dataclass(frozen=True)
class _PendingTarget:
    direction: SignalDirection
    quantity: float
    reasons: tuple[str, ...]


@dataclass
class _OpenPosition:
    side: SignalDirection
    quantity: float
    entry_timestamp: datetime
    market_entry_price: float
    entry_reasons: list[str]
    entry_commission: float
    entry_slippage: float


class BacktestRunner:
    """Replays closed M5 events and calls the same engine used by the backend.

    Decisions are made at each closed M5 timestamp. A target generated at that
    close is filled at the next observed closed M5 bar's open. Any open position
    is liquidated at the last closed in-range bar's close. No real orders or
    network access are performed.
    """

    def __init__(self, strategy: Strategy, risk_evaluator: RiskEvaluator) -> None:
        self.strategy = strategy
        self.engine = QuantitativeEngine(strategy, risk_evaluator)

    async def run(self, request: BacktestRequest) -> BacktestResult:
        self._validate_input_series(request.market_data)
        missing = [
            timeframe
            for timeframe in REQUIRED_TIMEFRAMES
            if timeframe not in request.market_data.series
        ]
        if missing:
            names = ", ".join(tf.value for tf in missing)
            raise InvalidMarketDataError(f"Backtest requires timeframes: {names}.")

        all_m5 = request.market_data.series[Timeframe.M5].bars
        closed_m5 = [bar for bar in all_m5 if bar.is_closed is True]
        in_range_bars = [
            bar for bar in all_m5 if request.start <= bar.timestamp <= request.end
        ]
        skipped_decisions = sum(1 for bar in in_range_bars if bar.is_closed is not True)

        temporal_gaps = 0
        previous_closed_timestamp = None
        gap_timestamps = set()
        for bar in closed_m5:
            if previous_closed_timestamp is not None:
                delta = bar.timestamp - previous_closed_timestamp
                if delta.total_seconds() % M5_DURATION.total_seconds() != 0:
                    raise InvalidMarketDataError(
                        f"M5 timestamp is not aligned to a five-minute interval: {bar.timestamp}."
                    )
                if (
                    delta > M5_DURATION
                    and request.start <= bar.timestamp <= request.end
                ):
                    temporal_gaps += 1
                    gap_timestamps.add(bar.timestamp)
            previous_closed_timestamp = bar.timestamp

        costs = request.costs
        commission_rate = costs.commission_bps / 10_000.0
        slippage_rate = costs.slippage_bps / 10_000.0
        trades: list[BacktestTrade] = []
        open_position: _OpenPosition | None = None
        pending_target: _PendingTarget | None = None
        last_in_range_bar = None

        def close_position(
            market_price: float, timestamp, exit_reasons: list[str]
        ) -> None:
            nonlocal open_position
            if open_position is None:
                return
            if market_price <= 0:
                raise InvalidMarketDataError("Backtest fill prices must be positive.")

            exit_commission = open_position.quantity * market_price * commission_rate
            exit_slippage = open_position.quantity * market_price * slippage_rate
            side_sign = 1.0 if open_position.side == SignalDirection.LONG else -1.0
            gross_pnl = (
                side_sign
                * (market_price - open_position.market_entry_price)
                * open_position.quantity
            )
            total_commission = open_position.entry_commission + exit_commission
            total_slippage = open_position.entry_slippage + exit_slippage
            trades.append(
                BacktestTrade(
                    side=open_position.side,
                    quantity=open_position.quantity,
                    entry_timestamp=open_position.entry_timestamp,
                    exit_timestamp=timestamp,
                    entry_market_price=open_position.market_entry_price,
                    exit_market_price=market_price,
                    gross_pnl=gross_pnl,
                    commission=total_commission,
                    slippage_cost=total_slippage,
                    net_pnl=gross_pnl - total_commission - total_slippage,
                    entry_reasons=open_position.entry_reasons,
                    exit_reasons=exit_reasons,
                )
            )
            open_position = None

        def execute_target(target: _PendingTarget, bar) -> None:
            nonlocal open_position
            market_price = bar.open
            if market_price <= 0:
                raise InvalidMarketDataError("Backtest fill prices must be positive.")
            fill_timestamp = bar.timestamp - M5_DURATION

            if open_position is not None and (
                target.direction == SignalDirection.FLAT
                or target.direction != open_position.side
            ):
                close_position(market_price, fill_timestamp, list(target.reasons))

            if target.direction != SignalDirection.FLAT and open_position is None:
                quantity = target.quantity
                if quantity <= 0:
                    return
                open_position = _OpenPosition(
                    side=target.direction,
                    quantity=quantity,
                    entry_timestamp=fill_timestamp,
                    market_entry_price=market_price,
                    entry_reasons=list(target.reasons),
                    entry_commission=quantity * market_price * commission_rate,
                    entry_slippage=quantity * market_price * slippage_rate,
                )

        for bar in closed_m5:
            if bar.timestamp > request.end:
                break

            if bar.timestamp >= request.start:
                last_in_range_bar = bar

            if pending_target is not None and bar.timestamp >= request.start:
                execute_target(pending_target, bar)
                pending_target = None

            if bar.timestamp < request.start:
                continue
            if bar.timestamp in gap_timestamps:
                skipped_decisions += 1
                continue

            decision_snapshot = self._snapshot_as_of(request.market_data, bar.timestamp)
            position = (
                open_position.side
                if open_position is not None
                else SignalDirection.FLAT
            )
            configuration = request.configuration.model_copy(
                update={"current_position": position}
            )
            evaluation_request = EvaluationRequest(
                market_data=decision_snapshot,
                configuration=configuration,
            )
            try:
                response = await self.engine.evaluate(evaluation_request)
            except InsufficientHistoryError:
                skipped_decisions += 1
                continue

            risk = response.risk_decision
            direction = response.signal.direction
            if not risk.approved or direction == SignalDirection.FLAT:
                pending_target = _PendingTarget(
                    SignalDirection.FLAT, 0.0, tuple(response.signal.motivational_codes)
                )
            elif open_position is not None and direction == open_position.side:
                # A hold preserves the original size; the simulator does not pyramid.
                pending_target = _PendingTarget(
                    direction,
                    open_position.quantity,
                    tuple(response.signal.motivational_codes),
                )
            else:
                pending_target = _PendingTarget(
                    direction,
                    risk.recommended_quantity,
                    tuple(response.signal.motivational_codes),
                )
        unfilled_final_signal = (
            pending_target is not None
            and pending_target.direction != SignalDirection.FLAT
            and (
                open_position is None or pending_target.direction != open_position.side
            )
        )
        if open_position is not None and last_in_range_bar is not None:
            close_position(
                last_in_range_bar.close,
                last_in_range_bar.timestamp,
                ["end_of_period_liquidation"],
            )

        gross_pnl = sum(trade.gross_pnl for trade in trades)
        net_pnl = sum(trade.net_pnl for trade in trades)
        total_commission = sum(trade.commission for trade in trades)
        total_slippage = sum(trade.slippage_cost for trade in trades)
        winning = sum(1 for trade in trades if trade.net_pnl > 0)
        losing = sum(1 for trade in trades if trade.net_pnl < 0)
        win_rate = winning / len(trades) if trades else 0.0

        equity = request.initial_capital
        peak_equity = equity
        max_drawdown = 0.0
        for trade in trades:
            equity += trade.net_pnl
            peak_equity = max(peak_equity, equity)
            max_drawdown = max(max_drawdown, peak_equity - equity)

        input_fingerprint, run_id = self._fingerprints(request)
        metrics = BacktestMetrics(
            trade_count=len(trades),
            winning_trades=winning,
            losing_trades=losing,
            win_rate=win_rate,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            total_commission=total_commission,
            total_slippage_cost=total_slippage,
            return_pct=net_pnl / request.initial_capital * 100.0,
            closed_trade_max_drawdown=max_drawdown,
            skipped_decisions=skipped_decisions,
            temporal_gaps=temporal_gaps,
        )

        return BacktestResult(
            run_id=run_id,
            input_fingerprint=input_fingerprint,
            pair=request.market_data.pair,
            start=request.start,
            end=request.end,
            strategy_id=self.strategy.strategy_id,
            strategy_version=self.strategy.version,
            engine_version=ENGINE_VERSION,
            configuration=request.configuration,
            costs=costs,
            initial_capital=request.initial_capital,
            final_equity=request.initial_capital + net_pnl,
            metrics=metrics,
            trades=trades,
            unfilled_final_signal=unfilled_final_signal,
        )

    @staticmethod
    def _validate_input_series(snapshot: MarketDataSnapshot) -> None:
        for timeframe, series in snapshot.series.items():
            if series.quality == DataQuality.STALE:
                raise InvalidMarketDataError(
                    f"STALE data is not allowed for {timeframe.value}."
                )
            previous_timestamp = None
            for bar in series.bars:
                if bar.timestamp > snapshot.as_of:
                    raise InvalidMarketDataError(
                        f"Future bar in {timeframe.value}: {bar.timestamp} > {snapshot.as_of}."
                    )
                if (
                    previous_timestamp is not None
                    and bar.timestamp <= previous_timestamp
                ):
                    raise InvalidMarketDataError(
                        f"Bars in {timeframe.value} are duplicated or out of order."
                    )
                previous_timestamp = bar.timestamp

    @staticmethod
    def _snapshot_as_of(source: MarketDataSnapshot, as_of) -> MarketDataSnapshot:
        available_series = {}
        for timeframe, series in source.series.items():
            available_bars = [
                bar
                for bar in series.bars
                if bar.timestamp <= as_of and bar.is_closed is True
            ]
            available_series[timeframe] = TimeframeSeries(
                timeframe=timeframe,
                bars=available_bars,
                provenance=series.provenance,
                quality=series.quality,
            )
        return MarketDataSnapshot(
            pair=source.pair,
            as_of=as_of,
            series=available_series,
        )

    def _fingerprints(self, request: BacktestRequest) -> tuple[str, str]:
        snapshot_json = canonical_json_payload(
            request.market_data.model_dump(mode="json")
        )
        input_fingerprint = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
        run_payload = {
            "request": request.model_dump(mode="json"),
            "input_fingerprint": input_fingerprint,
            "strategy_id": self.strategy.strategy_id,
            "strategy_version": self.strategy.version,
            "engine_version": ENGINE_VERSION,
        }
        run_id = hashlib.sha256(
            canonical_json_payload(run_payload).encode("utf-8")
        ).hexdigest()
        return input_fingerprint, run_id
