
"""Configurable Forex market calendar."""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class ForexMarketCalendar:
    """Conventional Forex weekly schedule, configurable per broker.

    Default schedule:
    - Opens Sunday at 17:00 New York time.
    - Closes Friday at 17:00 New York time.
    - Closed on Saturday.
    - Uses IANA timezone rules for daylight saving time.

    Dates in closed_dates are interpreted in the configured market timezone.
    """

    timezone: str = "America/New_York"
    sunday_open: time = time(17, 0)
    friday_close: time = time(17, 0)
    closed_dates: frozenset[date] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"Unknown market timezone: {self.timezone}"
            ) from exc

        if self.sunday_open.tzinfo is not None:
            raise ValueError("sunday_open must be a local wall-clock time")

        if self.friday_close.tzinfo is not None:
            raise ValueError("friday_close must be a local wall-clock time")

    def _local_datetime(self, timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Timestamp must be timezone-aware")

        return timestamp.astimezone(ZoneInfo(self.timezone))

    def is_weekend(self, timestamp: datetime) -> bool:
        """Return whether the timestamp falls on local Saturday or Sunday."""
        local = self._local_datetime(timestamp)
        return local.weekday() >= 5

    def is_market_open(self, timestamp: datetime) -> bool:
        """Return whether the Forex market is open at this instant."""
        local = self._local_datetime(timestamp)
        local_time = local.timetz().replace(tzinfo=None)

        if local.date() in self.closed_dates:
            return False

        weekday = local.weekday()

        if weekday == 5:
            return False

        if weekday == 6:
            return local_time >= self.sunday_open

        if weekday == 4:
            return local_time < self.friday_close

        return True

    def is_candle_expected(
        self,
        timestamp: datetime,
        duration: timedelta,
    ) -> bool:
        """Return whether a complete candle should exist at timestamp."""
        if duration <= timedelta(0):
            raise ValueError("Candle duration must be positive")

        end = timestamp + duration

        # Both ends must be inside an open session. Checking the last
        # microsecond avoids treating a candle ending exactly at market
        # close as invalid.
        return (
            self.is_market_open(timestamp)
            and self.is_market_open(end - timedelta(microseconds=1))
        )
