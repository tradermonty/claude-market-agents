#!/usr/bin/env python3
"""
VIX threshold sensitivity analysis — grid search over VIX filter thresholds.

Runs baseline (no VIX filter) + multiple VIX threshold levels on the same
candidate set, outputs a comparison table and CSV results.

Usage:
    python -m backtest.vix_threshold_experiment --reports-dir reports/ \
        --vix-thresholds 18 20 22 25 \
        --disable-max-holding --trailing-stop weekly_nweek_low \
        --stop-loss 10 --max-positions 20 --entry-quality-filter
"""

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from backtest.html_parser import EarningsReportParser
from backtest.metrics_calculator import MetricsCalculator
from backtest.portfolio_simulator import PortfolioSimulator
from backtest.price_fetcher import PriceFetcher, aggregate_ticker_periods
from backtest.trade_simulator import TradeSimulator
from backtest.vix_filter import VixDay, apply_vix_filter

logger = logging.getLogger(__name__)


@dataclass
class ExperimentConfig:
    label: str  # e.g. "baseline", "vix_18.0", "vix_20.0"
    vix_threshold: Optional[float]  # None = baseline (no VIX filter)


@dataclass
class ExperimentResult:
    config: ExperimentConfig
    candidates_after_filter: int
    filtered_by_vix: int
    trades: int
    win_rate: float
    avg_return: float
    total_pnl: float
    profit_factor: float
    trade_sharpe: float
    max_drawdown: float
    stop_loss_rate: float
    peak_positions: int
    capital_required: float


DEFAULT_THRESHOLDS = [18.0, 20.0, 22.0, 25.0]


def build_parameter_grid(
    thresholds: Optional[List[float]] = None,
    include_baseline: bool = True,
) -> List[ExperimentConfig]:
    """Build parameter grid for VIX threshold experiments.

    Args:
        thresholds: VIX threshold values to test.
        include_baseline: Add a baseline config (no VIX filter).

    Returns:
        List of ExperimentConfig objects.
    """
    if thresholds is None:
        thresholds = list(DEFAULT_THRESHOLDS)

    grid: List[ExperimentConfig] = []

    if include_baseline:
        grid.append(ExperimentConfig(label="baseline", vix_threshold=None))

    for th in thresholds:
        grid.append(ExperimentConfig(label=f"vix_{th}", vix_threshold=th))

    return grid


def run_single(
    config: ExperimentConfig,
    candidates: list,
    vix_data: Dict[str, VixDay],
    price_data: dict,
    args,
) -> ExperimentResult:
    """Run a single VIX threshold experiment configuration.

    - config.vix_threshold is None → baseline (no VIX filter)
    - otherwise → apply_vix_filter() then simulate

    Zero-candidate handling: if VIX filter removes all candidates,
    skip Simulator and pass empty trades + vix_skipped to MetricsCalculator.
    """
    # Apply VIX filter (only for non-baseline)
    if config.vix_threshold is not None:
        filtered_candidates, vix_skipped = apply_vix_filter(
            candidates, vix_data, config.vix_threshold
        )
    else:
        filtered_candidates = list(candidates)
        vix_skipped = []

    filtered_by_vix = len(vix_skipped)
    candidates_after = len(filtered_candidates)

    # Zero candidates → skip simulator, go straight to metrics
    if not filtered_candidates:
        calculator = MetricsCalculator()
        metrics = calculator.calculate([], vix_skipped, position_size=args.position_size)
        return ExperimentResult(
            config=config,
            candidates_after_filter=candidates_after,
            filtered_by_vix=filtered_by_vix,
            trades=metrics.total_trades,
            win_rate=metrics.win_rate,
            avg_return=metrics.avg_return,
            total_pnl=metrics.total_pnl,
            profit_factor=metrics.profit_factor,
            trade_sharpe=metrics.trade_sharpe,
            max_drawdown=metrics.max_drawdown,
            stop_loss_rate=metrics.stop_loss_rate,
            peak_positions=metrics.peak_positions,
            capital_required=metrics.capital_requirement,
        )

    # Determine effective max_holding_days
    max_holding = getattr(args, "max_holding_days", None)
    if max_holding is None:
        max_holding = (
            None
            if getattr(args, "disable_max_holding", False)
            else getattr(args, "max_holding", 90)
        )

    # Simulate trades
    if getattr(args, "max_positions", None) is not None:
        sim = PortfolioSimulator(
            max_positions=args.max_positions,
            position_size=args.position_size,
            stop_loss_pct=args.stop_loss,
            slippage_pct=args.slippage,
            max_holding_days=max_holding,
            stop_mode=args.stop_mode,
            entry_mode=args.entry_mode,
            trailing_stop=getattr(args, "trailing_stop", None),
            trailing_ema_period=getattr(args, "trailing_ema_period", 10),
            trailing_nweek_period=getattr(args, "trailing_nweek_period", 4),
            trailing_transition_weeks=getattr(args, "trailing_transition_weeks", 3),
            data_end_date=getattr(args, "data_end_date", None),
            enable_rotation=not getattr(args, "no_rotation", False),
        )
        trades, skipped = sim.simulate_portfolio(filtered_candidates, price_data)
    else:
        sim = TradeSimulator(
            position_size=args.position_size,
            stop_loss_pct=args.stop_loss,
            slippage_pct=args.slippage,
            max_holding_days=max_holding,
            stop_mode=args.stop_mode,
            daily_entry_limit=getattr(args, "daily_entry_limit", None),
            entry_mode=args.entry_mode,
            trailing_stop=getattr(args, "trailing_stop", None),
            trailing_ema_period=getattr(args, "trailing_ema_period", 10),
            trailing_nweek_period=getattr(args, "trailing_nweek_period", 4),
            trailing_transition_weeks=getattr(args, "trailing_transition_weeks", 3),
            data_end_date=getattr(args, "data_end_date", None),
        )
        trades, skipped = sim.simulate_all(filtered_candidates, price_data)

    # Merge VIX-skipped into skipped list for metrics
    all_skipped = vix_skipped + skipped

    calculator = MetricsCalculator()
    metrics = calculator.calculate(trades, all_skipped, position_size=args.position_size)

    return ExperimentResult(
        config=config,
        candidates_after_filter=candidates_after,
        filtered_by_vix=filtered_by_vix,
        trades=metrics.total_trades,
        win_rate=metrics.win_rate,
        avg_return=metrics.avg_return,
        total_pnl=metrics.total_pnl,
        profit_factor=metrics.profit_factor,
        trade_sharpe=metrics.trade_sharpe,
        max_drawdown=metrics.max_drawdown,
        stop_loss_rate=metrics.stop_loss_rate,
        peak_positions=metrics.peak_positions,
        capital_required=metrics.capital_requirement,
    )


def run_experiment(
    grid: List[ExperimentConfig],
    candidates: list,
    vix_data: Dict[str, VixDay],
    price_data: dict,
    args,
) -> List[ExperimentResult]:
    """Run all configurations in the grid."""
    results = []
    for i, config in enumerate(grid):
        logger.info(f"Running [{i + 1}/{len(grid)}]: {config.label}")
        result = run_single(config, candidates, vix_data, price_data, args)
        logger.info(
            f"  -> Filtered={result.filtered_by_vix}, Candidates={result.candidates_after_filter}, "
            f"Trades={result.trades}, PnL=${result.total_pnl:,.0f}, "
            f"PF={result.profit_factor:.2f}, Sharpe={result.trade_sharpe:.2f}"
        )
        results.append(result)
    return results


def print_comparison_table(results: List[ExperimentResult], sort_by: str = "total_pnl"):
    """Print comparison table to stdout."""
    if not results:
        print("No results to display.")
        return

    valid_sort_keys = {
        "total_pnl",
        "profit_factor",
        "trade_sharpe",
        "win_rate",
        "max_drawdown",
        "stop_loss_rate",
        "avg_return",
        "filtered_by_vix",
        "candidates_after_filter",
    }
    if sort_by not in valid_sort_keys:
        sort_by = "total_pnl"

    sorted_results = sorted(results, key=lambda r: getattr(r, sort_by), reverse=True)

    print("\n" + "=" * 140)
    print("VIX THRESHOLD SENSITIVITY ANALYSIS")
    print("=" * 140)

    header = (
        f"{'Label':<14} {'Thresh':>6} {'Filt':>5} {'Cands':>6} "
        f"{'Trades':>6} {'WinR%':>6} {'TotalPnL':>12} {'PF':>6} "
        f"{'Sharpe':>7} {'StopR%':>7} {'PeakPos':>8} {'MaxDD':>10}"
    )
    print(header)
    print("-" * 140)

    for r in sorted_results:
        c = r.config
        thresh = "--" if c.vix_threshold is None else f"{c.vix_threshold:.0f}"

        print(
            f"{c.label:<14} {thresh:>6} {r.filtered_by_vix:>5} {r.candidates_after_filter:>6} "
            f"{r.trades:>6} {r.win_rate:>5.1f}% {r.total_pnl:>11,.0f}$ {r.profit_factor:>6.2f} "
            f"{r.trade_sharpe:>7.2f} {r.stop_loss_rate:>6.1f}% {r.peak_positions:>8} "
            f"${r.max_drawdown:>9,.0f}"
        )

    print("=" * 140)


CSV_HEADERS = [
    "label",
    "vix_threshold",
    "filtered_by_vix",
    "candidates_after_filter",
    "trades",
    "win_rate",
    "avg_return",
    "total_pnl",
    "profit_factor",
    "trade_sharpe",
    "stop_loss_rate",
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
            writer.writerow(
                [
                    c.label,
                    c.vix_threshold if c.vix_threshold is not None else "",
                    r.filtered_by_vix,
                    r.candidates_after_filter,
                    r.trades,
                    r.win_rate,
                    r.avg_return,
                    r.total_pnl,
                    r.profit_factor,
                    r.trade_sharpe,
                    r.stop_loss_rate,
                    r.peak_positions,
                    r.capital_required,
                    r.max_drawdown,
                ]
            )
    logger.info(f"Results CSV written to {output_path}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="VIX Threshold Sensitivity Analysis",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        allow_abbrev=False,
    )
    parser.add_argument(
        "--reports-dir", default="reports/", help="Directory with earnings trade HTML reports"
    )
    parser.add_argument(
        "--output-dir",
        default="reports/backtest/vix_experiment/",
        help="CSV output directory",
    )
    parser.add_argument(
        "--vix-thresholds",
        nargs="+",
        type=float,
        default=DEFAULT_THRESHOLDS,
        help="VIX threshold values to test",
    )
    parser.add_argument(
        "--data-end-date",
        default=None,
        help="Backtest end date YYYY-MM-DD (required for reproducibility)",
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
        "--daily-entry-limit",
        type=int,
        default=None,
        help="Max new entries per day (None = unlimited)",
    )
    parser.add_argument(
        "--entry-mode",
        default="report_open",
        choices=["report_open", "next_day_open"],
        help="Entry timing",
    )
    parser.add_argument(
        "--trailing-stop",
        default=None,
        choices=["weekly_ema", "weekly_nweek_low"],
        help="Trailing stop mode",
    )
    parser.add_argument(
        "--trailing-ema-period", type=int, default=10, help="Weekly EMA period for trailing stop"
    )
    parser.add_argument(
        "--trailing-nweek-period", type=int, default=4, help="N-week low period for trailing stop"
    )
    parser.add_argument(
        "--trailing-transition-weeks",
        type=int,
        default=3,
        help="Completed weeks before trailing stop activates",
    )
    parser.add_argument(
        "--disable-max-holding",
        action="store_true",
        help="Disable max holding period (requires --trailing-stop)",
    )
    parser.add_argument(
        "--max-holding",
        type=int,
        default=90,
        help="Max holding period (calendar days)",
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        default=None,
        help="Maximum concurrent positions (enables portfolio mode)",
    )
    parser.add_argument(
        "--no-rotation",
        action="store_true",
        help="Disable position rotation (requires --max-positions)",
    )
    parser.add_argument(
        "--min-grade",
        default="D",
        choices=["A", "B", "C", "D"],
        help="Minimum grade to include",
    )
    parser.add_argument(
        "--min-score", type=float, default=None, help="Minimum score filter (inclusive)"
    )
    parser.add_argument(
        "--max-score", type=float, default=None, help="Maximum score filter (exclusive)"
    )
    parser.add_argument(
        "--min-gap", type=float, default=None, help="Minimum gap %% filter (inclusive)"
    )
    parser.add_argument(
        "--max-gap", type=float, default=None, help="Maximum gap %% filter (exclusive)"
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
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    grade_order = {"A": 0, "B": 1, "C": 2, "D": 3}
    min_grade_idx = grade_order.get(args.min_grade, 3)

    # Determine effective max_holding_days
    max_holding = None if args.disable_max_holding else args.max_holding

    # Validate entry quality filter args
    from backtest.entry_filter import (
        EXCLUDE_PRICE_MAX,
        EXCLUDE_PRICE_MIN,
        RISK_GAP_THRESHOLD,
        RISK_SCORE_THRESHOLD,
        is_filter_active,
        validate_filter_args,
    )

    # Validate VIX thresholds
    errors = []
    for th in args.vix_thresholds:
        if th <= 0:
            errors.append(f"--vix-thresholds values must be > 0, got {th}")

    errors.extend(validate_filter_args(args))
    if errors:
        for e in errors:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

    filter_active = is_filter_active(args)

    # Parse candidates
    parser = EarningsReportParser()
    candidates = parser.parse_all_reports(args.reports_dir)
    candidates = [c for c in candidates if grade_order.get(c.grade, 3) <= min_grade_idx]
    logger.info(f"Candidates after grade filter: {len(candidates)}")

    # Score range filter
    if args.min_score is not None:
        candidates = [c for c in candidates if c.score is not None and c.score >= args.min_score]
    if args.max_score is not None:
        candidates = [c for c in candidates if c.score is not None and c.score < args.max_score]
    if args.min_score is not None or args.max_score is not None:
        lo = args.min_score if args.min_score is not None else "-"
        hi = args.max_score if args.max_score is not None else "-"
        logger.info(f"After score filter [{lo}, {hi}): {len(candidates)} candidates")

    # Gap size filter
    if args.min_gap is not None:
        candidates = [
            c for c in candidates if c.gap_size is not None and c.gap_size >= args.min_gap
        ]
    if args.max_gap is not None:
        candidates = [c for c in candidates if c.gap_size is not None and c.gap_size < args.max_gap]
    if args.min_gap is not None or args.max_gap is not None:
        lo = args.min_gap if args.min_gap is not None else "-"
        hi = args.max_gap if args.max_gap is not None else "-"
        logger.info(f"After gap filter [{lo}%, {hi}%): {len(candidates)} candidates")

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

    # Fetch VIX data once (shared across all thresholds)
    from datetime import timedelta

    from backtest.vix_filter import VIX_LOOKBACK_DAYS

    min_report_dt = min(
        __import__("datetime").datetime.strptime(c.report_date, "%Y-%m-%d") for c in candidates
    )
    max_report_dt = max(
        __import__("datetime").datetime.strptime(c.report_date, "%Y-%m-%d") for c in candidates
    )
    vix_from = (min_report_dt - timedelta(days=VIX_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    # Extend end date to cover entry_date > report_date (next_day_open, holidays)
    vix_to = (max_report_dt + timedelta(days=VIX_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    fetcher = PriceFetcher(api_key=args.fmp_api_key)

    logger.info(f"Fetching VIX data from {vix_from} to {vix_to}")
    from backtest.vix_filter import fetch_vix_data

    vix_data = fetch_vix_data(fetcher, vix_from, vix_to)
    logger.info(f"VIX data: {len(vix_data)} trading days")

    # Fetch stock price data once (all candidates, shared)
    buffer = 400 if args.trailing_stop else 120
    ticker_periods = aggregate_ticker_periods(candidates, buffer_days=buffer)

    if args.data_end_date:
        for ticker in list(ticker_periods.keys()):
            start, end = ticker_periods[ticker]
            if end > args.data_end_date:
                ticker_periods[ticker] = (start, args.data_end_date)
        ticker_periods = {k: v for k, v in ticker_periods.items() if v[0] <= v[1]}

    logger.info(f"Fetching prices for {len(ticker_periods)} tickers")
    price_data = fetcher.bulk_fetch(ticker_periods)

    # Build args namespace for run_single
    args.max_holding_days = max_holding

    # Build grid
    grid = build_parameter_grid(thresholds=args.vix_thresholds)
    logger.info(f"Grid size: {len(grid)} configurations")

    # Run experiment
    results = run_experiment(grid, candidates, vix_data, price_data, args)

    # Output
    print_comparison_table(results, sort_by=args.sort_by)

    output_path = Path(args.output_dir) / "vix_threshold_results.csv"
    write_results_csv(results, output_path)

    logger.info("Experiment complete.")


if __name__ == "__main__":
    main()
