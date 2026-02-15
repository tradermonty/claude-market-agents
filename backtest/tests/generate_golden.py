#!/usr/bin/env python3
"""Generate golden test fixture data.

Run: python -m backtest.tests.generate_golden
"""

import json
from pathlib import Path

from backtest.html_parser import TradeCandidate
from backtest.metrics_calculator import MetricsCalculator
from backtest.price_fetcher import PriceBar
from backtest.tests.fake_price_fetcher import FakePriceFetcher
from backtest.trade_simulator import TradeSimulator

FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOLDEN_DIR = FIXTURES_DIR / "golden"


def _load_mock_prices() -> dict:
    raw = json.loads((FIXTURES_DIR / "mock_prices.json").read_text())
    data = {}
    for ticker, records in raw.items():
        data[ticker] = [
            PriceBar(
                date=r["date"],
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                adj_close=r.get("adjClose"),
                volume=r["volume"],
            )
            for r in records
        ]
    return data


def _load_mock_candidates() -> list:
    raw = json.loads((FIXTURES_DIR / "mock_candidates.json").read_text())
    return [
        TradeCandidate(
            ticker=c["ticker"],
            report_date=c["report_date"],
            grade=c["grade"],
            grade_source=c["grade_source"],
            score=c.get("score"),
            price=c.get("price"),
            gap_size=c.get("gap_size"),
            company_name=c.get("company_name"),
        )
        for c in raw
    ]


def generate_stop_mode_comparison():
    """Generate golden data for all 4 stop modes."""
    prices = _load_mock_prices()
    candidates = _load_mock_candidates()
    fetcher = FakePriceFetcher(prices)
    price_data = fetcher.bulk_fetch({c.ticker: ("2025-09-25", "2026-02-28") for c in candidates})
    calculator = MetricsCalculator()

    results = {}
    for mode in ["intraday", "close", "skip_entry_day", "close_next_open"]:
        sim = TradeSimulator(
            position_size=10000.0,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            stop_mode=mode,
        )
        trades, skipped = sim.simulate_all(candidates, price_data)
        metrics = calculator.calculate(trades, skipped)
        results[mode] = {
            "total_trades": metrics.total_trades,
            "wins": metrics.wins,
            "losses": metrics.losses,
            "win_rate": metrics.win_rate,
            "total_pnl": metrics.total_pnl,
            "avg_return": metrics.avg_return,
            "profit_factor": metrics.profit_factor,
            "trade_sharpe": metrics.trade_sharpe,
            "stop_loss_total": metrics.stop_loss_total,
            "total_skipped": metrics.total_skipped,
        }

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    out_path = GOLDEN_DIR / "stop_mode_comparison.json"

    def _serialize(obj):
        if isinstance(obj, float) and obj == float("inf"):
            return "Infinity"
        return obj

    out_path.write_text(json.dumps(results, indent=2, default=_serialize))
    print(f"Written: {out_path}")
    return results


if __name__ == "__main__":
    generate_stop_mode_comparison()
    print("Golden data generation complete.")
