#!/usr/bin/env python3
"""
Earnings Trade Backtest - Main Entry Point

Parses earnings trade HTML reports, fetches historical price data,
simulates trades, and generates performance reports.

Usage:
    python -m backtest.main --reports-dir reports/ --output-dir reports/backtest/
"""

import argparse
import logging
import sys
from pathlib import Path

from backtest.html_parser import EarningsReportParser
from backtest.metrics_calculator import MetricsCalculator
from backtest.price_fetcher import PriceFetcher, aggregate_ticker_periods
from backtest.report_generator import ReportGenerator
from backtest.run_manifest import write_manifest
from backtest.trade_simulator import TradeSimulator

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Earnings Trade Backtest System",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--reports-dir", default="reports/", help="Directory with earnings trade HTML reports"
    )
    parser.add_argument(
        "--output-dir", default="reports/backtest/", help="Output directory for results"
    )
    parser.add_argument(
        "--position-size", type=float, default=10000, help="Position size per trade ($)"
    )
    parser.add_argument("--stop-loss", type=float, default=10.0, help="Stop loss percentage")
    parser.add_argument("--slippage", type=float, default=0.5, help="Slippage percentage on stop")
    parser.add_argument(
        "--max-holding", type=int, default=90, help="Max holding period (calendar days)"
    )
    parser.add_argument(
        "--min-grade", default="D", choices=["A", "B", "C", "D"], help="Minimum grade to include"
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
    parser.add_argument(
        "--stop-mode",
        default="intraday",
        choices=["intraday", "close", "skip_entry_day", "close_next_open"],
        help="Stop loss mode: intraday (low-based), close (close-based), skip_entry_day (skip day-0), close_next_open (close trigger, next open exit)",
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
        help="Entry timing: report_open (enter at report date) or next_day_open (enter next trading day)",
    )
    parser.add_argument("--fmp-api-key", default=None, help="FMP API key (overrides env/config)")
    parser.add_argument(
        "--parse-only", action="store_true", help="Only parse HTML, skip price fetch and simulation"
    )
    parser.add_argument("--walk-forward", action="store_true", help="Run walk-forward validation")
    parser.add_argument("--wf-folds", type=int, default=3, help="Number of walk-forward folds")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    return parser.parse_args()


def validate_args(args) -> None:
    """Validate CLI arguments. Exits with code 2 on invalid input."""
    errors = []

    if args.stop_loss < 0 or args.stop_loss > 100:
        errors.append(f"--stop-loss must be 0-100, got {args.stop_loss}")
    if args.slippage < 0 or args.slippage > 50:
        errors.append(f"--slippage must be 0-50, got {args.slippage}")
    if args.position_size <= 0:
        errors.append(f"--position-size must be > 0, got {args.position_size}")
    if args.max_holding < 1:
        errors.append(f"--max-holding must be >= 1, got {args.max_holding}")

    if args.min_score is not None and (args.min_score < 0 or args.min_score > 100):
        errors.append(f"--min-score must be 0-100, got {args.min_score}")
    if args.max_score is not None and (args.max_score < 0 or args.max_score > 100):
        errors.append(f"--max-score must be 0-100, got {args.max_score}")
    if (
        args.min_score is not None
        and args.max_score is not None
        and args.min_score >= args.max_score
    ):
        errors.append(f"--min-score ({args.min_score}) must be < --max-score ({args.max_score})")

    if args.min_gap is not None and args.min_gap < 0:
        errors.append(f"--min-gap must be >= 0, got {args.min_gap}")
    if args.max_gap is not None and args.max_gap < 0:
        errors.append(f"--max-gap must be >= 0, got {args.max_gap}")
    if args.min_gap is not None and args.max_gap is not None and args.min_gap >= args.max_gap:
        errors.append(f"--min-gap ({args.min_gap}) must be < --max-gap ({args.max_gap})")

    if args.daily_entry_limit is not None and args.daily_entry_limit < 1:
        errors.append(f"--daily-entry-limit must be >= 1, got {args.daily_entry_limit}")
    if args.wf_folds < 1:
        errors.append(f"--wf-folds must be >= 1, got {args.wf_folds}")

    if errors:
        for e in errors:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def main():
    args = parse_args()
    validate_args(args)
    setup_logging(args.verbose)

    config = {
        "position_size": args.position_size,
        "stop_loss": args.stop_loss,
        "slippage": args.slippage,
        "max_holding": args.max_holding,
        "min_grade": args.min_grade,
        "min_score": args.min_score,
        "max_score": args.max_score,
        "min_gap": args.min_gap,
        "max_gap": args.max_gap,
        "stop_mode": args.stop_mode,
        "daily_entry_limit": args.daily_entry_limit,
        "entry_mode": args.entry_mode,
    }

    grade_order = {"A": 0, "B": 1, "C": 2, "D": 3}
    min_grade_idx = grade_order.get(args.min_grade, 3)

    # Step 1: Parse HTML reports
    logger.info("=" * 60)
    logger.info("Step 1: Parsing HTML reports")
    logger.info("=" * 60)

    parser = EarningsReportParser()
    candidates = parser.parse_all_reports(args.reports_dir)

    # Filter by minimum grade
    candidates = [c for c in candidates if grade_order.get(c.grade, 3) <= min_grade_idx]

    logger.info(f"After grade filter (>= {args.min_grade}): {len(candidates)} candidates")

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

    # Summary
    grade_counts = {}
    for c in candidates:
        grade_counts[c.grade] = grade_counts.get(c.grade, 0) + 1
    for g in ["A", "B", "C", "D"]:
        if g in grade_counts:
            logger.info(f"  Grade {g}: {grade_counts[g]}")

    if args.parse_only:
        logger.info("Parse-only mode: skipping price fetch and simulation")
        # Still output CSV of parsed candidates
        _write_candidates_csv(candidates, Path(args.output_dir) / "parsed_candidates.csv")
        return

    if not candidates:
        logger.error("No candidates found. Check reports directory.")
        sys.exit(1)

    # Step 2: Fetch price data
    logger.info("=" * 60)
    logger.info("Step 2: Fetching historical price data")
    logger.info("=" * 60)

    fetcher = PriceFetcher(api_key=args.fmp_api_key)
    ticker_periods = aggregate_ticker_periods(candidates)
    logger.info(f"Fetching prices for {len(ticker_periods)} unique tickers")

    price_data = fetcher.bulk_fetch(ticker_periods)

    # Step 3: Simulate trades
    logger.info("=" * 60)
    logger.info("Step 3: Simulating trades")
    logger.info("=" * 60)

    simulator = TradeSimulator(
        position_size=args.position_size,
        stop_loss_pct=args.stop_loss,
        slippage_pct=args.slippage,
        max_holding_days=args.max_holding,
        stop_mode=args.stop_mode,
        daily_entry_limit=args.daily_entry_limit,
        entry_mode=args.entry_mode,
    )
    trades, skipped = simulator.simulate_all(candidates, price_data)

    # Step 4: Calculate metrics
    logger.info("=" * 60)
    logger.info("Step 4: Calculating metrics")
    logger.info("=" * 60)

    calculator = MetricsCalculator()
    metrics = calculator.calculate(trades, skipped, position_size=args.position_size)

    # Step 5: Generate reports
    logger.info("=" * 60)
    logger.info("Step 5: Generating reports")
    logger.info("=" * 60)

    generator = ReportGenerator()
    generator.generate(metrics, trades, skipped, args.output_dir, config)

    # Write run manifest for reproducibility
    write_manifest(
        output_dir=args.output_dir,
        config=config,
        summary_metrics={
            "total_trades": metrics.total_trades,
            "win_rate": metrics.win_rate,
            "total_pnl": metrics.total_pnl,
            "profit_factor": metrics.profit_factor,
            "trade_sharpe": metrics.trade_sharpe,
            "max_drawdown": metrics.max_drawdown,
        },
        candidate_count=len(candidates),
        trade_count=len(trades),
        skipped_count=len(skipped),
    )

    # Step 6 (optional): Walk-forward validation
    if args.walk_forward:
        logger.info("=" * 60)
        logger.info("Step 6: Walk-Forward Validation")
        logger.info("=" * 60)
        from backtest.walk_forward import WalkForwardValidator

        wf = WalkForwardValidator(
            simulator=simulator,
            calculator=calculator,
            n_folds=args.wf_folds,
        )
        wf_results = wf.run(candidates, price_data)
        wf.print_summary(wf_results)

    # Print summary
    logger.info("=" * 60)
    logger.info("BACKTEST COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Total Trades: {metrics.total_trades}")
    logger.info(f"Win Rate: {metrics.win_rate:.1f}%")
    logger.info(f"Total P&L: ${metrics.total_pnl:,.2f}")
    logger.info(f"Profit Factor: {metrics.profit_factor:.2f}")
    logger.info(f"Trade Sharpe: {metrics.trade_sharpe:.2f}")
    logger.info(f"Max Drawdown: ${metrics.max_drawdown:,.2f}")
    logger.info(f"Peak Positions: {metrics.peak_positions}")
    logger.info(f"Capital Required: ${metrics.capital_requirement:,.0f}")
    logger.info(f"Skipped: {metrics.total_skipped}")
    logger.info(f"Reports: {args.output_dir}")


def _write_candidates_csv(candidates, path):
    """Write parsed candidates to CSV for inspection."""
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "ticker",
                "report_date",
                "grade",
                "grade_source",
                "score",
                "price",
                "gap_size",
                "company_name",
            ]
        )
        for c in sorted(candidates, key=lambda x: (x.report_date, x.ticker)):
            w.writerow(
                [
                    c.ticker,
                    c.report_date,
                    c.grade,
                    c.grade_source,
                    c.score,
                    c.price,
                    c.gap_size,
                    c.company_name,
                ]
            )
    logger.info(f"Wrote {len(candidates)} candidates to {path}")


if __name__ == "__main__":
    main()
