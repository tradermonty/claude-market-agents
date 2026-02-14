#!/usr/bin/env python3
"""
Performance metrics calculator for earnings trade backtest.

Computes:
- Overall metrics (win rate, PnL, profit factor, max DD, Trade Sharpe)
- Grade-level breakdown (A/B/C/D comparison)
- Score range analysis
- Statistical tests (Welch t-test for A/B vs C/D)
- Gap size and monthly breakdowns
"""

import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

import numpy as np
from scipy import stats

from backtest.trade_simulator import TradeResult, SkippedTrade

logger = logging.getLogger(__name__)


@dataclass
class GradeMetrics:
    grade: str
    count: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    avg_return: float
    median_return: float
    stop_loss_count: int
    stop_loss_rate: float
    avg_holding_days_win: float
    avg_holding_days_loss: float
    avg_holding_days_stop: float


@dataclass
class ScoreRangeMetrics:
    range_label: str
    count: int
    wins: int
    win_rate: float
    avg_return: float
    total_pnl: float


@dataclass
class GapSizeMetrics:
    range_label: str
    count: int
    wins: int
    win_rate: float
    avg_return: float
    total_pnl: float


@dataclass
class MonthlyMetrics:
    month: str  # YYYY-MM
    count: int
    wins: int
    win_rate: float
    total_pnl: float
    avg_return: float


@dataclass
class StatTestResult:
    test_name: str
    group_a_label: str
    group_b_label: str
    group_a_mean: float
    group_b_mean: float
    group_a_n: int
    group_b_n: int
    t_statistic: float
    p_value: float
    ci_lower: float
    ci_upper: float
    significant: bool


@dataclass
class BacktestMetrics:
    # Overall
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    avg_return: float
    median_return: float
    profit_factor: float
    max_drawdown: float
    max_drawdown_pct: float
    trade_sharpe: float

    # Skip stats
    total_skipped: int
    skip_reasons: Dict[str, int]

    # Grade breakdown
    grade_metrics: List[GradeMetrics]
    grade_metrics_html_only: List[GradeMetrics]  # grade_source="html" only

    # Score ranges
    score_range_metrics: List[ScoreRangeMetrics]

    # Score correlation
    score_return_correlation: float
    score_return_p_value: float

    # A/B vs C/D t-test
    ab_vs_cd_test: Optional[StatTestResult]

    # Gap size breakdown
    gap_size_metrics: List[GapSizeMetrics]

    # Monthly
    monthly_metrics: List[MonthlyMetrics]

    # Stop loss stats
    stop_loss_total: int
    stop_loss_rate: float


class MetricsCalculator:
    """Calculate comprehensive backtest performance metrics."""

    def calculate(
        self,
        trades: List[TradeResult],
        skipped: List[SkippedTrade],
    ) -> BacktestMetrics:
        """Calculate all metrics from trade results."""
        if not trades:
            return self._empty_metrics(skipped)

        returns = [t.return_pct for t in trades]
        pnls = [t.pnl for t in trades]
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl < 0]

        total_profit = sum(t.pnl for t in wins) if wins else 0
        total_loss = abs(sum(t.pnl for t in losses)) if losses else 0

        corr, p_val = self._score_correlation(trades)

        return BacktestMetrics(
            total_trades=len(trades),
            wins=len(wins),
            losses=len(losses),
            win_rate=len(wins) / len(trades) * 100,
            total_pnl=round(sum(pnls), 2),
            avg_return=round(np.mean(returns), 2),
            median_return=round(np.median(returns), 2),
            profit_factor=round(total_profit / total_loss, 2) if total_loss > 0 else (0.0 if total_profit == 0 else float('inf')),
            max_drawdown=round(self._max_drawdown(trades), 2),
            max_drawdown_pct=round(self._max_drawdown_pct(trades), 2),
            trade_sharpe=round(self._trade_sharpe(returns), 2),
            total_skipped=len(skipped),
            skip_reasons=self._skip_breakdown(skipped),
            grade_metrics=self._grade_breakdown(trades, html_only=False),
            grade_metrics_html_only=self._grade_breakdown(trades, html_only=True),
            score_range_metrics=self._score_range_breakdown(trades),
            score_return_correlation=corr,
            score_return_p_value=p_val,
            ab_vs_cd_test=self._ab_vs_cd_test(trades),
            gap_size_metrics=self._gap_size_breakdown(trades),
            monthly_metrics=self._monthly_breakdown(trades),
            stop_loss_total=sum(1 for t in trades if t.exit_reason == "stop_loss"),
            stop_loss_rate=round(
                sum(1 for t in trades if t.exit_reason == "stop_loss") / len(trades) * 100, 1
            ),
        )

    def _max_drawdown(self, trades: List[TradeResult]) -> float:
        """Calculate max drawdown from cumulative PnL (dollar amount)."""
        sorted_trades = sorted(trades, key=lambda t: t.entry_date)
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in sorted_trades:
            cumulative += t.pnl
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def _max_drawdown_pct(self, trades: List[TradeResult]) -> float:
        """Calculate max drawdown as percentage of peak equity."""
        sorted_trades = sorted(trades, key=lambda t: t.entry_date)
        cumulative = 0.0
        peak = 0.0
        max_dd_pct = 0.0
        for t in sorted_trades:
            cumulative += t.pnl
            if cumulative > peak:
                peak = cumulative
            if peak > 0:
                dd_pct = (peak - cumulative) / peak * 100
                if dd_pct > max_dd_pct:
                    max_dd_pct = dd_pct
        return max_dd_pct

    def _trade_sharpe(self, returns: List[float]) -> float:
        """Trade Sharpe ratio (mean / std of trade returns)."""
        if len(returns) < 2:
            return 0.0
        std = np.std(returns, ddof=1)
        if std == 0:
            return 0.0
        return np.mean(returns) / std

    def _skip_breakdown(self, skipped: List[SkippedTrade]) -> Dict[str, int]:
        reasons = {}
        for s in skipped:
            reasons[s.skip_reason] = reasons.get(s.skip_reason, 0) + 1
        return reasons

    def _grade_breakdown(self, trades: List[TradeResult], html_only: bool) -> List[GradeMetrics]:
        """Calculate metrics per grade."""
        filtered = trades
        if html_only:
            filtered = [t for t in trades if t.grade_source == "html"]

        by_grade = {}
        for t in filtered:
            by_grade.setdefault(t.grade, []).append(t)

        result = []
        for grade in ['A', 'B', 'C', 'D']:
            group = by_grade.get(grade, [])
            if not group:
                result.append(GradeMetrics(
                    grade=grade, count=0, wins=0, losses=0,
                    win_rate=0, total_pnl=0, avg_return=0, median_return=0,
                    stop_loss_count=0, stop_loss_rate=0,
                    avg_holding_days_win=0, avg_holding_days_loss=0,
                    avg_holding_days_stop=0,
                ))
                continue

            wins = [t for t in group if t.pnl > 0]
            losses = [t for t in group if t.pnl < 0]
            stops = [t for t in group if t.exit_reason == "stop_loss"]
            non_stop_losses = [t for t in group if t.pnl < 0 and t.exit_reason != "stop_loss"]

            result.append(GradeMetrics(
                grade=grade,
                count=len(group),
                wins=len(wins),
                losses=len(losses),
                win_rate=round(len(wins) / len(group) * 100, 1),
                total_pnl=round(sum(t.pnl for t in group), 2),
                avg_return=round(np.mean([t.return_pct for t in group]), 2),
                median_return=round(np.median([t.return_pct for t in group]), 2),
                stop_loss_count=len(stops),
                stop_loss_rate=round(len(stops) / len(group) * 100, 1),
                avg_holding_days_win=round(np.mean([t.holding_days for t in wins]), 1) if wins else 0,
                avg_holding_days_loss=round(np.mean([t.holding_days for t in non_stop_losses]), 1) if non_stop_losses else 0,
                avg_holding_days_stop=round(np.mean([t.holding_days for t in stops]), 1) if stops else 0,
            ))

        return result

    def _score_range_breakdown(self, trades: List[TradeResult]) -> List[ScoreRangeMetrics]:
        """Breakdown by score ranges: 85+, 70-84, 55-69, <55. Excludes score=None."""
        scored = [t for t in trades if t.score is not None]
        ranges = [
            ("85+", lambda s: s >= 85),
            ("70-84", lambda s: 70 <= s < 85),
            ("55-69", lambda s: 55 <= s < 70),
            ("<55", lambda s: s < 55),
        ]
        result = []
        for label, pred in ranges:
            group = [t for t in scored if pred(t.score)]
            if not group:
                result.append(ScoreRangeMetrics(
                    range_label=label, count=0, wins=0,
                    win_rate=0, avg_return=0, total_pnl=0,
                ))
                continue
            wins = [t for t in group if t.pnl > 0]
            result.append(ScoreRangeMetrics(
                range_label=label,
                count=len(group),
                wins=len(wins),
                win_rate=round(len(wins) / len(group) * 100, 1),
                avg_return=round(np.mean([t.return_pct for t in group]), 2),
                total_pnl=round(sum(t.pnl for t in group), 2),
            ))
        return result

    def _score_correlation(self, trades: List[TradeResult]) -> Tuple[float, float]:
        """Pearson correlation between score and return. Excludes score=None."""
        scored = [t for t in trades if t.score is not None]
        if len(scored) < 3:
            return (0.0, 1.0)
        scores = [t.score for t in scored]
        returns = [t.return_pct for t in scored]
        try:
            r, p = stats.pearsonr(scores, returns)
            return (round(r, 4), round(p, 4))
        except Exception:
            return (0.0, 1.0)

    def _ab_vs_cd_test(self, trades: List[TradeResult]) -> Optional[StatTestResult]:
        """Welch t-test: A/B grade returns vs C/D grade returns."""
        # Use HTML-sourced grades only for the test
        ab = [t.return_pct for t in trades if t.grade in ('A', 'B') and t.grade_source == "html"]
        cd = [t.return_pct for t in trades if t.grade in ('C', 'D') and t.grade_source == "html"]

        if len(ab) < 2 or len(cd) < 2:
            return None

        t_stat, p_val = stats.ttest_ind(ab, cd, equal_var=False)

        # Guard against nan from scipy (identical values)
        if np.isnan(t_stat):
            t_stat = 0.0
            p_val = 1.0

        # 95% CI for difference in means
        mean_diff = np.mean(ab) - np.mean(cd)
        se = np.sqrt(np.var(ab, ddof=1) / len(ab) + np.var(cd, ddof=1) / len(cd))

        if se == 0:
            # All values identical within groups → no meaningful CI
            ci_lower = mean_diff
            ci_upper = mean_diff
        else:
            # Approximate df for Welch's t-test
            df_num = (np.var(ab, ddof=1) / len(ab) + np.var(cd, ddof=1) / len(cd)) ** 2
            df_den = (
                (np.var(ab, ddof=1) / len(ab)) ** 2 / (len(ab) - 1)
                + (np.var(cd, ddof=1) / len(cd)) ** 2 / (len(cd) - 1)
            )
            df = df_num / df_den if df_den > 0 else 1
            t_crit = stats.t.ppf(0.975, df)
            ci_lower = mean_diff - t_crit * se
            ci_upper = mean_diff + t_crit * se

        return StatTestResult(
            test_name="Welch's t-test",
            group_a_label="A/B Grade",
            group_b_label="C/D Grade",
            group_a_mean=round(np.mean(ab), 2),
            group_b_mean=round(np.mean(cd), 2),
            group_a_n=len(ab),
            group_b_n=len(cd),
            t_statistic=round(t_stat, 4),
            p_value=round(p_val, 4),
            ci_lower=round(ci_lower, 2),
            ci_upper=round(ci_upper, 2),
            significant=p_val < 0.05,
        )

    def _gap_size_breakdown(self, trades: List[TradeResult]) -> List[GapSizeMetrics]:
        """Breakdown by gap-up size."""
        ranges = [
            ("0-5%", lambda g: g is not None and 0 <= g < 5),
            ("5-10%", lambda g: g is not None and 5 <= g < 10),
            ("10-20%", lambda g: g is not None and 10 <= g < 20),
            ("20%+", lambda g: g is not None and g >= 20),
            ("Unknown", lambda g: g is None),
        ]
        result = []
        for label, pred in ranges:
            group = [t for t in trades if pred(t.gap_size)]
            if not group:
                result.append(GapSizeMetrics(
                    range_label=label, count=0, wins=0,
                    win_rate=0, avg_return=0, total_pnl=0,
                ))
                continue
            wins = [t for t in group if t.pnl > 0]
            result.append(GapSizeMetrics(
                range_label=label,
                count=len(group),
                wins=len(wins),
                win_rate=round(len(wins) / len(group) * 100, 1),
                avg_return=round(np.mean([t.return_pct for t in group]), 2),
                total_pnl=round(sum(t.pnl for t in group), 2),
            ))
        return result

    def _monthly_breakdown(self, trades: List[TradeResult]) -> List[MonthlyMetrics]:
        """Monthly performance breakdown."""
        by_month = {}
        for t in trades:
            month = t.entry_date[:7]  # YYYY-MM
            by_month.setdefault(month, []).append(t)

        result = []
        for month in sorted(by_month.keys()):
            group = by_month[month]
            wins = [t for t in group if t.pnl > 0]
            result.append(MonthlyMetrics(
                month=month,
                count=len(group),
                wins=len(wins),
                win_rate=round(len(wins) / len(group) * 100, 1),
                total_pnl=round(sum(t.pnl for t in group), 2),
                avg_return=round(np.mean([t.return_pct for t in group]), 2),
            ))
        return result

    def _empty_metrics(self, skipped: List[SkippedTrade]) -> BacktestMetrics:
        """Return empty metrics when no trades."""
        return BacktestMetrics(
            total_trades=0, wins=0, losses=0, win_rate=0,
            total_pnl=0, avg_return=0, median_return=0,
            profit_factor=0, max_drawdown=0, max_drawdown_pct=0,
            trade_sharpe=0, total_skipped=len(skipped),
            skip_reasons=self._skip_breakdown(skipped),
            grade_metrics=[], grade_metrics_html_only=[],
            score_range_metrics=[], score_return_correlation=0,
            score_return_p_value=1.0, ab_vs_cd_test=None,
            gap_size_metrics=[], monthly_metrics=[],
            stop_loss_total=0, stop_loss_rate=0,
        )
