from datetime import UTC, datetime, timedelta

import pytest

from backtesting.runner import BacktestRunner
from backtesting.schemas import BacktestCostModel, BacktestRequest
from core.schemas import (
    DataProvenance,
    DataQuality,
    DataSourceKind,
    MarketBar,
    MarketDataSnapshot,
    RiskAccountState,
    RiskConfiguration,
    RiskDecision,
    Signal,
    SignalDirection,
    StrategyConfiguration,
    StrategyId,
    Timeframe,
    TimeframeSeries,
    VolumeType,
)
from quantitative.risk import DeterministicRiskEvaluator

BASE_TIME = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)


class ScriptedStrategy:
    strategy_id = StrategyId.TREND_FOLLOWING.value
    version = "test-1.0"

    def __init__(self):
        self.observed_timestamps = []

    def required_history(self, configuration):
        return {
            Timeframe.M5: 1,
            Timeframe.M15: 1,
            Timeframe.H1: 1,
        }

    def evaluate(self, snapshot, configuration):
        self.observed_timestamps.append(
            (
                snapshot.as_of,
                {
                    tf: [bar.timestamp for bar in s.bars]
                    for tf, s in snapshot.series.items()
                },
            )
        )
        for series in snapshot.series.values():
            assert all(bar.timestamp <= snapshot.as_of for bar in series.bars)
            assert all(bar.is_closed is True for bar in series.bars)

        count = len(snapshot.series[Timeframe.M5].bars)
        if count == 1:
            direction = SignalDirection.LONG
            code = "scripted_entry"
        elif count == 3:
            direction = SignalDirection.FLAT
            code = "scripted_exit"
        elif configuration.current_position == SignalDirection.LONG:
            direction = SignalDirection.LONG
            code = "scripted_hold"
        else:
            direction = SignalDirection.FLAT
            code = "scripted_flat"
        return Signal(direction=direction, confidence=0.8, motivational_codes=[code])


class NoTradeStrategy(ScriptedStrategy):
    def evaluate(self, snapshot, configuration):
        self.observed_timestamps.append(
            (
                snapshot.as_of,
                {
                    tf: [bar.timestamp for bar in s.bars]
                    for tf, s in snapshot.series.items()
                },
            )
        )
        return Signal(
            direction=SignalDirection.FLAT,
            confidence=0.5,
            motivational_codes=["no_setup"],
        )


class AllowRisk:
    def evaluate(
        self, signal, snapshot, configuration, risk_configuration, account_state
    ):
        return RiskDecision(approved=True, reasons=[], recommended_quantity=1.0)


def _bar(timestamp, open_price, *, is_closed=True):
    return MarketBar(
        timestamp=timestamp,
        open=open_price,
        high=open_price + 1.0,
        low=open_price - 1.0,
        close=open_price + 0.5,
        volume_type=VolumeType.UNAVAILABLE,
        is_closed=is_closed,
    )


def _request(
    *,
    gaps=False,
    costs=None,
    include_future_higher_bar=False,
    m5_closed=True,
    conversion_rate=1.0,
):
    offsets = [0, 5, 15, 20, 25, 30] if gaps else [0, 5, 10, 15, 20, 25]
    timestamps = [BASE_TIME + timedelta(minutes=offset) for offset in offsets]
    as_of = timestamps[-1]
    provenance = DataProvenance(
        kind=DataSourceKind.BROKER,
        provider_id="backtest-fixture",
        received_at=as_of,
    )

    m5_bars = [
        _bar(timestamp, 100.0 + index, is_closed=m5_closed)
        for index, timestamp in enumerate(timestamps)
    ]
    m15_bars = [_bar(timestamps[0], 100.0)]
    h1_bars = [_bar(timestamps[0], 100.0)]
    if include_future_higher_bar:
        m15_bars.append(_bar(timestamps[2], 101.0))

    snapshot = MarketDataSnapshot(
        pair="EURUSD",
        as_of=as_of,
        series={
            Timeframe.M5: TimeframeSeries(
                timeframe=Timeframe.M5,
                bars=m5_bars,
                provenance=provenance,
                quality=DataQuality.COMPLETE,
            ),
            Timeframe.M15: TimeframeSeries(
                timeframe=Timeframe.M15,
                bars=m15_bars,
                provenance=provenance,
                quality=DataQuality.COMPLETE,
            ),
            Timeframe.H1: TimeframeSeries(
                timeframe=Timeframe.H1,
                bars=h1_bars,
                provenance=provenance,
                quality=DataQuality.COMPLETE,
            ),
        },
    )
    return BacktestRequest(
        market_data=snapshot,
        configuration=StrategyConfiguration(strategy_id=StrategyId.TREND_FOLLOWING),
        risk_configuration=RiskConfiguration(
            max_risk_per_position_pct=0.01,
            max_aggregate_exposure_pct=10.0,
            max_daily_loss_pct=0.03,
            max_drawdown_pct=0.10,
            stop_distance_pct=0.01,
            max_price_age_seconds=3600,
            quantity_step=0.01,
        ),
        account_state=RiskAccountState(
            equity=10_000.0,
            daily_start_equity=10_000.0,
            peak_equity=10_000.0,
            gross_exposure=0.0,
            price_to_account_currency_rate=conversion_rate,
            position_direction=SignalDirection.FLAT,
            position_quantity=0.0,
            consecutive_losses=0,
        ),
        start=timestamps[0],
        end=timestamps[-1],
        initial_capital=10_000.0,
        costs=costs or BacktestCostModel(),
    )


def _with_m5_bars(request, bars):
    series = request.market_data.series[Timeframe.M5].model_copy(update={"bars": bars})
    snapshot = request.market_data.model_copy(
        update={
            "series": {
                **request.market_data.series,
                Timeframe.M5: series,
            }
        }
    )
    return request.model_copy(update={"market_data": snapshot})


@pytest.mark.asyncio
async def test_backtest_runs_chronologically_and_creates_trade():
    strategy = ScriptedStrategy()
    runner = BacktestRunner(strategy, AllowRisk())

    result = await runner.run(_request())

    assert result.metrics.trade_count == 1
    assert result.trades[0].side == SignalDirection.LONG
    assert result.trades[0].entry_market_price == 101.0
    assert result.trades[0].exit_market_price == 103.0
    assert result.trades[0].gross_pnl == 2.0
    assert result.run_id
    assert result.input_fingerprint
    assert result.strategy_version == strategy.version
    assert result.engine_version
    assert result.backtest_version == "1.2.0"
    assert len(result.equity_curve) == 6
    assert result.equity_curve[-1].equity == result.final_equity
    assert result.metrics.profit_factor is None
    assert result.metrics.expectancy_per_trade == pytest.approx(2.0)
    assert result.metrics.time_in_market_pct > 0.0
    assert result.metrics.max_gross_exposure > 0.0
    assert result.metrics.average_gross_exposure > 0.0
    assert [item[0] for item in strategy.observed_timestamps] == sorted(
        item[0] for item in strategy.observed_timestamps
    )


@pytest.mark.asyncio
async def test_backtest_uses_the_shared_deterministic_risk_evaluator():
    result = await BacktestRunner(ScriptedStrategy(), DeterministicRiskEvaluator()).run(
        _request()
    )

    assert result.metrics.trade_count == 1
    trade = result.trades[0]
    assert trade.quantity * 100.5 * result.risk_configuration.stop_distance_pct <= (
        result.initial_capital * result.risk_configuration.max_risk_per_position_pct
    )


@pytest.mark.asyncio
async def test_backtest_no_trade_is_a_valid_result():
    result = await BacktestRunner(NoTradeStrategy(), AllowRisk()).run(_request())

    assert result.trades == []
    assert result.metrics.trade_count == 0
    assert result.metrics.win_rate == 0.0
    assert result.metrics.net_pnl == 0.0
    assert result.final_equity == result.initial_capital
    assert all(point.equity == result.initial_capital for point in result.equity_curve)
    assert result.metrics.max_drawdown_abs == 0.0
    assert result.metrics.max_drawdown_pct == 0.0
    assert result.metrics.profit_factor is None
    assert result.metrics.expectancy_per_trade is None
    assert result.metrics.time_in_market_pct == 0.0
    assert result.metrics.max_gross_exposure == 0.0
    assert result.metrics.average_gross_exposure == 0.0


@pytest.mark.asyncio
async def test_mark_to_market_drawdown_includes_open_position_loss():
    request = _request()
    bars = request.market_data.series[Timeframe.M5].bars.copy()
    bars[1] = MarketBar(
        timestamp=bars[1].timestamp,
        open=101.0,
        high=102.0,
        low=98.0,
        close=99.0,
        volume_type=VolumeType.UNAVAILABLE,
        is_closed=True,
    )

    result = await BacktestRunner(ScriptedStrategy(), AllowRisk()).run(
        _with_m5_bars(request, bars)
    )

    open_loss_point = result.equity_curve[1]
    assert open_loss_point.position_direction == SignalDirection.LONG
    assert open_loss_point.unrealized_pnl == pytest.approx(-2.0)
    assert result.metrics.max_drawdown_abs == pytest.approx(2.0)
    assert result.metrics.max_drawdown_pct == pytest.approx(2.0 / 10_000.0)


@pytest.mark.asyncio
async def test_losing_trade_metrics_are_finite_and_consistent():
    request = _request()
    bars = request.market_data.series[Timeframe.M5].bars.copy()
    bars[3] = MarketBar(
        timestamp=bars[3].timestamp,
        open=90.0,
        high=91.0,
        low=89.0,
        close=90.5,
        volume_type=VolumeType.UNAVAILABLE,
        is_closed=True,
    )

    result = await BacktestRunner(ScriptedStrategy(), AllowRisk()).run(
        _with_m5_bars(request, bars)
    )

    assert result.metrics.losing_trades == 1
    assert result.metrics.profit_factor == 0.0
    assert result.metrics.expectancy_per_trade == pytest.approx(-11.0)
    assert result.metrics.net_pnl == pytest.approx(-11.0)
    assert result.equity_curve[-1].equity == pytest.approx(9_989.0)


@pytest.mark.asyncio
async def test_currency_conversion_scales_pnl_and_costs():
    request = _request(
        costs=BacktestCostModel(commission_bps=10.0, slippage_bps=10.0),
        conversion_rate=2.0,
    )
    result = await BacktestRunner(ScriptedStrategy(), AllowRisk()).run(request)

    assert result.trades[0].gross_pnl == pytest.approx(4.0)
    assert result.trades[0].commission == pytest.approx(0.408)
    assert result.trades[0].slippage_cost == pytest.approx(0.408)
    assert result.trades[0].net_pnl == pytest.approx(3.184)
    assert result.equity_curve[-1].equity == pytest.approx(10_003.184)


@pytest.mark.asyncio
async def test_empty_replay_returns_defined_metrics_without_non_finite_values():
    request = _request()
    empty_series = {
        timeframe: series.model_copy(update={"bars": []})
        for timeframe, series in request.market_data.series.items()
    }
    snapshot = request.market_data.model_copy(update={"series": empty_series})
    empty_request = request.model_copy(update={"market_data": snapshot})

    result = await BacktestRunner(NoTradeStrategy(), AllowRisk()).run(empty_request)

    assert result.equity_curve == []
    assert result.final_equity == result.initial_capital
    assert result.metrics.max_drawdown_abs == 0.0
    assert result.metrics.max_drawdown_pct == 0.0
    assert result.metrics.profit_factor is None
    assert result.metrics.expectancy_per_trade is None
    assert result.metrics.time_in_market_pct is None


@pytest.mark.asyncio
async def test_commission_and_slippage_reduce_net_pnl():
    request = _request(costs=BacktestCostModel(commission_bps=10.0, slippage_bps=10.0))
    result = await BacktestRunner(ScriptedStrategy(), AllowRisk()).run(request)

    trade = result.trades[0]
    assert trade.gross_pnl == 2.0
    assert trade.commission == pytest.approx(0.204)
    assert trade.slippage_cost == pytest.approx(0.204)
    assert trade.net_pnl == pytest.approx(1.592)
    assert result.metrics.net_pnl < result.metrics.gross_pnl


@pytest.mark.asyncio
async def test_backtest_counts_gaps_and_skips_the_first_post_gap_decision():
    result = await BacktestRunner(ScriptedStrategy(), AllowRisk()).run(
        _request(gaps=True)
    )

    assert result.metrics.temporal_gaps == 1
    assert result.metrics.skipped_decisions >= 1


@pytest.mark.asyncio
async def test_future_higher_timeframe_bars_are_hidden_until_available():
    strategy = NoTradeStrategy()
    result = await BacktestRunner(strategy, AllowRisk()).run(
        _request(include_future_higher_bar=True)
    )

    for as_of, timestamps_by_timeframe in strategy.observed_timestamps:
        assert all(
            timestamp <= as_of
            for timestamps in timestamps_by_timeframe.values()
            for timestamp in timestamps
        )
    assert result.metrics.trade_count == 0


@pytest.mark.asyncio
async def test_run_identifier_is_deterministic_for_identical_input():
    request = _request()
    first = await BacktestRunner(ScriptedStrategy(), AllowRisk()).run(request)
    second = await BacktestRunner(ScriptedStrategy(), AllowRisk()).run(request)

    assert first.run_id == second.run_id
    assert first.input_fingerprint == second.input_fingerprint
    assert first.trades == second.trades
    assert first.equity_curve == second.equity_curve
    assert first.metrics == second.metrics


@pytest.mark.asyncio
async def test_incomplete_m5_bars_are_not_replayed():
    strategy = NoTradeStrategy()
    result = await BacktestRunner(strategy, AllowRisk()).run(_request(m5_closed=False))

    assert strategy.observed_timestamps == []
    assert result.metrics.skipped_decisions == 6
    assert result.trades == []


def test_request_rejects_end_after_market_data_as_of():
    request = _request()

    with pytest.raises(ValueError, match="later than market_data.as_of"):
        BacktestRequest(
            market_data=request.market_data,
            configuration=request.configuration,
            risk_configuration=request.risk_configuration,
            account_state=request.account_state,
            start=request.start,
            end=request.end + timedelta(minutes=5),
            initial_capital=request.initial_capital,
            costs=request.costs,
        )
