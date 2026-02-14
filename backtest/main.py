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
from backtest.price_fetcher import PriceFetcher, aggregate_ticker_periods
from backtest.trade_simulator import TradeSimulator
from backtest.metrics_calculator import MetricsCalculator
from backtest.report_generator import ReportGenerator

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Earnings Trade Backtest System",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--reports-dir', default='reports/', help='Directory with earnings trade HTML reports')
    parser.add_argument('--output-dir', default='reports/backtest/', help='Output directory for results')
    parser.add_argument('--position-size', type=float, default=10000, help='Position size per trade ($)')
    parser.add_argument('--stop-loss', type=float, default=10.0, help='Stop loss percentage')
    parser.add_argument('--slippage', type=float, default=0.5, help='Slippage percentage on stop')
    parser.add_argument('--max-holding', type=int, default=90, help='Max holding period (calendar days)')
    parser.add_argument('--min-grade', default='D', choices=['A', 'B', 'C', 'D'], help='Minimum grade to include')
    parser.add_argument('--fmp-api-key', default=None, help='FMP API key (overrides env/config)')
    parser.add_argument('--parse-only', action='store_true', help='Only parse HTML, skip price fetch and simulation')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose logging')
    return parser.parse_args()


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )
    # Quiet noisy loggers
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)


def main():
    args = parse_args()
    setup_logging(args.verbose)

    config = {
        'position_size': args.position_size,
        'stop_loss': args.stop_loss,
        'slippage': args.slippage,
        'max_holding': args.max_holding,
        'min_grade': args.min_grade,
    }

    grade_order = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
    min_grade_idx = grade_order.get(args.min_grade, 3)

    # Step 1: Parse HTML reports
    logger.info("=" * 60)
    logger.info("Step 1: Parsing HTML reports")
    logger.info("=" * 60)

    parser = EarningsReportParser()
    candidates = parser.parse_all_reports(args.reports_dir)

    # Filter by minimum grade
    candidates = [
        c for c in candidates
        if grade_order.get(c.grade, 3) <= min_grade_idx
    ]

    logger.info(f"After grade filter (>= {args.min_grade}): {len(candidates)} candidates")

    # Summary
    grade_counts = {}
    for c in candidates:
        grade_counts[c.grade] = grade_counts.get(c.grade, 0) + 1
    for g in ['A', 'B', 'C', 'D']:
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
    )
    trades, skipped = simulator.simulate_all(candidates, price_data)

    # Step 4: Calculate metrics
    logger.info("=" * 60)
    logger.info("Step 4: Calculating metrics")
    logger.info("=" * 60)

    calculator = MetricsCalculator()
    metrics = calculator.calculate(trades, skipped)

    # Step 5: Generate reports
    logger.info("=" * 60)
    logger.info("Step 5: Generating reports")
    logger.info("=" * 60)

    generator = ReportGenerator()
    generator.generate(metrics, trades, skipped, args.output_dir, config)

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
    logger.info(f"Skipped: {metrics.total_skipped}")
    logger.info(f"Reports: {args.output_dir}")


def _write_candidates_csv(candidates, path):
    """Write parsed candidates to CSV for inspection."""
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['ticker', 'report_date', 'grade', 'grade_source', 'score', 'price', 'gap_size', 'company_name'])
        for c in sorted(candidates, key=lambda x: (x.report_date, x.ticker)):
            w.writerow([c.ticker, c.report_date, c.grade, c.grade_source, c.score, c.price, c.gap_size, c.company_name])
    logger.info(f"Wrote {len(candidates)} candidates to {path}")


if __name__ == '__main__':
    main()
