#!/usr/bin/env python3
"""Golden (snapshot) tests for deterministic backtest results.

These tests verify that the simulator + calculator produce the exact same
results when given fixed mock data. If the golden files need updating
after an intentional change, re-run: python -m backtest.tests.generate_golden
"""

import json
import math
from pathlib import Path

import pytest

from backtest.html_parser import TradeCandidate
from backtest.metrics_calculator import MetricsCalculator
from backtest.price_fetcher import PriceBar
from backtest.tests.fake_price_fetcher import FakePriceFetcher
from backtest.trade_simulator import TradeSimulator

FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOLDEN_DIR = FIXTURES_DIR / "golden"


def _load_mock_prices():
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


def _load_mock_candidates():
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


def _parse_golden_value(val):
    """Handle Infinity in golden JSON."""
    if val == "Infinity":
        return float("inf")
    return val


class TestStopModeComparison:
    """Verify that 4 stop modes produce known-good metrics from fixed data."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.prices = _load_mock_prices()
        self.candidates = _load_mock_candidates()
        self.fetcher = FakePriceFetcher(self.prices)
        self.price_data = self.fetcher.bulk_fetch(
            {c.ticker: ("2025-09-25", "2026-02-28") for c in self.candidates}
        )
        self.calculator = MetricsCalculator()

        golden_path = GOLDEN_DIR / "stop_mode_comparison.json"
        # Parse JSON with Infinity support
        text = golden_path.read_text()
        text = text.replace("Infinity", '"Infinity"')
        self.golden = json.loads(text)

    @pytest.mark.parametrize(
        "stop_mode", ["intraday", "close", "skip_entry_day", "close_next_open"]
    )
    def test_stop_mode_metrics(self, stop_mode):
        sim = TradeSimulator(
            position_size=10000.0,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            stop_mode=stop_mode,
        )
        trades, skipped = sim.simulate_all(self.candidates, self.price_data)
        metrics = self.calculator.calculate(trades, skipped)

        expected = self.golden[stop_mode]
        assert metrics.total_trades == expected["total_trades"]
        assert metrics.wins == expected["wins"]
        assert metrics.losses == expected["losses"]
        assert metrics.win_rate == pytest.approx(expected["win_rate"], abs=0.01)
        assert metrics.total_pnl == pytest.approx(expected["total_pnl"], abs=0.01)
        assert metrics.avg_return == pytest.approx(expected["avg_return"], abs=0.01)

        expected_pf = _parse_golden_value(expected["profit_factor"])
        if math.isinf(expected_pf):
            assert math.isinf(metrics.profit_factor)
        else:
            assert metrics.profit_factor == pytest.approx(expected_pf, abs=0.01)

        assert metrics.trade_sharpe == pytest.approx(expected["trade_sharpe"], abs=0.01)
        assert metrics.stop_loss_total == expected["stop_loss_total"]
        assert metrics.total_skipped == expected["total_skipped"]

    def test_deterministic_across_runs(self):
        """Running the same simulation twice gives identical results."""
        sim = TradeSimulator(
            position_size=10000.0,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            stop_mode="intraday",
        )
        trades1, _ = sim.simulate_all(self.candidates, self.price_data)
        trades2, _ = sim.simulate_all(self.candidates, self.price_data)

        assert len(trades1) == len(trades2)
        for t1, t2 in zip(trades1, trades2):
            assert t1.ticker == t2.ticker
            assert t1.pnl == t2.pnl
            assert t1.return_pct == t2.return_pct
            assert t1.exit_reason == t2.exit_reason
