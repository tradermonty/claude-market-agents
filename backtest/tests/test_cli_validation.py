#!/usr/bin/env python3
"""Tests for CLI argument validation."""

import subprocess
import sys


def run_cli(*extra_args: str) -> subprocess.CompletedProcess:
    """Run backtest.main with given args and return the result."""
    return subprocess.run(
        [sys.executable, "-m", "backtest.main", "--parse-only", *extra_args],
        capture_output=True,
        text=True,
    )


class TestCLIValidation:
    """CLI argument validation tests — invalid args should exit with code 2."""

    def test_stop_loss_too_high(self):
        result = run_cli("--stop-loss", "150")
        assert result.returncode == 2
        assert "--stop-loss" in result.stderr

    def test_stop_loss_negative(self):
        result = run_cli("--stop-loss", "-5")
        assert result.returncode == 2
        assert "--stop-loss" in result.stderr

    def test_slippage_too_high(self):
        result = run_cli("--slippage", "60")
        assert result.returncode == 2
        assert "--slippage" in result.stderr

    def test_position_size_zero(self):
        result = run_cli("--position-size", "0")
        assert result.returncode == 2
        assert "--position-size" in result.stderr

    def test_max_holding_zero(self):
        result = run_cli("--max-holding", "0")
        assert result.returncode == 2
        assert "--max-holding" in result.stderr

    def test_min_score_exceeds_max_score(self):
        result = run_cli("--min-score", "80", "--max-score", "70")
        assert result.returncode == 2
        assert "--min-score" in result.stderr

    def test_min_gap_exceeds_max_gap(self):
        result = run_cli("--min-gap", "20", "--max-gap", "10")
        assert result.returncode == 2
        assert "--min-gap" in result.stderr

    def test_daily_entry_limit_zero(self):
        result = run_cli("--daily-entry-limit", "0")
        assert result.returncode == 2
        assert "--daily-entry-limit" in result.stderr

    def test_wf_folds_zero(self):
        result = run_cli("--wf-folds", "0")
        assert result.returncode == 2
        assert "--wf-folds" in result.stderr

    def test_multiple_errors_listed(self):
        result = run_cli("--stop-loss", "150", "--slippage", "60")
        assert result.returncode == 2
        assert "--stop-loss" in result.stderr
        assert "--slippage" in result.stderr

    def test_valid_args_pass(self):
        result = run_cli("--stop-loss", "10", "--slippage", "0.5")
        # Should not exit with code 2 (may fail for other reasons like missing reports dir)
        assert result.returncode != 2
