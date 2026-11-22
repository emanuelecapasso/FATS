
"""Historical market data quality checks."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

from core.schemas import MarketBar, Timeframe
from data.market_calendar import ForexMarketCalendar

TIMEFRAME_DURATION = {
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.H1: timedelta(hours=1),
}


@dataclass(frozen=True)
class DataGap:
    previous_timestamp: datetime
    next_timestamp: datetime
    first_missing_timestamp: datetime
    last_missing_timestamp: datetime
    missing_candles: int
    weekend_intervals_skipped: int


@dataclass(frozen=True)
class DataQualityReport:
    timeframe: Timeframe
    candles_checked: int
    gaps: tuple[DataGap, ...]
    total_missing_candles: int
    weekend_intervals_skipped: int

    @property
    def is_complete(self) -> bool:
        return (
            self.candles_checked > 0
            and self.total_missing_candles == 0
        )


class MarketDataQualityChecker:
    """Detect missing candles using the configured Forex market calendar."""

    def __init__(
        self,
        calendar: ForexMarketCalendar | None = None,
    ) -> None:
        self.calendar = calendar or ForexMarketCalendar()

    def check(
        self,
        bars: Sequence[MarketBar],
        timeframe: Timeframe,
    ) -> DataQualityReport:
        if timeframe not in TIMEFRAME_DURATION:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        ordered_bars = sorted(bars, key=lambda bar: bar.timestamp)

        for bar in ordered_bars:
            timestamp = bar.timestamp

            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError(
                    "All candle timestamps must be timezone-aware"
                )

            if timestamp.utcoffset() != timedelta(0):
                raise ValueError("All candle timestamps must be in UTC")

        timestamps = [bar.timestamp for bar in ordered_bars]

        if len(timestamps) != len(set(timestamps)):
            raise ValueError("Duplicate candle timestamps found")

        interval = TIMEFRAME_DURATION[timeframe]
        gaps: list[DataGap] = []
        total_missing = 0
        total_weekend_skipped = 0

        for previous, following in pairwise(timestamps):
            delta = following - previous

            if delta <= timedelta(0):
                raise ValueError(
                    "Candle timestamps must be strictly increasing"
                )

            if delta % interval != timedelta(0):
                raise ValueError(
                    "Candle timestamps are not aligned to the selected timeframe"
                )

            steps = int(delta / interval)
            missing_timestamps: list[datetime] = []
            weekend_skipped = 0

            for step in range(1, steps):
                expected = previous + interval * step

                if not self.calendar.is_candle_expected(expected, interval):
                    if self.calendar.is_weekend(expected):
                        weekend_skipped += 1
                    continue

                missing_timestamps.append(expected)

            total_weekend_skipped += weekend_skipped

            if missing_timestamps:
                missing_count = len(missing_timestamps)
                total_missing += missing_count

                gaps.append(
                    DataGap(
                        previous_timestamp=previous,
                        next_timestamp=following,
                        first_missing_timestamp=missing_timestamps[0],
                        last_missing_timestamp=missing_timestamps[-1],
                        missing_candles=missing_count,
                        weekend_intervals_skipped=weekend_skipped,
                    )
                )

        return DataQualityReport(
            timeframe=timeframe,
            candles_checked=len(ordered_bars),
            gaps=tuple(gaps),
            total_missing_candles=total_missing,
            weekend_intervals_skipped=total_weekend_skipped,
        )
