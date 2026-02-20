#!/usr/bin/env python3
"""Unit tests for trailing stop sensitivity experiment."""

import csv
from argparse import Namespace
from datetime import datetime, timedelta

from backtest.html_parser import TradeCandidate
from backtest.price_fetcher import PriceBar
from backtest.trailing_stop_experiment import (
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


def make_args(**overrides):
    defaults = {
        "position_size": 10000,
        "stop_loss": 10.0,
        "slippage": 0.5,
        "stop_mode": "intraday",
        "entry_mode": "next_day_open",
        "data_end_date": "2026-02-14",
    }
    defaults.update(overrides)
    return Namespace(**defaults)


class TestBuildParameterGrid:
    """Test grid generation logic."""

    def test_ema_only(self):
        grid = build_parameter_grid()
        assert len(grid) == 9  # 3 periods x 3 transitions
        for c in grid:
            assert c.trailing_stop == "weekly_ema"
            assert c.max_holding_days is None

    def test_ema_plus_baseline(self):
        grid = build_parameter_grid(include_baseline=True)
        assert len(grid) == 10  # 9 EMA + 1 baseline
        baseline = [c for c in grid if c.trailing_stop is None]
        assert len(baseline) == 1
        assert baseline[0].label == "baseline"
        assert baseline[0].max_holding_days == 90

    def test_ema_plus_nweek(self):
        grid = build_parameter_grid(include_nweek=True)
        assert len(grid) == 18  # 9 EMA + 9 nweek

    def test_full_grid(self):
        grid = build_parameter_grid(include_baseline=True, include_nweek=True)
        assert len(grid) == 19  # 1 baseline + 9 EMA + 9 nweek

    def test_labels_unique(self):
        grid = build_parameter_grid(include_baseline=True, include_nweek=True)
        labels = [c.label for c in grid]
        assert len(labels) == len(set(labels)), f"Duplicate labels: {labels}"

    def test_ema_periods(self):
        grid = build_parameter_grid()
        periods = {c.trailing_ema_period for c in grid}
        assert periods == {5, 10, 20}

    def test_transition_weeks(self):
        grid = build_parameter_grid()
        transitions = {c.trailing_transition_weeks for c in grid}
        assert transitions == {2, 3, 5}

    def test_nweek_periods(self):
        grid = build_parameter_grid(include_nweek=True)
        nweek_configs = [c for c in grid if c.trailing_stop == "weekly_nweek_low"]
        periods = {c.trailing_nweek_period for c in nweek_configs}
        assert periods == {2, 4, 8}

    def test_keep_max_holding(self):
        grid = build_parameter_grid(keep_max_holding=True)
        for c in grid:
            assert c.max_holding_days == 90

    def test_custom_max_holding(self):
        grid = build_parameter_grid(
            include_baseline=True, keep_max_holding=True, max_holding_days=60
        )
        for c in grid:
            assert c.max_holding_days == 60


class TestRunSingle:
    """Test running a single experiment config."""

    def _make_price_data(self, days=200):
        """Generate synthetic price data for testing."""
        bars = []
        base = datetime(2025, 9, 1)
        price = 100.0
        for i in range(days):
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            bars.append(make_bar(d, price, price + 5, price - 2, price + 1))
            price += 0.5  # Steady uptrend
        return {"TEST": bars}

    def test_baseline_result_completeness(self):
        config = ExperimentConfig(
            label="baseline",
            trailing_stop=None,
            max_holding_days=90,
        )
        candidates = [make_candidate()]
        price_data = self._make_price_data()
        args = make_args()

        result = run_single(config, candidates, price_data, args)

        assert isinstance(result, ExperimentResult)
        assert result.config is config
        assert result.trades >= 0
        assert 0.0 <= result.win_rate <= 100.0
        assert isinstance(result.total_pnl, float)
        assert isinstance(result.profit_factor, float)
        assert isinstance(result.trade_sharpe, float)
        assert isinstance(result.stop_loss_rate, float)
        assert isinstance(result.trend_break_rate, float)
        assert isinstance(result.protective_exit_rate, float)
        assert isinstance(result.median_holding_days, float)
        assert isinstance(result.peak_positions, int)
        assert isinstance(result.capital_required, (int, float))
        assert isinstance(result.max_drawdown, (int, float))

    def test_ema_result(self):
        config = ExperimentConfig(
            label="ema_p10_t3",
            trailing_stop="weekly_ema",
            trailing_ema_period=10,
            trailing_transition_weeks=3,
        )
        candidates = [make_candidate()]
        price_data = self._make_price_data()
        args = make_args()

        result = run_single(config, candidates, price_data, args)
        assert isinstance(result, ExperimentResult)
        assert result.trades > 0

    def test_no_candidates(self):
        config = ExperimentConfig(
            label="baseline",
            trailing_stop=None,
            max_holding_days=90,
        )
        args = make_args()
        result = run_single(config, [], {}, args)
        assert result.trades == 0
        assert result.total_pnl == 0


class TestWriteResultsCsv:
    """Test CSV output."""

    def test_csv_output(self, tmp_path):
        results = [
            ExperimentResult(
                config=ExperimentConfig(label="baseline", trailing_stop=None, max_holding_days=90),
                trades=100,
                win_rate=55.0,
                avg_return=2.5,
                total_pnl=10000.0,
                profit_factor=1.5,
                trade_sharpe=0.3,
                stop_loss_rate=20.0,
                trend_break_rate=0.0,
                protective_exit_rate=20.0,
                median_holding_days=45.0,
                peak_positions=50,
                capital_required=500000.0,
                max_drawdown=5000.0,
            ),
            ExperimentResult(
                config=ExperimentConfig(
                    label="ema_p10_t3",
                    trailing_stop="weekly_ema",
                    trailing_ema_period=10,
                    trailing_transition_weeks=3,
                ),
                trades=100,
                win_rate=58.0,
                avg_return=3.0,
                total_pnl=15000.0,
                profit_factor=1.8,
                trade_sharpe=0.35,
                stop_loss_rate=18.0,
                trend_break_rate=10.0,
                protective_exit_rate=28.0,
                median_holding_days=35.0,
                peak_positions=55,
                capital_required=550000.0,
                max_drawdown=4000.0,
            ),
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
        assert rows[2][0] == "ema_p10_t3"

    def test_csv_headers(self, tmp_path):
        results = [
            ExperimentResult(
                config=ExperimentConfig(
                    label="nwl_p4_t3",
                    trailing_stop="weekly_nweek_low",
                    trailing_nweek_period=4,
                    trailing_transition_weeks=3,
                ),
                trades=100,
                win_rate=55.0,
                avg_return=2.0,
                total_pnl=8000.0,
                profit_factor=1.4,
                trade_sharpe=0.25,
                stop_loss_rate=22.0,
                trend_break_rate=8.0,
                protective_exit_rate=30.0,
                median_holding_days=40.0,
                peak_positions=48,
                capital_required=480000.0,
                max_drawdown=6000.0,
            ),
        ]
        csv_path = tmp_path / "results.csv"
        write_results_csv(results, csv_path)

        with open(csv_path) as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert row["label"] == "nwl_p4_t3"
        assert row["mode"] == "weekly_nweek_low"
        assert row["period"] == "4"

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
                config=ExperimentConfig(label="baseline", trailing_stop=None, max_holding_days=90),
                trades=100,
                win_rate=55.0,
                avg_return=2.5,
                total_pnl=10000.0,
                profit_factor=1.5,
                trade_sharpe=0.3,
                stop_loss_rate=20.0,
                trend_break_rate=0.0,
                protective_exit_rate=20.0,
                median_holding_days=45.0,
                peak_positions=50,
                capital_required=500000.0,
                max_drawdown=5000.0,
            ),
            ExperimentResult(
                config=ExperimentConfig(
                    label="ema_p10_t3",
                    trailing_stop="weekly_ema",
                    trailing_ema_period=10,
                    trailing_transition_weeks=3,
                ),
                trades=100,
                win_rate=58.0,
                avg_return=3.0,
                total_pnl=15000.0,
                profit_factor=1.8,
                trade_sharpe=0.35,
                stop_loss_rate=18.0,
                trend_break_rate=10.0,
                protective_exit_rate=28.0,
                median_holding_days=35.0,
                peak_positions=55,
                capital_required=550000.0,
                max_drawdown=4000.0,
            ),
        ]
        print_comparison_table(results, sort_by="total_pnl")
        captured = capsys.readouterr()
        assert "TRAILING STOP SENSITIVITY" in captured.out
        assert "baseline" in captured.out
        assert "ema_p10_t3" in captured.out

    def test_invalid_sort_key(self, capsys):
        results = [
            ExperimentResult(
                config=ExperimentConfig(label="test", trailing_stop=None, max_holding_days=90),
                trades=10,
                win_rate=50.0,
                avg_return=1.0,
                total_pnl=1000.0,
                profit_factor=1.0,
                trade_sharpe=0.1,
                stop_loss_rate=10.0,
                trend_break_rate=0.0,
                protective_exit_rate=10.0,
                median_holding_days=30.0,
                peak_positions=5,
                capital_required=50000.0,
                max_drawdown=500.0,
            ),
        ]
        # Should not crash with invalid sort key
        print_comparison_table(results, sort_by="invalid_key")
        captured = capsys.readouterr()
        assert "test" in captured.out
