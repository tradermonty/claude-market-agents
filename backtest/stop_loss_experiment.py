#!/usr/bin/env python3
"""
Stop loss mode comparison experiment.

Runs all 3 stop modes (intraday, close, skip_entry_day) on the same candidate set
and outputs a comparison table.

Usage:
    python -m backtest.stop_loss_experiment --reports-dir reports/
"""

import argparse
import logging
import sys
from typing import Dict

from backtest.html_parser import EarningsReportParser
from backtest.metrics_calculator import MetricsCalculator
from backtest.price_fetcher import PriceFetcher, aggregate_ticker_periods
from backtest.trade_simulator import TradeSimulator

logger = logging.getLogger(__name__)

STOP_MODES = ["intraday", "close", "skip_entry_day", "close_next_open"]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Stop Loss Mode Comparison Experiment",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--reports-dir", default="reports/", help="Directory with earnings trade HTML reports"
    )
    parser.add_argument(
        "--position-size", type=float, default=10000, help="Position size per trade ($)"
    )
    parser.add_argument("--stop-loss", type=float, default=10.0, help="Stop loss percentage")
    parser.add_argument("--slippage", type=float, default=0.5, help="Slippage percentage")
    parser.add_argument(
        "--max-holding", type=int, default=90, help="Max holding period (calendar days)"
    )
    parser.add_argument(
        "--min-grade", default="D", choices=["A", "B", "C", "D"], help="Minimum grade"
    )
    parser.add_argument(
        "--entry-mode",
        default="report_open",
        choices=["report_open", "next_day_open"],
        help="Entry timing: report_open or next_day_open",
    )
    parser.add_argument("--fmp-api-key", default=None, help="FMP API key")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    return parser.parse_args()


def run_experiment(candidates, price_data, args) -> Dict[str, dict]:
    """Run backtest with each stop mode and return results keyed by mode."""
    results = {}
    calculator = MetricsCalculator()

    for mode in STOP_MODES:
        sim = TradeSimulator(
            position_size=args.position_size,
            stop_loss_pct=args.stop_loss,
            slippage_pct=args.slippage,
            max_holding_days=args.max_holding,
            stop_mode=mode,
            entry_mode=args.entry_mode,
        )
        trades, skipped = sim.simulate_all(candidates, price_data)
        metrics = calculator.calculate(trades, skipped, position_size=args.position_size)

        # Grade-level stop rates
        grade_stops = {}
        for gm in metrics.grade_metrics_html_only:
            if gm.count > 0:
                grade_stops[gm.grade] = {
                    "count": gm.count,
                    "stop_rate": gm.stop_loss_rate,
                    "avg_return": gm.avg_return,
                    "win_rate": gm.win_rate,
                }

        results[mode] = {
            "trades": len(trades),
            "win_rate": metrics.win_rate,
            "total_pnl": metrics.total_pnl,
            "profit_factor": metrics.profit_factor,
            "trade_sharpe": metrics.trade_sharpe,
            "stop_rate": metrics.stop_loss_rate,
            "avg_return": metrics.avg_return,
            "max_drawdown": metrics.max_drawdown,
            "grade_stops": grade_stops,
        }

    return results


def print_comparison(results: Dict[str, dict]):
    """Print comparison table."""
    print("\n" + "=" * 80)
    print("STOP LOSS MODE COMPARISON")
    print("=" * 80)

    # Overall comparison
    header = f"{'Metric':<20}"
    for mode in STOP_MODES:
        header += f" {mode:>16}"
    print(header)
    print("-" * 80)

    metrics_to_show = [
        ("Trades", "trades", "{:>16d}"),
        ("Win Rate", "win_rate", "{:>15.1f}%"),
        ("Avg Return", "avg_return", "{:>15.1f}%"),
        ("Total P&L", "total_pnl", "{:>15,.0f}$"),
        ("Profit Factor", "profit_factor", "{:>16.2f}"),
        ("Trade Sharpe", "trade_sharpe", "{:>16.2f}"),
        ("Stop Rate", "stop_rate", "{:>15.1f}%"),
        ("Max Drawdown", "max_drawdown", "{:>15,.0f}$"),
    ]

    for label, key, fmt in metrics_to_show:
        row = f"{label:<20}"
        for mode in STOP_MODES:
            val = results[mode][key]
            row += " " + fmt.format(val)
        print(row)

    # Grade-level stop rates
    print("\n" + "-" * 80)
    print("STOP RATE BY GRADE")
    print("-" * 80)
    for grade in ["A", "B", "C", "D"]:
        row = f"Grade {grade:<14}"
        for mode in STOP_MODES:
            gs = results[mode]["grade_stops"].get(grade)
            if gs:
                row += f" {gs['stop_rate']:>6.1f}% ({gs['count']:>3d}t)"
            else:
                row += f" {'N/A':>16}"
        print(row)

    # Grade-level win rates
    print("\n" + "-" * 80)
    print("WIN RATE BY GRADE")
    print("-" * 80)
    for grade in ["A", "B", "C", "D"]:
        row = f"Grade {grade:<14}"
        for mode in STOP_MODES:
            gs = results[mode]["grade_stops"].get(grade)
            if gs:
                row += f" {gs['win_rate']:>6.1f}% ({gs['count']:>3d}t)"
            else:
                row += f" {'N/A':>16}"
        print(row)

    # Grade-level avg return
    print("\n" + "-" * 80)
    print("AVG RETURN BY GRADE")
    print("-" * 80)
    for grade in ["A", "B", "C", "D"]:
        row = f"Grade {grade:<14}"
        for mode in STOP_MODES:
            gs = results[mode]["grade_stops"].get(grade)
            if gs:
                row += f" {gs['avg_return']:>6.1f}% ({gs['count']:>3d}t)"
            else:
                row += f" {'N/A':>16}"
        print(row)

    print("\n" + "=" * 80)


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

    # Parse
    parser = EarningsReportParser()
    candidates = parser.parse_all_reports(args.reports_dir)
    candidates = [c for c in candidates if grade_order.get(c.grade, 3) <= min_grade_idx]
    logger.info(f"Candidates after grade filter: {len(candidates)}")

    if not candidates:
        logger.error("No candidates found.")
        sys.exit(1)

    # Fetch prices (once, shared across all modes)
    fetcher = PriceFetcher(api_key=args.fmp_api_key)
    ticker_periods = aggregate_ticker_periods(candidates)
    price_data = fetcher.bulk_fetch(ticker_periods)

    # Run experiment
    results = run_experiment(candidates, price_data, args)

    # Print comparison
    print_comparison(results)


if __name__ == "__main__":
    main()
