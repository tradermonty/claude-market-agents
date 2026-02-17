#!/usr/bin/env python3
"""Tests for entry quality filter logic."""

import argparse

from backtest.entry_filter import (
    apply_entry_quality_filter,
    is_filter_active,
    should_skip_candidate,
    validate_filter_args,
)
from backtest.html_parser import TradeCandidate
from backtest.trade_simulator import SkippedTrade


def _candidate(
    ticker="TEST",
    report_date="2025-10-01",
    grade="B",
    score=70.0,
    price=50.0,
    gap_size=5.0,
):
    return TradeCandidate(
        ticker=ticker,
        report_date=report_date,
        grade=grade,
        grade_source="html",
        score=score,
        price=price,
        gap_size=gap_size,
    )


class TestShouldSkipCandidate:
    """Test should_skip_candidate with default thresholds."""

    # --- Price filter boundary tests ---
    def test_price_9_99_passes(self):
        skip, reason = should_skip_candidate(_candidate(price=9.99))
        assert not skip
        assert reason is None

    def test_price_10_0_skipped(self):
        skip, reason = should_skip_candidate(_candidate(price=10.0))
        assert skip
        assert reason == "filter_low_price_10_30"

    def test_price_20_skipped(self):
        skip, reason = should_skip_candidate(_candidate(price=20.0))
        assert skip
        assert reason == "filter_low_price_10_30"

    def test_price_29_99_skipped(self):
        skip, reason = should_skip_candidate(_candidate(price=29.99))
        assert skip
        assert reason == "filter_low_price_10_30"

    def test_price_30_0_passes(self):
        skip, reason = should_skip_candidate(_candidate(price=30.0))
        assert not skip
        assert reason is None

    def test_price_none_passes(self):
        skip, reason = should_skip_candidate(_candidate(price=None))
        assert not skip
        assert reason is None

    # --- Gap+Score combo tests ---
    def test_gap_10_score_85_skipped(self):
        skip, reason = should_skip_candidate(_candidate(price=50.0, gap_size=10.0, score=85.0))
        assert skip
        assert reason == "filter_high_gap_score_10_85"

    def test_gap_15_score_90_skipped(self):
        skip, reason = should_skip_candidate(_candidate(price=50.0, gap_size=15.0, score=90.0))
        assert skip
        assert reason == "filter_high_gap_score_10_85"

    def test_gap_9_9_score_85_passes(self):
        skip, reason = should_skip_candidate(_candidate(price=50.0, gap_size=9.9, score=85.0))
        assert not skip
        assert reason is None

    def test_gap_10_score_84_9_passes(self):
        skip, reason = should_skip_candidate(_candidate(price=50.0, gap_size=10.0, score=84.9))
        assert not skip
        assert reason is None

    def test_gap_none_passes(self):
        skip, reason = should_skip_candidate(_candidate(price=50.0, gap_size=None, score=90.0))
        assert not skip
        assert reason is None

    def test_score_none_passes(self):
        skip, reason = should_skip_candidate(_candidate(price=50.0, gap_size=15.0, score=None))
        assert not skip
        assert reason is None

    # --- Priority: price filter takes precedence over combo ---
    def test_both_filters_match_returns_price_reason(self):
        # price=15 triggers price filter; gap=12, score=90 triggers combo
        skip, reason = should_skip_candidate(_candidate(price=15.0, gap_size=12.0, score=90.0))
        assert skip
        assert reason == "filter_low_price_10_30"

    # --- Normal pass ---
    def test_normal_candidate_passes(self):
        skip, reason = should_skip_candidate(_candidate(price=50.0, gap_size=5.0, score=70.0))
        assert not skip
        assert reason is None


class TestShouldSkipCandidateCustomThresholds:
    """Test should_skip_candidate with custom thresholds."""

    def test_custom_price_range(self):
        # price=25 should be skipped with custom range [20, 50)
        skip, reason = should_skip_candidate(_candidate(price=25.0), price_min=20, price_max=50)
        assert skip
        assert reason == "filter_low_price_20_50"

    def test_custom_price_range_boundary_pass(self):
        skip, _ = should_skip_candidate(_candidate(price=19.99), price_min=20, price_max=50)
        assert not skip

    def test_custom_price_range_upper_boundary_pass(self):
        skip, _ = should_skip_candidate(_candidate(price=50.0), price_min=20, price_max=50)
        assert not skip

    def test_custom_gap_score_combo(self):
        skip, reason = should_skip_candidate(
            _candidate(price=50.0, gap_size=5.0, score=70.0),
            gap_threshold=5,
            score_threshold=70,
        )
        assert skip
        assert reason == "filter_high_gap_score_5_70"

    def test_custom_gap_score_combo_below_threshold(self):
        skip, _ = should_skip_candidate(
            _candidate(price=50.0, gap_size=4.9, score=70.0),
            gap_threshold=5,
            score_threshold=70,
        )
        assert not skip


class TestApplyEntryQualityFilter:
    """Test apply_entry_quality_filter."""

    def test_empty_list(self):
        passed, skipped = apply_entry_quality_filter([])
        assert passed == []
        assert skipped == []

    def test_all_pass(self):
        candidates = [
            _candidate(ticker="GOOD1", price=50.0, gap_size=5.0, score=70.0),
            _candidate(ticker="GOOD2", price=80.0, gap_size=3.0, score=60.0),
        ]
        passed, skipped = apply_entry_quality_filter(candidates)
        assert len(passed) == 2
        assert len(skipped) == 0

    def test_all_filtered(self):
        candidates = [
            _candidate(ticker="BAD1", price=15.0),
            _candidate(ticker="BAD2", price=25.0),
        ]
        passed, skipped = apply_entry_quality_filter(candidates)
        assert len(passed) == 0
        assert len(skipped) == 2

    def test_mixed(self):
        candidates = [
            _candidate(ticker="GOOD", price=50.0, gap_size=5.0, score=70.0),
            _candidate(ticker="LOWP", price=15.0, gap_size=3.0, score=60.0),
            _candidate(ticker="HIGS", price=50.0, gap_size=12.0, score=90.0),
        ]
        passed, skipped = apply_entry_quality_filter(candidates)
        assert len(passed) == 1
        assert passed[0].ticker == "GOOD"
        assert len(skipped) == 2
        # Verify skip reasons reflect default thresholds
        reasons = {s.ticker: s.skip_reason for s in skipped}
        assert reasons["LOWP"] == "filter_low_price_10_30"
        assert reasons["HIGS"] == "filter_high_gap_score_10_85"

    def test_skipped_trade_fields(self):
        candidates = [
            _candidate(ticker="SKIP", report_date="2025-11-01", grade="A", score=90.0, price=20.0),
        ]
        _, skipped = apply_entry_quality_filter(candidates)
        assert len(skipped) == 1
        s = skipped[0]
        assert isinstance(s, SkippedTrade)
        assert s.ticker == "SKIP"
        assert s.report_date == "2025-11-01"
        assert s.grade == "A"
        assert s.score == 90.0
        assert s.skip_reason == "filter_low_price_10_30"

    def test_custom_thresholds(self):
        candidates = [
            _candidate(ticker="A", price=35.0, gap_size=5.0, score=70.0),
        ]
        passed, skipped = apply_entry_quality_filter(candidates, price_min=20, price_max=40)
        assert len(passed) == 0
        assert len(skipped) == 1


class TestIsFilterActive:
    """Test is_filter_active truth source logic."""

    def _make_args(self, **kwargs):
        defaults = {
            "entry_quality_filter": False,
            "exclude_price_min": None,
            "exclude_price_max": None,
            "risk_gap_threshold": None,
            "risk_score_threshold": None,
        }
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_flag_only(self):
        assert is_filter_active(self._make_args(entry_quality_filter=True))

    def test_exclude_price_min_only(self):
        assert is_filter_active(self._make_args(exclude_price_min=15.0))

    def test_exclude_price_max_only(self):
        assert is_filter_active(self._make_args(exclude_price_max=50.0))

    def test_risk_gap_threshold_only(self):
        assert is_filter_active(self._make_args(risk_gap_threshold=5.0))

    def test_risk_score_threshold_only(self):
        assert is_filter_active(self._make_args(risk_score_threshold=80.0))

    def test_nothing_specified(self):
        assert not is_filter_active(self._make_args())

    def test_missing_attribute_returns_false(self):
        # When args doesn't have entry_quality_filter attribute at all
        args = argparse.Namespace(
            exclude_price_min=None,
            exclude_price_max=None,
            risk_gap_threshold=None,
            risk_score_threshold=None,
        )
        assert not is_filter_active(args)


class TestValidateFilterArgs:
    """Test validate_filter_args."""

    def _make_args(self, **kwargs):
        defaults = {
            "entry_quality_filter": False,
            "exclude_price_min": None,
            "exclude_price_max": None,
            "risk_gap_threshold": None,
            "risk_score_threshold": None,
        }
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_valid_defaults(self):
        errors = validate_filter_args(self._make_args())
        assert errors == []

    def test_valid_with_all_overrides(self):
        errors = validate_filter_args(
            self._make_args(
                exclude_price_min=5.0,
                exclude_price_max=25.0,
                risk_gap_threshold=8.0,
                risk_score_threshold=90.0,
            )
        )
        assert errors == []

    def test_negative_price_min(self):
        errors = validate_filter_args(self._make_args(exclude_price_min=-5.0))
        assert len(errors) == 1
        assert "--exclude-price-min" in errors[0]

    def test_price_min_exceeds_default_max(self):
        # exclude_price_min=40, effective max=30 (default)
        errors = validate_filter_args(self._make_args(exclude_price_min=40.0))
        assert len(errors) == 1
        assert "--exclude-price-max" in errors[0]

    def test_price_max_less_than_min(self):
        errors = validate_filter_args(
            self._make_args(exclude_price_min=40.0, exclude_price_max=30.0)
        )
        assert len(errors) == 1
        assert "--exclude-price-max" in errors[0]

    def test_risk_gap_threshold_negative(self):
        errors = validate_filter_args(self._make_args(risk_gap_threshold=-1.0))
        assert len(errors) == 1
        assert "--risk-gap-threshold" in errors[0]

    def test_risk_score_threshold_above_100(self):
        errors = validate_filter_args(self._make_args(risk_score_threshold=150.0))
        assert len(errors) == 1
        assert "--risk-score-threshold" in errors[0]

    def test_risk_score_threshold_negative(self):
        errors = validate_filter_args(self._make_args(risk_score_threshold=-10.0))
        assert len(errors) == 1
        assert "--risk-score-threshold" in errors[0]

    def test_multiple_errors(self):
        errors = validate_filter_args(
            self._make_args(
                exclude_price_min=-5.0,
                risk_score_threshold=150.0,
            )
        )
        assert len(errors) >= 2
