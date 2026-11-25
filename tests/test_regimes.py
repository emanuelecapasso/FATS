from datetime import UTC, datetime, timedelta

from backtesting.regimes import (
    MarketRegime,
    RegimeClassificationConfig,
    RegimeRobustnessAnalyzer,
)
from core.schemas import MarketBar, VolumeType


START = datetime(2026, 1, 1, tzinfo=UTC)


def _bars(closes):
    return [
        MarketBar(
            timestamp=START + timedelta(hours=index),
            open=close,
            high=close + 0.1,
            low=close - 0.1,
            close=close,
            volume_type=VolumeType.UNAVAILABLE,
            is_closed=True,
        )
        for index, close in enumerate(closes)
    ]


def _analyzer():
    return RegimeRobustnessAnalyzer(
        RegimeClassificationConfig(
            trend_lookback_bars=3,
            volatility_lookback_bars=3,
            trend_return_threshold=0.05,
            high_volatility_threshold=0.05,
            minimum_observations=4,
        )
    )


def test_regime_classifier_handles_trend_sideways_and_insufficient_history():
    analyzer = _analyzer()

    assert analyzer._classify(_bars([100, 101, 102, 110]), START + timedelta(hours=3)) == MarketRegime.TREND
    assert analyzer._classify(_bars([100, 100.1, 100, 100.1]), START + timedelta(hours=3)) == MarketRegime.SIDEWAYS
    assert analyzer._classify(_bars([100, 101]), START + timedelta(hours=1)) == MarketRegime.UNCLASSIFIED


def test_high_volatility_has_precedence_over_trend_and_future_bars_are_ignored():
    analyzer = _analyzer()
    bars = _bars([100, 110, 100, 110, 130])

    assert analyzer._classify(bars, START + timedelta(hours=3)) == MarketRegime.HIGH_VOLATILITY
    assert analyzer._classify(bars, START + timedelta(hours=2)) == MarketRegime.UNCLASSIFIED
