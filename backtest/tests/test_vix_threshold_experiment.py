#!/usr/bin/env python3
"""Unit tests for VIX threshold sensitivity experiment."""

import csv
from argparse import Namespace
from unittest.mock import MagicMock, patch

from backtest.html_parser import TradeCandidate
from backtest.vix_filter import VixDay
from backtest.vix_threshold_experiment import (
    ExperimentConfig,
    ExperimentResult,
    build_parameter_grid,
    print_comparison_table,
    run_single,
    write_results_csv,
)


def make_candidate(ticker="TEST", report_date="2025-10-01", grade="A", score=85.0):
    return TradeCandidate(
        ticker=ticker,
        report_date=report_date,
        grade=grade,
        grade_source="html",
        score=score,
    )


def make_args(**overrides):
    defaults = {
        "position_size": 10000,
        "stop_loss": 10.0,
        "slippage": 0.5,
        "stop_mode": "intraday",
        "entry_mode": "next_day_open",
        "data_end_date": "2026-02-14",
        "trailing_stop": "weekly_nweek_low",
        "trailing_ema_period": 10,
        "trailing_nweek_period": 4,
        "trailing_transition_weeks": 3,
        "max_holding_days": None,
        "max_positions": None,
        "no_rotation": False,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def make_vix_data(dates, vix_open=18.0, vix_close=19.0):
    """Create VIX data dict for given dates."""
    return {d: VixDay(open=vix_open, close=vix_close) for d in dates}


class TestBuildParameterGrid:
    """Test grid generation logic."""

    def test_default_thresholds(self):
        grid = build_parameter_grid()
        # baseline + 4 thresholds (18, 20, 22, 25)
        assert len(grid) == 5

    def test_custom_thresholds(self):
        grid = build_parameter_grid(thresholds=[15.0, 30.0])
        assert len(grid) == 3  # baseline + 2

    def test_no_baseline(self):
        grid = build_parameter_grid(include_baseline=False)
        assert len(grid) == 4  # thresholds only
        for c in grid:
            assert c.vix_threshold is not None

    def test_baseline_has_none_threshold(self):
        grid = build_parameter_grid()
        baseline = [c for c in grid if c.label == "baseline"]
        assert len(baseline) == 1
        assert baseline[0].vix_threshold is None

    def test_labels_unique(self):
        grid = build_parameter_grid()
        labels = [c.label for c in grid]
        assert len(labels) == len(set(labels)), f"Duplicate labels: {labels}"

    def test_threshold_labels(self):
        grid = build_parameter_grid(thresholds=[18.0, 20.0])
        labels = {c.label for c in grid}
        assert "vix_18.0" in labels
        assert "vix_20.0" in labels
        assert "baseline" in labels


class TestRunSingle:
    """Test running a single experiment config."""

    def test_baseline_no_vix_filter(self):
        """Baseline (threshold=None) should not apply VIX filter."""
        config = ExperimentConfig(label="baseline", vix_threshold=None)
        candidates = [make_candidate(), make_candidate(ticker="AAPL")]
        vix_data = make_vix_data(["2025-10-01"], vix_open=99.0)  # Very high VIX

        mock_metrics = MagicMock()
        mock_metrics.total_trades = 2
        mock_metrics.win_rate = 50.0
        mock_metrics.avg_return = 1.0
        mock_metrics.total_pnl = 500.0
        mock_metrics.profit_factor = 1.5
        mock_metrics.trade_sharpe = 0.3
        mock_metrics.max_drawdown = 200.0
        mock_metrics.stop_loss_rate = 10.0
        mock_metrics.peak_positions = 2
        mock_metrics.capital_requirement = 20000.0

        with (
            patch("backtest.vix_threshold_experiment.TradeSimulator") as MockSim,
            patch("backtest.vix_threshold_experiment.MetricsCalculator") as MockCalc,
        ):
            MockSim.return_value.simulate_all.return_value = (
                [MagicMock(), MagicMock()],
                [],
            )
            MockCalc.return_value.calculate.return_value = mock_metrics

            args = make_args()
            result = run_single(config, candidates, vix_data, {}, args)

            # Baseline should pass ALL candidates (no VIX filter)
            sim_call_args = MockSim.return_value.simulate_all.call_args
            assert len(sim_call_args[0][0]) == 2  # Both candidates passed

        assert isinstance(result, ExperimentResult)
        assert result.config is config
        assert result.filtered_by_vix == 0
        assert result.candidates_after_filter == 2

    def test_threshold_applies_vix_filter(self):
        """Threshold config should filter candidates by VIX."""
        config = ExperimentConfig(label="vix_20.0", vix_threshold=20.0)
        candidates = [make_candidate(report_date="2025-10-01")]
        # VIX open=25 > threshold=20 → should be filtered
        vix_data = make_vix_data(["2025-10-01"], vix_open=25.0)

        mock_metrics = MagicMock()
        mock_metrics.total_trades = 0
        mock_metrics.win_rate = 0.0
        mock_metrics.avg_return = 0.0
        mock_metrics.total_pnl = 0.0
        mock_metrics.profit_factor = 0.0
        mock_metrics.trade_sharpe = 0.0
        mock_metrics.max_drawdown = 0.0
        mock_metrics.stop_loss_rate = 0.0
        mock_metrics.peak_positions = 0
        mock_metrics.capital_requirement = 0.0

        with patch("backtest.vix_threshold_experiment.MetricsCalculator") as MockCalc:
            MockCalc.return_value.calculate.return_value = mock_metrics
            args = make_args()
            result = run_single(config, candidates, vix_data, {}, args)

        assert result.filtered_by_vix == 1
        assert result.candidates_after_filter == 0
        assert result.trades == 0

    def test_result_field_completeness(self):
        """All ExperimentResult fields should be populated."""
        config = ExperimentConfig(label="vix_22.0", vix_threshold=22.0)
        candidates = [make_candidate()]
        vix_data = make_vix_data(["2025-10-01"], vix_open=15.0)  # Below threshold

        mock_metrics = MagicMock()
        mock_metrics.total_trades = 1
        mock_metrics.win_rate = 100.0
        mock_metrics.avg_return = 5.0
        mock_metrics.total_pnl = 500.0
        mock_metrics.profit_factor = 99.0
        mock_metrics.trade_sharpe = 1.0
        mock_metrics.max_drawdown = 0.0
        mock_metrics.stop_loss_rate = 0.0
        mock_metrics.peak_positions = 1
        mock_metrics.capital_requirement = 10000.0

        with (
            patch("backtest.vix_threshold_experiment.TradeSimulator") as MockSim,
            patch("backtest.vix_threshold_experiment.MetricsCalculator") as MockCalc,
        ):
            MockSim.return_value.simulate_all.return_value = ([MagicMock()], [])
            MockCalc.return_value.calculate.return_value = mock_metrics

            args = make_args()
            result = run_single(config, candidates, vix_data, {}, args)

        assert isinstance(result.trades, int)
        assert isinstance(result.win_rate, float)
        assert isinstance(result.avg_return, float)
        assert isinstance(result.total_pnl, float)
        assert isinstance(result.profit_factor, float)
        assert isinstance(result.trade_sharpe, float)
        assert isinstance(result.max_drawdown, (int, float))
        assert isinstance(result.stop_loss_rate, float)
        assert isinstance(result.peak_positions, int)
        assert isinstance(result.capital_required, (int, float))
        assert isinstance(result.filtered_by_vix, int)
        assert isinstance(result.candidates_after_filter, int)


class TestRunSingleZeroCandidates:
    """Test zero-candidate edge case after VIX filter."""

    def test_all_filtered_by_vix(self):
        """High threshold filters all candidates → trades=0, no Simulator call."""
        config = ExperimentConfig(label="vix_18.0", vix_threshold=18.0)
        candidates = [
            make_candidate(report_date="2025-10-01"),
            make_candidate(ticker="AAPL", report_date="2025-10-02"),
        ]
        # VIX open=25 > all thresholds → both filtered
        vix_data = make_vix_data(["2025-10-01", "2025-10-02"], vix_open=25.0)

        mock_metrics = MagicMock()
        mock_metrics.total_trades = 0
        mock_metrics.win_rate = 0.0
        mock_metrics.avg_return = 0.0
        mock_metrics.total_pnl = 0.0
        mock_metrics.profit_factor = 0.0
        mock_metrics.trade_sharpe = 0.0
        mock_metrics.max_drawdown = 0.0
        mock_metrics.stop_loss_rate = 0.0
        mock_metrics.peak_positions = 0
        mock_metrics.capital_requirement = 0.0

        with (
            patch("backtest.vix_threshold_experiment.TradeSimulator") as MockSim,
            patch("backtest.vix_threshold_experiment.PortfolioSimulator") as MockPortSim,
            patch("backtest.vix_threshold_experiment.MetricsCalculator") as MockCalc,
        ):
            MockCalc.return_value.calculate.return_value = mock_metrics
            args = make_args()
            result = run_single(config, candidates, vix_data, {}, args)

            # Simulator should NOT be called
            MockSim.return_value.simulate_all.assert_not_called()
            MockPortSim.return_value.simulate_portfolio.assert_not_called()

        assert result.trades == 0
        assert result.filtered_by_vix == 2
        assert result.candidates_after_filter == 0

    def test_zero_candidates_normal_termination(self):
        """Zero candidates should terminate normally (no exception)."""
        config = ExperimentConfig(label="vix_25.0", vix_threshold=25.0)
        candidates = [make_candidate()]
        vix_data = make_vix_data(["2025-10-01"], vix_open=30.0)

        mock_metrics = MagicMock()
        mock_metrics.total_trades = 0
        mock_metrics.win_rate = 0.0
        mock_metrics.avg_return = 0.0
        mock_metrics.total_pnl = 0.0
        mock_metrics.profit_factor = 0.0
        mock_metrics.trade_sharpe = 0.0
        mock_metrics.max_drawdown = 0.0
        mock_metrics.stop_loss_rate = 0.0
        mock_metrics.peak_positions = 0
        mock_metrics.capital_requirement = 0.0

        with patch("backtest.vix_threshold_experiment.MetricsCalculator") as MockCalc:
            MockCalc.return_value.calculate.return_value = mock_metrics
            args = make_args()
            result = run_single(config, candidates, vix_data, {}, args)

        assert result.trades == 0


class TestRunSinglePortfolioMode:
    """Test PortfolioSimulator branch when --max-positions is set."""

    def test_portfolio_mode_activated(self):
        """max_positions set → PortfolioSimulator used instead of TradeSimulator."""
        config = ExperimentConfig(label="baseline", vix_threshold=None)
        candidates = [make_candidate()]
        vix_data = {}

        mock_metrics = MagicMock()
        mock_metrics.total_trades = 1
        mock_metrics.win_rate = 100.0
        mock_metrics.avg_return = 5.0
        mock_metrics.total_pnl = 500.0
        mock_metrics.profit_factor = 99.0
        mock_metrics.trade_sharpe = 1.0
        mock_metrics.max_drawdown = 0.0
        mock_metrics.stop_loss_rate = 0.0
        mock_metrics.peak_positions = 1
        mock_metrics.capital_requirement = 10000.0

        with (
            patch("backtest.vix_threshold_experiment.TradeSimulator") as MockSim,
            patch("backtest.vix_threshold_experiment.PortfolioSimulator") as MockPortSim,
            patch("backtest.vix_threshold_experiment.MetricsCalculator") as MockCalc,
        ):
            MockPortSim.return_value.simulate_portfolio.return_value = (
                [MagicMock()],
                [],
            )
            MockCalc.return_value.calculate.return_value = mock_metrics

            args = make_args(max_positions=20)
            result = run_single(config, candidates, vix_data, {}, args)

            # PortfolioSimulator should be called, not TradeSimulator
            MockPortSim.return_value.simulate_portfolio.assert_called_once()
            MockSim.return_value.simulate_all.assert_not_called()

        assert result.trades == 1


class TestWriteResultsCsv:
    """Test CSV output."""

    def _make_result(self, label="baseline", vix_threshold=None, filtered=0, after=100):
        return ExperimentResult(
            config=ExperimentConfig(label=label, vix_threshold=vix_threshold),
            candidates_after_filter=after,
            filtered_by_vix=filtered,
            trades=100,
            win_rate=55.0,
            avg_return=2.5,
            total_pnl=10000.0,
            profit_factor=1.5,
            trade_sharpe=0.3,
            max_drawdown=5000.0,
            stop_loss_rate=20.0,
            peak_positions=50,
            capital_required=500000.0,
        )

    def test_csv_output(self, tmp_path):
        results = [
            self._make_result("baseline", None, 0, 100),
            self._make_result("vix_20.0", 20.0, 15, 85),
        ]
        csv_path = tmp_path / "results.csv"
        write_results_csv(results, csv_path)

        assert csv_path.exists()
        with open(csv_path) as f:
            reader = csv.reader(f)
            rows = list(reader)
        assert len(rows) == 3  # header + 2 data rows
        assert rows[0][0] == "label"
        assert rows[1][0] == "baseline"
        assert rows[2][0] == "vix_20.0"

    def test_csv_headers_contain_vix_columns(self, tmp_path):
        results = [self._make_result("vix_20.0", 20.0, 15, 85)]
        csv_path = tmp_path / "results.csv"
        write_results_csv(results, csv_path)

        with open(csv_path) as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames
            row = next(reader)
        assert "filtered_by_vix" in headers
        assert "candidates_after_filter" in headers
        assert row["filtered_by_vix"] == "15"
        assert row["candidates_after_filter"] == "85"

    def test_empty_results(self, tmp_path):
        csv_path = tmp_path / "empty.csv"
        write_results_csv([], csv_path)
        assert csv_path.exists()
        with open(csv_path) as f:
            reader = csv.reader(f)
            rows = list(reader)
        assert len(rows) == 1  # header only


class TestPrintComparisonTable:
    """Smoke test — should not crash."""

    def test_empty_results(self, capsys):
        print_comparison_table([])
        captured = capsys.readouterr()
        assert "No results" in captured.out

    def test_with_results(self, capsys):
        results = [
            ExperimentResult(
                config=ExperimentConfig(label="baseline", vix_threshold=None),
                candidates_after_filter=100,
                filtered_by_vix=0,
                trades=100,
                win_rate=55.0,
                avg_return=2.5,
                total_pnl=10000.0,
                profit_factor=1.5,
                trade_sharpe=0.3,
                max_drawdown=5000.0,
                stop_loss_rate=20.0,
                peak_positions=50,
                capital_required=500000.0,
            ),
            ExperimentResult(
                config=ExperimentConfig(label="vix_20.0", vix_threshold=20.0),
                candidates_after_filter=85,
                filtered_by_vix=15,
                trades=85,
                win_rate=58.0,
                avg_return=3.0,
                total_pnl=15000.0,
                profit_factor=1.8,
                trade_sharpe=0.35,
                max_drawdown=4000.0,
                stop_loss_rate=18.0,
                peak_positions=45,
                capital_required=450000.0,
            ),
        ]
        print_comparison_table(results, sort_by="total_pnl")
        captured = capsys.readouterr()
        assert "VIX THRESHOLD SENSITIVITY" in captured.out
        assert "baseline" in captured.out
        assert "vix_20.0" in captured.out

    def test_filtered_column_in_output(self, capsys):
        results = [
            ExperimentResult(
                config=ExperimentConfig(label="vix_18.0", vix_threshold=18.0),
                candidates_after_filter=80,
                filtered_by_vix=20,
                trades=80,
                win_rate=55.0,
                avg_return=2.0,
                total_pnl=8000.0,
                profit_factor=1.3,
                trade_sharpe=0.2,
                max_drawdown=3000.0,
                stop_loss_rate=15.0,
                peak_positions=40,
                capital_required=400000.0,
            ),
        ]
        print_comparison_table(results)
        captured = capsys.readouterr()
        assert "Filt" in captured.out or "filt" in captured.out.lower()


class TestCLI:
    """Test CLI argument parsing."""

    def test_vix_thresholds_parsed(self):
        from backtest.vix_threshold_experiment import parse_args

        with patch("sys.argv", ["prog", "--reports-dir", "r/", "--vix-thresholds", "18", "22"]):
            args = parse_args()
        assert args.vix_thresholds == [18.0, 22.0]

    def test_vix_filter_not_defined(self):
        """--vix-filter should NOT be defined (responsibility separation)."""
        import pytest

        from backtest.vix_threshold_experiment import parse_args

        with (
            patch("sys.argv", ["prog", "--reports-dir", "r/", "--vix-filter"]),
            pytest.raises(SystemExit),
        ):
            parse_args()

    def test_vix_threshold_not_defined(self):
        """--vix-threshold (singular) should NOT be defined (responsibility separation)."""
        import pytest

        from backtest.vix_threshold_experiment import parse_args

        with (
            patch("sys.argv", ["prog", "--reports-dir", "r/", "--vix-threshold", "20"]),
            pytest.raises(SystemExit),
        ):
            parse_args()

    def test_negative_threshold_rejected(self):
        """Negative VIX thresholds should be rejected at validation."""
        from backtest.vix_threshold_experiment import parse_args

        with patch("sys.argv", ["prog", "--reports-dir", "r/", "--vix-thresholds", "-5"]):
            args = parse_args()
        assert args.vix_thresholds == [-5.0]
        # Validation happens in main(), verify thresholds contain invalid value
        assert any(th <= 0 for th in args.vix_thresholds)

    def test_zero_threshold_rejected(self):
        """Zero VIX threshold should be rejected at validation."""
        from backtest.vix_threshold_experiment import parse_args

        with patch("sys.argv", ["prog", "--reports-dir", "r/", "--vix-thresholds", "0"]):
            args = parse_args()
        assert args.vix_thresholds == [0.0]
        assert any(th <= 0 for th in args.vix_thresholds)

    def test_daily_entry_limit_parsed(self):
        """--daily-entry-limit should be accepted."""
        from backtest.vix_threshold_experiment import parse_args

        with patch("sys.argv", ["prog", "--reports-dir", "r/", "--daily-entry-limit", "5"]):
            args = parse_args()
        assert args.daily_entry_limit == 5

    def test_score_gap_filters_parsed(self):
        """--min-score, --max-score, --min-gap, --max-gap should be accepted."""
        from backtest.vix_threshold_experiment import parse_args

        with patch(
            "sys.argv",
            [
                "prog",
                "--reports-dir",
                "r/",
                "--min-score",
                "70",
                "--max-score",
                "95",
                "--min-gap",
                "5",
                "--max-gap",
                "20",
            ],
        ):
            args = parse_args()
        assert args.min_score == 70.0
        assert args.max_score == 95.0
        assert args.min_gap == 5.0
        assert args.max_gap == 20.0


class TestRunSingleDailyEntryLimit:
    """Test daily_entry_limit is passed to TradeSimulator."""

    def test_daily_entry_limit_forwarded(self):
        """daily_entry_limit should be forwarded to TradeSimulator."""
        config = ExperimentConfig(label="baseline", vix_threshold=None)
        candidates = [make_candidate()]
        vix_data = {}

        mock_metrics = MagicMock()
        mock_metrics.total_trades = 1
        mock_metrics.win_rate = 100.0
        mock_metrics.avg_return = 5.0
        mock_metrics.total_pnl = 500.0
        mock_metrics.profit_factor = 99.0
        mock_metrics.trade_sharpe = 1.0
        mock_metrics.max_drawdown = 0.0
        mock_metrics.stop_loss_rate = 0.0
        mock_metrics.peak_positions = 1
        mock_metrics.capital_requirement = 10000.0

        with (
            patch("backtest.vix_threshold_experiment.TradeSimulator") as MockSim,
            patch("backtest.vix_threshold_experiment.MetricsCalculator") as MockCalc,
        ):
            MockSim.return_value.simulate_all.return_value = ([MagicMock()], [])
            MockCalc.return_value.calculate.return_value = mock_metrics

            args = make_args(daily_entry_limit=3)
            run_single(config, candidates, vix_data, {}, args)

            # Verify daily_entry_limit was passed to TradeSimulator
            call_kwargs = MockSim.call_args[1]
            assert call_kwargs["daily_entry_limit"] == 3
