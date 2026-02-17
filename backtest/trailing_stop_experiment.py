#!/usr/bin/env python3
"""
Trailing stop sensitivity analysis — grid search over EMA/N-week-low parameters.

Runs baseline + multiple trailing stop parameter combinations on the same
candidate set, outputs a comparison table and CSV results.

Usage:
    python -m backtest.trailing_stop_experiment --reports-dir reports/ \
        --data-end-date 2026-02-14 --include-baseline --include-nweek
"""

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np

from backtest.html_parser import EarningsReportParser
from backtest.metrics_calculator import MetricsCalculator
from backtest.price_fetcher import PriceFetcher, aggregate_ticker_periods
from backtest.trade_simulator import TradeSimulator

logger = logging.getLogger(__name__)


@dataclass
class ExperimentConfig:
    label: str  # e.g. "ema_p10_t3", "nwl_p4_t3", "baseline"
    trailing_stop: Optional[str]  # None | "weekly_ema" | "weekly_nweek_low"
    trailing_ema_period: int = 10
    trailing_nweek_period: int = 4
    trailing_transition_weeks: int = 3
    max_holding_days: Optional[int] = None  # None for trailing, 90 for baseline


@dataclass
class ExperimentResult:
    config: ExperimentConfig
    trades: int
    win_rate: float
    avg_return: float
    total_pnl: float
    profit_factor: float
    trade_sharpe: float
    stop_loss_rate: float
    trend_break_rate: float
    protective_exit_rate: float
    median_holding_days: float
    peak_positions: int
    capital_required: float
    max_drawdown: float


EMA_PERIODS = [5, 10, 20]
NWEEK_PERIODS = [2, 4, 8]
TRANSITION_WEEKS = [2, 3, 5]


def build_parameter_grid(
    include_baseline: bool = False,
    include_nweek: bool = False,
    keep_max_holding: bool = False,
    max_holding_days: int = 90,
) -> List[ExperimentConfig]:
    """Build parameter grid for trailing stop experiments.

    Args:
        include_baseline: Add a baseline config (no trailing stop).
        include_nweek: Include weekly_nweek_low mode in grid.
        keep_max_holding: Keep max_holding_days even for trailing runs.
        max_holding_days: Max holding period for baseline / keep_max_holding.

    Returns:
        List of ExperimentConfig objects.
    """
    grid: List[ExperimentConfig] = []

    if include_baseline:
        grid.append(
            ExperimentConfig(
                label="baseline",
                trailing_stop=None,
                max_holding_days=max_holding_days,
            )
        )

    trailing_hold = max_holding_days if keep_max_holding else None

    # EMA grid
    for period in EMA_PERIODS:
        for trans in TRANSITION_WEEKS:
            grid.append(
                ExperimentConfig(
                    label=f"ema_p{period}_t{trans}",
                    trailing_stop="weekly_ema",
                    trailing_ema_period=period,
                    trailing_transition_weeks=trans,
                    max_holding_days=trailing_hold,
                )
            )

    # N-week low grid
    if include_nweek:
        for period in NWEEK_PERIODS:
            for trans in TRANSITION_WEEKS:
                grid.append(
                    ExperimentConfig(
                        label=f"nwl_p{period}_t{trans}",
                        trailing_stop="weekly_nweek_low",
                        trailing_nweek_period=period,
                        trailing_transition_weeks=trans,
                        max_holding_days=trailing_hold,
                    )
                )

    return grid


def run_single(config: ExperimentConfig, candidates, price_data, args) -> ExperimentResult:
    """Run a single experiment configuration and return results."""
    sim = TradeSimulator(
        position_size=args.position_size,
        stop_loss_pct=args.stop_loss,
        slippage_pct=args.slippage,
        max_holding_days=config.max_holding_days,
        stop_mode=args.stop_mode,
        entry_mode=args.entry_mode,
        trailing_stop=config.trailing_stop,
        trailing_ema_period=config.trailing_ema_period,
        trailing_nweek_period=config.trailing_nweek_period,
        trailing_transition_weeks=config.trailing_transition_weeks,
        data_end_date=args.data_end_date,
    )
    trades, skipped = sim.simulate_all(candidates, price_data)
    calculator = MetricsCalculator()
    metrics = calculator.calculate(trades, skipped, position_size=args.position_size)

    holding_days = [t.holding_days for t in trades]
    median_hold = float(np.median(holding_days)) if holding_days else 0.0

    return ExperimentResult(
        config=config,
        trades=metrics.total_trades,
        win_rate=metrics.win_rate,
        avg_return=metrics.avg_return,
        total_pnl=metrics.total_pnl,
        profit_factor=metrics.profit_factor,
        trade_sharpe=metrics.trade_sharpe,
        stop_loss_rate=metrics.stop_loss_rate,
        trend_break_rate=metrics.trend_break_rate,
        protective_exit_rate=metrics.protective_exit_rate,
        median_holding_days=round(median_hold, 1),
        peak_positions=metrics.peak_positions,
        capital_required=metrics.capital_requirement,
        max_drawdown=metrics.max_drawdown,
    )


def run_experiment(
    grid: List[ExperimentConfig], candidates, price_data, args
) -> List[ExperimentResult]:
    """Run all configurations in the grid."""
    results = []
    for i, config in enumerate(grid):
        logger.info(f"Running [{i + 1}/{len(grid)}]: {config.label}")
        result = run_single(config, candidates, price_data, args)
        logger.info(
            f"  -> Trades={result.trades}, PnL=${result.total_pnl:,.0f}, "
            f"PF={result.profit_factor:.2f}, Sharpe={result.trade_sharpe:.2f}"
        )
        results.append(result)
    return results


def print_comparison_table(results: List[ExperimentResult], sort_by: str = "total_pnl"):
    """Print comparison table to stdout."""
    if not results:
        print("No results to display.")
        return

    # Sort results
    valid_sort_keys = {
        "total_pnl",
        "profit_factor",
        "trade_sharpe",
        "win_rate",
        "max_drawdown",
        "median_holding_days",
        "stop_loss_rate",
        "trend_break_rate",
        "avg_return",
    }
    if sort_by not in valid_sort_keys:
        sort_by = "total_pnl"

    sorted_results = sorted(results, key=lambda r: getattr(r, sort_by), reverse=True)

    print("\n" + "=" * 140)
    print("TRAILING STOP SENSITIVITY ANALYSIS")
    print("=" * 140)

    header = (
        f"{'Label':<18} {'Mode':<5} {'Period':>6} {'Trans':>5} "
        f"{'Trades':>6} {'WinR%':>6} {'TotalPnL':>12} {'PF':>6} "
        f"{'Sharpe':>7} {'StopR%':>7} {'TrndR%':>7} {'MedHold':>8} "
        f"{'PeakPos':>8} {'MaxDD':>10}"
    )
    print(header)
    print("-" * 140)

    for r in sorted_results:
        c = r.config
        if c.trailing_stop is None:
            mode = "base"
            period = "--"
            trans = "--"
        elif c.trailing_stop == "weekly_ema":
            mode = "ema"
            period = str(c.trailing_ema_period)
            trans = str(c.trailing_transition_weeks)
        else:
            mode = "nwl"
            period = str(c.trailing_nweek_period)
            trans = str(c.trailing_transition_weeks)

        print(
            f"{c.label:<18} {mode:<5} {period:>6} {trans:>5} "
            f"{r.trades:>6} {r.win_rate:>5.1f}% {r.total_pnl:>11,.0f}$ {r.profit_factor:>6.2f} "
            f"{r.trade_sharpe:>7.2f} {r.stop_loss_rate:>6.1f}% {r.trend_break_rate:>6.1f}% "
            f"{r.median_holding_days:>7.0f}d {r.peak_positions:>8} "
            f"${r.max_drawdown:>9,.0f}"
        )

    print("=" * 140)


CSV_HEADERS = [
    "label",
    "mode",
    "period",
    "transition_weeks",
    "trades",
    "win_rate",
    "avg_return",
    "total_pnl",
    "profit_factor",
    "trade_sharpe",
    "stop_loss_rate",
    "trend_break_rate",
    "protective_exit_rate",
    "median_holding_days",
    "peak_positions",
    "capital_required",
    "max_drawdown",
]


def write_results_csv(results: List[ExperimentResult], output_path: Path):
    """Write experiment results to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)
        for r in results:
            c = r.config
            if c.trailing_stop is None:
                mode = "baseline"
                period = ""
            elif c.trailing_stop == "weekly_ema":
                mode = "weekly_ema"
                period = str(c.trailing_ema_period)
            else:
                mode = "weekly_nweek_low"
                period = str(c.trailing_nweek_period)

            writer.writerow(
                [
                    c.label,
                    mode,
                    period,
                    c.trailing_transition_weeks,
                    r.trades,
                    r.win_rate,
                    r.avg_return,
                    r.total_pnl,
                    r.profit_factor,
                    r.trade_sharpe,
                    r.stop_loss_rate,
                    r.trend_break_rate,
                    r.protective_exit_rate,
                    r.median_holding_days,
                    r.peak_positions,
                    r.capital_required,
                    r.max_drawdown,
                ]
            )
    logger.info(f"Results CSV written to {output_path}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Trailing Stop Sensitivity Analysis",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--reports-dir", default="reports/", help="Directory with earnings trade HTML reports"
    )
    parser.add_argument(
        "--output-dir", default="reports/trailing_experiment/", help="CSV output directory"
    )
    parser.add_argument(
        "--data-end-date",
        required=True,
        help="Backtest end date YYYY-MM-DD (required for reproducibility)",
    )
    parser.add_argument(
        "--include-baseline", action="store_true", help="Include baseline (no trailing stop) row"
    )
    parser.add_argument(
        "--include-nweek", action="store_true", help="Include weekly_nweek_low mode"
    )
    parser.add_argument(
        "--keep-max-holding",
        action="store_true",
        help="Keep max_holding_days even for trailing stop runs",
    )
    parser.add_argument(
        "--sort-by",
        default="total_pnl",
        help="Sort column for comparison table",
    )
    # Shared simulation parameters
    parser.add_argument(
        "--position-size", type=float, default=10000, help="Position size per trade ($)"
    )
    parser.add_argument("--stop-loss", type=float, default=10.0, help="Stop loss percentage")
    parser.add_argument("--slippage", type=float, default=0.5, help="Slippage percentage")
    parser.add_argument(
        "--stop-mode",
        default="intraday",
        choices=["intraday", "close", "skip_entry_day", "close_next_open"],
        help="Stop loss mode",
    )
    parser.add_argument(
        "--entry-mode",
        default="report_open",
        choices=["report_open", "next_day_open"],
        help="Entry timing",
    )
    parser.add_argument(
        "--min-grade",
        default="D",
        choices=["A", "B", "C", "D"],
        help="Minimum grade to include",
    )
    parser.add_argument("--fmp-api-key", default=None, help="FMP API key")
    parser.add_argument(
        "--entry-quality-filter",
        action="store_true",
        help="Enable entry quality filter",
    )
    parser.add_argument("--exclude-price-min", type=float, default=None)
    parser.add_argument("--exclude-price-max", type=float, default=None)
    parser.add_argument("--risk-gap-threshold", type=float, default=None)
    parser.add_argument("--risk-score-threshold", type=float, default=None)
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    return parser.parse_args()


def main():
    args = parse_args()
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    grade_order = {"A": 0, "B": 1, "C": 2, "D": 3}
    min_grade_idx = grade_order.get(args.min_grade, 3)

    # Validate entry quality filter args
    from backtest.entry_filter import (
        EXCLUDE_PRICE_MAX,
        EXCLUDE_PRICE_MIN,
        RISK_GAP_THRESHOLD,
        RISK_SCORE_THRESHOLD,
        is_filter_active,
        validate_filter_args,
    )

    filter_errors = validate_filter_args(args)
    if filter_errors:
        for e in filter_errors:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

    filter_active = is_filter_active(args)

    # Parse candidates
    parser = EarningsReportParser()
    candidates = parser.parse_all_reports(args.reports_dir)
    candidates = [c for c in candidates if grade_order.get(c.grade, 3) <= min_grade_idx]
    logger.info(f"Candidates after grade filter: {len(candidates)}")

    # Apply entry quality filter
    if filter_active:
        from backtest.entry_filter import apply_entry_quality_filter

        eff_price_min = (
            args.exclude_price_min if args.exclude_price_min is not None else EXCLUDE_PRICE_MIN
        )
        eff_price_max = (
            args.exclude_price_max if args.exclude_price_max is not None else EXCLUDE_PRICE_MAX
        )
        eff_gap_th = (
            args.risk_gap_threshold if args.risk_gap_threshold is not None else RISK_GAP_THRESHOLD
        )
        eff_score_th = (
            args.risk_score_threshold
            if args.risk_score_threshold is not None
            else RISK_SCORE_THRESHOLD
        )
        candidates, _ = apply_entry_quality_filter(
            candidates,
            price_min=eff_price_min,
            price_max=eff_price_max,
            gap_threshold=eff_gap_th,
            score_threshold=eff_score_th,
        )
        logger.info(f"After entry quality filter: {len(candidates)}")

    if not candidates:
        logger.error("No candidates found.")
        sys.exit(1)

    # Fetch prices once (shared across all configs)
    fetcher = PriceFetcher(api_key=args.fmp_api_key)
    ticker_periods = aggregate_ticker_periods(candidates, buffer_days=400)

    # Limit fetch to data_end_date
    for ticker in list(ticker_periods.keys()):
        start, end = ticker_periods[ticker]
        if end > args.data_end_date:
            ticker_periods[ticker] = (start, args.data_end_date)
    ticker_periods = {k: v for k, v in ticker_periods.items() if v[0] <= v[1]}

    logger.info(f"Fetching prices for {len(ticker_periods)} tickers")
    price_data = fetcher.bulk_fetch(ticker_periods)

    # Build grid
    grid = build_parameter_grid(
        include_baseline=args.include_baseline,
        include_nweek=args.include_nweek,
        keep_max_holding=args.keep_max_holding,
    )
    logger.info(f"Grid size: {len(grid)} configurations")

    # Run experiment
    results = run_experiment(grid, candidates, price_data, args)

    # Output
    print_comparison_table(results, sort_by=args.sort_by)

    output_path = Path(args.output_dir) / "trailing_stop_sensitivity.csv"
    write_results_csv(results, output_path)

    logger.info("Experiment complete.")


if __name__ == "__main__":
    main()
