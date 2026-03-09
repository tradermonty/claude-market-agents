#!/usr/bin/env python3
"""Unit tests for stop_loss_experiment module."""

import sys
from argparse import Namespace
from unittest.mock import MagicMock, patch

from backtest.html_parser import TradeCandidate
from backtest.metrics_calculator import MetricsCalculator
from backtest.stop_loss_experiment import (
    STOP_MODES,
    main,
    parse_args,
    print_comparison,
    run_experiment,
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
        "max_holding": 90,
        "entry_mode": "report_open",
    }
    defaults.update(overrides)
    return Namespace(**defaults)


class TestParseArgs:
    def test_parse_args_defaults(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["experiment"])
        args = parse_args()
        assert args.position_size == 10000
        assert args.stop_loss == 10.0
        assert args.max_holding == 90
        assert args.min_grade == "D"
        assert args.entry_mode == "report_open"


class TestRunExperiment:
    def test_run_experiment_returns_all_modes(self):
        candidates = [make_candidate(), make_candidate(ticker="FOO", grade="B")]
        args = make_args()

        real_calculator = MetricsCalculator()
        empty_metrics = real_calculator.calculate([], [], position_size=10000)

        mock_simulator_cls = MagicMock()
        mock_simulator_instance = MagicMock()
        mock_simulator_instance.simulate_all.return_value = ([], [])
        mock_simulator_cls.return_value = mock_simulator_instance

        mock_calculator_cls = MagicMock()
        mock_calculator_instance = MagicMock()
        mock_calculator_instance.calculate.return_value = empty_metrics
        mock_calculator_cls.return_value = mock_calculator_instance

        with (
            patch("backtest.stop_loss_experiment.TradeSimulator", mock_simulator_cls),
            patch("backtest.stop_loss_experiment.MetricsCalculator", mock_calculator_cls),
        ):
            results = run_experiment(candidates, {}, args)

        assert set(results.keys()) == set(STOP_MODES)
        for mode in STOP_MODES:
            assert "trades" in results[mode]
            assert "win_rate" in results[mode]
            assert "total_pnl" in results[mode]
            assert "profit_factor" in results[mode]
            assert "trade_sharpe" in results[mode]
            assert "stop_rate" in results[mode]
            assert "avg_return" in results[mode]
            assert "max_drawdown" in results[mode]
            assert "grade_stops" in results[mode]


class TestPrintComparison:
    def test_print_comparison_output(self, capsys):
        mode_data = {
            "trades": 10,
            "win_rate": 50.0,
            "total_pnl": 1000,
            "profit_factor": 1.5,
            "trade_sharpe": 0.8,
            "stop_rate": 20.0,
            "avg_return": 5.0,
            "max_drawdown": -500,
            "grade_stops": {
                "A": {
                    "count": 5,
                    "stop_rate": 10.0,
                    "avg_return": 8.0,
                    "win_rate": 60.0,
                },
            },
        }
        results = {mode: dict(mode_data) for mode in STOP_MODES}

        print_comparison(results)
        captured = capsys.readouterr()

        assert "STOP LOSS MODE COMPARISON" in captured.out
        assert "WIN RATE BY GRADE" in captured.out
        assert "AVG RETURN BY GRADE" in captured.out
        for mode in STOP_MODES:
            assert mode in captured.out


class TestMain:
    def test_main_with_mocks(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["experiment", "--reports-dir", "reports/"])

        candidates = [make_candidate()]

        real_calculator = MetricsCalculator()
        empty_metrics = real_calculator.calculate([], [], position_size=10000)

        mock_parser_cls = MagicMock()
        mock_parser_cls.return_value.parse_all_reports.return_value = candidates

        mock_fetcher_cls = MagicMock()
        mock_fetcher_cls.return_value.bulk_fetch.return_value = {}

        mock_simulator_cls = MagicMock()
        mock_simulator_cls.return_value.simulate_all.return_value = ([], [])

        mock_calculator_cls = MagicMock()
        mock_calculator_cls.return_value.calculate.return_value = empty_metrics

        mock_aggregate = MagicMock(return_value={})

        with (
            patch("backtest.stop_loss_experiment.EarningsReportParser", mock_parser_cls),
            patch("backtest.stop_loss_experiment.PriceFetcher", mock_fetcher_cls),
            patch("backtest.stop_loss_experiment.TradeSimulator", mock_simulator_cls),
            patch("backtest.stop_loss_experiment.MetricsCalculator", mock_calculator_cls),
            patch("backtest.stop_loss_experiment.aggregate_ticker_periods", mock_aggregate),
        ):
            main()

        mock_parser_cls.return_value.parse_all_reports.assert_called_once_with("reports/")
        mock_fetcher_cls.return_value.bulk_fetch.assert_called_once()
        assert mock_simulator_cls.return_value.simulate_all.call_count == len(STOP_MODES)
