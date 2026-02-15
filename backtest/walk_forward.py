#!/usr/bin/env python3
"""
Walk-forward validation for earnings trade backtest.

Expanding window approach:
- Fold 1: Train oldest N months, Test next month
- Fold 2: Train oldest N+1 months, Test next month
- ...

Prevents overfitting by evaluating out-of-sample performance.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from backtest.metrics_calculator import BacktestMetrics, MetricsCalculator
from backtest.price_fetcher import PriceBar
from backtest.trade_simulator import SkippedTrade, TradeResult, TradeSimulator

logger = logging.getLogger(__name__)


@dataclass
class FoldResult:
    fold_num: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_trades: int
    test_trades: int
    train_win_rate: float
    test_win_rate: float
    train_pnl: float
    test_pnl: float
    train_profit_factor: float
    test_profit_factor: float
    train_sharpe: float
    test_sharpe: float
    train_avg_return: float
    test_avg_return: float


@dataclass
class WalkForwardResult:
    folds: List[FoldResult]
    overfitting_score: float  # mean(test_sharpe / train_sharpe), 1.0 = no overfit
    oos_metrics: Optional[BacktestMetrics] = None  # All OOS trades pooled


class WalkForwardValidator:
    """Walk-forward validation with expanding window."""

    def __init__(
        self,
        simulator: TradeSimulator,
        calculator: MetricsCalculator,
        n_folds: int = 3,
    ):
        self.simulator = simulator
        self.calculator = calculator
        self.n_folds = n_folds

    def run(
        self,
        candidates: list,
        price_data: Dict[str, List[PriceBar]],
    ) -> WalkForwardResult:
        """Run walk-forward validation."""
        # Sort candidates by report_date
        sorted_cands = sorted(candidates, key=lambda c: c.report_date)
        if not sorted_cands:
            return WalkForwardResult(folds=[], overfitting_score=0.0)

        # Get unique months
        months = sorted({c.report_date[:7] for c in sorted_cands})
        logger.info(f"Walk-forward: {len(months)} months available: {months}")

        if len(months) < self.n_folds + 1:
            logger.warning(
                f"Not enough months ({len(months)}) for {self.n_folds} folds. "
                f"Need at least {self.n_folds + 1}."
            )
            return WalkForwardResult(folds=[], overfitting_score=0.0)

        # Generate fold splits: expanding window
        # Last n_folds months become test months, everything before is (expanding) train
        folds = self._generate_folds(months)
        logger.info(f"Generated {len(folds)} folds")

        fold_results = []
        all_oos_trades: List[TradeResult] = []
        all_oos_skipped: List[SkippedTrade] = []

        for fold_num, (train_months, test_months) in enumerate(folds, 1):
            train_cands = [c for c in sorted_cands if c.report_date[:7] in set(train_months)]
            test_cands = [c for c in sorted_cands if c.report_date[:7] in set(test_months)]

            logger.info(
                f"Fold {fold_num}: train={train_months[0]}..{train_months[-1]} ({len(train_cands)} cands), "
                f"test={test_months[0]}..{test_months[-1]} ({len(test_cands)} cands)"
            )

            # Simulate train set
            train_trades, train_skip = self.simulator.simulate_all(train_cands, price_data)
            train_metrics = self.calculator.calculate(
                train_trades,
                train_skip,
                position_size=self.simulator.position_size,
            )

            # Simulate test set
            test_trades, test_skip = self.simulator.simulate_all(test_cands, price_data)
            all_oos_trades.extend(test_trades)
            all_oos_skipped.extend(test_skip)
            test_metrics = self.calculator.calculate(
                test_trades,
                test_skip,
                position_size=self.simulator.position_size,
            )

            fold_results.append(
                FoldResult(
                    fold_num=fold_num,
                    train_start=train_months[0],
                    train_end=train_months[-1],
                    test_start=test_months[0],
                    test_end=test_months[-1],
                    train_trades=train_metrics.total_trades,
                    test_trades=test_metrics.total_trades,
                    train_win_rate=train_metrics.win_rate,
                    test_win_rate=test_metrics.win_rate,
                    train_pnl=train_metrics.total_pnl,
                    test_pnl=test_metrics.total_pnl,
                    train_profit_factor=train_metrics.profit_factor,
                    test_profit_factor=test_metrics.profit_factor,
                    train_sharpe=train_metrics.trade_sharpe,
                    test_sharpe=test_metrics.trade_sharpe,
                    train_avg_return=train_metrics.avg_return,
                    test_avg_return=test_metrics.avg_return,
                )
            )

        # Overfitting score
        of_score = self._overfitting_score(fold_results)

        # OOS pooled metrics
        oos_metrics = None
        if all_oos_trades:
            oos_metrics = self.calculator.calculate(
                all_oos_trades,
                all_oos_skipped,
                position_size=self.simulator.position_size,
            )

        return WalkForwardResult(
            folds=fold_results, overfitting_score=of_score, oos_metrics=oos_metrics
        )

    def _generate_folds(self, months: List[str]) -> List[Tuple[List[str], List[str]]]:
        """
        Generate expanding window folds.

        For n_folds=3 and months=[A,B,C,D,E,F]:
        - Fold 1: Train [A,B,C], Test [D]
        - Fold 2: Train [A,B,C,D], Test [E]
        - Fold 3: Train [A,B,C,D,E], Test [F]
        """
        folds = []
        # We use the last n_folds months as individual test periods
        for i in range(self.n_folds):
            test_idx = len(months) - self.n_folds + i
            train_months = months[:test_idx]
            test_months = [months[test_idx]]
            if train_months:  # Need at least 1 train month
                folds.append((train_months, test_months))
        return folds

    @staticmethod
    def _overfitting_score(folds: List[FoldResult]) -> float:
        """
        Overfitting score = mean(test_sharpe / train_sharpe).
        1.0 = no overfitting, <0.5 = significant overfitting.
        Skips folds where train_sharpe is 0.
        """
        ratios = []
        for f in folds:
            if f.train_sharpe != 0:
                ratios.append(f.test_sharpe / f.train_sharpe)
        if not ratios:
            return 0.0
        return round(float(np.mean(ratios)), 4)

    def print_summary(self, result: WalkForwardResult):
        """Print walk-forward results to console."""
        if not result.folds:
            logger.warning("No walk-forward folds to display")
            return

        print("\n" + "=" * 90)
        print("WALK-FORWARD VALIDATION RESULTS")
        print("=" * 90)

        header = (
            f"{'Fold':<6} {'Train Period':<18} {'Test Period':<12} "
            f"{'Train Trades':>12} {'Test Trades':>11} "
            f"{'Train WR':>9} {'Test WR':>8} "
            f"{'Train PF':>9} {'Test PF':>8} "
        )
        print(header)
        print("-" * 90)

        for f in result.folds:
            row = (
                f"{f.fold_num:<6} {f.train_start}..{f.train_end:<8} {f.test_start:<12} "
                f"{f.train_trades:>12} {f.test_trades:>11} "
                f"{f.train_win_rate:>8.1f}% {f.test_win_rate:>7.1f}% "
                f"{f.train_profit_factor:>9.2f} {f.test_profit_factor:>8.2f} "
            )
            print(row)

        print("-" * 90)

        # Sharpe comparison
        print(f"\n{'Fold':<6} {'Train Sharpe':>13} {'Test Sharpe':>12} {'Ratio':>8}")
        print("-" * 45)
        for f in result.folds:
            ratio = f.test_sharpe / f.train_sharpe if f.train_sharpe != 0 else 0
            print(f"{f.fold_num:<6} {f.train_sharpe:>13.2f} {f.test_sharpe:>12.2f} {ratio:>8.2f}")

        print(f"\nOverfitting Score: {result.overfitting_score:.4f}")
        if result.overfitting_score >= 0.5:
            print("  → PASS: Test performance is >= 50% of train (low overfitting risk)")
        else:
            print("  → WARNING: Test performance is < 50% of train (possible overfitting)")

        # OOS Pooled Metrics
        if result.oos_metrics is not None:
            m = result.oos_metrics
            print("\nOOS POOLED METRICS (all test trades combined)")
            print("─" * 50)
            print(
                f"Trades: {m.total_trades}   Win Rate: {m.win_rate:.1f}%   Avg Return: {m.avg_return:.1f}%"
            )
            print(
                f"P&L: ${m.total_pnl:,.0f}   Profit Factor: {m.profit_factor:.2f}   Trade Sharpe: {m.trade_sharpe:.2f}"
            )

        print("=" * 90)
