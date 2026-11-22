"""Deterministic multi-timeframe Mean Reversion strategy.

H1 filters strong directional regimes, M15 measures price extension from its
rolling mean, and M5 confirms a move back toward that mean. The strategy uses
only the supplied snapshot and configuration and has no external dependencies.
"""

import math
from dataclasses import dataclass
from datetime import timedelta

from core.schemas import (
    DataQuality,
    MarketDataSnapshot,
    Signal,
    SignalDirection,
    StrategyConfiguration,
    StrategyId,
    Timeframe,
)
from quantitative.exceptions import InsufficientHistoryError, InvalidMarketDataError

STRATEGY_VERSION = "1.0.0"


@dataclass(frozen=True)
class MeanReversionParameters:
    h1_fast_period: int = 20
    h1_slow_period: int = 50
    max_h1_trend_spread: float = 0.005
    m15_bollinger_period: int = 20
    entry_zscore: float = 2.0
    exit_zscore: float = 0.2
    stop_zscore: float = 3.0
    m5_mean_period: int = 5


_DEFAULTS = MeanReversionParameters()
_INTEGER_PARAMETERS = frozenset(
    {
        "h1_fast_period",
        "h1_slow_period",
        "m15_bollinger_period",
        "m5_mean_period",
    }
)
_PARAMETER_NAMES = _INTEGER_PARAMETERS | frozenset(
    {
        "max_h1_trend_spread",
        "entry_zscore",
        "exit_zscore",
        "stop_zscore",
    }
)


def _read_parameters(configuration: StrategyConfiguration) -> MeanReversionParameters:
    if configuration.strategy_id != StrategyId.MEAN_REVERSION:
        raise ValueError("MeanReversionStrategy requires strategy_id='mean_reversion'.")

    unknown = set(configuration.parameters) - _PARAMETER_NAMES
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown Mean Reversion parameters: {names}.")

    values = {}
    for name in sorted(_PARAMETER_NAMES):
        raw = configuration.parameters.get(name, getattr(_DEFAULTS, name))
        if not math.isfinite(raw) or raw <= 0:
            raise ValueError(f"Parameter {name} must be positive and finite.")
        if name in _INTEGER_PARAMETERS:
            if not raw.is_integer():
                raise ValueError(f"Parameter {name} must be an integer.")
            values[name] = int(raw)
        else:
            values[name] = raw

    if values["h1_fast_period"] >= values["h1_slow_period"]:
        raise ValueError("h1_fast_period must be less than h1_slow_period.")
    if values["entry_zscore"] <= values["exit_zscore"]:
        raise ValueError("entry_zscore must exceed exit_zscore.")
    if values["stop_zscore"] <= values["entry_zscore"]:
        raise ValueError("stop_zscore must exceed entry_zscore.")

    return MeanReversionParameters(**values)


def _ema(values: list[float], period: int) -> float:
    """SMA-seeded EMA with deterministic arithmetic."""
    if len(values) < period:
        raise InsufficientHistoryError(
            f"EMA requires {period} observations; received {len(values)}."
        )
    current = sum(values[:period]) / period
    alpha = 2.0 / (period + 1)
    for value in values[period:]:
        current = alpha * value + (1.0 - alpha) * current
    return current


def _zscore(values: list[float]) -> float:
    """Population-standard-deviation z-score of the final observation."""
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    deviation = math.sqrt(variance)
    if deviation == 0:
        return 0.0
    return (values[-1] - mean) / deviation


class MeanReversionStrategy:
    """H1 trend filter, M15 Bollinger z-score, and M5 mean-cross confirmation."""

    strategy_id = StrategyId.MEAN_REVERSION.value
    version = STRATEGY_VERSION

    def required_history(
        self, configuration: StrategyConfiguration
    ) -> dict[Timeframe, int]:
        params = _read_parameters(configuration)
        return {
            Timeframe.M5: params.m5_mean_period + 1,
            Timeframe.M15: params.m15_bollinger_period,
            Timeframe.H1: params.h1_slow_period,
        }

    def evaluate(
        self,
        snapshot: MarketDataSnapshot,
        configuration: StrategyConfiguration,
    ) -> Signal:
        params = _read_parameters(configuration)
        required = self.required_history(configuration)
        bars_by_timeframe = {}

        for timeframe in (Timeframe.M5, Timeframe.M15, Timeframe.H1):
            series = snapshot.series.get(timeframe)
            if series is None:
                raise InsufficientHistoryError(
                    f"Required timeframe {timeframe.value} is missing."
                )
            if series.quality == DataQuality.STALE:
                raise InvalidMarketDataError(
                    f"STALE data is not allowed for {timeframe.value}."
                )
            if len(series.bars) < required[timeframe]:
                raise InsufficientHistoryError(
                    f"Insufficient history for {timeframe.value}: required "
                    f"{required[timeframe]}, available {len(series.bars)}."
                )

            previous_timestamp = None
            for bar in series.bars:
                if bar.timestamp > snapshot.as_of:
                    raise InvalidMarketDataError(
                        f"Future bar in {timeframe.value}: "
                        f"{bar.timestamp} > {snapshot.as_of}."
                    )
                if bar.is_closed is not True:
                    raise InvalidMarketDataError(
                        f"Unclosed or unknown bar in {timeframe.value}: "
                        f"is_closed={bar.is_closed}."
                    )
                if (
                    previous_timestamp is not None
                    and bar.timestamp <= previous_timestamp
                ):
                    raise InvalidMarketDataError(
                        f"Bars in {timeframe.value} are duplicated or out of order."
                    )
                previous_timestamp = bar.timestamp

            bars_by_timeframe[timeframe] = series.bars

        m5 = bars_by_timeframe[Timeframe.M5]
        m15 = bars_by_timeframe[Timeframe.M15]
        h1 = bars_by_timeframe[Timeframe.H1]

        # M5 confirmation needs adjacent closed candles; missing bars are not filled.
        if m5[-1].timestamp - m5[-2].timestamp != timedelta(minutes=5):
            raise InsufficientHistoryError(
                "Missing contiguous M5 bars for mean-cross confirmation."
            )

        h1_closes = [bar.close for bar in h1]
        h1_fast = _ema(h1_closes, params.h1_fast_period)
        h1_slow = _ema(h1_closes, params.h1_slow_period)
        h1_last = h1_closes[-1]
        h1_spread = math.inf if h1_last == 0 else abs(h1_fast - h1_slow) / abs(h1_last)
        strong_h1_trend = h1_spread > params.max_h1_trend_spread

        m15_closes = [bar.close for bar in m15[-params.m15_bollinger_period :]]
        m15_zscore = _zscore(m15_closes)

        m5_closes = [bar.close for bar in m5]
        previous_mean = sum(m5_closes[-(params.m5_mean_period + 1) : -1]) / (
            params.m5_mean_period
        )
        current_mean = sum(m5_closes[-params.m5_mean_period :]) / (
            params.m5_mean_period
        )
        previous_close = m5_closes[-2]
        current_close = m5_closes[-1]
        crossed_up = previous_close <= previous_mean and current_close > current_mean
        crossed_down = previous_close >= previous_mean and current_close < current_mean

        position = configuration.current_position
        if position == SignalDirection.LONG:
            if strong_h1_trend:
                return self._signal(
                    SignalDirection.FLAT, 0.75, "long_exit", "h1_trend_filter"
                )
            if m15_zscore <= -params.stop_zscore:
                return self._signal(
                    SignalDirection.FLAT, 0.75, "long_exit", "adverse_zscore_stop"
                )
            if m15_zscore >= -params.exit_zscore:
                return self._signal(
                    SignalDirection.FLAT, 0.75, "long_exit", "mean_reached"
                )
            return self._signal(
                SignalDirection.LONG, 0.70, "position_maintained", "mean_not_reached"
            )

        if position == SignalDirection.SHORT:
            if strong_h1_trend:
                return self._signal(
                    SignalDirection.FLAT, 0.75, "short_exit", "h1_trend_filter"
                )
            if m15_zscore >= params.stop_zscore:
                return self._signal(
                    SignalDirection.FLAT, 0.75, "short_exit", "adverse_zscore_stop"
                )
            if m15_zscore <= params.exit_zscore:
                return self._signal(
                    SignalDirection.FLAT, 0.75, "short_exit", "mean_reached"
                )
            return self._signal(
                SignalDirection.SHORT, 0.70, "position_maintained", "mean_not_reached"
            )

        if strong_h1_trend:
            return self._signal(
                SignalDirection.FLAT, 0.50, "no_entry", "h1_trend_filter"
            )
        if m15_zscore <= -params.entry_zscore and crossed_up:
            return self._signal(
                SignalDirection.LONG,
                0.85,
                "long_entry",
                "m15_oversold",
                "m5_crossed_above_mean",
            )
        if m15_zscore >= params.entry_zscore and crossed_down:
            return self._signal(
                SignalDirection.SHORT,
                0.85,
                "short_entry",
                "m15_overbought",
                "m5_crossed_below_mean",
            )
        return self._signal(
            SignalDirection.FLAT,
            0.50,
            "no_entry",
            "m15_extreme_or_m5_confirmation_missing",
        )

    @staticmethod
    def _signal(
        direction: SignalDirection,
        confidence: float,
        *motivational_codes: str,
    ) -> Signal:
        return Signal(
            direction=direction,
            confidence=confidence,
            motivational_codes=list(motivational_codes),
        )
