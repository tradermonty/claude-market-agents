"""Unit tests for backtest.report_generator.ReportGenerator."""

import csv
from pathlib import Path

from backtest.metrics_calculator import MetricsCalculator
from backtest.report_generator import ReportGenerator
from backtest.trade_simulator import SkippedTrade, TradeResult


def _make_trades():
    """Return a list with one winning and one losing TradeResult."""
    win = TradeResult(
        ticker="AAPL",
        grade="A",
        grade_source="html",
        score=85.0,
        report_date="2025-10-15",
        entry_date="2025-10-15",
        entry_price=150.0,
        exit_date="2025-11-15",
        exit_price=165.0,
        shares=66,
        invested=9900.0,
        pnl=990.0,
        return_pct=10.0,
        holding_days=31,
        exit_reason="max_holding",
        gap_size=5.0,
        company_name="Apple Inc.",
    )
    loss = TradeResult(
        ticker="MSFT",
        grade="B",
        grade_source="html",
        score=72.0,
        report_date="2025-10-16",
        entry_date="2025-10-16",
        entry_price=300.0,
        exit_date="2025-11-16",
        exit_price=270.0,
        shares=33,
        invested=9900.0,
        pnl=-990.0,
        return_pct=-10.0,
        holding_days=31,
        exit_reason="stop_loss",
        gap_size=3.0,
        company_name="Microsoft Corp.",
    )
    return [win, loss]


def _make_skipped():
    """Return a list with one SkippedTrade."""
    return [
        SkippedTrade(
            ticker="GOOG",
            report_date="2025-10-17",
            grade="C",
            score=60.0,
            skip_reason="no_price_data",
        )
    ]


class TestReportGenerator:
    """Tests for ReportGenerator.generate()."""

    def test_generate_creates_files(self, tmp_path: Path):
        trades = _make_trades()
        skipped = _make_skipped()
        metrics = MetricsCalculator().calculate(trades, skipped)

        ReportGenerator().generate(metrics, trades, skipped, str(tmp_path))

        assert (tmp_path / "earnings_trade_backtest_result.html").exists()
        assert (tmp_path / "earnings_trade_backtest_trades.csv").exists()
        assert (tmp_path / "earnings_trade_backtest_skipped.csv").exists()

    def test_trades_csv_content(self, tmp_path: Path):
        trades = _make_trades()
        skipped = _make_skipped()
        metrics = MetricsCalculator().calculate(trades, skipped)

        ReportGenerator().generate(metrics, trades, skipped, str(tmp_path))

        csv_path = tmp_path / "earnings_trade_backtest_trades.csv"
        with open(csv_path, newline="") as f:
            reader = csv.reader(f)
            rows = list(reader)

        header = rows[0]
        assert "ticker" in header
        assert "pnl" in header
        assert "return_pct" in header
        # Header + 2 data rows
        assert len(rows) == 3

    def test_html_contains_sections(self, tmp_path: Path):
        trades = _make_trades()
        skipped = _make_skipped()
        metrics = MetricsCalculator().calculate(trades, skipped)

        ReportGenerator().generate(metrics, trades, skipped, str(tmp_path))

        html = (tmp_path / "earnings_trade_backtest_result.html").read_text()
        assert "Earnings Trade Backtest Results" in html
        assert "Grade Performance" in html
        assert "Score vs Return" in html
        assert "All Trades" in html

    def test_generate_empty_trades_only_html(self, tmp_path: Path):
        trades = []
        skipped = []
        metrics = MetricsCalculator().calculate(trades, skipped)

        ReportGenerator().generate(metrics, trades, skipped, str(tmp_path))

        assert (tmp_path / "earnings_trade_backtest_result.html").exists()
        assert not (tmp_path / "earnings_trade_backtest_trades.csv").exists()
        assert not (tmp_path / "earnings_trade_backtest_skipped.csv").exists()


class TestFilterConfigHtml:
    """Tests for _filter_config_html() VIX display."""

    def test_filter_config_html_vix_on(self):
        gen = ReportGenerator()
        cfg = {"vix_filter": True, "vix_threshold": 20.0}
        html = gen._filter_config_html(cfg)
        assert "VIX Filter: ON (VIX > 20.0)" in html

    def test_filter_config_html_vix_off(self):
        gen = ReportGenerator()
        cfg = {"vix_filter": False}
        html = gen._filter_config_html(cfg)
        assert "VIX" not in html

    def test_filter_config_html_vix_custom_threshold(self):
        gen = ReportGenerator()
        cfg = {"vix_filter": True, "vix_threshold": 25.0}
        html = gen._filter_config_html(cfg)
        assert "VIX Filter: ON (VIX > 25.0)" in html
