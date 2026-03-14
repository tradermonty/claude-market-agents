#!/usr/bin/env python3
"""Unit tests for backtest.main module."""

import argparse
from unittest.mock import MagicMock, patch

import pytest

from backtest.html_parser import TradeCandidate
from backtest.main import main, parse_args, validate_args
from backtest.trade_simulator import SkippedTrade, TradeResult


def _make_valid_namespace(**overrides):
    """Build an argparse.Namespace with all required fields set to valid defaults."""
    defaults = {
        "reports_dir": "reports/",
        "output_dir": "reports/backtest/",
        "position_size": 10000,
        "stop_loss": 10.0,
        "slippage": 0.5,
        "max_holding": 90,
        "min_grade": "D",
        "min_score": None,
        "max_score": None,
        "min_gap": None,
        "max_gap": None,
        "stop_mode": "intraday",
        "daily_entry_limit": None,
        "entry_mode": "report_open",
        "trailing_stop": None,
        "trailing_ema_period": 10,
        "trailing_nweek_period": 4,
        "trailing_transition_weeks": 3,
        "disable_max_holding": False,
        "data_end_date": None,
        "fmp_api_key": None,
        "parse_only": False,
        "walk_forward": False,
        "wf_folds": 3,
        "charts": False,
        "max_positions": None,
        "no_rotation": False,
        "verbose": False,
        "entry_quality_filter": False,
        "exclude_price_min": None,
        "exclude_price_max": None,
        "risk_gap_threshold": None,
        "risk_score_threshold": None,
        "vix_filter": False,
        "vix_threshold": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _make_candidate(ticker="AAPL", report_date="2025-01-15", grade="A"):
    """Create a TradeCandidate for testing."""
    return TradeCandidate(
        ticker=ticker,
        report_date=report_date,
        grade=grade,
        grade_source="html",
        score=85.0,
        price=150.0,
        gap_size=5.0,
        company_name=f"{ticker} Inc",
    )


def _make_trade_result(ticker="AAPL", pnl=500.0, return_pct=5.0):
    """Create a TradeResult for testing."""
    return TradeResult(
        ticker=ticker,
        grade="A",
        grade_source="html",
        score=85.0,
        report_date="2025-01-15",
        entry_date="2025-01-15",
        entry_price=150.0,
        exit_date="2025-02-15",
        exit_price=150.0 + pnl / 66,  # approximate
        shares=66,
        invested=9900.0,
        pnl=pnl,
        return_pct=return_pct,
        holding_days=31,
        exit_reason="max_holding",
        gap_size=5.0,
        company_name=f"{ticker} Inc",
    )


# ---------- Test 1: parse_args defaults ----------


class TestParseArgs:
    def test_parse_args_defaults(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["main"])
        args = parse_args()
        assert args.position_size == 10000
        assert args.stop_loss == 10.0
        assert args.slippage == 0.5
        assert args.max_holding == 90
        assert args.min_grade == "D"
        assert args.min_score is None
        assert args.max_score is None
        assert args.min_gap is None
        assert args.max_gap is None
        assert args.stop_mode == "intraday"
        assert args.daily_entry_limit is None
        assert args.entry_mode == "report_open"
        assert args.trailing_stop is None
        assert args.trailing_ema_period == 10
        assert args.trailing_nweek_period == 4
        assert args.trailing_transition_weeks == 3
        assert args.disable_max_holding is False
        assert args.data_end_date is None
        assert args.fmp_api_key is None
        assert args.parse_only is False
        assert args.walk_forward is False
        assert args.wf_folds == 3
        assert args.charts is False
        assert args.max_positions is None
        assert args.no_rotation is False
        assert args.verbose is False
        assert args.vix_filter is False
        assert args.vix_threshold is None
        assert args.reports_dir == "reports/"
        assert args.output_dir == "reports/backtest/"


# ---------- Tests 2-5: validate_args ----------


class TestValidateArgs:
    def test_validate_args_valid(self):
        args = _make_valid_namespace()
        # Should not raise
        validate_args(args)

    def test_validate_args_stop_loss_too_high(self):
        args = _make_valid_namespace(stop_loss=150)
        with pytest.raises(SystemExit) as exc_info:
            validate_args(args)
        assert exc_info.value.code == 2

    def test_validate_args_trailing_without_disable(self):
        args = _make_valid_namespace(disable_max_holding=True, trailing_stop=None)
        with pytest.raises(SystemExit) as exc_info:
            validate_args(args)
        assert exc_info.value.code == 2

    def test_validate_args_score_filter_inverted(self):
        args = _make_valid_namespace(min_score=80, max_score=50)
        with pytest.raises(SystemExit) as exc_info:
            validate_args(args)
        assert exc_info.value.code == 2


# ---------- Tests 6-8: main() ----------


class TestMain:
    def test_main_no_candidates_exits(self, monkeypatch, tmp_path):
        """Patch parser to return empty list, run with --parse-only."""
        monkeypatch.setattr(
            "sys.argv",
            ["main", "--parse-only", "--output-dir", str(tmp_path)],
        )
        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_all_reports.return_value = []

        with patch("backtest.main.EarningsReportParser", return_value=mock_parser_instance):
            # parse-only with 0 candidates writes empty CSV and returns normally
            main()

        # Verify CSV was created (even if empty besides header)
        csv_path = tmp_path / "parsed_candidates.csv"
        assert csv_path.exists()

    def test_main_parse_only(self, monkeypatch, tmp_path):
        """Patch parser to return 2 candidates, run --parse-only, verify CSV output."""
        monkeypatch.setattr(
            "sys.argv",
            ["main", "--parse-only", "--output-dir", str(tmp_path)],
        )

        candidates = [
            _make_candidate("AAPL", "2025-01-15", "A"),
            _make_candidate("MSFT", "2025-01-16", "B"),
        ]
        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_all_reports.return_value = candidates

        with patch("backtest.main.EarningsReportParser", return_value=mock_parser_instance):
            main()

        csv_path = tmp_path / "parsed_candidates.csv"
        assert csv_path.exists()
        lines = csv_path.read_text().strip().split("\n")
        # Header + 2 data rows
        assert len(lines) == 3

    def test_main_full_pipeline_mocked(self, monkeypatch, tmp_path):
        """Mock all pipeline stages and verify main() runs end-to-end."""
        monkeypatch.setattr(
            "sys.argv",
            ["main", "--reports-dir", "reports/", "--output-dir", str(tmp_path)],
        )

        candidates = [
            _make_candidate("AAPL", "2025-01-15", "A"),
            _make_candidate("GOOG", "2025-01-16", "B"),
        ]
        trades = [
            _make_trade_result("AAPL", pnl=500.0, return_pct=5.0),
            _make_trade_result("GOOG", pnl=-200.0, return_pct=-2.0),
        ]
        skipped = [
            SkippedTrade(
                ticker="NFLX",
                report_date="2025-01-17",
                grade="C",
                score=60.0,
                skip_reason="no_price_data",
            ),
        ]

        # Calculate real metrics so downstream code works
        from backtest.metrics_calculator import MetricsCalculator

        real_metrics = MetricsCalculator().calculate(trades, skipped, position_size=10000)

        # Mock EarningsReportParser
        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_all_reports.return_value = candidates

        # Mock PriceFetcher
        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.bulk_fetch.return_value = {
            "AAPL": [MagicMock(date="2025-03-01")],
            "GOOG": [MagicMock(date="2025-03-01")],
        }

        # Mock TradeSimulator
        mock_simulator_instance = MagicMock()
        mock_simulator_instance.simulate_all.return_value = (trades, skipped)

        # Mock MetricsCalculator
        mock_calculator_instance = MagicMock()
        mock_calculator_instance.calculate.return_value = real_metrics

        # Mock ReportGenerator
        mock_generator_instance = MagicMock()

        with (
            patch(
                "backtest.main.EarningsReportParser",
                return_value=mock_parser_instance,
            ),
            patch(
                "backtest.main.PriceFetcher",
                return_value=mock_fetcher_instance,
            ),
            patch(
                "backtest.main.aggregate_ticker_periods",
                return_value={
                    "AAPL": ("2025-01-01", "2025-03-01"),
                    "GOOG": ("2025-01-01", "2025-03-01"),
                },
            ),
            patch(
                "backtest.main.TradeSimulator",
                return_value=mock_simulator_instance,
            ),
            patch(
                "backtest.main.MetricsCalculator",
                return_value=mock_calculator_instance,
            ),
            patch(
                "backtest.main.ReportGenerator",
                return_value=mock_generator_instance,
            ),
            patch("backtest.main.write_manifest") as mock_manifest,
        ):
            main()

        # Verify pipeline stages were called
        mock_parser_instance.parse_all_reports.assert_called_once()
        mock_fetcher_instance.bulk_fetch.assert_called_once()
        mock_simulator_instance.simulate_all.assert_called_once()
        mock_calculator_instance.calculate.assert_called_once()
        mock_generator_instance.generate.assert_called_once()
        mock_manifest.assert_called_once()

    def test_parse_only_with_vix_filter_disables_vix(self, monkeypatch, tmp_path):
        """--parse-only + --vix-filter: warning logged, PriceFetcher not called for VIX."""
        monkeypatch.setattr(
            "sys.argv",
            [
                "main",
                "--parse-only",
                "--vix-filter",
                "--output-dir",
                str(tmp_path),
            ],
        )

        candidates = [_make_candidate("AAPL", "2025-01-15", "A")]
        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_all_reports.return_value = candidates

        with (
            patch(
                "backtest.main.EarningsReportParser",
                return_value=mock_parser_instance,
            ),
            patch("backtest.main.PriceFetcher") as mock_pf_cls,
        ):
            main()

        # PriceFetcher should never be instantiated in parse-only mode
        mock_pf_cls.assert_not_called()
        # CSV should still be written
        assert (tmp_path / "parsed_candidates.csv").exists()

    def test_all_candidates_filtered_generates_empty_report(self, monkeypatch, tmp_path):
        """When all candidates are removed by VIX filter, empty report is generated without crash."""
        monkeypatch.setattr(
            "sys.argv",
            [
                "main",
                "--vix-filter",
                "--vix-threshold",
                "15",
                "--output-dir",
                str(tmp_path),
            ],
        )

        candidates = [
            _make_candidate("AAPL", "2025-01-15", "A"),
            _make_candidate("MSFT", "2025-01-16", "B"),
        ]
        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_all_reports.return_value = candidates

        # VIX fetcher returns high VIX for all dates → all candidates filtered
        from backtest.price_fetcher import PriceBar

        vix_bars = [
            PriceBar(
                date="2025-01-10",
                open=20.0,
                high=22.0,
                low=19.0,
                close=21.0,
                adj_close=21.0,
                volume=100000,
            ),
            PriceBar(
                date="2025-01-15",
                open=20.0,
                high=22.0,
                low=19.0,
                close=21.0,
                adj_close=21.0,
                volume=100000,
            ),
            PriceBar(
                date="2025-01-16",
                open=22.0,
                high=24.0,
                low=21.0,
                close=23.0,
                adj_close=23.0,
                volume=120000,
            ),
        ]
        mock_vix_fetcher = MagicMock()
        mock_vix_fetcher.fetch_prices.return_value = vix_bars

        # Calculate real metrics for empty trades
        from backtest.metrics_calculator import MetricsCalculator

        real_metrics = MetricsCalculator().calculate([], [], position_size=10000)

        mock_calculator = MagicMock()
        mock_calculator.calculate.return_value = real_metrics
        mock_generator = MagicMock()

        with (
            patch(
                "backtest.main.EarningsReportParser",
                return_value=mock_parser_instance,
            ),
            patch(
                "backtest.main.PriceFetcher",
                return_value=mock_vix_fetcher,
            ),
            patch(
                "backtest.main.MetricsCalculator",
                return_value=mock_calculator,
            ),
            patch(
                "backtest.main.ReportGenerator",
                return_value=mock_generator,
            ),
            patch("backtest.main.write_manifest") as mock_manifest,
        ):
            # Should NOT raise or sys.exit
            main()

        # Report should be generated with empty trades
        mock_generator.generate.assert_called_once()
        call_args = mock_generator.generate.call_args
        trades_arg = call_args[0][1]  # trades is 2nd positional arg
        skipped_arg = call_args[0][2]  # skipped is 3rd positional arg
        assert trades_arg == []
        assert len(skipped_arg) == 2  # both candidates were VIX-filtered
        assert all(s.skip_reason.startswith("filter_high_vix") for s in skipped_arg)
        mock_manifest.assert_called_once()
        # filtered_candidates.csv should be written
        assert (tmp_path / "filtered_candidates.csv").exists()
