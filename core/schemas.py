"""Canonical, versioned request and response contracts for quantitative evaluation."""

from datetime import datetime, timedelta
from enum import StrEnum
from math import isfinite
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Pair = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=32,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    ),
]
Price = Annotated[float, Field(gt=0, allow_inf_nan=False)]
NonNegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class ContractModel(BaseModel):
    """Base model that rejects undeclared fields to keep the public contract stable."""

    model_config = ConfigDict(extra="forbid")


class StrategyId(StrEnum):
    TREND_FOLLOWING = "trend_following"
    MEAN_REVERSION = "mean_reversion"


class SignalDirection(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class MarketBar(ContractModel):
    """OHLCV bar; timestamp is the close time and must be expressed in UTC."""

    timestamp: datetime
    open: Price
    high: Price
    low: Price
    close: Price
    volume: NonNegative

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("timestamp must include a UTC timezone offset")
        return value

    @model_validator(mode="after")
    def prices_must_fit_inside_high_low(self) -> "MarketBar":
        if self.high < max(self.open, self.close, self.low):
            raise ValueError(
                "high must be greater than or equal to open, low, and close"
            )
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("low must be less than or equal to open, high, and close")
        return self


class MultiTimeframeBars(ContractModel):
    m5: list[MarketBar] = Field(min_length=1, max_length=5000)
    m15: list[MarketBar] = Field(min_length=1, max_length=5000)
    h1: list[MarketBar] = Field(min_length=1, max_length=5000)


class StrategyConfiguration(ContractModel):
    strategy: StrategyId
    parameters: dict[str, float] = Field(default_factory=dict, max_length=32)

    @field_validator("parameters")
    @classmethod
    def parameters_must_be_finite(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not key.strip() for key in value):
            raise ValueError("parameter names must not be empty")
        if any(not isfinite(number) for number in value.values()):
            raise ValueError("strategy parameters must be finite numbers")
        return value


class EvaluationRequest(ContractModel):
    schema_version: Literal["1.0"]
    pair: Pair
    as_of: datetime
    bars: MultiTimeframeBars
    configuration: StrategyConfiguration

    @field_validator("as_of")
    @classmethod
    def as_of_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("as_of must include a UTC timezone offset")
        return value

    @model_validator(mode="after")
    def validate_bar_order_and_decision_time(self) -> "EvaluationRequest":
        for timeframe in ("m5", "m15", "h1"):
            series = getattr(self.bars, timeframe)
            timestamps = [bar.timestamp for bar in series]
            if timestamps != sorted(timestamps):
                raise ValueError(f"{timeframe} bars must be ordered by timestamp")
            if len(timestamps) != len(set(timestamps)):
                raise ValueError(
                    f"{timeframe} bars must not contain duplicate timestamps"
                )
            if timestamps[-1] > self.as_of:
                raise ValueError(f"{timeframe} bars cannot extend past as_of")
        return self


class QuantitativeSignal(ContractModel):
    direction: SignalDirection
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    rationale_codes: list[str] = Field(default_factory=list, max_length=32)


class RiskDecision(ContractModel):
    approved: bool
    reason_codes: list[str] = Field(default_factory=list, max_length=32)
    recommended_quantity: Price | None = None


class EvaluationResponse(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    pair: Pair
    as_of: datetime
    strategy: StrategyId
    signal: QuantitativeSignal
    risk: RiskDecision

    @field_validator("as_of")
    @classmethod
    def as_of_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("as_of must include a UTC timezone offset")
        return value


class ErrorDetail(ContractModel):
    location: list[str | int]
    message: str
    type: str


class ErrorResponse(ContractModel):
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)
