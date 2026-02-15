#!/usr/bin/env python3
"""Unit tests for walk-forward validation."""

import pytest
from backtest.walk_forward import WalkForwardValidator, FoldResult, WalkForwardResult
from backtest.trade_simulator import TradeSimulator
from backtest.metrics_calculator import MetricsCalculator
from backtest.price_fetcher import PriceBar
from backtest.html_parser import TradeCandidate
from datetime import datetime, timedelta


def make_candidate(ticker, report_date, grade="B", score=75.0):
    return TradeCandidate(
        ticker=ticker,
        report_date=report_date,
        grade=grade,
        grade_source="html",
        score=score,
    )


def make_bar(date, open_p, high, low, close, adj_close=None, volume=1000):
    return PriceBar(
        date=date, open=open_p, high=high, low=low, close=close,
        adj_close=adj_close if adj_close is not None else close,
        volume=volume,
    )


def make_bars_for_ticker(start_date_str, num_days=120, base_price=100):
    """Generate a series of PriceBars starting from start_date_str."""
    bars = []
    dt = datetime.strptime(start_date_str, '%Y-%m-%d')
    for i in range(num_days):
        d = (dt + timedelta(days=i)).strftime('%Y-%m-%d')
        p = base_price + i * 0.1
        bars.append(make_bar(d, p, p + 5, p - 2, p + 1))
    return bars


class TestFoldGeneration:
    """Test that folds are generated correctly."""

    def test_3_folds_from_6_months(self):
        sim = TradeSimulator()
        calc = MetricsCalculator()
        wf = WalkForwardValidator(simulator=sim, calculator=calc, n_folds=3)
        months = ["2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02"]
        folds = wf._generate_folds(months)

        assert len(folds) == 3
        # Fold 1: train [09,10,11], test [12]
        assert folds[0][0] == ["2025-09", "2025-10", "2025-11"]
        assert folds[0][1] == ["2025-12"]
        # Fold 2: train [09,10,11,12], test [01]
        assert folds[1][0] == ["2025-09", "2025-10", "2025-11", "2025-12"]
        assert folds[1][1] == ["2026-01"]
        # Fold 3: train [09,10,11,12,01], test [02]
        assert folds[2][0] == ["2025-09", "2025-10", "2025-11", "2025-12", "2026-01"]
        assert folds[2][1] == ["2026-02"]

    def test_no_overlap(self):
        """Train and test should not overlap."""
        sim = TradeSimulator()
        calc = MetricsCalculator()
        wf = WalkForwardValidator(simulator=sim, calculator=calc, n_folds=3)
        months = ["2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02"]
        folds = wf._generate_folds(months)

        for train, test in folds:
            overlap = set(train) & set(test)
            assert len(overlap) == 0, f"Overlap found: {overlap}"

    def test_all_months_covered(self):
        """All months appear in at least one fold (train or test)."""
        sim = TradeSimulator()
        calc = MetricsCalculator()
        wf = WalkForwardValidator(simulator=sim, calculator=calc, n_folds=3)
        months = ["2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02"]
        folds = wf._generate_folds(months)

        all_months_seen = set()
        for train, test in folds:
            all_months_seen.update(train)
            all_months_seen.update(test)
        assert all_months_seen == set(months)

    def test_insufficient_months(self):
        """Not enough months → empty result."""
        sim = TradeSimulator()
        calc = MetricsCalculator()
        wf = WalkForwardValidator(simulator=sim, calculator=calc, n_folds=3)

        # Only 3 months, need 4 for 3 folds
        candidates = [
            make_candidate("A", "2025-09-15"),
            make_candidate("B", "2025-10-15"),
            make_candidate("C", "2025-11-15"),
        ]
        result = wf.run(candidates, {})
        assert len(result.folds) == 0
        assert result.overfitting_score == 0.0
        assert result.oos_metrics is None


class TestOverfittingScore:
    """Test overfitting score calculation."""

    def test_no_overfitting(self):
        """test_sharpe == train_sharpe → score = 1.0."""
        folds = [
            FoldResult(fold_num=1, train_start="", train_end="", test_start="", test_end="",
                       train_trades=10, test_trades=5, train_win_rate=60, test_win_rate=60,
                       train_pnl=1000, test_pnl=500, train_profit_factor=2.0, test_profit_factor=2.0,
                       train_sharpe=0.5, test_sharpe=0.5, train_avg_return=2.0, test_avg_return=2.0),
        ]
        score = WalkForwardValidator._overfitting_score(folds)
        assert score == 1.0

    def test_significant_overfitting(self):
        """test_sharpe << train_sharpe → score < 0.5."""
        folds = [
            FoldResult(fold_num=1, train_start="", train_end="", test_start="", test_end="",
                       train_trades=10, test_trades=5, train_win_rate=60, test_win_rate=40,
                       train_pnl=1000, test_pnl=-200, train_profit_factor=2.0, test_profit_factor=0.5,
                       train_sharpe=1.0, test_sharpe=0.2, train_avg_return=5.0, test_avg_return=-1.0),
        ]
        score = WalkForwardValidator._overfitting_score(folds)
        assert score < 0.5

    def test_zero_train_sharpe_skipped(self):
        """train_sharpe=0 folds are skipped."""
        folds = [
            FoldResult(fold_num=1, train_start="", train_end="", test_start="", test_end="",
                       train_trades=10, test_trades=5, train_win_rate=50, test_win_rate=60,
                       train_pnl=0, test_pnl=500, train_profit_factor=1.0, test_profit_factor=2.0,
                       train_sharpe=0.0, test_sharpe=0.5, train_avg_return=0.0, test_avg_return=2.0),
        ]
        score = WalkForwardValidator._overfitting_score(folds)
        assert score == 0.0  # No valid ratios


class TestWalkForwardEndToEnd:
    """End-to-end walk-forward with synthetic data."""

    def test_basic_run(self):
        """Run walk-forward with synthetic candidates and verify fold structure."""
        sim = TradeSimulator(position_size=10000, stop_loss_pct=10.0)
        calc = MetricsCalculator()
        wf = WalkForwardValidator(simulator=sim, calculator=calc, n_folds=2)

        # Create candidates across 4 months
        candidates = []
        for month, tickers in [
            ("2025-09", ["A", "B"]),
            ("2025-10", ["C", "D"]),
            ("2025-11", ["E", "F"]),
            ("2025-12", ["G", "H"]),
        ]:
            for t in tickers:
                candidates.append(make_candidate(f"{t}_{month}", f"{month}-15"))

        # Create price data for each ticker
        price_data = {}
        for c in candidates:
            price_data[c.ticker] = make_bars_for_ticker("2025-09-01", num_days=150)

        result = wf.run(candidates, price_data)
        assert len(result.folds) == 2

        # Fold 1: train [09, 10], test [11]
        f1 = result.folds[0]
        assert f1.train_start == "2025-09"
        assert f1.test_start == "2025-11"
        assert f1.train_trades > 0
        assert f1.test_trades > 0

        # Fold 2: train [09, 10, 11], test [12]
        f2 = result.folds[1]
        assert f2.train_start == "2025-09"
        assert f2.test_start == "2025-12"
        assert f2.train_trades >= f1.train_trades  # Expanding window

        # OOS pooled metrics
        assert result.oos_metrics is not None
        assert result.oos_metrics.total_trades == f1.test_trades + f2.test_trades
        assert result.oos_metrics.profit_factor > 0


class TestOosPooledMetrics:
    """Test OOS pooled metrics calculation."""

    def test_oos_trades_count_matches_sum_of_folds(self):
        """OOS pooled trade count = sum of all fold test trades."""
        sim = TradeSimulator(position_size=10000, stop_loss_pct=10.0)
        calc = MetricsCalculator()
        wf = WalkForwardValidator(simulator=sim, calculator=calc, n_folds=3)

        candidates = []
        for month, tickers in [
            ("2025-08", ["A", "B", "C"]),
            ("2025-09", ["D", "E"]),
            ("2025-10", ["F", "G"]),
            ("2025-11", ["H", "I"]),
            ("2025-12", ["J", "K", "L"]),
        ]:
            for t in tickers:
                candidates.append(make_candidate(f"{t}_{month}", f"{month}-15"))

        price_data = {}
        for c in candidates:
            price_data[c.ticker] = make_bars_for_ticker("2025-08-01", num_days=180)

        result = wf.run(candidates, price_data)
        assert len(result.folds) == 3
        assert result.oos_metrics is not None

        sum_test_trades = sum(f.test_trades for f in result.folds)
        assert result.oos_metrics.total_trades == sum_test_trades

    def test_empty_folds_no_oos_metrics(self):
        """No folds → oos_metrics is None."""
        sim = TradeSimulator()
        calc = MetricsCalculator()
        wf = WalkForwardValidator(simulator=sim, calculator=calc, n_folds=3)

        # Only 2 months, need 4 for 3 folds
        candidates = [
            make_candidate("A", "2025-09-15"),
            make_candidate("B", "2025-10-15"),
        ]
        result = wf.run(candidates, {})
        assert len(result.folds) == 0
        assert result.oos_metrics is None
