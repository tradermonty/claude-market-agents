#!/usr/bin/env python3
"""
Trade chart generator for earnings trade backtest.

Generates candlestick chart PNGs for each trade with entry/exit markers,
stop loss lines, and volume bars. Uses mplfinance for rendering.

Usage (standalone):
    python -m backtest.chart_generator --trades-csv reports/backtest/earnings_trade_backtest_trades.csv
"""

import argparse
import csv
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from backtest.price_fetcher import PriceBar
from backtest.trade_simulator import TradeResult

logger = logging.getLogger(__name__)

# Pre-entry and post-exit padding (trading days)
PRE_ENTRY_DAYS = 10
POST_EXIT_DAYS = 5


def _check_imports():
    """Check that chart dependencies are available."""
    try:
        import matplotlib

        matplotlib.use("Agg")  # Headless backend for batch PNG generation
        import mplfinance  # noqa: F401
        import pandas  # noqa: F401
    except ImportError:
        logger.error("Chart dependencies not installed. Run: pip install -e '.[charts]'")
        sys.exit(1)


class ChartGenerator:
    """Generate candlestick chart PNGs for individual trades."""

    # Dark theme colors matching HTML report
    BG_COLOR = "#0d1117"
    PANEL_COLOR = "#161b22"
    TEXT_COLOR = "#e6edf3"
    GRID_COLOR = "#21262d"
    GREEN = "#3fb950"
    RED = "#f85149"
    BLUE = "#58a6ff"

    def generate_all_charts(
        self,
        trades: List[TradeResult],
        price_data: Dict[str, List[PriceBar]],
        output_dir: str,
        stop_loss_pct: float = 10.0,
    ) -> int:
        """Generate chart PNGs for all trades.

        Returns the number of charts generated.
        """
        _check_imports()

        charts_dir = Path(output_dir) / "charts"
        charts_dir.mkdir(parents=True, exist_ok=True)

        generated = 0
        skipped = 0
        for trade in trades:
            bars = price_data.get(trade.ticker)
            if not bars:
                logger.debug("No price data for %s, skipping chart", trade.ticker)
                skipped += 1
                continue

            success = self.generate_trade_chart(trade, bars, str(charts_dir), stop_loss_pct)
            if success:
                generated += 1
            else:
                skipped += 1

        logger.info("Charts: %d generated, %d skipped -> %s", generated, skipped, charts_dir)
        return generated

    def generate_trade_chart(
        self,
        trade: TradeResult,
        bars: List[PriceBar],
        output_dir: str,
        stop_loss_pct: float = 10.0,
    ) -> bool:
        """Generate a single trade chart PNG. Returns True on success."""
        import matplotlib.pyplot as plt
        import mplfinance as mpf
        import pandas as pd

        window = self._slice_price_window(trade, bars)
        if len(window) < 2:
            logger.debug(
                "Insufficient price data for %s %s (%d bars)",
                trade.ticker,
                trade.entry_date,
                len(window),
            )
            return False

        # Build DataFrame with adjusted prices
        df = pd.DataFrame(
            {
                "Date": [datetime.strptime(b.date, "%Y-%m-%d") for b in window],
                "Open": [b.adjusted_open for b in window],
                "High": [b.adjusted_high for b in window],
                "Low": [b.adjusted_low for b in window],
                "Close": [
                    b.adj_close if (b.adj_close is not None and b.adj_close > 0) else b.close
                    for b in window
                ],
                "Volume": [b.volume for b in window],
            }
        )
        df.set_index("Date", inplace=True)

        # Marker data — find closest matching dates in the index
        entry_dt = datetime.strptime(trade.entry_date, "%Y-%m-%d")
        exit_dt = datetime.strptime(trade.exit_date, "%Y-%m-%d")

        entry_match = self._find_nearest_date(df.index, entry_dt)
        exit_match = self._find_nearest_date(df.index, exit_dt)

        exit_color = self.GREEN if trade.return_pct >= 0 else self.RED

        addplots = []
        if entry_match is not None:
            entry_markers = [
                trade.entry_price if d == entry_match else float("nan") for d in df.index
            ]
            addplots.append(
                mpf.make_addplot(
                    entry_markers,
                    type="scatter",
                    markersize=150,
                    marker="^",
                    color=self.GREEN,
                    edgecolors="white",
                    linewidths=0.8,
                )
            )
        if exit_match is not None:
            exit_markers = [trade.exit_price if d == exit_match else float("nan") for d in df.index]
            addplots.append(
                mpf.make_addplot(
                    exit_markers,
                    type="scatter",
                    markersize=150,
                    marker="v",
                    color=exit_color,
                    edgecolors="white",
                    linewidths=0.8,
                )
            )

        # Stop loss line
        stop_price = trade.entry_price * (1 - stop_loss_pct / 100)
        hlines = {
            "hlines": [stop_price],
            "colors": [self.RED],
            "linestyle": "--",
            "linewidths": 1,
        }

        # Custom dark style
        mc = mpf.make_marketcolors(
            up=self.GREEN,
            down=self.RED,
            edge={"up": self.GREEN, "down": self.RED},
            wick={"up": self.GREEN, "down": self.RED},
            volume={"up": self.GREEN, "down": self.RED},
        )
        style = mpf.make_mpf_style(
            marketcolors=mc,
            figcolor=self.BG_COLOR,
            facecolor=self.PANEL_COLOR,
            edgecolor=self.GRID_COLOR,
            gridcolor=self.GRID_COLOR,
            gridstyle=":",
            rc={
                "axes.labelcolor": self.TEXT_COLOR,
                "xtick.color": self.TEXT_COLOR,
                "ytick.color": self.TEXT_COLOR,
            },
        )

        # Title
        score_str = f"{trade.score:.0f}" if trade.score is not None else "N/A"
        title = (
            f"{trade.ticker} | Grade {trade.grade} | Score {score_str} | "
            f"{trade.return_pct:+.1f}% | {trade.exit_reason}"
        )

        filename = self._generate_chart_filename(trade)
        filepath = Path(output_dir) / filename

        plot_kwargs = {
            "type": "candle",
            "style": style,
            "volume": True,
            "hlines": hlines,
            "title": title,
            "figsize": (12, 7),
            "returnfig": True,
        }
        if addplots:
            plot_kwargs["addplot"] = addplots

        fig, axes = mpf.plot(df, **plot_kwargs)

        # Style title
        axes[0].set_title(title, color=self.TEXT_COLOR, fontsize=11, pad=10)

        # Annotate entry
        if entry_match is not None:
            axes[0].annotate(
                f"Entry ${trade.entry_price:.2f}",
                xy=(entry_match, trade.entry_price),
                xytext=(10, -25),
                textcoords="offset points",
                fontsize=8,
                color=self.GREEN,
                arrowprops={"arrowstyle": "->", "color": self.GREEN, "lw": 0.8},
            )

        # Annotate exit
        if exit_match is not None:
            exit_label = f"Exit ${trade.exit_price:.2f} ({trade.exit_reason})"
            axes[0].annotate(
                exit_label,
                xy=(exit_match, trade.exit_price),
                xytext=(10, 25),
                textcoords="offset points",
                fontsize=8,
                color=exit_color,
                arrowprops={"arrowstyle": "->", "color": exit_color, "lw": 0.8},
            )

        # Annotate stop loss line
        axes[0].text(
            df.index[0],
            stop_price,
            f" SL ${stop_price:.2f}",
            color=self.RED,
            fontsize=7,
            va="bottom",
        )

        fig.savefig(filepath, dpi=100, facecolor=self.BG_COLOR)
        plt.close(fig)

        logger.debug("Chart saved: %s", filepath)
        return True

    @staticmethod
    def _find_nearest_date(index, target_dt: datetime) -> Optional[datetime]:
        """Find the nearest date in a DatetimeIndex on or after target_dt."""
        for d in index:
            if d >= target_dt:
                return d
        # If no date on or after, try on or before
        for d in reversed(index):
            if d <= target_dt:
                return d
        return None

    @staticmethod
    def _slice_price_window(trade: TradeResult, bars: List[PriceBar]) -> List[PriceBar]:
        """Slice bars to entry - PRE_ENTRY_DAYS .. exit + POST_EXIT_DAYS."""
        entry_dt = datetime.strptime(trade.entry_date, "%Y-%m-%d")
        exit_dt = datetime.strptime(trade.exit_date, "%Y-%m-%d")

        # Find entry index in bars
        entry_idx: Optional[int] = None
        exit_idx: Optional[int] = None
        for i, b in enumerate(bars):
            bar_dt = datetime.strptime(b.date, "%Y-%m-%d")
            if entry_idx is None and bar_dt >= entry_dt:
                entry_idx = i
            if bar_dt <= exit_dt:
                exit_idx = i

        if entry_idx is None or exit_idx is None:
            return []

        start = max(0, entry_idx - PRE_ENTRY_DAYS)
        end = min(len(bars), exit_idx + POST_EXIT_DAYS + 1)
        return bars[start:end]

    @staticmethod
    def _generate_chart_filename(trade: TradeResult) -> str:
        """Generate chart filename: {ticker}_{entry_date}_{grade}.png"""
        safe_ticker = trade.ticker.replace("/", "-").replace(".", "-")
        return f"{safe_ticker}_{trade.entry_date}_{trade.grade}.png"


def _load_trades_from_csv(csv_path: str) -> List[TradeResult]:
    """Load TradeResult objects from backtest CSV output."""
    trades = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            score_val = row.get("score", "")
            gap_val = row.get("gap_size", "")
            trades.append(
                TradeResult(
                    ticker=row["ticker"],
                    grade=row["grade"],
                    grade_source=row.get("grade_source", "html"),
                    score=float(score_val) if score_val else None,
                    report_date=row["report_date"],
                    entry_date=row["entry_date"],
                    entry_price=float(row["entry_price"]),
                    exit_date=row["exit_date"],
                    exit_price=float(row["exit_price"]),
                    shares=int(row["shares"]),
                    invested=float(row["invested"]),
                    pnl=float(row["pnl"]),
                    return_pct=float(row["return_pct"]),
                    holding_days=int(row["holding_days"]),
                    exit_reason=row["exit_reason"],
                    gap_size=float(gap_val) if gap_val else None,
                    company_name=row.get("company_name") or None,
                )
            )
    return trades


def _collect_ticker_periods(trades: List[TradeResult]) -> Dict[str, tuple]:
    """Collect date ranges needed per ticker for chart generation."""
    from collections import defaultdict

    ranges: Dict[str, List[datetime]] = defaultdict(list)
    for t in trades:
        entry_dt = datetime.strptime(t.entry_date, "%Y-%m-%d")
        exit_dt = datetime.strptime(t.exit_date, "%Y-%m-%d")
        # Add buffer for chart padding
        ranges[t.ticker].append(entry_dt - timedelta(days=20))
        ranges[t.ticker].append(exit_dt + timedelta(days=10))

    result = {}
    for ticker, dates in ranges.items():
        result[ticker] = (
            min(dates).strftime("%Y-%m-%d"),
            max(dates).strftime("%Y-%m-%d"),
        )
    return result


def main():
    """Standalone CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate trade chart PNGs from backtest CSV",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--trades-csv",
        required=True,
        help="Path to backtest trades CSV",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: same dir as CSV)",
    )
    parser.add_argument(
        "--stop-loss",
        type=float,
        default=10.0,
        help="Stop loss percentage for line display",
    )
    parser.add_argument(
        "--fmp-api-key",
        default=None,
        help="FMP API key (overrides env/config)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    _check_imports()

    csv_path = Path(args.trades_csv)
    if not csv_path.exists():
        logger.error("CSV not found: %s", csv_path)
        sys.exit(1)

    output_dir = args.output_dir or str(csv_path.parent)

    logger.info("Loading trades from %s", csv_path)
    trades = _load_trades_from_csv(str(csv_path))
    logger.info("Loaded %d trades", len(trades))

    logger.info("Fetching price data for %d tickers", len({t.ticker for t in trades}))
    from backtest.price_fetcher import PriceFetcher

    fetcher = PriceFetcher(api_key=args.fmp_api_key)
    ticker_periods = _collect_ticker_periods(trades)
    price_data = fetcher.bulk_fetch(ticker_periods)

    gen = ChartGenerator()
    gen.generate_all_charts(trades, price_data, output_dir, stop_loss_pct=args.stop_loss)


if __name__ == "__main__":
    main()
