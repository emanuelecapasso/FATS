"""Deterministic trading strategies."""

from strategies.mean_reversion import MeanReversionStrategy
from strategies.trend_following import TrendFollowingStrategy

__all__ = ["MeanReversionStrategy", "TrendFollowingStrategy"]
