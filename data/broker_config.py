
"""Persistent broker configuration domain model."""

from dataclasses import dataclass, field
from datetime import date, time

from data.market_calendar import ForexMarketCalendar


@dataclass(frozen=True)
class BrokerConfig:
    """Market-session configuration for a single data provider."""

    provider_id: str
    name: str
    timezone: str = "America/New_York"
    sunday_open: time = time(17, 0)
    friday_close: time = time(17, 0)
    closed_dates: frozenset[date] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.provider_id or self.provider_id != self.provider_id.strip():
            raise ValueError("provider_id must be a non-empty trimmed string")

        if not self.name or self.name != self.name.strip():
            raise ValueError("name must be a non-empty trimmed string")

        # Delegate timezone and wall-clock validation to the calendar.
        self.to_calendar()

    def to_calendar(self) -> ForexMarketCalendar:
        """Build the Forex calendar associated with this provider."""
        return ForexMarketCalendar(
            timezone=self.timezone,
            sunday_open=self.sunday_open,
            friday_close=self.friday_close,
            closed_dates=self.closed_dates,
        )
