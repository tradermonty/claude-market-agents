#!/usr/bin/env python3
"""Unit tests for backtest.vix_filter module."""

import argparse
from unittest.mock import MagicMock

from backtest.price_fetcher import PriceBar
from backtest.trade_simulator import SkippedTrade
from backtest.vix_filter import VixDay

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
        assert result["2025-10-13"] == VixDay(open=18.0, close=18.5)
        assert result["2025-10-14"] == VixDay(open=19.0, close=20.5)
        fake_fetcher.fetch_prices.assert_called_once_with("^VIX", "2025-10-13", "2025-10-14")

    def test_empty_response_returns_empty_dict(self):
        from backtest.vix_filter import fetch_vix_data

        fake_fetcher = MagicMock()
        fake_fetcher.fetch_prices.return_value = []
        result = fetch_vix_data(fake_fetcher, "2025-10-13", "2025-10-14")
        assert result == {}


class TestResolveVix:
    """Tests for _resolve_vix() — uses report_date open, fallback to T-1 close."""

    def test_report_date_uses_open(self):
        """When report_date is a trading day, use its VIX open."""
        from backtest.vix_filter import _resolve_vix

        vix_data = {
            "2025-10-14": VixDay(open=18.0, close=19.0),
            "2025-10-15": VixDay(open=22.0, close=25.0),
        }
        # Must return report_date's OPEN, not close
        assert _resolve_vix("2025-10-15", vix_data) == 22.0

    def test_non_trading_day_falls_back_to_previous_close(self):
        """report_date is Saturday → fallback to Friday's close."""
        from backtest.vix_filter import _resolve_vix

        # 2025-10-18 is Saturday, 2025-10-17 is Friday
        vix_data = {"2025-10-17": VixDay(open=18.0, close=19.0)}
        assert _resolve_vix("2025-10-18", vix_data) == 19.0

    def test_holiday_fallback_multiple_days(self):
        """Fall back to previous trading day's close."""
        from backtest.vix_filter import _resolve_vix

        # report_date=10/15 not in data, 10/14 not in data, 10/13 not in data, 10/12 has data
        vix_data = {"2025-10-12": VixDay(open=20.0, close=21.0)}
        assert _resolve_vix("2025-10-15", vix_data) == 21.0

    def test_fallback_beyond_range_returns_none(self):
        from backtest.vix_filter import _resolve_vix

        # report_date=10/15 not in data, fallback range is 10/14..10/10
        # Data at 10/09 is outside range
        vix_data = {"2025-10-09": VixDay(open=20.0, close=21.0)}
        assert _resolve_vix("2025-10-15", vix_data) is None

    def test_empty_data_returns_none(self):
        from backtest.vix_filter import _resolve_vix

        assert _resolve_vix("2025-10-15", {}) is None

    def test_prefers_open_over_previous_close(self):
        """If both report_date open and T-1 close exist, use open."""
        from backtest.vix_filter import _resolve_vix

        vix_data = {
            "2025-10-14": VixDay(open=15.0, close=16.0),
            "2025-10-15": VixDay(open=22.0, close=25.0),
        }
        # open=22.0 should win over T-1 close=16.0
        assert _resolve_vix("2025-10-15", vix_data) == 22.0


class TestShouldSkipByVix:
    """Tests for should_skip_by_vix()."""

    def test_vix_above_threshold_skips(self):
        from backtest.vix_filter import should_skip_by_vix

        # report_date=10/15, VIX open=22
        vix_data = {"2025-10-15": VixDay(open=22.0, close=25.0)}
        skip, reason = should_skip_by_vix("2025-10-15", vix_data, vix_threshold=20.0)
        assert skip is True
        assert reason == "filter_high_vix_20.0"

    def test_vix_at_threshold_does_not_skip(self):
        """VIX exactly at threshold should NOT skip (> not >=)."""
        from backtest.vix_filter import should_skip_by_vix

        vix_data = {"2025-10-15": VixDay(open=20.0, close=22.0)}
        skip, reason = should_skip_by_vix("2025-10-15", vix_data, vix_threshold=20.0)
        assert skip is False
        assert reason is None

    def test_vix_below_threshold_passes(self):
        from backtest.vix_filter import should_skip_by_vix

        vix_data = {"2025-10-15": VixDay(open=19.99, close=22.0)}
        skip, reason = should_skip_by_vix("2025-10-15", vix_data, vix_threshold=20.0)
        assert skip is False
        assert reason is None

    def test_vix_just_above_threshold(self):
        from backtest.vix_filter import should_skip_by_vix

        vix_data = {"2025-10-15": VixDay(open=20.01, close=22.0)}
        skip, reason = should_skip_by_vix("2025-10-15", vix_data, vix_threshold=20.0)
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

        vix_data = {"2025-10-15": VixDay(open=25.0, close=27.0)}
        skip, reason = should_skip_by_vix("2025-10-15", vix_data, vix_threshold=30.0)
        assert skip is False

        skip, reason = should_skip_by_vix("2025-10-15", vix_data, vix_threshold=24.0)
        assert skip is True
        assert reason == "filter_high_vix_24.0"


class TestApplyVixFilter:
    """Tests for apply_vix_filter()."""

    def test_all_pass(self):
        from backtest.vix_filter import apply_vix_filter

        candidates = [_make_candidate("AAPL", "2025-10-15"), _make_candidate("MSFT", "2025-10-16")]
        vix_data = {
            "2025-10-15": VixDay(open=15.0, close=16.0),
            "2025-10-16": VixDay(open=18.0, close=19.0),
        }
        passed, skipped = apply_vix_filter(candidates, vix_data, vix_threshold=20.0)
        assert len(passed) == 2
        assert len(skipped) == 0

    def test_all_skipped(self):
        from backtest.vix_filter import apply_vix_filter

        candidates = [_make_candidate("AAPL", "2025-10-15"), _make_candidate("MSFT", "2025-10-16")]
        vix_data = {
            "2025-10-15": VixDay(open=25.0, close=27.0),
            "2025-10-16": VixDay(open=30.0, close=32.0),
        }
        passed, skipped = apply_vix_filter(candidates, vix_data, vix_threshold=20.0)
        assert len(passed) == 0
        assert len(skipped) == 2

    def test_mixed(self):
        from backtest.vix_filter import apply_vix_filter

        candidates = [_make_candidate("AAPL", "2025-10-15"), _make_candidate("MSFT", "2025-10-16")]
        vix_data = {
            "2025-10-15": VixDay(open=15.0, close=16.0),
            "2025-10-16": VixDay(open=25.0, close=27.0),
        }
        passed, skipped = apply_vix_filter(candidates, vix_data, vix_threshold=20.0)
        assert len(passed) == 1
        assert passed[0].ticker == "AAPL"
        assert len(skipped) == 1
        assert skipped[0].ticker == "MSFT"

    def test_skipped_trade_fields(self):
        from backtest.vix_filter import apply_vix_filter

        candidates = [_make_candidate("AAPL", "2025-10-15")]
        vix_data = {"2025-10-15": VixDay(open=25.0, close=27.0)}
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
