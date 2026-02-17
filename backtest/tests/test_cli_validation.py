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


def run_experiment_cli(module: str, *extra_args: str) -> subprocess.CompletedProcess:
    """Run an experiment CLI with --help to verify arg parsing."""
    return subprocess.run(
        [sys.executable, "-m", module, *extra_args],
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

    def test_disable_max_holding_without_trailing(self):
        result = run_cli("--disable-max-holding")
        assert result.returncode == 2
        assert "--disable-max-holding" in result.stderr

    def test_trailing_ema_period_too_low(self):
        result = run_cli("--trailing-ema-period", "1")
        assert result.returncode == 2
        assert "--trailing-ema-period" in result.stderr

    def test_trailing_nweek_period_too_low(self):
        result = run_cli("--trailing-nweek-period", "1")
        assert result.returncode == 2
        assert "--trailing-nweek-period" in result.stderr

    def test_data_end_date_invalid_format(self):
        result = run_cli("--data-end-date", "2025-13-01")
        assert result.returncode == 2
        assert "--data-end-date" in result.stderr

    def test_data_end_date_valid(self):
        result = run_cli("--data-end-date", "2025-12-31")
        # Should not exit with code 2
        assert result.returncode != 2

    def test_trailing_stop_with_disable_max_holding(self):
        result = run_cli("--trailing-stop", "weekly_ema", "--disable-max-holding")
        # Should not exit with code 2 (valid combo)
        assert result.returncode != 2

    def test_max_positions_zero(self):
        result = run_cli("--max-positions", "0")
        assert result.returncode == 2
        assert "--max-positions" in result.stderr

    def test_no_rotation_without_max_positions(self):
        result = run_cli("--no-rotation")
        assert result.returncode == 2
        assert "--no-rotation" in result.stderr

    def test_max_positions_valid(self):
        result = run_cli("--max-positions", "20")
        # Should not exit with code 2
        assert result.returncode != 2

    def test_max_positions_with_no_rotation(self):
        result = run_cli("--max-positions", "20", "--no-rotation")
        # Should not exit with code 2 (valid combo)
        assert result.returncode != 2

    # --- Entry quality filter tests ---
    def test_entry_quality_filter_flag_accepted(self):
        result = run_cli("--entry-quality-filter")
        assert result.returncode != 2

    def test_exclude_price_min_negative_rejected(self):
        result = run_cli("--exclude-price-min", "-5")
        assert result.returncode == 2
        assert "--exclude-price-min" in result.stderr

    def test_exclude_price_max_less_than_min_rejected(self):
        result = run_cli("--exclude-price-min", "40", "--exclude-price-max", "30")
        assert result.returncode == 2
        assert "--exclude-price-max" in result.stderr

    def test_exclude_price_min_alone_exceeds_default_max_rejected(self):
        # --exclude-price-min 40, effective max=30 (default) -> error
        result = run_cli("--exclude-price-min", "40")
        assert result.returncode == 2
        assert "--exclude-price-max" in result.stderr

    def test_risk_score_threshold_out_of_range_rejected(self):
        result = run_cli("--risk-score-threshold", "150")
        assert result.returncode == 2
        assert "--risk-score-threshold" in result.stderr

    def test_override_alone_implicitly_activates_filter(self):
        # --exclude-price-min 15 alone should implicitly activate filter
        result = run_cli("--exclude-price-min", "15")
        # Should not exit with code 2 (valid override)
        assert result.returncode != 2
        # Verify filter was actually activated via log output
        assert "Entry quality filter" in result.stderr


class TestExperimentCLIFilterValidation:
    """Regression tests for entry quality filter on experiment CLIs."""

    def test_stop_loss_experiment_accepts_filter_flag(self):
        result = run_experiment_cli("backtest.stop_loss_experiment", "--help")
        assert result.returncode == 0
        assert "--entry-quality-filter" in result.stdout

    def test_stop_loss_experiment_rejects_invalid_threshold(self):
        result = run_experiment_cli(
            "backtest.stop_loss_experiment",
            "--exclude-price-min",
            "-5",
        )
        assert result.returncode == 2
        assert "--exclude-price-min" in result.stderr

    def test_trailing_experiment_accepts_filter_flag(self):
        result = run_experiment_cli("backtest.trailing_stop_experiment", "--help")
        assert result.returncode == 0
        assert "--entry-quality-filter" in result.stdout

    def test_trailing_experiment_rejects_invalid_threshold(self):
        result = run_experiment_cli(
            "backtest.trailing_stop_experiment",
            "--data-end-date",
            "2026-02-14",
            "--risk-score-threshold",
            "150",
        )
        assert result.returncode == 2
        assert "--risk-score-threshold" in result.stderr
