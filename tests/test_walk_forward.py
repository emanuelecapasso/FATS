from datetime import UTC, datetime, timedelta

import pytest

from backtesting.schemas import (
    BacktestCostModel,
    WalkForwardCandidate,
    WalkForwardFoldPlan,
    WalkForwardPeriodStatus,
    WalkForwardRequest,
    WalkForwardSelectionMetric,
    WalkForwardTieBreak,
    WalkForwardWindow,
)
from backtesting.walk_forward import WalkForwardRunner
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

BASE_TIME = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)


class PriceThresholdStrategy:
    strategy_id = StrategyId.TREND_FOLLOWING.value
    version = "walk-forward-test-1"

    def __init__(self):
        self.observed = []

    def required_history(self, configuration):
        return {Timeframe.M5: 1, Timeframe.M15: 1, Timeframe.H1: 1}

    def evaluate(self, snapshot, configuration):
        self.observed.append(
            (
                snapshot.as_of,
                [
                    bar.timestamp
                    for series in snapshot.series.values()
                    for bar in series.bars
                ],
            )
        )
        threshold = configuration.parameters["threshold"]
        close = snapshot.series[Timeframe.M5].bars[-1].close
        if configuration.current_position == SignalDirection.LONG:
            direction = (
                SignalDirection.FLAT if close < threshold else SignalDirection.LONG
            )
        else:
            direction = (
                SignalDirection.LONG if close >= threshold else SignalDirection.FLAT
            )
        return Signal(
            direction=direction, confidence=1.0, motivational_codes=["threshold"]
        )


class NoTradeStrategy(PriceThresholdStrategy):
    def evaluate(self, snapshot, configuration):
        self.observed.append(
            (
                snapshot.as_of,
                [
                    bar.timestamp
                    for series in snapshot.series.values()
                    for bar in series.bars
                ],
            )
        )
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


def _snapshot(*, prices=None, missing_m5=(), missing_h1=False):
    prices = prices or [
        100.5,
        100.5,
        100.5,
        100.5,
        102.0,
        103.0,
        103.0,
        99.0,
        98.0,
        98.0,
        98.0,
    ]
    timestamps = [BASE_TIME + timedelta(minutes=5 * index) for index in range(11)]
    as_of = timestamps[-1]
    provenance = DataProvenance(
        kind=DataSourceKind.BROKER,
        provider_id="walk-forward-test",
        received_at=as_of,
    )
    m5_bars = [
        _bar(timestamp, price)
        for index, (timestamp, price) in enumerate(zip(timestamps, prices, strict=True))
        if index not in missing_m5
    ]
    series = {
        Timeframe.M5: TimeframeSeries(
            timeframe=Timeframe.M5,
            bars=m5_bars,
            provenance=provenance,
            quality=DataQuality.COMPLETE,
        ),
        Timeframe.M15: TimeframeSeries(
            timeframe=Timeframe.M15,
            bars=[_bar(timestamps[0], 100.0)],
            provenance=provenance,
            quality=DataQuality.COMPLETE,
        ),
        Timeframe.H1: TimeframeSeries(
            timeframe=Timeframe.H1,
            bars=[] if missing_h1 else [_bar(timestamps[0], 100.0)],
            provenance=provenance,
            quality=DataQuality.PARTIAL if missing_h1 else DataQuality.COMPLETE,
        ),
    }
    return MarketDataSnapshot(pair="EURUSD", as_of=as_of, series=series)


def _fold():
    return WalkForwardFoldPlan(
        fold_id="fold-01",
        training=WalkForwardWindow(
            start=BASE_TIME, end=BASE_TIME + timedelta(minutes=10)
        ),
        validation=WalkForwardWindow(
            start=BASE_TIME + timedelta(minutes=15),
            end=BASE_TIME + timedelta(minutes=25),
        ),
        test=WalkForwardWindow(
            start=BASE_TIME + timedelta(minutes=30),
            end=BASE_TIME + timedelta(minutes=40),
        ),
    )


def _candidate(candidate_id, threshold):
    return WalkForwardCandidate(
        candidate_id=candidate_id,
        configuration=StrategyConfiguration(
            strategy_id=StrategyId.TREND_FOLLOWING,
            parameters={"threshold": threshold},
        ),
    )


def _request(*, snapshot=None, candidates=None, fold=None):
    return WalkForwardRequest(
        market_data=snapshot or _snapshot(),
        candidates=candidates
        or [_candidate("low-threshold", 100.0), _candidate("high-threshold", 103.5)],
        folds=[fold or _fold()],
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
            price_to_account_currency_rate=1.0,
            position_direction=SignalDirection.FLAT,
            position_quantity=0.0,
            consecutive_losses=0,
        ),
        initial_capital=10_000.0,
        costs=BacktestCostModel(commission_bps=0.0, slippage_bps=0.0),
        purge_minutes=5,
        embargo_minutes=5,
        selection_metric=WalkForwardSelectionMetric.NET_RETURN_PCT,
        maximize=True,
        tie_break=WalkForwardTieBreak.CANDIDATE_ID_ASCENDING,
    )


@pytest.mark.asyncio
async def test_selects_on_validation_then_reports_later_out_of_sample_loss():
    strategy = PriceThresholdStrategy()
    report = await WalkForwardRunner(strategy, AllowRisk()).run(_request())

    fold = report.folds[0]
    assert fold.selected_candidate_id == "low-threshold"
    assert fold.candidates[0].candidate_id == "high-threshold"
    assert fold.test_result.status == WalkForwardPeriodStatus.COMPLETED
    assert fold.test_result.result.metrics.net_pnl < 0.0
    assert report.aggregate.total_net_pnl == fold.test_result.result.metrics.net_pnl
    assert report.dataset_fingerprint
    assert report.strategy_version == strategy.version
    assert all(
        timestamp <= as_of
        for as_of, timestamps in strategy.observed
        for timestamp in timestamps
    )


@pytest.mark.asyncio
async def test_candidate_id_tie_break_is_stable_and_input_order_independent():
    candidates = [_candidate("zeta", 999.0), _candidate("alpha", 999.0)]
    first = await WalkForwardRunner(NoTradeStrategy(), AllowRisk()).run(
        _request(candidates=candidates)
    )
    second = await WalkForwardRunner(NoTradeStrategy(), AllowRisk()).run(
        _request(candidates=list(reversed(candidates)))
    )

    assert first.folds[0].selected_candidate_id == "alpha"
    assert first.folds[0].selected_candidate_id == second.folds[0].selected_candidate_id
    assert first.run_id == second.run_id
    assert first.aggregate.total_trade_count == 0
    assert first.aggregate.profit_factor is None
    assert first.aggregate.expectancy_per_trade is None


@pytest.mark.asyncio
async def test_no_data_and_insufficient_history_have_explicit_statuses():
    no_data = await WalkForwardRunner(NoTradeStrategy(), AllowRisk()).run(
        _request(snapshot=_snapshot(missing_m5=range(11)))
    )
    assert (
        no_data.folds[0].candidates[0].validation.status
        == WalkForwardPeriodStatus.NO_DATA
    )
    assert no_data.folds[0].selected_candidate_id is None
    assert no_data.folds[0].test_result.status == WalkForwardPeriodStatus.NOT_RUN

    insufficient = await WalkForwardRunner(NoTradeStrategy(), AllowRisk()).run(
        _request(snapshot=_snapshot(missing_h1=True))
    )
    assert (
        insufficient.folds[0].candidates[0].validation.status
        == WalkForwardPeriodStatus.INSUFFICIENT_HISTORY
    )
    assert insufficient.folds[0].selected_candidate_id is None


def test_fold_plan_requires_explicit_purge_and_embargo_gaps():
    fold = _fold().model_copy(
        update={
            "validation": WalkForwardWindow(
                start=BASE_TIME + timedelta(minutes=14),
                end=BASE_TIME + timedelta(minutes=25),
            )
        }
    )

    with pytest.raises(ValueError, match="purge_minutes"):
        _request(fold=fold)


@pytest.mark.asyncio
async def test_walk_forward_reports_temporal_gaps_in_reused_backtest_metrics():
    report = await WalkForwardRunner(NoTradeStrategy(), AllowRisk()).run(
        _request(snapshot=_snapshot(missing_m5={7}))
    )

    assert report.folds[0].test_result.result.metrics.temporal_gaps == 1
