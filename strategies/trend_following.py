"""Deterministic multi-timeframe Trend Following strategy.

H1 sets the directional regime, M15 confirms it, and M5 triggers entries and
manages exits. Indicators use only explicitly closed bars in the supplied
snapshot; no provider, clock, network, persistence, or AI dependency is used.
"""

import math
from dataclasses import dataclass
from datetime import timedelta
from itertools import pairwise

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
class TrendFollowingParameters:
    h1_fast_period: int = 20
    h1_slow_period: int = 50
    m15_ema_period: int = 20
    m5_entry_ema_period: int = 9
    breakout_lookback: int = 3


_DEFAULTS = TrendFollowingParameters()
_PARAMETER_NAMES = frozenset(
    {
        "h1_fast_period",
        "h1_slow_period",
        "m15_ema_period",
        "m5_entry_ema_period",
        "breakout_lookback",
    }
)


def _read_parameters(configuration: StrategyConfiguration) -> TrendFollowingParameters:
    if configuration.strategy_id != StrategyId.TREND_FOLLOWING:
        raise ValueError(
            "TrendFollowingStrategy requires strategy_id='trend_following'."
        )

    unknown = set(configuration.parameters) - _PARAMETER_NAMES
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown Trend Following parameters: {names}.")

    values = {}
    for name in sorted(_PARAMETER_NAMES):
        raw = configuration.parameters.get(name, getattr(_DEFAULTS, name))
        if not math.isfinite(raw) or raw < 1 or not raw.is_integer():
            raise ValueError(f"Parameter {name} must be a positive integer.")
        values[name] = int(raw)

    if values["h1_fast_period"] >= values["h1_slow_period"]:
        raise ValueError("h1_fast_period must be less than h1_slow_period.")

    return TrendFollowingParameters(**values)


def _ema(values: list[float], period: int) -> float:
    """SMA-seeded EMA; deterministic and independent of external libraries."""
    if len(values) < period:
        raise InsufficientHistoryError(
            f"EMA requires {period} observations; received {len(values)}."
        )

    current = sum(values[:period]) / period
    alpha = 2.0 / (period + 1)
    for value in values[period:]:
        current = alpha * value + (1.0 - alpha) * current
    return current


class TrendFollowingStrategy:
    """EMA regime + confirmation + M5 breakout strategy with target-position output."""

    strategy_id = StrategyId.TREND_FOLLOWING.value
    version = STRATEGY_VERSION

    def required_history(
        self, configuration: StrategyConfiguration
    ) -> dict[Timeframe, int]:
        params = _read_parameters(configuration)
        return {
            Timeframe.M5: max(params.m5_entry_ema_period, params.breakout_lookback + 1),
            Timeframe.M15: params.m15_ema_period + 1,
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
                        f"Future bar in {timeframe.value}: {bar.timestamp} > {snapshot.as_of}."
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

        # A breakout window must be contiguous. Gaps are never forward-filled.
        m5_window = m5[-(params.breakout_lookback + 1) :]
        expected_step = timedelta(minutes=5)
        if any(
            right.timestamp - left.timestamp != expected_step
            for left, right in pairwise(m5_window)
        ):
            raise InsufficientHistoryError(
                "Missing contiguous M5 bars in the breakout lookback window."
            )

        h1_closes = [bar.close for bar in h1]
        h1_fast = _ema(h1_closes, params.h1_fast_period)
        h1_slow = _ema(h1_closes, params.h1_slow_period)
        h1_last = h1_closes[-1]
        bullish_h1 = h1_fast > h1_slow and h1_last > h1_fast
        bearish_h1 = h1_fast < h1_slow and h1_last < h1_fast

        m15_closes = [bar.close for bar in m15]
        m15_ema = _ema(m15_closes, params.m15_ema_period)
        m15_previous_ema = _ema(m15_closes[:-1], params.m15_ema_period)
        bullish_m15 = m15_closes[-1] > m15_ema > m15_previous_ema
        bearish_m15 = m15_closes[-1] < m15_ema < m15_previous_ema

        m5_closes = [bar.close for bar in m5]
        m5_ema = _ema(m5_closes, params.m5_entry_ema_period)
        m5_last = m5[-1]
        prior_m5 = m5[-(params.breakout_lookback + 1) : -1]
        bullish_breakout = m5_last.close > m5_ema and m5_last.close > max(
            bar.high for bar in prior_m5
        )
        bearish_breakout = m5_last.close < m5_ema and m5_last.close < min(
            bar.low for bar in prior_m5
        )

        bullish_trend = bullish_h1 and bullish_m15
        bearish_trend = bearish_h1 and bearish_m15
        position = configuration.current_position

        if position == SignalDirection.LONG:
            if bullish_trend and m5_last.close >= m5_ema:
                return self._signal(
                    SignalDirection.LONG, 0.70, "position_maintained", "bullish_trend"
                )
            return self._signal(
                SignalDirection.FLAT,
                0.75,
                "long_exit",
                "trend_or_m5_ema_exit",
            )

        if position == SignalDirection.SHORT:
            if bearish_trend and m5_last.close <= m5_ema:
                return self._signal(
                    SignalDirection.SHORT, 0.70, "position_maintained", "bearish_trend"
                )
            return self._signal(
                SignalDirection.FLAT,
                0.75,
                "short_exit",
                "trend_or_m5_ema_exit",
            )

        if bullish_trend and bullish_breakout:
            return self._signal(
                SignalDirection.LONG,
                0.85,
                "long_entry",
                "h1_m15_aligned",
                "m5_breakout",
            )
        if bearish_trend and bearish_breakout:
            return self._signal(
                SignalDirection.SHORT,
                0.85,
                "short_entry",
                "h1_m15_aligned",
                "m5_breakdown",
            )

        return self._signal(
            SignalDirection.FLAT,
            0.50,
            "no_entry",
            "trend_alignment_missing_or_breakout_unconfirmed",
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
