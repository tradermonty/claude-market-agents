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
        assert metrics.profit_factor == float('inf')


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
            trades.append(make_trade(
                ticker=f"A{i}", grade="A",
                pnl=1000.0, return_pct=10.0, exit_price=110.0,
            ))
        # C/D trades with low returns
        for i in range(10):
            trades.append(make_trade(
                ticker=f"C{i}", grade="C",
                pnl=-500.0, return_pct=-5.0, exit_price=95.0,
            ))
        metrics = calc.calculate(trades, [])
        assert metrics.ab_vs_cd_test is not None
        assert metrics.ab_vs_cd_test.significant == True
        assert metrics.ab_vs_cd_test.group_a_mean > metrics.ab_vs_cd_test.group_b_mean

    def test_no_significant_difference(self, calc):
        trades = []
        # A/B and C/D with similar returns
        for i in range(5):
            trades.append(make_trade(
                ticker=f"A{i}", grade="A",
                pnl=100.0 + i * 10, return_pct=1.0 + i * 0.1, exit_price=101.0 + i,
            ))
        for i in range(5):
            trades.append(make_trade(
                ticker=f"C{i}", grade="C",
                pnl=100.0 + i * 10, return_pct=1.0 + i * 0.1, exit_price=101.0 + i,
            ))
        metrics = calc.calculate(trades, [])
        assert metrics.ab_vs_cd_test is not None
        assert metrics.ab_vs_cd_test.significant == False
