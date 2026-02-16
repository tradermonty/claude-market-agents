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
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import stats

from backtest.trade_simulator import SkippedTrade, TradeResult

logger = logging.getLogger(__name__)


@dataclass
class DailyEquityPoint:
    date: str  # YYYY-MM-DD
    equity: float  # Cumulative realized PnL
    positions: int  # Number of open positions on this date


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
class CrossFilterMetrics:
    gap_range: str  # "0-5%", "5-10%", "10-20%", "20%+", "Unknown"
    score_range: str  # "85+", "70-84", "55-69", "<55"
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

    # Trend break stats
    trend_break_total: int = 0
    trend_break_rate: float = 0.0
    protective_exit_rate: float = 0.0  # stop_loss + trend_break combined

    # Cross filter (gap x score)
    cross_filter_metrics: List[CrossFilterMetrics] = field(default_factory=list)

    # Equity curve and position tracking
    daily_equity: List[DailyEquityPoint] = field(default_factory=list)
    peak_positions: int = 0
    capital_requirement: float = 0.0


class MetricsCalculator:
    """Calculate comprehensive backtest performance metrics."""

    def calculate(
        self,
        trades: List[TradeResult],
        skipped: List[SkippedTrade],
        position_size: float = 10000.0,
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
        daily_eq = self._daily_equity_series(trades)
        peak_pos = max((d.positions for d in daily_eq), default=0)
        capital_req = round(peak_pos * position_size, 2)

        return BacktestMetrics(
            total_trades=len(trades),
            wins=len(wins),
            losses=len(losses),
            win_rate=len(wins) / len(trades) * 100,
            total_pnl=round(sum(pnls), 2),
            avg_return=round(float(np.mean(returns)), 2),
            median_return=round(float(np.median(returns)), 2),
            profit_factor=round(total_profit / total_loss, 2)
            if total_loss > 0
            else (0.0 if total_profit == 0 else float("inf")),
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
            cross_filter_metrics=self._cross_filter_breakdown(trades),
            monthly_metrics=self._monthly_breakdown(trades),
            stop_loss_total=sum(1 for t in trades if t.exit_reason == "stop_loss"),
            stop_loss_rate=round(
                sum(1 for t in trades if t.exit_reason == "stop_loss") / len(trades) * 100, 1
            ),
            trend_break_total=sum(1 for t in trades if t.exit_reason == "trend_break"),
            trend_break_rate=round(
                sum(1 for t in trades if t.exit_reason == "trend_break") / len(trades) * 100, 1
            ),
            protective_exit_rate=round(
                sum(1 for t in trades if t.exit_reason in ("stop_loss", "trend_break"))
                / len(trades)
                * 100,
                1,
            ),
            daily_equity=daily_eq,
            peak_positions=peak_pos,
            capital_requirement=capital_req,
        )

    def _daily_equity_series(self, trades: List[TradeResult]) -> List[DailyEquityPoint]:
        """Build daily equity (cumulative realized PnL) and open position count."""
        if not trades:
            return []

        # Collect events: entry opens a position, exit closes and realizes PnL
        entry_dates = set()
        exit_dates = set()
        for t in trades:
            entry_dates.add(t.entry_date)
            exit_dates.add(t.exit_date)

        all_dates = sorted(entry_dates | exit_dates)
        if not all_dates:
            return []

        # Build date range from first entry to last exit
        start = datetime.strptime(all_dates[0], "%Y-%m-%d")
        end = datetime.strptime(all_dates[-1], "%Y-%m-%d")

        # Pre-compute: PnL realized on each exit_date, and position deltas
        realized_on: Dict[str, float] = defaultdict(float)
        open_delta: Dict[str, int] = defaultdict(int)  # +1 on entry_date, -1 on exit_date
        for t in trades:
            realized_on[t.exit_date] += t.pnl
            open_delta[t.entry_date] += 1
            open_delta[t.exit_date] -= 1

        result = []
        cumulative_pnl = 0.0
        open_positions = 0
        current = start
        while current <= end:
            ds = current.strftime("%Y-%m-%d")
            cumulative_pnl += realized_on.get(ds, 0.0)
            open_positions += open_delta.get(ds, 0)
            result.append(
                DailyEquityPoint(
                    date=ds,
                    equity=round(cumulative_pnl, 2),
                    positions=open_positions,
                )
            )
            current += timedelta(days=1)

        return result

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
        return float(np.mean(returns) / std)

    def _skip_breakdown(self, skipped: List[SkippedTrade]) -> Dict[str, int]:
        reasons: Dict[str, int] = {}
        for s in skipped:
            reasons[s.skip_reason] = reasons.get(s.skip_reason, 0) + 1
        return reasons

    def _grade_breakdown(self, trades: List[TradeResult], html_only: bool) -> List[GradeMetrics]:
        """Calculate metrics per grade."""
        filtered = trades
        if html_only:
            filtered = [t for t in trades if t.grade_source == "html"]

        by_grade: Dict[str, List[TradeResult]] = {}
        for t in filtered:
            by_grade.setdefault(t.grade, []).append(t)

        result = []
        for grade in ["A", "B", "C", "D"]:
            group = by_grade.get(grade, [])
            if not group:
                result.append(
                    GradeMetrics(
                        grade=grade,
                        count=0,
                        wins=0,
                        losses=0,
                        win_rate=0,
                        total_pnl=0,
                        avg_return=0,
                        median_return=0,
                        stop_loss_count=0,
                        stop_loss_rate=0,
                        avg_holding_days_win=0,
                        avg_holding_days_loss=0,
                        avg_holding_days_stop=0,
                    )
                )
                continue

            wins = [t for t in group if t.pnl > 0]
            losses = [t for t in group if t.pnl < 0]
            stops = [t for t in group if t.exit_reason == "stop_loss"]
            non_stop_losses = [t for t in group if t.pnl < 0 and t.exit_reason != "stop_loss"]

            result.append(
                GradeMetrics(
                    grade=grade,
                    count=len(group),
                    wins=len(wins),
                    losses=len(losses),
                    win_rate=round(len(wins) / len(group) * 100, 1),
                    total_pnl=round(sum(t.pnl for t in group), 2),
                    avg_return=round(float(np.mean([t.return_pct for t in group])), 2),
                    median_return=round(float(np.median([t.return_pct for t in group])), 2),
                    stop_loss_count=len(stops),
                    stop_loss_rate=round(len(stops) / len(group) * 100, 1),
                    avg_holding_days_win=round(float(np.mean([t.holding_days for t in wins])), 1)
                    if wins
                    else 0,
                    avg_holding_days_loss=round(
                        float(np.mean([t.holding_days for t in non_stop_losses])), 1
                    )
                    if non_stop_losses
                    else 0,
                    avg_holding_days_stop=round(float(np.mean([t.holding_days for t in stops])), 1)
                    if stops
                    else 0,
                )
            )

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
                result.append(
                    ScoreRangeMetrics(
                        range_label=label,
                        count=0,
                        wins=0,
                        win_rate=0,
                        avg_return=0,
                        total_pnl=0,
                    )
                )
                continue
            wins = [t for t in group if t.pnl > 0]
            result.append(
                ScoreRangeMetrics(
                    range_label=label,
                    count=len(group),
                    wins=len(wins),
                    win_rate=round(len(wins) / len(group) * 100, 1),
                    avg_return=round(float(np.mean([t.return_pct for t in group])), 2),
                    total_pnl=round(sum(t.pnl for t in group), 2),
                )
            )
        return result

    def _score_correlation(self, trades: List[TradeResult]) -> Tuple[float, float]:
        """Pearson correlation between score and return. Excludes score=None."""
        scored = [t for t in trades if t.score is not None]
        if len(scored) < 3:
            return (0.0, 1.0)
        scores = [t.score for t in scored]
        returns = [t.return_pct for t in scored]
        # Constant arrays cause ConstantInputWarning; skip pearsonr entirely
        if len(set(scores)) < 2 or len(set(returns)) < 2:
            return (0.0, 1.0)
        try:
            r, p = stats.pearsonr(scores, returns)
            return (round(float(r), 4), round(float(p), 4))
        except (ValueError, FloatingPointError) as e:
            logger.debug(f"Pearson correlation failed: {e}")
            return (0.0, 1.0)

    def _ab_vs_cd_test(self, trades: List[TradeResult]) -> Optional[StatTestResult]:
        """Welch t-test: A/B grade returns vs C/D grade returns."""
        # Use HTML-sourced grades only for the test
        ab = [t.return_pct for t in trades if t.grade in ("A", "B") and t.grade_source == "html"]
        cd = [t.return_pct for t in trades if t.grade in ("C", "D") and t.grade_source == "html"]

        if len(ab) < 2 or len(cd) < 2:
            return None

        t_stat, p_val = stats.ttest_ind(ab, cd, equal_var=False)

        # Guard against nan from scipy (identical values)
        if np.isnan(t_stat):
            t_stat = 0.0
            p_val = 1.0

        # 95% CI for difference in means
        mean_diff = float(np.mean(ab)) - float(np.mean(cd))
        se = float(np.sqrt(np.var(ab, ddof=1) / len(ab) + np.var(cd, ddof=1) / len(cd)))

        if se == 0:
            # All values identical within groups → no meaningful CI
            ci_lower = mean_diff
            ci_upper = mean_diff
        else:
            # Approximate df for Welch's t-test
            df_num = (np.var(ab, ddof=1) / len(ab) + np.var(cd, ddof=1) / len(cd)) ** 2
            df_den = (np.var(ab, ddof=1) / len(ab)) ** 2 / (len(ab) - 1) + (
                np.var(cd, ddof=1) / len(cd)
            ) ** 2 / (len(cd) - 1)
            df = df_num / df_den if df_den > 0 else 1
            t_crit = stats.t.ppf(0.975, df)
            ci_lower = mean_diff - t_crit * se
            ci_upper = mean_diff + t_crit * se

        return StatTestResult(
            test_name="Welch's t-test",
            group_a_label="A/B Grade",
            group_b_label="C/D Grade",
            group_a_mean=round(float(np.mean(ab)), 2),
            group_b_mean=round(float(np.mean(cd)), 2),
            group_a_n=len(ab),
            group_b_n=len(cd),
            t_statistic=round(t_stat, 4),
            p_value=round(p_val, 4),
            ci_lower=round(float(ci_lower), 2),
            ci_upper=round(float(ci_upper), 2),
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
                result.append(
                    GapSizeMetrics(
                        range_label=label,
                        count=0,
                        wins=0,
                        win_rate=0,
                        avg_return=0,
                        total_pnl=0,
                    )
                )
                continue
            wins = [t for t in group if t.pnl > 0]
            result.append(
                GapSizeMetrics(
                    range_label=label,
                    count=len(group),
                    wins=len(wins),
                    win_rate=round(len(wins) / len(group) * 100, 1),
                    avg_return=round(float(np.mean([t.return_pct for t in group])), 2),
                    total_pnl=round(sum(t.pnl for t in group), 2),
                )
            )
        return result

    @staticmethod
    def _classify_gap(gap_size: Optional[float]) -> str:
        if gap_size is None:
            return "Unknown"
        if gap_size < 5:
            return "0-5%"
        if gap_size < 10:
            return "5-10%"
        if gap_size < 20:
            return "10-20%"
        return "20%+"

    @staticmethod
    def _classify_score(score: Optional[float]) -> str:
        if score is None:
            return "No Score"
        if score >= 85:
            return "85+"
        if score >= 70:
            return "70-84"
        if score >= 55:
            return "55-69"
        return "<55"

    def _cross_filter_breakdown(self, trades: List[TradeResult]) -> List[CrossFilterMetrics]:
        """Gap range x Score range cross-analysis matrix."""
        gap_labels = ["0-5%", "5-10%", "10-20%", "20%+", "Unknown"]
        score_labels = ["85+", "70-84", "55-69", "<55", "No Score"]

        buckets: Dict[tuple, list] = {}
        for gl in gap_labels:
            for sl in score_labels:
                buckets[(gl, sl)] = []

        for t in trades:
            gl = self._classify_gap(t.gap_size)
            sl = self._classify_score(t.score)
            buckets[(gl, sl)].append(t)

        result = []
        for gl in gap_labels:
            for sl in score_labels:
                group = buckets[(gl, sl)]
                if not group:
                    continue
                wins = [t for t in group if t.pnl > 0]
                result.append(
                    CrossFilterMetrics(
                        gap_range=gl,
                        score_range=sl,
                        count=len(group),
                        wins=len(wins),
                        win_rate=round(len(wins) / len(group) * 100, 1),
                        avg_return=round(float(np.mean([t.return_pct for t in group])), 2),
                        total_pnl=round(sum(t.pnl for t in group), 2),
                    )
                )
        return result

    def _monthly_breakdown(self, trades: List[TradeResult]) -> List[MonthlyMetrics]:
        """Monthly performance breakdown."""
        by_month: Dict[str, List[TradeResult]] = {}
        for t in trades:
            month = t.entry_date[:7]  # YYYY-MM
            by_month.setdefault(month, []).append(t)

        result = []
        for month in sorted(by_month.keys()):
            group = by_month[month]
            wins = [t for t in group if t.pnl > 0]
            result.append(
                MonthlyMetrics(
                    month=month,
                    count=len(group),
                    wins=len(wins),
                    win_rate=round(len(wins) / len(group) * 100, 1),
                    total_pnl=round(sum(t.pnl for t in group), 2),
                    avg_return=round(float(np.mean([t.return_pct for t in group])), 2),
                )
            )
        return result

    def _empty_metrics(self, skipped: List[SkippedTrade]) -> BacktestMetrics:
        """Return empty metrics when no trades."""
        return BacktestMetrics(
            total_trades=0,
            wins=0,
            losses=0,
            win_rate=0,
            total_pnl=0,
            avg_return=0,
            median_return=0,
            profit_factor=0,
            max_drawdown=0,
            max_drawdown_pct=0,
            trade_sharpe=0,
            total_skipped=len(skipped),
            skip_reasons=self._skip_breakdown(skipped),
            grade_metrics=[],
            grade_metrics_html_only=[],
            score_range_metrics=[],
            score_return_correlation=0,
            score_return_p_value=1.0,
            ab_vs_cd_test=None,
            gap_size_metrics=[],
            cross_filter_metrics=[],
            monthly_metrics=[],
            stop_loss_total=0,
            stop_loss_rate=0,
            trend_break_total=0,
            trend_break_rate=0.0,
            protective_exit_rate=0.0,
            daily_equity=[],
            peak_positions=0,
            capital_requirement=0.0,
        )
