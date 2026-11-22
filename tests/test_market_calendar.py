
from datetime import UTC, date, datetime, timedelta

import pytest

from data.market_calendar import ForexMarketCalendar


def test_market_opens_sunday_at_1700_new_york():
    calendar = ForexMarketCalendar()

    assert calendar.is_market_open(
        datetime(2026, 1, 11, 22, 0, tzinfo=UTC)
    )


def test_market_closes_friday_at_1700_new_york():
    calendar = ForexMarketCalendar()

    assert not calendar.is_market_open(
        datetime(2026, 1, 9, 22, 0, tzinfo=UTC)
    )


def test_market_is_closed_on_saturday():
    calendar = ForexMarketCalendar()

    assert not calendar.is_market_open(
        datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    )


def test_market_uses_configured_timezone():
    calendar = ForexMarketCalendar(timezone="Europe/London")

    # Sunday at 17:00 London time.
    assert calendar.is_market_open(
        datetime(2026, 1, 11, 17, 0, tzinfo=UTC)
    )


def test_market_holiday_is_closed():
    calendar = ForexMarketCalendar(
        closed_dates=frozenset({date(2026, 1, 5)})
    )

    assert not calendar.is_market_open(
        datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    )


def test_invalid_candle_duration_is_rejected():
    calendar = ForexMarketCalendar()

    with pytest.raises(ValueError, match="positive"):
        calendar.is_candle_expected(
            datetime(2026, 1, 5, 12, 0, tzinfo=UTC),
            timedelta(0),
        )
