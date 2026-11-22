"""Versioned input and output models for deterministic historical replay."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from core.schemas import (
    MarketDataSnapshot,
    RiskAccountState,
    RiskConfiguration,
    SignalDirection,
    StrategyConfiguration,
    StrictBaseModel,
    validate_utc_datetime,
)


class BacktestCostModel(StrictBaseModel):
    commission_bps: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    slippage_bps: float = Field(default=0.0, ge=0.0, lt=10_000.0, allow_inf_nan=False)


class BacktestRequest(StrictBaseModel):
    market_data: MarketDataSnapshot
    configuration: StrategyConfiguration
    risk_configuration: RiskConfiguration
    account_state: RiskAccountState
    start: datetime
    end: datetime
    initial_capital: float = Field(default=10_000.0, gt=0.0, allow_inf_nan=False)
    costs: BacktestCostModel = Field(default_factory=BacktestCostModel)

    @field_validator("start", "end")
    @classmethod
    def check_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    @model_validator(mode="after")
    def check_range(self) -> "BacktestRequest":
        if self.start >= self.end:
            raise ValueError("Backtest start must be earlier than end.")
        if self.end > self.market_data.as_of:
            raise ValueError("Backtest end cannot be later than market_data.as_of.")
        if self.account_state.equity != self.initial_capital:
            raise ValueError(
                "account_state.equity must equal initial_capital at the start of a run."
            )
        if self.account_state.position_direction != SignalDirection.FLAT:
            raise ValueError("Backtest currently requires a flat initial position.")
        return self


class BacktestTrade(StrictBaseModel):
    side: SignalDirection
    quantity: float = Field(..., gt=0.0, allow_inf_nan=False)
    entry_timestamp: datetime
    exit_timestamp: datetime
    entry_market_price: float = Field(..., gt=0.0, allow_inf_nan=False)
    exit_market_price: float = Field(..., gt=0.0, allow_inf_nan=False)
    gross_pnl: float = Field(..., allow_inf_nan=False)
    commission: float = Field(..., ge=0.0, allow_inf_nan=False)
    slippage_cost: float = Field(..., ge=0.0, allow_inf_nan=False)
    net_pnl: float = Field(..., allow_inf_nan=False)
    entry_reasons: list[str] = Field(default_factory=list)
    exit_reasons: list[str] = Field(default_factory=list)

    @field_validator("entry_timestamp", "exit_timestamp")
    @classmethod
    def check_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class BacktestEquityPoint(StrictBaseModel):
    """Account-currency valuation at one observed closed M5 event."""

    timestamp: datetime
    equity: float = Field(..., allow_inf_nan=False)
    realized_pnl: float = Field(..., allow_inf_nan=False)
    unrealized_pnl: float = Field(..., allow_inf_nan=False)
    cumulative_commission: float = Field(..., ge=0.0, allow_inf_nan=False)
    cumulative_slippage_cost: float = Field(..., ge=0.0, allow_inf_nan=False)
    gross_exposure: float = Field(..., ge=0.0, allow_inf_nan=False)
    position_direction: SignalDirection
    position_quantity: float = Field(..., ge=0.0, allow_inf_nan=False)

    @field_validator("timestamp")
    @classmethod
    def check_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class BacktestMetrics(StrictBaseModel):
    trade_count: int = Field(..., ge=0)
    winning_trades: int = Field(..., ge=0)
    losing_trades: int = Field(..., ge=0)
    win_rate: float = Field(..., ge=0.0, le=1.0, allow_inf_nan=False)
    gross_pnl: float = Field(..., allow_inf_nan=False)
    net_pnl: float = Field(..., allow_inf_nan=False)
    total_commission: float = Field(..., ge=0.0, allow_inf_nan=False)
    total_slippage_cost: float = Field(..., ge=0.0, allow_inf_nan=False)
    return_pct: float = Field(..., allow_inf_nan=False)
    max_drawdown_abs: float = Field(..., ge=0.0, allow_inf_nan=False)
    max_drawdown_pct: float = Field(..., ge=0.0, allow_inf_nan=False)
    average_gross_exposure: float = Field(..., ge=0.0, allow_inf_nan=False)
    max_gross_exposure: float = Field(..., ge=0.0, allow_inf_nan=False)
    profit_factor: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    expectancy_per_trade: float | None = Field(default=None, allow_inf_nan=False)
    time_in_market_pct: float | None = Field(
        default=None, ge=0.0, le=1.0, allow_inf_nan=False
    )
    skipped_decisions: int = Field(..., ge=0)
    temporal_gaps: int = Field(..., ge=0)


class BacktestResult(StrictBaseModel):
    run_id: str
    input_fingerprint: str
    backtest_version: str
    pair: str
    start: datetime
    end: datetime
    strategy_id: str
    strategy_version: str
    engine_version: str
    configuration: StrategyConfiguration
    risk_configuration: RiskConfiguration
    initial_account_state: RiskAccountState
    costs: BacktestCostModel
    initial_capital: float = Field(..., gt=0.0, allow_inf_nan=False)
    final_equity: float = Field(..., allow_inf_nan=False)
    metrics: BacktestMetrics
    equity_curve: list[BacktestEquityPoint] = Field(default_factory=list)
    trades: list[BacktestTrade] = Field(default_factory=list)
    unfilled_final_signal: bool = False

    @field_validator("start", "end")
    @classmethod
    def check_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class WalkForwardSelectionMetric(str, Enum):
    NET_RETURN_PCT = "net_return_pct"
    NET_PNL = "net_pnl"
    MAX_DRAWDOWN_PCT = "max_drawdown_pct"
    PROFIT_FACTOR = "profit_factor"
    EXPECTANCY_PER_TRADE = "expectancy_per_trade"


class WalkForwardTieBreak(str, Enum):
    CANDIDATE_ID_ASCENDING = "candidate_id_ascending"


class WalkForwardPeriodStatus(str, Enum):
    COMPLETED = "completed"
    NO_TRADES = "no_trades"
    NO_DATA = "no_data"
    INSUFFICIENT_HISTORY = "insufficient_history"
    INVALID_DATA = "invalid_data"
    NOT_RUN = "not_run"


class WalkForwardWindow(StrictBaseModel):
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def check_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    @model_validator(mode="after")
    def check_range(self) -> "WalkForwardWindow":
        if self.start >= self.end:
            raise ValueError("Walk-forward window start must precede end.")
        return self


class WalkForwardFoldPlan(StrictBaseModel):
    fold_id: str = Field(..., min_length=1)
    training: WalkForwardWindow
    validation: WalkForwardWindow
    test: WalkForwardWindow

    @model_validator(mode="after")
    def check_period_order(self) -> "WalkForwardFoldPlan":
        if not (
            self.training.end < self.validation.start
            and self.validation.end < self.test.start
        ):
            raise ValueError(
                "Training, validation, and test windows must be strictly ordered and non-overlapping."
            )
        return self


class WalkForwardCandidate(StrictBaseModel):
    candidate_id: str = Field(..., min_length=1)
    configuration: StrategyConfiguration


class WalkForwardRequest(StrictBaseModel):
    market_data: MarketDataSnapshot
    candidates: list[WalkForwardCandidate] = Field(..., min_length=1)
    folds: list[WalkForwardFoldPlan] = Field(..., min_length=1)
    risk_configuration: RiskConfiguration
    account_state: RiskAccountState
    initial_capital: float = Field(..., gt=0.0, allow_inf_nan=False)
    costs: BacktestCostModel
    purge_minutes: int = Field(..., ge=0)
    embargo_minutes: int = Field(..., ge=0)
    selection_metric: WalkForwardSelectionMetric
    maximize: bool
    tie_break: WalkForwardTieBreak

    @model_validator(mode="after")
    def check_plan(self) -> "WalkForwardRequest":
        if self.account_state.equity != self.initial_capital:
            raise ValueError(
                "account_state.equity must equal initial_capital for every walk-forward run."
            )
        if self.account_state.position_direction != SignalDirection.FLAT:
            raise ValueError("Walk-forward runs require a flat initial position.")
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate_id values must be unique.")
        strategy_ids = {
            candidate.configuration.strategy_id for candidate in self.candidates
        }
        if len(strategy_ids) != 1:
            raise ValueError("All candidates must use the same strategy_id.")
        fold_ids = [fold.fold_id for fold in self.folds]
        if len(fold_ids) != len(set(fold_ids)):
            raise ValueError("fold_id values must be unique.")

        previous_test_end = None
        for fold in self.folds:
            if (
                fold.validation.start - fold.training.end
            ).total_seconds() < self.purge_minutes * 60:
                raise ValueError(
                    "Training-to-validation gap is shorter than purge_minutes."
                )
            if (
                fold.test.start - fold.validation.end
            ).total_seconds() < self.embargo_minutes * 60:
                raise ValueError(
                    "Validation-to-test gap is shorter than embargo_minutes."
                )
            if previous_test_end is not None and fold.test.start <= previous_test_end:
                raise ValueError(
                    "Fold test windows must be in order and non-overlapping."
                )
            previous_test_end = fold.test.end
            if fold.test.end > self.market_data.as_of:
                raise ValueError("A fold test end cannot exceed market_data.as_of.")
        return self


class WalkForwardPeriodResult(StrictBaseModel):
    status: WalkForwardPeriodStatus
    start: datetime
    end: datetime
    reason: str | None = None
    result: BacktestResult | None = None

    @field_validator("start", "end")
    @classmethod
    def check_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class WalkForwardCandidateReport(StrictBaseModel):
    candidate_id: str
    training: WalkForwardPeriodResult
    validation: WalkForwardPeriodResult


class WalkForwardFoldReport(StrictBaseModel):
    fold_id: str
    fold_fingerprint: str
    training: WalkForwardWindow
    validation: WalkForwardWindow
    test: WalkForwardWindow
    candidates: list[WalkForwardCandidateReport]
    selected_candidate_id: str | None = None
    selected_configuration: StrategyConfiguration | None = None
    selection_metric: WalkForwardSelectionMetric
    selection_score: float | None = Field(default=None, allow_inf_nan=False)
    test_result: WalkForwardPeriodResult


class WalkForwardAggregateMetrics(StrictBaseModel):
    test_fold_count: int = Field(..., ge=0)
    completed_test_folds: int = Field(..., ge=0)
    total_trade_count: int = Field(..., ge=0)
    total_net_pnl: float = Field(..., allow_inf_nan=False)
    mean_test_return_pct: float | None = Field(default=None, allow_inf_nan=False)
    max_test_drawdown_pct: float | None = Field(
        default=None, ge=0.0, allow_inf_nan=False
    )
    profit_factor: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    expectancy_per_trade: float | None = Field(default=None, allow_inf_nan=False)


class WalkForwardReport(StrictBaseModel):
    run_id: str
    dataset_fingerprint: str
    strategy_id: str
    strategy_version: str
    engine_version: str
    backtest_version: str
    walk_forward_version: str
    selection_metric: WalkForwardSelectionMetric
    maximize: bool
    tie_break: WalkForwardTieBreak
    purge_minutes: int = Field(..., ge=0)
    embargo_minutes: int = Field(..., ge=0)
    folds: list[WalkForwardFoldReport]
    aggregate: WalkForwardAggregateMetrics


def canonical_json_payload(value: Any) -> str:
    """Stable JSON encoding used for fingerprints and run IDs."""
    import json

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
