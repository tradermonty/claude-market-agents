#!/usr/bin/env python3
"""Unit tests for weekly bar aggregation and trend indicators."""

from backtest.price_fetcher import PriceBar
from backtest.weekly_bars import (
    WeeklyBar,
    aggregate_daily_to_weekly,
    compute_weekly_ema,
    compute_weekly_nweek_low,
)


def make_bar(date, open_p, high, low, close, adj_close=None, volume=1000):
    return PriceBar(
        date=date,
        open=open_p,
        high=high,
        low=low,
        close=close,
        adj_close=adj_close if adj_close is not None else close,
        volume=volume,
    )


class TestAggregateDailyToWeekly:
    """Daily-to-weekly bar aggregation tests."""

    def test_aggregate_basic_week(self):
        """Mon-Fri 5 days -> 1 WeeklyBar."""
        # 2025-10-06 (Mon) to 2025-10-10 (Fri)
        bars = [
            make_bar("2025-10-06", 100, 105, 98, 102),
            make_bar("2025-10-07", 102, 107, 100, 104),
            make_bar("2025-10-08", 104, 110, 103, 108),
            make_bar("2025-10-09", 108, 112, 106, 110),
            make_bar("2025-10-10", 110, 115, 108, 113),
        ]
        weekly = aggregate_daily_to_weekly(bars)
        assert len(weekly) == 1
        wb = weekly[0]
        assert wb.week_start == "2025-10-06"
        assert wb.week_ending == "2025-10-10"
        assert wb.open == 100.0  # first bar's adjusted_open
        assert wb.high == 115.0  # max adjusted_high
        assert wb.low == 98.0  # min adjusted_low
        assert wb.close == 113.0  # last bar's adj_close
        assert wb.volume == 5000  # 5 * 1000

    def test_aggregate_two_weeks(self):
        """Two full weeks -> 2 WeeklyBars."""
        bars = [
            # Week 1: 2025-10-06 to 2025-10-10
            make_bar("2025-10-06", 100, 105, 98, 102),
            make_bar("2025-10-07", 102, 107, 100, 104),
            make_bar("2025-10-08", 104, 108, 102, 106),
            make_bar("2025-10-09", 106, 110, 104, 108),
            make_bar("2025-10-10", 108, 112, 106, 110),
            # Week 2: 2025-10-13 to 2025-10-17
            make_bar("2025-10-13", 110, 115, 108, 112),
            make_bar("2025-10-14", 112, 118, 110, 116),
            make_bar("2025-10-15", 116, 120, 114, 118),
            make_bar("2025-10-16", 118, 122, 116, 120),
            make_bar("2025-10-17", 120, 125, 118, 123),
        ]
        weekly = aggregate_daily_to_weekly(bars)
        assert len(weekly) == 2
        assert weekly[0].week_ending == "2025-10-10"
        assert weekly[1].week_ending == "2025-10-17"
        assert weekly[1].open == 110.0
        assert weekly[1].close == 123.0

    def test_aggregate_partial_weeks(self):
        """Week with mid-week start (partial week is valid)."""
        # Start on Wednesday
        bars = [
            make_bar("2025-10-08", 104, 110, 103, 108),
            make_bar("2025-10-09", 108, 112, 106, 110),
            make_bar("2025-10-10", 110, 115, 108, 113),
        ]
        weekly = aggregate_daily_to_weekly(bars)
        assert len(weekly) == 1
        assert weekly[0].week_start == "2025-10-08"
        assert weekly[0].week_ending == "2025-10-10"

    def test_aggregate_holiday_week(self):
        """4-day week (holiday removes one day)."""
        bars = [
            make_bar("2025-10-06", 100, 105, 98, 102),
            make_bar("2025-10-07", 102, 107, 100, 104),
            # 2025-10-08 is holiday
            make_bar("2025-10-09", 106, 110, 104, 108),
            make_bar("2025-10-10", 108, 112, 106, 110),
        ]
        weekly = aggregate_daily_to_weekly(bars)
        assert len(weekly) == 1
        assert weekly[0].volume == 4000

    def test_aggregate_empty(self):
        """Empty input -> empty output."""
        assert aggregate_daily_to_weekly([]) == []

    def test_adjusted_prices_used(self):
        """adj_factor applied: adj_close != close -> adjusted OHLC."""
        # 2:1 split: close=200, adj_close=100 -> factor=0.5
        bars = [
            make_bar("2025-10-06", 200, 210, 190, 200, adj_close=100),
            make_bar("2025-10-07", 202, 212, 192, 202, adj_close=101),
        ]
        weekly = aggregate_daily_to_weekly(bars)
        assert len(weekly) == 1
        wb = weekly[0]
        # adjusted_open = 200 * (100/200) = 100
        assert abs(wb.open - 100.0) < 0.01
        # adjusted_high = max(210*0.5, 212*(101/202)) = max(105, 106) = 106
        assert abs(wb.high - 106.0) < 0.01
        # adjusted_low = min(190*0.5, 192*(101/202)) = min(95, 96) = 95
        assert abs(wb.low - 95.0) < 0.01
        # close = last adj_close = 101
        assert wb.close == 101.0


class TestComputeWeeklyEMA:
    """Weekly EMA computation tests."""

    def test_ema_known_values(self):
        """Hand-calculated 3-period EMA values."""
        weekly_bars = [
            WeeklyBar("2025-10-10", "2025-10-06", 100, 110, 90, 100.0, 1000),
            WeeklyBar("2025-10-17", "2025-10-13", 100, 110, 90, 110.0, 1000),
            WeeklyBar("2025-10-24", "2025-10-20", 100, 110, 90, 105.0, 1000),
            WeeklyBar("2025-10-31", "2025-10-27", 100, 110, 90, 115.0, 1000),
        ]
        ema = compute_weekly_ema(weekly_bars, period=3)
        assert len(ema) == 4
        assert ema[0] is None
        assert ema[1] is None
        # SMA seed at i=2: (100 + 110 + 105) / 3 = 105.0
        assert abs(ema[2] - 105.0) < 0.01
        # EMA at i=3: k = 2/(3+1) = 0.5; 115*0.5 + 105*0.5 = 110.0
        assert abs(ema[3] - 110.0) < 0.01

    def test_ema_insufficient_data(self):
        """Fewer bars than period -> all None."""
        weekly_bars = [
            WeeklyBar("2025-10-10", "2025-10-06", 100, 110, 90, 100.0, 1000),
        ]
        ema = compute_weekly_ema(weekly_bars, period=3)
        assert len(ema) == 1
        assert ema[0] is None

    def test_ema_empty(self):
        """Empty input."""
        assert compute_weekly_ema([], 10) == []

    def test_ema_period_1(self):
        """Period=1 EMA should just track the close."""
        weekly_bars = [
            WeeklyBar("2025-10-10", "2025-10-06", 100, 110, 90, 100.0, 1000),
            WeeklyBar("2025-10-17", "2025-10-13", 100, 110, 90, 120.0, 1000),
        ]
        ema = compute_weekly_ema(weekly_bars, period=1)
        # i=0: SMA seed = 100.0, i=1: k=1.0 => 120*1 + 100*0 = 120
        assert ema[0] == 100.0
        assert abs(ema[1] - 120.0) < 0.01


class TestComputeWeeklyNWeekLow:
    """Weekly N-week low computation tests."""

    def test_nweek_low_basic(self):
        """Basic N-week low with current week excluded."""
        weekly_bars = [
            WeeklyBar("2025-10-10", "2025-10-06", 100, 110, 95, 105, 1000),
            WeeklyBar("2025-10-17", "2025-10-13", 100, 112, 88, 108, 1000),
            WeeklyBar("2025-10-24", "2025-10-20", 100, 115, 92, 112, 1000),
            WeeklyBar("2025-10-31", "2025-10-27", 100, 120, 100, 118, 1000),
        ]
        nwl = compute_weekly_nweek_low(weekly_bars, period=2)
        assert len(nwl) == 4
        assert nwl[0] is None  # i < period
        assert nwl[1] is None  # i < period
        # i=2: window [0:2] = bars[0], bars[1] -> min(95, 88) = 88
        assert nwl[2] == 88.0
        # i=3: window [1:3] = bars[1], bars[2] -> min(88, 92) = 88
        assert nwl[3] == 88.0

    def test_nweek_low_warmup_none(self):
        """i < period returns None strictly."""
        weekly_bars = [
            WeeklyBar("2025-10-10", "2025-10-06", 100, 110, 50, 105, 1000),
            WeeklyBar("2025-10-17", "2025-10-13", 100, 110, 60, 108, 1000),
            WeeklyBar("2025-10-24", "2025-10-20", 100, 110, 70, 112, 1000),
        ]
        nwl = compute_weekly_nweek_low(weekly_bars, period=4)
        # All i < 4, so all None
        assert nwl == [None, None, None]

    def test_nweek_low_excludes_current(self):
        """Current week is NOT included in the N-week low window."""
        weekly_bars = [
            WeeklyBar("2025-10-10", "2025-10-06", 100, 110, 100, 105, 1000),
            WeeklyBar("2025-10-17", "2025-10-13", 100, 110, 100, 108, 1000),
            # Current week has very low low, but it should be excluded
            WeeklyBar("2025-10-24", "2025-10-20", 100, 110, 50, 55, 1000),
        ]
        nwl = compute_weekly_nweek_low(weekly_bars, period=2)
        # i=2: window [0:2] -> min(100, 100) = 100 (NOT 50)
        assert nwl[2] == 100.0

    def test_nweek_low_empty(self):
        """Empty input."""
        assert compute_weekly_nweek_low([], 4) == []
