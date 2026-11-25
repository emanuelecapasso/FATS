from datetime import UTC, datetime, timedelta

import pytest

from backtesting.portfolio import PortfolioBacktestRunner
from backtesting.schemas import (
    AccountCurrencyRate,
    BacktestCostModel,
    PortfolioBacktestRequest,
    PortfolioInstrument,
)
from core.schemas import (
    DataProvenance,
    DataQuality,
    DataSourceKind,
    MarketBar,
    MarketDataSnapshot,
    PortfolioAccountState,
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
from quantitative.exceptions import InvalidMarketDataError
from quantitative.risk import DeterministicRiskEvaluator

BASE_TIME = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)


class AlwaysLongStrategy:
    strategy_id = StrategyId.TREND_FOLLOWING.value
    version = "portfolio-test-1"

    def __init__(self):
        self.observed = []

    def required_history(self, configuration):
        return {Timeframe.M5: 1, Timeframe.M15: 1, Timeframe.H1: 1}

    def evaluate(self, snapshot, configuration):
        self.observed.append(
            (
                snapshot.as_of,
                [bar.timestamp for s in snapshot.series.values() for bar in s.bars],
            )
        )
        return Signal(
            direction=SignalDirection.LONG,
            confidence=1.0,
            motivational_codes=["portfolio_long"],
        )


class OneRoundTripStrategy(AlwaysLongStrategy):
    def evaluate(self, snapshot, configuration):
        self.observed.append(
            (
                snapshot.as_of,
                [bar.timestamp for s in snapshot.series.values() for bar in s.bars],
            )
        )
        count = len(snapshot.series[Timeframe.M5].bars)
        if configuration.current_position == SignalDirection.FLAT and count == 1:
            direction = SignalDirection.LONG
        elif configuration.current_position == SignalDirection.LONG and count >= 3:
            direction = SignalDirection.FLAT
        elif configuration.current_position == SignalDirection.LONG:
            direction = SignalDirection.LONG
        else:
            direction = SignalDirection.FLAT
        return Signal(
            direction=direction, confidence=1.0, motivational_codes=["round_trip"]
        )


class NoTradeStrategy(AlwaysLongStrategy):
    def evaluate(self, snapshot, configuration):
        return Signal(
            direction=SignalDirection.FLAT,
            confidence=1.0,
            motivational_codes=["no_trade"],
        )


class AllowRisk:
    def evaluate(
        self, signal, snapshot, configuration, risk_configuration, account_state
    ):
        return RiskDecision(approved=True, reasons=[], recommended_quantity=1.0)


def _bar(timestamp, price):
    return MarketBar(
        timestamp=timestamp,
        open=price,
        high=price + 1.0,
        low=price - 1.0,
        close=price,
        volume_type=VolumeType.UNAVAILABLE,
        is_closed=True,
    )


def _instrument(
    instrument_id,
    *,
    prices=None,
    conversion=0.5,
    conversion_timestamps=None,
    missing_m5=(),
    costs=None,
):
    prices = prices or [100.0, 101.0, 101.0, 102.0, 102.0, 102.0]
    timestamps = [BASE_TIME + timedelta(minutes=5 * index) for index in range(6)]
    provenance = DataProvenance(
        kind=DataSourceKind.BROKER,
        provider_id="portfolio-test",
        received_at=timestamps[-1],
    )
    m5 = [
        _bar(timestamp, price)
        for index, (timestamp, price) in enumerate(zip(timestamps, prices, strict=True))
        if index not in missing_m5
    ]
    snapshot = MarketDataSnapshot(
        pair=instrument_id,
        as_of=timestamps[-1],
        series={
            Timeframe.M5: TimeframeSeries(
                timeframe=Timeframe.M5,
                bars=m5,
                provenance=provenance,
                quality=DataQuality.COMPLETE,
            ),
            Timeframe.M15: TimeframeSeries(
                timeframe=Timeframe.M15,
                bars=[_bar(timestamps[0], 100.0), _bar(timestamps[3], 102.0)],
                provenance=provenance,
                quality=DataQuality.COMPLETE,
            ),
            Timeframe.H1: TimeframeSeries(
                timeframe=Timeframe.H1,
                bars=[_bar(timestamps[0], 100.0)],
                provenance=provenance,
                quality=DataQuality.COMPLETE,
            ),
        },
    )
    rate_timestamps = (
        timestamps if conversion_timestamps is None else conversion_timestamps
    )
    return PortfolioInstrument(
        instrument_id=instrument_id,
        price_currency="USD",
        market_data=snapshot,
        configuration=StrategyConfiguration(
            strategy_id=StrategyId.TREND_FOLLOWING,
            parameters={},
        ),
        costs=costs or BacktestCostModel(commission_bps=0.0, slippage_bps=0.0),
        conversion_rates=[
            AccountCurrencyRate(
                timestamp=timestamp,
                source_currency="USD",
                account_currency="EUR",
                rate=conversion,
            )
            for timestamp in rate_timestamps
        ],
    )


def _request(
    instruments, *, max_aggregate_exposure_pct=2.0, max_price_age_seconds=3600
):
    timestamps = [BASE_TIME + timedelta(minutes=5 * index) for index in range(6)]
    return PortfolioBacktestRequest(
        instruments=instruments,
        account_currency="EUR",
        risk_configuration=RiskConfiguration(
            max_risk_per_position_pct=0.01,
            max_aggregate_exposure_pct=max_aggregate_exposure_pct,
            max_daily_loss_pct=0.03,
            max_drawdown_pct=0.10,
            stop_distance_pct=0.01,
            max_price_age_seconds=max_price_age_seconds,
            quantity_step=0.01,
        ),
        account_state=PortfolioAccountState(
            account_currency="EUR",
            equity=10_000.0,
            daily_start_equity=10_000.0,
            peak_equity=10_000.0,
            gross_exposure=0.0,
            positions={},
        ),
        start=timestamps[0],
        end=timestamps[-1],
        initial_capital=10_000.0,
    )


@pytest.mark.asyncio
async def test_simultaneous_signals_use_stable_order_and_respect_aggregate_limit():
    request = _request(
        [
            _instrument("ZZZ", prices=[100.0] * 6),
            _instrument("AAA", prices=[100.0] * 6),
        ],
        max_aggregate_exposure_pct=0.02,
    )
    strategy = AlwaysLongStrategy()
    result = await PortfolioBacktestRunner(strategy, DeterministicRiskEvaluator()).run(
        request
    )

    assert result.instruments["AAA"].metrics.trade_count == 1
    assert result.instruments["ZZZ"].metrics.trade_count == 0
    assert result.metrics.max_gross_exposure <= 200.0
    assert all(
        point.gross_exposure <= point.equity * 0.02 + 1e-8
        for point in result.equity_curve
    )
    assert all(
        timestamp <= as_of
        for as_of, observed in strategy.observed
        for timestamp in observed
    )


@pytest.mark.asyncio
async def test_multiple_positions_share_one_account_and_are_reported_per_instrument():
    request = _request(
        [
            _instrument("AAA", prices=[100.0] * 6),
            _instrument("BBB", prices=[100.0] * 6),
        ]
    )
    result = await PortfolioBacktestRunner(
        AlwaysLongStrategy(), DeterministicRiskEvaluator()
    ).run(request)

    assert result.instruments["AAA"].metrics.trade_count == 1
    assert result.instruments["BBB"].metrics.trade_count == 1
    assert result.metrics.trade_count == 2
    assert result.final_equity == pytest.approx(
        result.initial_capital + result.metrics.net_pnl
    )
    assert result.equity_curve[-1].equity == pytest.approx(result.final_equity)


@pytest.mark.asyncio
async def test_currency_conversion_and_costs_affect_portfolio_pnl():
    instrument = _instrument(
        "AAA",
        costs=BacktestCostModel(commission_bps=10.0, slippage_bps=10.0),
    )
    instrument = instrument.model_copy(
        update={
            "conversion_rates": [
                rate.model_copy(
                    update={
                        "rate": (
                            0.5
                            if rate.timestamp < BASE_TIME + timedelta(minutes=10)
                            else 0.6
                        )
                    }
                )
                for rate in instrument.conversion_rates
            ]
        }
    )
    result = await PortfolioBacktestRunner(OneRoundTripStrategy(), AllowRisk()).run(
        _request([instrument])
    )

    trade = result.instruments["AAA"].trades[0]
    expected_gross = (
        (trade.exit_market_price - trade.entry_market_price) * trade.quantity * 0.6
    )
    expected_commission = (
        trade.quantity * trade.entry_market_price * 0.5
        + trade.quantity * trade.exit_market_price * 0.6
    ) * 0.001
    assert trade.entry_conversion_rate == 0.5
    assert trade.exit_conversion_rate == 0.6
    assert trade.gross_pnl == pytest.approx(expected_gross)
    assert trade.commission == pytest.approx(expected_commission)
    assert trade.slippage_cost == pytest.approx(expected_commission)
    assert trade.net_pnl == pytest.approx(expected_gross - 2 * expected_commission)
    assert result.final_equity == pytest.approx(10_000.0 + trade.net_pnl)


@pytest.mark.asyncio
async def test_missing_conversion_skips_flat_instrument_and_no_trade_is_explicit():
    instrument = _instrument("AAA", conversion_timestamps=[])
    result = await PortfolioBacktestRunner(
        NoTradeStrategy(), DeterministicRiskEvaluator()
    ).run(_request([instrument]))

    assert result.metrics.trade_count == 0
    assert result.instruments["AAA"].metrics.trade_count == 0
    assert result.instruments["AAA"].skipped_reasons["conversion_missing_or_stale"] > 0
    assert result.instruments["AAA"].status.value == "no_trades"


@pytest.mark.asyncio
async def test_stale_conversion_rejects_mark_of_open_position():
    instrument = _instrument(
        "AAA",
        conversion_timestamps=[BASE_TIME],
    )

    with pytest.raises(InvalidMarketDataError, match="conversion rate"):
        await PortfolioBacktestRunner(
            AlwaysLongStrategy(), DeterministicRiskEvaluator()
        ).run(_request([instrument], max_price_age_seconds=60))


@pytest.mark.asyncio
async def test_temporal_gap_is_counted_and_input_order_does_not_change_run_id():
    aaa = _instrument("AAA", missing_m5={2})
    bbb = _instrument("BBB")
    runner = PortfolioBacktestRunner(NoTradeStrategy(), DeterministicRiskEvaluator())
    first = await runner.run(_request([aaa, bbb]))
    second = await runner.run(_request([bbb, aaa]))

    assert first.instruments["AAA"].metrics.temporal_gaps == 1
    assert first.run_id == second.run_id
    assert first.equity_curve == second.equity_curve
