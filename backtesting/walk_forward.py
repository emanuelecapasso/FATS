"""Deterministic offline walk-forward selection and out-of-sample evaluation."""

import hashlib

from backtesting.runner import BACKTEST_VERSION, BacktestRunner
from backtesting.schemas import (
    BacktestRequest,
    WalkForwardAggregateMetrics,
    WalkForwardCandidateReport,
    WalkForwardFoldReport,
    WalkForwardPeriodResult,
    WalkForwardPeriodStatus,
    WalkForwardReport,
    WalkForwardRequest,
    WalkForwardSelectionMetric,
    WalkForwardTieBreak,
    canonical_json_payload,
)
from core.schemas import (
    DataQuality,
    MarketDataSnapshot,
    RiskAccountState,
    SignalDirection,
    Timeframe,
    TimeframeSeries,
)
from quantitative.engine import ENGINE_VERSION
from quantitative.exceptions import InsufficientHistoryError, InvalidMarketDataError
from quantitative.ports import RiskEvaluator, Strategy

WALK_FORWARD_VERSION = "1.0.0"
_VALID_SELECTION_STATUSES = {
    WalkForwardPeriodStatus.COMPLETED,
    WalkForwardPeriodStatus.NO_TRADES,
}


class WalkForwardRunner:
    """Selects on validation data and evaluates the chosen config on later data."""

    def __init__(self, strategy: Strategy, risk_evaluator: RiskEvaluator) -> None:
        self.strategy = strategy
        self.risk_evaluator = risk_evaluator

    async def run(self, request: WalkForwardRequest) -> WalkForwardReport:
        if any(
            candidate.configuration.strategy_id.value != self.strategy.strategy_id
            for candidate in request.candidates
        ):
            raise ValueError(
                "Candidate strategy_id does not match the strategy instance."
            )

        ordered_candidates = sorted(
            request.candidates, key=lambda item: item.candidate_id
        )
        fold_reports = []
        for fold in request.folds:
            candidate_reports = []
            validation_scores: list[tuple[str, float]] = []
            candidates_by_id = {
                candidate.candidate_id: candidate for candidate in ordered_candidates
            }

            for candidate in ordered_candidates:
                training = await self._evaluate_period(
                    request,
                    candidate.configuration,
                    fold.training.start,
                    fold.training.end,
                )
                validation = await self._evaluate_period(
                    request,
                    candidate.configuration,
                    fold.validation.start,
                    fold.validation.end,
                )
                candidate_reports.append(
                    WalkForwardCandidateReport(
                        candidate_id=candidate.candidate_id,
                        training=training,
                        validation=validation,
                    )
                )
                score = self._selection_score(validation, request.selection_metric)
                if score is not None:
                    validation_scores.append((candidate.candidate_id, score))

            selected_candidate_id, selection_score = self._select_candidate(
                validation_scores, request.maximize, request.tie_break
            )
            if selected_candidate_id is None:
                selected_configuration = None
                test_result = WalkForwardPeriodResult(
                    status=WalkForwardPeriodStatus.NOT_RUN,
                    start=fold.test.start,
                    end=fold.test.end,
                    reason=(
                        "No candidate had sufficient validation data and a defined "
                        "selection metric; the out-of-sample window was not run."
                    ),
                )
            else:
                selected_configuration = candidates_by_id[
                    selected_candidate_id
                ].configuration
                test_result = await self._evaluate_period(
                    request,
                    selected_configuration,
                    fold.test.start,
                    fold.test.end,
                )

            fold_fingerprint = self._fold_fingerprint(request, fold)
            fold_reports.append(
                WalkForwardFoldReport(
                    fold_id=fold.fold_id,
                    fold_fingerprint=fold_fingerprint,
                    training=fold.training,
                    validation=fold.validation,
                    test=fold.test,
                    candidates=candidate_reports,
                    selected_candidate_id=selected_candidate_id,
                    selected_configuration=selected_configuration,
                    selection_metric=request.selection_metric,
                    selection_score=selection_score,
                    test_result=test_result,
                )
            )

        dataset_fingerprint = hashlib.sha256(
            canonical_json_payload(request.market_data.model_dump(mode="json")).encode(
                "utf-8"
            )
        ).hexdigest()
        request_payload = request.model_dump(mode="json")
        request_payload["candidates"] = sorted(
            request_payload["candidates"], key=lambda item: item["candidate_id"]
        )
        run_payload = {
            "request": request_payload,
            "dataset_fingerprint": dataset_fingerprint,
            "strategy_id": self.strategy.strategy_id,
            "strategy_version": self.strategy.version,
            "engine_version": ENGINE_VERSION,
            "backtest_version": BACKTEST_VERSION,
            "walk_forward_version": WALK_FORWARD_VERSION,
        }
        run_id = hashlib.sha256(
            canonical_json_payload(run_payload).encode("utf-8")
        ).hexdigest()

        return WalkForwardReport(
            run_id=run_id,
            dataset_fingerprint=dataset_fingerprint,
            strategy_id=self.strategy.strategy_id,
            strategy_version=self.strategy.version,
            engine_version=ENGINE_VERSION,
            backtest_version=BACKTEST_VERSION,
            walk_forward_version=WALK_FORWARD_VERSION,
            selection_metric=request.selection_metric,
            maximize=request.maximize,
            tie_break=request.tie_break,
            purge_minutes=request.purge_minutes,
            embargo_minutes=request.embargo_minutes,
            folds=fold_reports,
            aggregate=self._aggregate(fold_reports),
        )

    async def _evaluate_period(
        self,
        request: WalkForwardRequest,
        configuration,
        start,
        end,
    ) -> WalkForwardPeriodResult:
        period_snapshot = self._snapshot_until(request.market_data, end)
        period_m5 = period_snapshot.series.get(Timeframe.M5)
        has_period_events = bool(
            period_m5
            and any(
                start <= bar.timestamp <= end and bar.is_closed is True
                for bar in period_m5.bars
            )
        )
        if not has_period_events:
            return WalkForwardPeriodResult(
                status=WalkForwardPeriodStatus.NO_DATA,
                start=start,
                end=end,
                reason="No closed M5 bars are available inside this window.",
            )

        if any(
            series.quality == DataQuality.STALE
            for series in period_snapshot.series.values()
        ):
            return WalkForwardPeriodResult(
                status=WalkForwardPeriodStatus.INVALID_DATA,
                start=start,
                end=end,
                reason="STALE market data is not allowed in walk-forward evaluation.",
            )

        required_history = self.strategy.required_history(configuration)
        available_series = period_snapshot.series
        insufficient = []
        for timeframe, required_count in required_history.items():
            series = available_series.get(timeframe)
            available_count = (
                sum(bar.is_closed is True for bar in series.bars) if series else 0
            )
            if available_count < required_count:
                insufficient.append(timeframe.value)
        if insufficient:
            return WalkForwardPeriodResult(
                status=WalkForwardPeriodStatus.INSUFFICIENT_HISTORY,
                start=start,
                end=end,
                reason="Insufficient closed-bar history for: "
                + ", ".join(insufficient),
            )

        backtest_request = BacktestRequest(
            market_data=period_snapshot,
            configuration=configuration,
            risk_configuration=request.risk_configuration,
            account_state=self._initial_account(request.account_state),
            start=start,
            end=end,
            initial_capital=request.initial_capital,
            costs=request.costs,
        )
        try:
            result = await BacktestRunner(self.strategy, self.risk_evaluator).run(
                backtest_request
            )
        except InvalidMarketDataError as exc:
            return WalkForwardPeriodResult(
                status=WalkForwardPeriodStatus.INVALID_DATA,
                start=start,
                end=end,
                reason=str(exc),
            )
        except InsufficientHistoryError as exc:
            return WalkForwardPeriodResult(
                status=WalkForwardPeriodStatus.INSUFFICIENT_HISTORY,
                start=start,
                end=end,
                reason=str(exc),
            )

        status = (
            WalkForwardPeriodStatus.COMPLETED
            if result.metrics.trade_count > 0
            else WalkForwardPeriodStatus.NO_TRADES
        )
        return WalkForwardPeriodResult(
            status=status,
            start=start,
            end=end,
            result=result,
        )

    @staticmethod
    def _snapshot_until(source: MarketDataSnapshot, end) -> MarketDataSnapshot:
        series = {
            timeframe: TimeframeSeries(
                timeframe=timeframe,
                bars=[bar for bar in item.bars if bar.timestamp <= end],
                provenance=item.provenance,
                quality=item.quality,
            )
            for timeframe, item in source.series.items()
        }
        return MarketDataSnapshot(pair=source.pair, as_of=end, series=series)

    @staticmethod
    def _initial_account(account: RiskAccountState) -> RiskAccountState:
        return account.model_copy(
            update={
                "equity": account.equity,
                "daily_start_equity": account.equity,
                "peak_equity": account.equity,
                "gross_exposure": 0.0,
                "position_direction": SignalDirection.FLAT,
                "position_quantity": 0.0,
            }
        )

    @staticmethod
    def _selection_score(
        period: WalkForwardPeriodResult,
        metric: WalkForwardSelectionMetric,
    ) -> float | None:
        if period.status not in _VALID_SELECTION_STATUSES or period.result is None:
            return None
        values = {
            WalkForwardSelectionMetric.NET_RETURN_PCT: period.result.metrics.return_pct,
            WalkForwardSelectionMetric.NET_PNL: period.result.metrics.net_pnl,
            WalkForwardSelectionMetric.MAX_DRAWDOWN_PCT: period.result.metrics.max_drawdown_pct,
            WalkForwardSelectionMetric.PROFIT_FACTOR: period.result.metrics.profit_factor,
            WalkForwardSelectionMetric.EXPECTANCY_PER_TRADE: period.result.metrics.expectancy_per_trade,
        }
        return values[metric]

    @staticmethod
    def _select_candidate(
        scores: list[tuple[str, float]],
        maximize: bool,
        tie_break: WalkForwardTieBreak,
    ) -> tuple[str | None, float | None]:
        if not scores:
            return None, None
        if tie_break != WalkForwardTieBreak.CANDIDATE_ID_ASCENDING:
            raise ValueError(f"Unsupported walk-forward tie-break rule: {tie_break}.")
        ordered = sorted(scores, key=lambda item: item[0])
        selected = (
            max(ordered, key=lambda item: item[1])
            if maximize
            else min(ordered, key=lambda item: item[1])
        )
        return selected

    @staticmethod
    def _fold_fingerprint(request: WalkForwardRequest, fold) -> str:
        data = WalkForwardRunner._snapshot_until(request.market_data, fold.test.end)
        payload = {
            "fold": fold.model_dump(mode="json"),
            "data": data.model_dump(mode="json"),
        }
        return hashlib.sha256(
            canonical_json_payload(payload).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _aggregate(folds: list[WalkForwardFoldReport]) -> WalkForwardAggregateMetrics:
        completed = [
            fold.test_result.result
            for fold in folds
            if fold.test_result.status in _VALID_SELECTION_STATUSES
            and fold.test_result.result is not None
        ]
        trades = [trade for result in completed for trade in result.trades]
        winning_pnl = sum(trade.net_pnl for trade in trades if trade.net_pnl > 0)
        losing_pnl = abs(sum(trade.net_pnl for trade in trades if trade.net_pnl < 0))
        total_trade_count = len(trades)
        return WalkForwardAggregateMetrics(
            test_fold_count=len(folds),
            completed_test_folds=len(completed),
            total_trade_count=total_trade_count,
            total_net_pnl=sum(result.metrics.net_pnl for result in completed),
            mean_test_return_pct=(
                sum(result.metrics.return_pct for result in completed) / len(completed)
                if completed
                else None
            ),
            max_test_drawdown_pct=(
                max(result.metrics.max_drawdown_pct for result in completed)
                if completed
                else None
            ),
            profit_factor=(winning_pnl / losing_pnl if losing_pnl > 0 else None),
            expectancy_per_trade=(
                sum(trade.net_pnl for trade in trades) / total_trade_count
                if total_trade_count
                else None
            ),
        )
