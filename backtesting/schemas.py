"""Versioned input and output models for deterministic historical replay."""

from datetime import datetime
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
    closed_trade_max_drawdown: float = Field(..., ge=0.0, allow_inf_nan=False)
    skipped_decisions: int = Field(..., ge=0)
    temporal_gaps: int = Field(..., ge=0)


class BacktestResult(StrictBaseModel):
    run_id: str
    input_fingerprint: str
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
    trades: list[BacktestTrade] = Field(default_factory=list)
    unfilled_final_signal: bool = False

    @field_validator("start", "end")
    @classmethod
    def check_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


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
