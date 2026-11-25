"""Classificazione deterministica dei regimi di mercato su barre H1 chiuse."""

from __future__ import annotations

import hashlib
import math
from enum import Enum
from typing import Any

from pydantic import Field

from backtesting.schemas import (
    BacktestResult,
    StrictBaseModel,
    WalkForwardPeriodStatus,
    WalkForwardWindow,
    canonical_json_payload,
)
from core.schemas import DataQuality, MarketDataSnapshot, Timeframe


class MarketRegime(str, Enum):
    TREND = "trend"
    SIDEWAYS = "sideways"
    HIGH_VOLATILITY = "high_volatility"
    UNCLASSIFIED = "unclassified"


class RegimeClassificationConfig(StrictBaseModel):
    """Le soglie sono frazioni: 0.01 equivale all'1%."""

    trend_lookback_bars: int = Field(..., ge=2)
    volatility_lookback_bars: int = Field(..., ge=2)
    trend_return_threshold: float = Field(..., ge=0.0, allow_inf_nan=False)
    high_volatility_threshold: float = Field(..., gt=0.0, allow_inf_nan=False)
    minimum_observations: int = Field(..., ge=2)


class RegimePeriodInput(StrictBaseModel):
    instrument_id: str = Field(..., min_length=1)
    fold_id: str = Field(..., min_length=1)
    period: WalkForwardWindow
    market_data: MarketDataSnapshot
    backtest: BacktestResult


class RegimeMetrics(StrictBaseModel):
    observation_count: int = Field(..., ge=0)
    trade_count: int = Field(..., ge=0)
    status: WalkForwardPeriodStatus
    net_pnl: float | None = Field(default=None, allow_inf_nan=False)
    return_pct: float | None = Field(default=None, allow_inf_nan=False)
    max_drawdown_pct: float | None = Field(
        default=None, ge=0.0, allow_inf_nan=False
    )


class RegimeSlice(StrictBaseModel):
    instrument_id: str
    fold_id: str
    period: WalkForwardWindow
    regime: MarketRegime
    metrics: RegimeMetrics


class RegimeRobustnessReport(StrictBaseModel):
    config: RegimeClassificationConfig
    strategy_id: str
    strategy_version: str
    engine_version: str
    dataset_fingerprint: str
    slices: list[RegimeSlice]


class RegimeRobustnessAnalyzer:
    """Attribuisce i mark M5 al regime H1 disponibile in quel timestamp.

    Precedenza: alta volatilità, poi trend, altrimenti laterale.
    Volatilità: deviazione standard di popolazione dei rendimenti H1.
    Trend: rendimento cumulato assoluto sulla finestra configurata.
    """

    def __init__(self, config: RegimeClassificationConfig) -> None:
        self.config = config

    def analyze(self, periods: list[RegimePeriodInput]) -> RegimeRobustnessReport:
        ordered = sorted(periods, key=lambda item: (item.instrument_id, item.fold_id))
        keys = [(item.instrument_id, item.fold_id) for item in ordered]
        if len(keys) != len(set(keys)):
            raise ValueError("Each instrument_id/fold_id pair must be unique.")
        slices: list[RegimeSlice] = []

        for item in ordered:
            series = item.market_data.series.get(Timeframe.H1)
            h1_bars = (
                []
                if series is None or series.quality == DataQuality.STALE
                else [
                    bar
                    for bar in series.bars
                    if bar.is_closed is True and bar.timestamp <= item.period.end
                ]
            )

            marks_by_regime: dict[MarketRegime, list[Any]] = {
                regime: [] for regime in MarketRegime
            }
            trades_by_regime: dict[MarketRegime, list[Any]] = {
                regime: [] for regime in MarketRegime
            }

            for mark in item.backtest.equity_curve:
                if item.period.start <= mark.timestamp <= item.period.end:
                    regime = self._classify(h1_bars, mark.timestamp)
                    marks_by_regime[regime].append(mark)

            for trade in item.backtest.trades:
                if item.period.start <= trade.exit_timestamp <= item.period.end:
                    regime = self._classify(h1_bars, trade.exit_timestamp)
                    trades_by_regime[regime].append(trade)

            for regime in MarketRegime:
                marks = marks_by_regime[regime]
                trades = trades_by_regime[regime]
                if regime == MarketRegime.UNCLASSIFIED:
                    status = WalkForwardPeriodStatus.INSUFFICIENT_HISTORY
                elif not marks:
                    status = WalkForwardPeriodStatus.NO_DATA
                elif not trades:
                    status = WalkForwardPeriodStatus.NO_TRADES
                else:
                    status = WalkForwardPeriodStatus.COMPLETED

                pnl = (
                    sum(trade.net_pnl for trade in trades)
                    if trades
                    else None
                )
                slices.append(
                    RegimeSlice(
                        instrument_id=item.instrument_id,
                        fold_id=item.fold_id,
                        period=item.period,
                        regime=regime,
                        metrics=RegimeMetrics(
                            observation_count=len(marks),
                            trade_count=len(trades),
                            status=status,
                            net_pnl=pnl,
                            return_pct=(
                                pnl / item.backtest.initial_capital * 100.0
                                if pnl is not None
                                else None
                            ),
                            max_drawdown_pct=(
                                self._drawdown(marks, item.backtest.initial_capital)
                                if marks
                                else None
                            ),
                        ),
                    )
                )

        fingerprint_payload = [
            item.model_dump(mode="json") for item in ordered
        ]
        fingerprint = hashlib.sha256(
            canonical_json_payload(fingerprint_payload).encode("utf-8")
        ).hexdigest()

        first = ordered[0].backtest if ordered else None
        return RegimeRobustnessReport(
            config=self.config,
            strategy_id=first.strategy_id if first else "",
            strategy_version=first.strategy_version if first else "",
            engine_version=first.engine_version if first else "",
            dataset_fingerprint=fingerprint,
            slices=slices,
        )

    def _classify(self, bars: list[Any], timestamp) -> MarketRegime:
        eligible = [bar for bar in bars if bar.timestamp <= timestamp]
        closes = [bar.close for bar in eligible]
        needed = max(
            self.config.minimum_observations,
            self.config.trend_lookback_bars + 1,
            self.config.volatility_lookback_bars + 1,
        )
        if len(closes) < needed or any(close <= 0.0 for close in closes):
            return MarketRegime.UNCLASSIFIED

        trend_return = (
            closes[-1] / closes[-self.config.trend_lookback_bars - 1] - 1.0
        )
        volatility_closes = closes[-self.config.volatility_lookback_bars - 1 :]
        volatility_returns = [
            current / previous - 1.0
            for previous, current in zip(
                volatility_closes[:-1], volatility_closes[1:], strict=True
            )
        ]
        if not math.isfinite(trend_return) or any(
            not math.isfinite(value) for value in volatility_returns
        ):
            return MarketRegime.UNCLASSIFIED
        average_return = sum(volatility_returns) / len(volatility_returns)
        volatility = math.sqrt(
            sum((value - average_return) ** 2 for value in volatility_returns)
            / len(volatility_returns)
        )
        if volatility >= self.config.high_volatility_threshold:
            return MarketRegime.HIGH_VOLATILITY

        if abs(trend_return) >= self.config.trend_return_threshold:
            return MarketRegime.TREND
        return MarketRegime.SIDEWAYS

    @staticmethod
    def _drawdown(marks: list[Any], initial_capital: float) -> float:
        peak = initial_capital
        maximum = 0.0
        for mark in marks:
            peak = max(peak, mark.equity)
            if peak > 0.0:
                maximum = max(maximum, (peak - mark.equity) / peak)
        return maximum
