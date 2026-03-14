#!/usr/bin/env python3
"""Unit tests for backtest.vix_filter module."""

import argparse
from typing import Dict
from unittest.mock import MagicMock

from backtest.price_fetcher import PriceBar
from backtest.trade_simulator import SkippedTrade

# ---- Helpers ----


def _make_candidate(ticker="AAPL", report_date="2025-10-15"):
    """Create a minimal TradeCandidate-like object for testing."""
    from backtest.html_parser import TradeCandidate

    return TradeCandidate(
        ticker=ticker,
        report_date=report_date,
        grade="A",
        grade_source="html",
        score=85.0,
        price=150.0,
        gap_size=5.0,
        company_name=f"{ticker} Inc",
    )


def _make_vix_data(entries: Dict[str, float]) -> Dict[str, float]:
    """Helper to create vix_data dict."""
    return entries


class TestFetchVixData:
    """Tests for fetch_vix_data()."""

    def test_basic_fetch(self):
        from backtest.vix_filter import fetch_vix_data

        fake_fetcher = MagicMock()
        fake_fetcher.fetch_prices.return_value = [
            PriceBar(
                date="2025-10-13",
                open=18.0,
                high=19.0,
                low=17.5,
                close=18.5,
                adj_close=18.5,
                volume=100000,
            ),
            PriceBar(
                date="2025-10-14",
                open=19.0,
                high=21.0,
                low=18.0,
                close=20.5,
                adj_close=20.5,
                volume=120000,
            ),
        ]
        result = fetch_vix_data(fake_fetcher, "2025-10-13", "2025-10-14")
        assert result == {"2025-10-13": 18.5, "2025-10-14": 20.5}
        fake_fetcher.fetch_prices.assert_called_once_with("^VIX", "2025-10-13", "2025-10-14")

    def test_empty_response_returns_empty_dict(self):
        from backtest.vix_filter import fetch_vix_data

        fake_fetcher = MagicMock()
        fake_fetcher.fetch_prices.return_value = []
        result = fetch_vix_data(fake_fetcher, "2025-10-13", "2025-10-14")
        assert result == {}


class TestResolveVix:
    """Tests for _resolve_vix() — uses T-1 to avoid look-ahead bias."""

    def test_previous_day_hit(self):
        """report_date=10/15 → looks up 10/14 (T-1)."""
        from backtest.vix_filter import _resolve_vix

        vix_data = {"2025-10-14": 22.0, "2025-10-15": 99.0}
        # Must return T-1 (10/14), NOT report_date (10/15)
        assert _resolve_vix("2025-10-15", vix_data) == 22.0

    def test_report_date_itself_is_ignored(self):
        """Even if report_date has VIX data, it should not be used (look-ahead)."""
        from backtest.vix_filter import _resolve_vix

        vix_data = {"2025-10-15": 30.0}  # Only report_date, no T-1
        # T-1 (10/14) not in data, fallback searches further back → None
        assert _resolve_vix("2025-10-15", vix_data) is None

    def test_weekend_fallback(self):
        """report_date is Monday 10/13 → T-1 is Sunday → falls back to Friday 10/10."""
        from backtest.vix_filter import _resolve_vix

        # 2025-10-13 is Monday, T-1 = Sunday 10/12 (no data), fallback to Friday 10/10
        vix_data = {"2025-10-10": 19.0}
        assert _resolve_vix("2025-10-13", vix_data) == 19.0

    def test_holiday_fallback_multiple_days(self):
        """Fall back up to 5+1 days from T-1."""
        from backtest.vix_filter import _resolve_vix

        # report_date=10/15, T-1=10/14, data only at 10/11 (3 days before T-1)
        vix_data = {"2025-10-11": 21.0}
        assert _resolve_vix("2025-10-15", vix_data) == 21.0

    def test_fallback_beyond_range_returns_none(self):
        from backtest.vix_filter import _resolve_vix

        # report_date=10/15, T-1=10/14, range is T-1 to T-6 (10/14..10/09)
        # Data at 10/08 is outside range
        vix_data = {"2025-10-08": 21.0}
        assert _resolve_vix("2025-10-15", vix_data) is None

    def test_empty_data_returns_none(self):
        from backtest.vix_filter import _resolve_vix

        assert _resolve_vix("2025-10-15", {}) is None


class TestShouldSkipByVix:
    """Tests for should_skip_by_vix().

    All vix_data keys are T-1 relative to report_date since _resolve_vix
    uses the previous day's close to avoid look-ahead.
    """

    def test_vix_above_threshold_skips(self):
        from backtest.vix_filter import should_skip_by_vix

        # report_date=10/16, VIX on 10/15 (T-1) = 22
        vix_data = {"2025-10-15": 22.0}
        skip, reason = should_skip_by_vix("2025-10-16", vix_data, vix_threshold=20.0)
        assert skip is True
        assert reason == "filter_high_vix_20.0"

    def test_vix_at_threshold_does_not_skip(self):
        """VIX exactly at threshold should NOT skip (> not >=)."""
        from backtest.vix_filter import should_skip_by_vix

        vix_data = {"2025-10-15": 20.0}
        skip, reason = should_skip_by_vix("2025-10-16", vix_data, vix_threshold=20.0)
        assert skip is False
        assert reason is None

    def test_vix_below_threshold_passes(self):
        from backtest.vix_filter import should_skip_by_vix

        vix_data = {"2025-10-15": 19.99}
        skip, reason = should_skip_by_vix("2025-10-16", vix_data, vix_threshold=20.0)
        assert skip is False
        assert reason is None

    def test_vix_just_above_threshold(self):
        from backtest.vix_filter import should_skip_by_vix

        vix_data = {"2025-10-15": 20.01}
        skip, reason = should_skip_by_vix("2025-10-16", vix_data, vix_threshold=20.0)
        assert skip is True
        assert reason == "filter_high_vix_20.0"

    def test_missing_vix_data_fail_open(self):
        """When VIX data is missing, fail open (don't skip)."""
        from backtest.vix_filter import should_skip_by_vix

        skip, reason = should_skip_by_vix("2025-10-16", {}, vix_threshold=20.0)
        assert skip is False
        assert reason is None

    def test_custom_threshold(self):
        from backtest.vix_filter import should_skip_by_vix

        # T-1 data for report_date=10/16
        vix_data = {"2025-10-15": 25.0}
        skip, reason = should_skip_by_vix("2025-10-16", vix_data, vix_threshold=30.0)
        assert skip is False

        skip, reason = should_skip_by_vix("2025-10-16", vix_data, vix_threshold=24.0)
        assert skip is True
        assert reason == "filter_high_vix_24.0"


class TestApplyVixFilter:
    """Tests for apply_vix_filter().

    VIX data keyed to T-1 of each candidate's report_date.
    """

    def test_all_pass(self):
        from backtest.vix_filter import apply_vix_filter

        candidates = [_make_candidate("AAPL", "2025-10-15"), _make_candidate("MSFT", "2025-10-16")]
        # T-1 data: 10/14 for AAPL, 10/15 for MSFT
        vix_data = {"2025-10-14": 15.0, "2025-10-15": 18.0}
        passed, skipped = apply_vix_filter(candidates, vix_data, vix_threshold=20.0)
        assert len(passed) == 2
        assert len(skipped) == 0

    def test_all_skipped(self):
        from backtest.vix_filter import apply_vix_filter

        candidates = [_make_candidate("AAPL", "2025-10-15"), _make_candidate("MSFT", "2025-10-16")]
        vix_data = {"2025-10-14": 25.0, "2025-10-15": 30.0}
        passed, skipped = apply_vix_filter(candidates, vix_data, vix_threshold=20.0)
        assert len(passed) == 0
        assert len(skipped) == 2

    def test_mixed(self):
        from backtest.vix_filter import apply_vix_filter

        candidates = [_make_candidate("AAPL", "2025-10-15"), _make_candidate("MSFT", "2025-10-16")]
        vix_data = {"2025-10-14": 15.0, "2025-10-15": 25.0}
        passed, skipped = apply_vix_filter(candidates, vix_data, vix_threshold=20.0)
        assert len(passed) == 1
        assert passed[0].ticker == "AAPL"
        assert len(skipped) == 1
        assert skipped[0].ticker == "MSFT"

    def test_skipped_trade_fields(self):
        from backtest.vix_filter import apply_vix_filter

        candidates = [_make_candidate("AAPL", "2025-10-15")]
        vix_data = {"2025-10-14": 25.0}
        _, skipped = apply_vix_filter(candidates, vix_data, vix_threshold=20.0)
        assert len(skipped) == 1
        s = skipped[0]
        assert isinstance(s, SkippedTrade)
        assert s.ticker == "AAPL"
        assert s.report_date == "2025-10-15"
        assert s.grade == "A"
        assert s.score == 85.0
        assert s.skip_reason == "filter_high_vix_20.0"


class TestIsVixFilterActive:
    """Tests for is_vix_filter_active()."""

    def test_flag_only(self):
        from backtest.vix_filter import is_vix_filter_active

        args = argparse.Namespace(vix_filter=True, vix_threshold=None)
        assert is_vix_filter_active(args) is True

    def test_threshold_only(self):
        from backtest.vix_filter import is_vix_filter_active

        args = argparse.Namespace(vix_filter=False, vix_threshold=25.0)
        assert is_vix_filter_active(args) is True

    def test_neither(self):
        from backtest.vix_filter import is_vix_filter_active

        args = argparse.Namespace(vix_filter=False, vix_threshold=None)
        assert is_vix_filter_active(args) is False

    def test_missing_attribute_safe(self):
        """Gracefully handle objects without vix_filter/vix_threshold attributes."""
        from backtest.vix_filter import is_vix_filter_active

        args = argparse.Namespace()  # no vix_filter, no vix_threshold
        assert is_vix_filter_active(args) is False


class TestValidateVixFilterArgs:
    """Tests for validate_vix_filter_args()."""

    def test_valid_threshold(self):
        from backtest.vix_filter import validate_vix_filter_args

        args = argparse.Namespace(vix_threshold=20.0)
        assert validate_vix_filter_args(args) == []

    def test_none_threshold(self):
        from backtest.vix_filter import validate_vix_filter_args

        args = argparse.Namespace(vix_threshold=None)
        assert validate_vix_filter_args(args) == []

    def test_zero_threshold_rejected(self):
        from backtest.vix_filter import validate_vix_filter_args

        args = argparse.Namespace(vix_threshold=0)
        errors = validate_vix_filter_args(args)
        assert len(errors) == 1
        assert "--vix-threshold" in errors[0]

    def test_negative_threshold_rejected(self):
        from backtest.vix_filter import validate_vix_filter_args

        args = argparse.Namespace(vix_threshold=-5.0)
        errors = validate_vix_filter_args(args)
        assert len(errors) == 1
        assert "--vix-threshold" in errors[0]
