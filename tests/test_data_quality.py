
from datetime import UTC, date, datetime, timedelta

import pytest

from core.schemas import MarketBar, Timeframe
from data.market_calendar import ForexMarketCalendar
from data.quality import MarketDataQualityChecker


def make_bar(timestamp: datetime) -> MarketBar:
    return MarketBar(
        timestamp=timestamp,
        open=1.10,
        high=1.12,
        low=1.09,
        close=1.11,
        volume=100.0,
    )


def test_detects_missing_h1_candles():
    checker = MarketDataQualityChecker()

    bars = [
        make_bar(datetime(2026, 1, 5, 10, tzinfo=UTC)),
        make_bar(datetime(2026, 1, 5, 13, tzinfo=UTC)),
    ]

    report = checker.check(bars, Timeframe.H1)

    assert report.candles_checked == 2
    assert report.total_missing_candles == 2
    assert len(report.gaps) == 1
    assert report.gaps[0].missing_candles == 2
    assert report.is_complete is False


def test_detects_missing_m5_candles():
    checker = MarketDataQualityChecker()

    bars = [
        make_bar(datetime(2026, 1, 5, 10, 0, tzinfo=UTC)),
        make_bar(datetime(2026, 1, 5, 10, 20, tzinfo=UTC)),
    ]

    report = checker.check(bars, Timeframe.M5)

    assert report.total_missing_candles == 3
    assert report.gaps[0].first_missing_timestamp == datetime(
        2026, 1, 5, 10, 5, tzinfo=UTC
    )
    assert report.gaps[0].last_missing_timestamp == datetime(
        2026, 1, 5, 10, 15, tzinfo=UTC
    )


def test_weekend_intervals_are_not_counted_as_missing():
    checker = MarketDataQualityChecker()

    bars = [
        make_bar(datetime(2026, 1, 9, 22, tzinfo=UTC)),
        make_bar(datetime(2026, 1, 11, 22, tzinfo=UTC)),
    ]

    report = checker.check(bars, Timeframe.H1)

    assert report.total_missing_candles == 0
    assert report.weekend_intervals_skipped == 41
    assert report.gaps == ()
    assert report.is_complete is True


def test_weekday_gap_is_detected_across_weekend():
    checker = MarketDataQualityChecker()

    bars = [
        make_bar(datetime(2026, 1, 9, 20, tzinfo=UTC)),
        make_bar(datetime(2026, 1, 12, 1, tzinfo=UTC)),
    ]

    report = checker.check(bars, Timeframe.H1)

    assert report.total_missing_candles == 4
    assert report.weekend_intervals_skipped == 41
    assert report.gaps[0].missing_candles == 4


def test_unsorted_bars_are_checked_in_timestamp_order():
    checker = MarketDataQualityChecker()

    bars = [
        make_bar(datetime(2026, 1, 5, 13, tzinfo=UTC)),
        make_bar(datetime(2026, 1, 5, 10, tzinfo=UTC)),
    ]

    report = checker.check(bars, Timeframe.H1)

    assert report.total_missing_candles == 2


def test_duplicate_timestamps_are_rejected():
    checker = MarketDataQualityChecker()
    timestamp = datetime(2026, 1, 5, 10, tzinfo=UTC)

    with pytest.raises(ValueError, match="Duplicate"):
        checker.check(
            [make_bar(timestamp), make_bar(timestamp)],
            Timeframe.H1,
        )


def test_naive_timestamps_are_rejected():
    checker = MarketDataQualityChecker()

    with pytest.raises(ValueError, match="timezone-aware"):
        checker.check(
            [make_bar(datetime(2026, 1, 5, 10))],
            Timeframe.H1,
        )


def test_friday_market_closes_at_1700_new_york():
    calendar = ForexMarketCalendar()

    before_close = datetime(2026, 1, 9, 21, 59, tzinfo=UTC)
    at_close = datetime(2026, 1, 9, 22, 0, tzinfo=UTC)

    assert calendar.is_market_open(before_close) is True
    assert calendar.is_market_open(at_close) is False


def test_sunday_market_opens_at_1700_new_york():
    calendar = ForexMarketCalendar()

    before_open = datetime(2026, 1, 11, 21, 59, tzinfo=UTC)
    at_open = datetime(2026, 1, 11, 22, 0, tzinfo=UTC)

    assert calendar.is_market_open(before_open) is False
    assert calendar.is_market_open(at_open) is True


def test_daylight_saving_time_is_handled_automatically():
    calendar = ForexMarketCalendar()

    # New York switches to daylight saving time on March 8, 2026.
    # Sunday 17:00 New York is therefore 21:00 UTC.
    market_open = datetime(2026, 3, 8, 21, 0, tzinfo=UTC)

    assert calendar.is_market_open(market_open) is True


def test_candle_ending_exactly_at_market_close_is_expected():
    calendar = ForexMarketCalendar()

    timestamp = datetime(2026, 1, 9, 21, 0, tzinfo=UTC)

    assert calendar.is_candle_expected(
        timestamp,
        timedelta(hours=1),
    ) is True


def test_candle_crossing_market_close_is_not_expected():
    calendar = ForexMarketCalendar()

    timestamp = datetime(2026, 1, 9, 21, 30, tzinfo=UTC)

    assert calendar.is_candle_expected(
        timestamp,
        timedelta(hours=1),
    ) is False


def test_configured_closed_date_is_not_open():
    calendar = ForexMarketCalendar(
        closed_dates=frozenset({date(2026, 1, 5)}),
    )

    timestamp = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)

    assert calendar.is_market_open(timestamp) is False


def test_calendar_rejects_naive_timestamps():
    calendar = ForexMarketCalendar()

    with pytest.raises(ValueError, match="timezone-aware"):
        calendar.is_market_open(datetime(2026, 1, 5, 12))


def test_calendar_rejects_invalid_timezone():
    with pytest.raises(ValueError, match="Unknown market timezone"):
        ForexMarketCalendar(timezone="Invalid/Timezone")
