from datetime import UTC, date, datetime, timedelta

import pytest

from data.market_calendar import ForexMarketCalendar


def test_market_opens_sunday_at_1700_new_york():
    calendar = ForexMarketCalendar()

    assert calendar.is_market_open(datetime(2026, 1, 11, 22, 0, tzinfo=UTC))


def test_market_closes_friday_at_1700_new_york():
    calendar = ForexMarketCalendar()

    assert not calendar.is_market_open(datetime(2026, 1, 9, 22, 0, tzinfo=UTC))


def test_market_is_closed_on_saturday():
    calendar = ForexMarketCalendar()

    assert not calendar.is_market_open(datetime(2026, 1, 10, 12, 0, tzinfo=UTC))


def test_market_uses_configured_timezone():
    calendar = ForexMarketCalendar(timezone="Europe/London")

    # Sunday at 17:00 London time.
    assert calendar.is_market_open(datetime(2026, 1, 11, 17, 0, tzinfo=UTC))


def test_market_holiday_is_closed():
    calendar = ForexMarketCalendar(closed_dates=frozenset({date(2026, 1, 5)}))

    assert not calendar.is_market_open(datetime(2026, 1, 5, 12, 0, tzinfo=UTC))


def test_invalid_candle_duration_is_rejected():
    calendar = ForexMarketCalendar()

    with pytest.raises(ValueError, match="positive"):
        calendar.is_candle_expected(
            datetime(2026, 1, 5, 12, 0, tzinfo=UTC),
            timedelta(0),
        )


def test_market_closes_friday_before_and_at_boundary():
    calendar = ForexMarketCalendar()

    assert calendar.is_market_open(datetime(2026, 1, 9, 21, 59, tzinfo=UTC))
    assert not calendar.is_market_open(datetime(2026, 1, 9, 22, 0, tzinfo=UTC))


def test_market_reopens_sunday_at_boundary():
    calendar = ForexMarketCalendar()

    assert not calendar.is_market_open(datetime(2026, 1, 11, 21, 59, tzinfo=UTC))
    assert calendar.is_market_open(datetime(2026, 1, 11, 22, 0, tzinfo=UTC))


def test_candle_ending_exactly_at_friday_close_is_valid():
    calendar = ForexMarketCalendar()

    assert calendar.is_candle_expected(
        datetime(2026, 1, 9, 21, 0, tzinfo=UTC),
        timedelta(hours=1),
    )


def test_candle_crossing_friday_close_is_invalid():
    calendar = ForexMarketCalendar()

    assert not calendar.is_candle_expected(
        datetime(2026, 1, 9, 21, 30, tzinfo=UTC),
        timedelta(hours=1),
    )


def test_dst_changes_sunday_opening_utc_time():
    calendar = ForexMarketCalendar()

    # New York: UTC-5 in January, UTC-4 in July.
    assert calendar.is_market_open(datetime(2026, 1, 11, 22, 0, tzinfo=UTC))
    assert calendar.is_market_open(datetime(2026, 7, 12, 21, 0, tzinfo=UTC))


def test_naive_timestamp_is_rejected():
    calendar = ForexMarketCalendar()

    with pytest.raises(ValueError, match="timezone-aware"):
        calendar.is_market_open(datetime(2026, 1, 9, 15, 0))


def test_invalid_timezone_is_rejected():
    with pytest.raises(ValueError, match="Unknown market timezone"):
        ForexMarketCalendar(timezone="Invalid/Timezone")
