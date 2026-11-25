from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --- SCHEMA VERSIONING ---
# Regole di aggiornamento:
# - MINOR (1.x): Aggiunte opzionali retrocompatibili (es. nuovi campi con default).
# - MAJOR (2.0): Breaking changes (rimozione campi, cambio unità, cambio semantica timestamp).
# Ogni modifica deve aggiornare questa costante, i modelli e i test nello stesso commit.

SCHEMA_MAJOR_VERSION = 1
CURRENT_SCHEMA_VERSION = "1.6"


# --- ENUMS ---


class Timeframe(str, Enum):
    M5 = "M5"
    M15 = "M15"
    H1 = "H1"


class DataSourceKind(str, Enum):
    BROKER = "broker"
    EXTERNAL = "external"


class DataQuality(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    STALE = "stale"


class VolumeType(str, Enum):
    TRADED = "traded"  # Volume reale negoziato (es. Azioni, Crypto)
    TICK_COUNT = (
        "tick_count"  # Numero di tick/variazioni di prezzo (comune nel Forex OTC)
    )
    UNAVAILABLE = "unavailable"  # Dato non disponibile (es. Forex Midpoint)


class ErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    INSUFFICIENT_DATA = "insufficient_data"
    QUANTITATIVE_UNAVAILABLE = "quantitative_unavailable"


class StrategyId(str, Enum):
    TREND_FOLLOWING = "trend_following"
    MEAN_REVERSION = "mean_reversion"


class SignalDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


# --- UTILS & BASE MODEL ---


def validate_utc_datetime(dt: datetime) -> datetime:
    """Verifica che il datetime sia timezone-aware e impostato su UTC."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt).total_seconds() != 0:
        raise ValueError("Il timestamp deve essere timezone-aware e in UTC.")
    return dt


class StrictBaseModel(BaseModel):
    """Modello base che vieta campi extra in tutti gli eredi per garantire contratti rigidi."""

    model_config = ConfigDict(extra="forbid")


# --- MARKET DATA SNAPSHOT ---


class MarketBar(StrictBaseModel):
    timestamp: datetime
    open: float = Field(..., allow_inf_nan=False)
    high: float = Field(..., allow_inf_nan=False)
    low: float = Field(..., allow_inf_nan=False)
    close: float = Field(..., allow_inf_nan=False)
    volume: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    volume_type: VolumeType = Field(default=VolumeType.UNAVAILABLE)
    is_closed: bool | None = Field(
        default=None,
        description="Indica se la barra è chiusa. Se None, lo stato è ignoto.",
    )

    @model_validator(mode="before")
    @classmethod
    def infer_default_volume_type(cls, data: Any) -> Any:
        if isinstance(data, dict) and "volume_type" not in data:
            updated = dict(data)
            updated["volume_type"] = (
                VolumeType.UNAVAILABLE
                if updated.get("volume") is None
                else VolumeType.TRADED
            )
            return updated
        return data

    @field_validator("timestamp")
    @classmethod
    def check_timestamp_utc(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)

    @model_validator(mode="after")
    def check_bar_coherence(self) -> "MarketBar":
        if not (
            self.low <= min(self.open, self.close)
            and self.high >= max(self.open, self.close)
        ):
            raise ValueError(
                f"OHLC incoerente per {self.timestamp}: O={self.open}, H={self.high}, L={self.low}, C={self.close}"
            )

        if self.volume is None and self.volume_type != VolumeType.UNAVAILABLE:
            raise ValueError(
                "Se il volume è None, volume_type deve essere UNAVAILABLE."
            )
        if self.volume is not None and self.volume_type == VolumeType.UNAVAILABLE:
            raise ValueError(
                "Se il volume è valorizzato, volume_type non può essere UNAVAILABLE."
            )

        return self


class DataProvenance(StrictBaseModel):
    kind: DataSourceKind
    provider_id: str = Field(..., min_length=1)
    received_at: datetime
    price_type: str | None = Field(default=None, min_length=1)

    @field_validator("received_at")
    @classmethod
    def check_received_at_utc(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)


class TimeframeSeries(StrictBaseModel):
    timeframe: Timeframe
    bars: list[MarketBar]
    provenance: DataProvenance
    quality: DataQuality


class MarketDataSnapshot(StrictBaseModel):
    pair: str = Field(..., min_length=1)
    as_of: datetime
    series: dict[Timeframe, TimeframeSeries]

    @field_validator("as_of")
    @classmethod
    def check_as_of_utc(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)

    @model_validator(mode="after")
    def check_series_timeframes(self) -> "MarketDataSnapshot":
        for tf_key, tf_series in self.series.items():
            if tf_key != tf_series.timeframe:
                raise ValueError(
                    f"Mismatch timeframe: chiave {tf_key.value}, serie {tf_series.timeframe.value}"
                )
            for bar in tf_series.bars:
                if bar.timestamp > self.as_of:
                    raise ValueError(
                        f"Look-ahead vietato: barra a {bar.timestamp} > as_of {self.as_of}"
                    )
        return self


# --- API & QUANTITATIVE CONTRACTS ---


class StrategyConfiguration(StrictBaseModel):
    strategy_id: StrategyId
    current_position: SignalDirection = Field(
        default=SignalDirection.FLAT,
        description="Posizione attuale usata per distinguere mantenimento e uscita.",
    )
    parameters: dict[str, float] = Field(
        default_factory=dict,
        description="PROVVISORIO: Dizionario generico per i parametri. Non rappresenta il contratto finale.",
    )


class RiskConfiguration(StrictBaseModel):
    """Risk ratios are fractions (0.01 = 1%); quantity_step is instrument units."""

    max_risk_per_position_pct: float = Field(..., gt=0.0, le=1.0, allow_inf_nan=False)
    max_aggregate_exposure_pct: float = Field(..., gt=0.0, allow_inf_nan=False)
    max_daily_loss_pct: float = Field(
        default=0.03, gt=0.0, le=0.03, allow_inf_nan=False
    )
    max_consecutive_losses: int = Field(default=3, ge=1, le=3)
    max_drawdown_pct: float = Field(..., gt=0.0, le=1.0, allow_inf_nan=False)
    stop_distance_pct: float = Field(..., gt=0.0, le=1.0, allow_inf_nan=False)
    max_price_age_seconds: int = Field(..., gt=0)
    quantity_step: float = Field(..., gt=0.0, allow_inf_nan=False)


class PortfolioPositionState(StrictBaseModel):
    """One open holding valued in account currency at a UTC event time."""

    instrument_id: str = Field(..., min_length=1)
    direction: SignalDirection
    quantity: float = Field(..., gt=0.0, allow_inf_nan=False)
    mark_price: float = Field(..., gt=0.0, allow_inf_nan=False)
    price_to_account_currency_rate: float = Field(..., gt=0.0, allow_inf_nan=False)
    gross_exposure: float = Field(..., gt=0.0, allow_inf_nan=False)
    unrealized_pnl: float = Field(..., allow_inf_nan=False)
    valued_at: datetime

    @field_validator("valued_at")
    @classmethod
    def check_valued_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    @model_validator(mode="after")
    def check_direction(self) -> "PortfolioPositionState":
        if self.direction == SignalDirection.FLAT:
            raise ValueError("A portfolio position must be directional.")
        return self


class PortfolioAccountState(StrictBaseModel):
    """Shared account state; all monetary values use account currency."""

    account_currency: str = Field(..., pattern=r"^[A-Z]{3}$")
    equity: float = Field(..., gt=0.0, allow_inf_nan=False)
    daily_start_equity: float = Field(..., gt=0.0, allow_inf_nan=False)
    peak_equity: float = Field(..., gt=0.0, allow_inf_nan=False)
    gross_exposure: float = Field(..., ge=0.0, allow_inf_nan=False)
    positions: dict[str, PortfolioPositionState] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_portfolio(self) -> "PortfolioAccountState":
        if self.peak_equity < self.equity or self.peak_equity < self.daily_start_equity:
            raise ValueError(
                "peak_equity cannot be lower than account equity or daily start."
            )
        for instrument_id, position in self.positions.items():
            if instrument_id != position.instrument_id:
                raise ValueError("Portfolio position key must match instrument_id.")
        expected_exposure = sum(
            position.gross_exposure for position in self.positions.values()
        )
        if abs(expected_exposure - self.gross_exposure) > max(
            1e-8, abs(expected_exposure) * 1e-9
        ):
            raise ValueError(
                "gross_exposure must equal the sum of open position exposure."
            )
        return self


class RiskAccountState(StrictBaseModel):
    """Account values at a decision point; money and exposure use account currency."""

    equity: float = Field(..., gt=0.0, allow_inf_nan=False)
    daily_start_equity: float = Field(..., gt=0.0, allow_inf_nan=False)
    peak_equity: float = Field(..., gt=0.0, allow_inf_nan=False)
    gross_exposure: float = Field(..., ge=0.0, allow_inf_nan=False)
    reserved_exposure_delta: float = Field(default=0.0, allow_inf_nan=False)
    price_to_account_currency_rate: float = Field(..., gt=0.0, allow_inf_nan=False)
    position_direction: SignalDirection
    position_quantity: float = Field(..., ge=0.0, allow_inf_nan=False)
    consecutive_losses: int | None = Field(default=None, ge=0)
    instrument_id: str | None = Field(default=None, min_length=1)
    portfolio_positions: dict[str, PortfolioPositionState] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_position(self) -> "RiskAccountState":
        if (self.position_direction == SignalDirection.FLAT) != (
            self.position_quantity == 0
        ):
            raise ValueError(
                "FLAT requires zero quantity and directional positions require positive quantity."
            )
        if self.peak_equity < self.equity:
            raise ValueError("peak_equity cannot be lower than equity.")
        if self.peak_equity < self.daily_start_equity:
            raise ValueError("peak_equity cannot be lower than daily_start_equity.")
        for instrument_id, position in self.portfolio_positions.items():
            if instrument_id != position.instrument_id:
                raise ValueError("Portfolio position key must match instrument_id.")
        positions_exposure = sum(
            position.gross_exposure for position in self.portfolio_positions.values()
        )
        if (
            self.portfolio_positions
            and self.gross_exposure + max(1e-8, abs(positions_exposure) * 1e-9)
            < positions_exposure
        ):
            raise ValueError(
                "gross_exposure cannot be below portfolio position exposure."
            )
        if self.instrument_id is not None:
            current = self.portfolio_positions.get(self.instrument_id)
            if current is None and self.position_direction != SignalDirection.FLAT:
                raise ValueError("Current position must exist in portfolio_positions.")
            if current is not None and (
                current.direction != self.position_direction
                or current.quantity != self.position_quantity
            ):
                raise ValueError(
                    "Current position fields must match portfolio_positions."
                )
        return self


class EvaluationRequest(StrictBaseModel):
    schema_version: str = Field(
        default=CURRENT_SCHEMA_VERSION,
        pattern=rf"^{SCHEMA_MAJOR_VERSION}\.\d+$",
        description="Versione semantica dello schema dati (MAJOR.MINOR), distinta dalla versione HTTP.",
    )
    market_data: MarketDataSnapshot
    configuration: StrategyConfiguration
    risk_configuration: RiskConfiguration | None = None
    account_state: RiskAccountState | None = None


class Signal(StrictBaseModel):
    direction: SignalDirection
    confidence: float = Field(..., ge=0.0, le=1.0)
    motivational_codes: list[str] = Field(default_factory=list)


class RiskDecision(StrictBaseModel):
    approved: bool
    reasons: list[str] = Field(default_factory=list)
    recommended_quantity: float = Field(..., ge=0.0, allow_inf_nan=False)


class EvaluationResponse(StrictBaseModel):
    schema_version: str = Field(
        default=CURRENT_SCHEMA_VERSION,
        pattern=rf"^{SCHEMA_MAJOR_VERSION}\.\d+$",
        description="Versione dello schema con cui è stato serializzato l'output quantitativo.",
    )
    pair: str
    timestamp: datetime
    signal: Signal
    risk_decision: RiskDecision

    strategy_id: str | None = Field(
        default=None,
        description="Identificativo univoco della strategia che ha prodotto il segnale",
    )
    strategy_version: str | None = Field(
        default=None, description="Versione dell'implementazione della strategia"
    )
    engine_version: str | None = Field(
        default=None,
        description="Versione del QuantitativeEngine che ha orchestrato il calcolo",
    )

    @field_validator("timestamp")
    @classmethod
    def check_timestamp_utc(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)


class ErrorResponse(StrictBaseModel):
    error_code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
