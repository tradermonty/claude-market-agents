#!/usr/bin/env python3
"""Unit tests for the metrics calculator."""

import pytest

from backtest.metrics_calculator import MetricsCalculator
from backtest.trade_simulator import TradeResult


def make_trade(
    ticker="TEST",
    grade="A",
    grade_source="html",
    score=85.0,
    pnl=0.0,
    return_pct=0.0,
    entry_price=100.0,
    exit_price=100.0,
):
    return TradeResult(
        ticker=ticker,
        grade=grade,
        grade_source=grade_source,
        score=score,
        report_date="2025-10-01",
        entry_date="2025-10-02",
        entry_price=entry_price,
        exit_date="2025-10-10",
        exit_price=exit_price,
        shares=100,
        invested=entry_price * 100,
        pnl=pnl,
        return_pct=return_pct,
        holding_days=8,
        exit_reason="end_of_data",
    )


@pytest.fixture
def calc():
    return MetricsCalculator()


class TestProfitFactorAllBreakeven:
    """C-5: All trades breakeven → profit_factor=0.0 (not inf)."""

    def test_all_breakeven(self, calc):
        trades = [
            make_trade(ticker="A", pnl=0.0, return_pct=0.0),
            make_trade(ticker="B", pnl=0.0, return_pct=0.0),
            make_trade(ticker="C", pnl=0.0, return_pct=0.0),
        ]
        metrics = calc.calculate(trades, [])
        assert metrics.profit_factor == 0.0


class TestProfitFactorNoLoss:
    """C-5: All trades profitable → profit_factor=inf."""

    def test_all_wins(self, calc):
        trades = [
            make_trade(ticker="A", pnl=500.0, return_pct=5.0, exit_price=105.0),
            make_trade(ticker="B", pnl=1000.0, return_pct=10.0, exit_price=110.0),
        ]
        metrics = calc.calculate(trades, [])
        assert metrics.profit_factor == float("inf")


class TestWelchTTestIdentical:
    """C-4: Identical returns within groups → se=0, should not crash."""

    def test_identical_returns(self, calc):
        trades = [
            make_trade(ticker="A1", grade="A", pnl=500.0, return_pct=5.0, exit_price=105.0),
            make_trade(ticker="A2", grade="A", pnl=500.0, return_pct=5.0, exit_price=105.0),
            make_trade(ticker="A3", grade="B", pnl=500.0, return_pct=5.0, exit_price=105.0),
            make_trade(ticker="C1", grade="C", pnl=500.0, return_pct=5.0, exit_price=105.0),
            make_trade(ticker="C2", grade="C", pnl=500.0, return_pct=5.0, exit_price=105.0),
            make_trade(ticker="D1", grade="D", pnl=500.0, return_pct=5.0, exit_price=105.0),
        ]
        metrics = calc.calculate(trades, [])
        # Should not crash, and result should be present
        assert metrics.ab_vs_cd_test is not None
        assert metrics.ab_vs_cd_test.p_value == 1.0
        assert metrics.ab_vs_cd_test.ci_lower == metrics.ab_vs_cd_test.ci_upper


class TestWelchTTestNormal:
    """C-4: Normal case with different returns between A/B and C/D."""

    def test_significant_difference(self, calc):
        trades = []
        # A/B trades with high returns
        for i in range(10):
            trades.append(
                make_trade(
                    ticker=f"A{i}",
                    grade="A",
                    pnl=1000.0,
                    return_pct=10.0,
                    exit_price=110.0,
                )
            )
        # C/D trades with low returns
        for i in range(10):
            trades.append(
                make_trade(
                    ticker=f"C{i}",
                    grade="C",
                    pnl=-500.0,
                    return_pct=-5.0,
                    exit_price=95.0,
                )
            )
        metrics = calc.calculate(trades, [])
        assert metrics.ab_vs_cd_test is not None
        assert metrics.ab_vs_cd_test.significant
        assert metrics.ab_vs_cd_test.group_a_mean > metrics.ab_vs_cd_test.group_b_mean

    def test_no_significant_difference(self, calc):
        trades = []
        # A/B and C/D with similar returns
        for i in range(5):
            trades.append(
                make_trade(
                    ticker=f"A{i}",
                    grade="A",
                    pnl=100.0 + i * 10,
                    return_pct=1.0 + i * 0.1,
                    exit_price=101.0 + i,
                )
            )
        for i in range(5):
            trades.append(
                make_trade(
                    ticker=f"C{i}",
                    grade="C",
                    pnl=100.0 + i * 10,
                    return_pct=1.0 + i * 0.1,
                    exit_price=101.0 + i,
                )
            )
        metrics = calc.calculate(trades, [])
        assert metrics.ab_vs_cd_test is not None
        assert not metrics.ab_vs_cd_test.significant


class TestDailyEquityCurve:
    """Daily equity curve and position tracking."""

    def test_non_overlapping_trades(self, calc):
        """Two non-overlapping trades → staircase equity, peak_positions=1."""
        trades = [
            TradeResult(
                ticker="A",
                grade="A",
                grade_source="html",
                score=85.0,
                report_date="2025-10-01",
                entry_date="2025-10-02",
                entry_price=100.0,
                exit_date="2025-10-04",
                exit_price=110.0,
                shares=100,
                invested=10000.0,
                pnl=1000.0,
                return_pct=10.0,
                holding_days=2,
                exit_reason="end_of_data",
            ),
            TradeResult(
                ticker="B",
                grade="A",
                grade_source="html",
                score=80.0,
                report_date="2025-10-05",
                entry_date="2025-10-06",
                entry_price=100.0,
                exit_date="2025-10-08",
                exit_price=105.0,
                shares=100,
                invested=10000.0,
                pnl=500.0,
                return_pct=5.0,
                holding_days=2,
                exit_reason="end_of_data",
            ),
        ]
        metrics = calc.calculate(trades, [])
        eq = metrics.daily_equity

        assert len(eq) > 0
        assert metrics.peak_positions == 1

        # After first trade exits on 10/04, equity should be 1000
        eq_by_date = {d.date: d for d in eq}
        assert eq_by_date["2025-10-04"].equity == 1000.0
        # After second trade exits on 10/08, equity should be 1500
        assert eq_by_date["2025-10-08"].equity == 1500.0

    def test_overlapping_trades(self, calc):
        """Two overlapping trades → peak_positions=2."""
        trades = [
            TradeResult(
                ticker="A",
                grade="A",
                grade_source="html",
                score=85.0,
                report_date="2025-10-01",
                entry_date="2025-10-02",
                entry_price=100.0,
                exit_date="2025-10-10",
                exit_price=110.0,
                shares=100,
                invested=10000.0,
                pnl=1000.0,
                return_pct=10.0,
                holding_days=8,
                exit_reason="end_of_data",
            ),
            TradeResult(
                ticker="B",
                grade="A",
                grade_source="html",
                score=80.0,
                report_date="2025-10-03",
                entry_date="2025-10-04",
                entry_price=100.0,
                exit_date="2025-10-10",
                exit_price=105.0,
                shares=100,
                invested=10000.0,
                pnl=500.0,
                return_pct=5.0,
                holding_days=6,
                exit_reason="end_of_data",
            ),
        ]
        metrics = calc.calculate(trades, [])
        assert metrics.peak_positions == 2

        eq_by_date = {d.date: d for d in metrics.daily_equity}
        # Between 10/04 and 10/09, both trades are open → 2 positions
        assert eq_by_date["2025-10-05"].positions == 2

    def test_empty_trades(self, calc):
        """Empty trades → empty equity, peak=0."""
        metrics = calc.calculate([], [])
        assert metrics.daily_equity == []
        assert metrics.peak_positions == 0
        assert metrics.capital_requirement == 0.0

    def test_capital_requirement(self, calc):
        """Capital requirement = peak_positions * position_size."""
        trades = [
            TradeResult(
                ticker="A",
                grade="A",
                grade_source="html",
                score=85.0,
                report_date="2025-10-01",
                entry_date="2025-10-02",
                entry_price=100.0,
                exit_date="2025-10-10",
                exit_price=110.0,
                shares=100,
                invested=10000.0,
                pnl=1000.0,
                return_pct=10.0,
                holding_days=8,
                exit_reason="end_of_data",
            ),
            TradeResult(
                ticker="B",
                grade="A",
                grade_source="html",
                score=80.0,
                report_date="2025-10-03",
                entry_date="2025-10-04",
                entry_price=100.0,
                exit_date="2025-10-10",
                exit_price=105.0,
                shares=100,
                invested=10000.0,
                pnl=500.0,
                return_pct=5.0,
                holding_days=6,
                exit_reason="end_of_data",
            ),
        ]
        metrics = calc.calculate(trades, [], position_size=10000.0)
        assert metrics.peak_positions == 2
        assert metrics.capital_requirement == 20000.0


class TestCrossFilterBreakdown:
    """Cross-filter breakdown: gap_range x score_range bucket assignment."""

    def test_bucket_assignment(self, calc):
        trades = [
            # gap=3%, score=90 → "0-5%", "85+"
            make_trade(ticker="A1", score=90.0, pnl=500.0, return_pct=5.0, exit_price=105.0),
            # gap=7%, score=75 → "5-10%", "70-84"
            make_trade(ticker="A2", score=75.0, pnl=300.0, return_pct=3.0, exit_price=103.0),
            # gap=15%, score=60 → "10-20%", "55-69"
            make_trade(ticker="A3", score=60.0, pnl=-200.0, return_pct=-2.0, exit_price=98.0),
            # gap=25%, score=40 → "20%+", "<55"
            make_trade(ticker="A4", score=40.0, pnl=-500.0, return_pct=-5.0, exit_price=95.0),
            # gap=None, score=None → "Unknown", "No Score"
            make_trade(ticker="A5", score=None, pnl=100.0, return_pct=1.0, exit_price=101.0),
        ]
        # Set gap_size manually
        trades[0].gap_size = 3.0
        trades[1].gap_size = 7.0
        trades[2].gap_size = 15.0
        trades[3].gap_size = 25.0
        trades[4].gap_size = None

        metrics = calc.calculate(trades, [])
        cf = metrics.cross_filter_metrics

        # Should have entries for each unique (gap, score) pair
        assert len(cf) == 5

        lookup = {(c.gap_range, c.score_range): c for c in cf}
        assert ("0-5%", "85+") in lookup
        assert lookup[("0-5%", "85+")].count == 1
        assert lookup[("0-5%", "85+")].avg_return == 5.0

        assert ("5-10%", "70-84") in lookup
        assert lookup[("5-10%", "70-84")].count == 1

        assert ("10-20%", "55-69") in lookup
        assert ("20%+", "<55") in lookup
        assert ("Unknown", "No Score") in lookup

    def test_boundary_values(self, calc):
        """Test boundary values: score=85 → '85+', gap=5 → '5-10%'."""
        trades = [
            make_trade(ticker="B1", score=85.0, pnl=100.0, return_pct=1.0, exit_price=101.0),
            make_trade(ticker="B2", score=84.9, pnl=100.0, return_pct=1.0, exit_price=101.0),
            make_trade(ticker="B3", score=70.0, pnl=100.0, return_pct=1.0, exit_price=101.0),
            make_trade(ticker="B4", score=55.0, pnl=100.0, return_pct=1.0, exit_price=101.0),
        ]
        trades[0].gap_size = 5.0  # boundary → "5-10%"
        trades[1].gap_size = 4.99  # → "0-5%"
        trades[2].gap_size = 10.0  # boundary → "10-20%"
        trades[3].gap_size = 20.0  # boundary → "20%+"

        metrics = calc.calculate(trades, [])
        lookup = {(c.gap_range, c.score_range): c for c in metrics.cross_filter_metrics}

        assert ("5-10%", "85+") in lookup  # score=85, gap=5
        assert ("0-5%", "70-84") in lookup  # score=84.9, gap=4.99
        assert ("10-20%", "70-84") in lookup  # score=70, gap=10
        assert ("20%+", "55-69") in lookup  # score=55, gap=20


class TestConstantInputCorrelation:
    """Constant scores/returns must not trigger ConstantInputWarning."""

    def test_constant_scores_no_warning(self, calc):
        import warnings

        trades = [
            make_trade(ticker=f"C{i}", score=85.0, pnl=100.0 * i, return_pct=1.0 * i)
            for i in range(5)
        ]
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            metrics = calc.calculate(trades, [])
        assert metrics.score_return_correlation == 0.0
        assert metrics.score_return_p_value == 1.0

    def test_constant_returns_no_warning(self, calc):
        import warnings

        trades = [
            make_trade(ticker=f"C{i}", score=70.0 + i * 5, pnl=500.0, return_pct=5.0)
            for i in range(5)
        ]
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            metrics = calc.calculate(trades, [])
        assert metrics.score_return_correlation == 0.0
        assert metrics.score_return_p_value == 1.0

    def test_varying_data_computes_correlation(self, calc):
        trades = [
            make_trade(ticker="V1", score=90.0, pnl=1000.0, return_pct=10.0),
            make_trade(ticker="V2", score=80.0, pnl=500.0, return_pct=5.0),
            make_trade(ticker="V3", score=70.0, pnl=100.0, return_pct=1.0),
            make_trade(ticker="V4", score=60.0, pnl=-200.0, return_pct=-2.0),
        ]
        metrics = calc.calculate(trades, [])
        assert metrics.score_return_correlation != 0.0
