#!/usr/bin/env python3
"""Unit tests for the trade simulator."""

import pytest
from backtest.trade_simulator import TradeSimulator, TradeResult, SkippedTrade
from backtest.price_fetcher import PriceBar
from backtest.html_parser import TradeCandidate


@pytest.fixture
def simulator():
    return TradeSimulator(
        position_size=10000,
        stop_loss_pct=10.0,
        slippage_pct=0.5,
        max_holding_days=90,
    )


def make_candidate(ticker="TEST", report_date="2025-10-01", grade="A", score=85.0):
    return TradeCandidate(
        ticker=ticker,
        report_date=report_date,
        grade=grade,
        grade_source="html",
        score=score,
    )


def make_bar(date, open_p, high, low, close, adj_close=None, volume=1000):
    return PriceBar(
        date=date,
        open=open_p,
        high=high,
        low=low,
        close=close,
        adj_close=adj_close if adj_close is not None else close,
        volume=volume,
    )


class TestNormalTrade:
    """Normal trade: entry -> 90 day hold -> exit at close."""

    def test_max_holding(self, simulator):
        # report_date = Oct 1, entry = Oct 2 (next trading day)
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),  # report day
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry day (open=100)
        ]
        # Add bars for 90+ calendar days
        from datetime import datetime, timedelta
        base = datetime(2025, 10, 3)
        for i in range(100):
            d = (base + timedelta(days=i)).strftime('%Y-%m-%d')
            # Price stays above stop (100 * 0.9 = 90)
            bars.append(make_bar(d, 102, 106, 95, 103))

        candidate = make_candidate(report_date="2025-10-01")
        price_data = {"TEST": bars}
        trades, skipped = simulator.simulate_all([candidate], price_data)

        assert len(trades) == 1
        assert len(skipped) == 0
        t = trades[0]
        assert t.entry_date == "2025-10-02"
        assert t.entry_price == 100.0
        assert t.exit_reason == "max_holding"
        assert t.holding_days >= 90
        assert t.shares == 100  # 10000 / 100 = 100


class TestStopLoss:
    """Stop loss triggers when low hits stop price."""

    def test_stop_loss(self, simulator):
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry at 100
            make_bar("2025-10-03", 101, 103, 99, 100),  # above stop (90)
            make_bar("2025-10-04", 95, 96, 85, 88),     # low 85 < stop 90
        ]
        candidate = make_candidate(report_date="2025-10-01")
        price_data = {"TEST": bars}
        trades, skipped = simulator.simulate_all([candidate], price_data)

        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "stop_loss"
        # Exit price = stop_price * (1 - slippage)
        # stop = 100 * 0.9 = 90, exit = 90 * 0.995 = 89.55
        assert abs(t.exit_price - 89.55) < 0.01
        assert t.pnl < 0


class TestEntryDayStop:
    """Stop loss on entry day itself."""

    def test_entry_day_stop(self, simulator):
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 100, 80, 85),  # entry at 100, low 80 < stop 90
        ]
        candidate = make_candidate(report_date="2025-10-01")
        price_data = {"TEST": bars}
        trades, skipped = simulator.simulate_all([candidate], price_data)

        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "stop_loss"
        assert t.entry_date == "2025-10-02"
        assert t.exit_date == "2025-10-02"


class TestEndOfData:
    """Data runs out before max holding period."""

    def test_end_of_data(self, simulator):
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),
            make_bar("2025-10-03", 101, 103, 95, 101),
            make_bar("2025-10-04", 101, 104, 95, 103),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        price_data = {"TEST": bars}
        trades, skipped = simulator.simulate_all([candidate], price_data)

        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "end_of_data"
        assert t.exit_date == "2025-10-04"


class TestNoPriceData:
    """No price data available -> SkippedTrade."""

    def test_no_data(self, simulator):
        candidate = make_candidate(report_date="2025-10-01")
        price_data = {}
        trades, skipped = simulator.simulate_all([candidate], price_data)

        assert len(trades) == 0
        assert len(skipped) == 1
        assert skipped[0].skip_reason == "no_price_data"


class TestZeroShares:
    """Stock too expensive for position size -> SkippedTrade."""

    def test_zero_shares(self, simulator):
        # Stock at $15000 per share, position size is $10000
        bars = [
            make_bar("2025-10-01", 15000, 15500, 14500, 15000),
            make_bar("2025-10-02", 15000, 15500, 14500, 15200),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        price_data = {"TEST": bars}
        trades, skipped = simulator.simulate_all([candidate], price_data)

        assert len(trades) == 0
        assert len(skipped) == 1
        assert skipped[0].skip_reason == "zero_shares"


class TestSplitAdjustment:
    """Stock split: adjClose != close should use adjustment factor."""

    def test_split_adjusted(self, simulator):
        # Simulating a 2:1 split: close = 200 but adjClose = 100
        bars = [
            make_bar("2025-10-01", 200, 210, 190, 200, adj_close=100),
            make_bar("2025-10-02", 200, 210, 190, 205, adj_close=102.5),
            make_bar("2025-10-03", 195, 200, 170, 175, adj_close=87.5),  # Low triggers stop
        ]
        candidate = make_candidate(report_date="2025-10-01")
        price_data = {"TEST": bars}
        trades, skipped = simulator.simulate_all([candidate], price_data)

        assert len(trades) == 1
        t = trades[0]
        # Entry at adjusted open of Oct 2: 200 * (102.5/205) = 100
        assert abs(t.entry_price - 100.0) < 0.1
        # Shares based on adjusted price
        assert t.shares == 100  # 10000 / 100
        # M-7: verify exit details
        assert t.exit_reason == "stop_loss"
        # adjusted_low on Oct 3: 170 * (87.5/175) = 85 < stop (100*0.9=90)
        assert t.exit_price > 0


class TestMultipleCandidates:
    """Multiple candidates with mixed outcomes."""

    def test_multiple(self, simulator):
        bars_a = [
            make_bar("2025-10-01", 50, 55, 48, 50),
            make_bar("2025-10-02", 50, 55, 48, 52),
            make_bar("2025-10-03", 52, 60, 51, 58),
        ]
        bars_b = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 100, 80, 85),  # stop loss
        ]

        candidates = [
            make_candidate("GOOD", "2025-10-01", "A", 90),
            make_candidate("BAD", "2025-10-01", "C", 60),
            make_candidate("MISSING", "2025-10-01", "D", 40),
        ]
        price_data = {"GOOD": bars_a, "BAD": bars_b}
        trades, skipped = simulator.simulate_all(candidates, price_data)

        assert len(trades) == 2  # GOOD and BAD
        assert len(skipped) == 1  # MISSING
        assert skipped[0].ticker == "MISSING"


class TestAdjCloseZero:
    """C-2/C-3: adj_close=0.0 should fallback to close (not use 0.0)."""

    def test_adj_close_zero_end_of_data(self, simulator):
        """adj_close=0.0 on last bar: exit falls back to close=100."""
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100, adj_close=100),
            make_bar("2025-10-02", 100, 105, 95, 102, adj_close=102),  # entry
            make_bar("2025-10-03", 101, 103, 95, 100, adj_close=100),
        ]
        # Last bar: adj_close=0.0, low=0 so stop check is skipped by low>0 guard
        bars.append(make_bar("2025-10-04", 101, 103, 0, 100, adj_close=0.0))

        candidate = make_candidate(report_date="2025-10-01")
        price_data = {"TEST": bars}
        trades, skipped = simulator.simulate_all([candidate], price_data)
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "end_of_data"
        # adj_close=0.0 → fallback to close=100
        assert t.exit_price == 100.0


class TestLowZeroNoFalseStop:
    """C-2: low=0 should not trigger false stop loss."""

    def test_low_zero_no_false_stop(self, simulator):
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry at 100
            make_bar("2025-10-03", 101, 103, 0, 100),    # low=0, should NOT stop
            make_bar("2025-10-04", 101, 104, 95, 103),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        price_data = {"TEST": bars}
        trades, skipped = simulator.simulate_all([candidate], price_data)
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason != "stop_loss"  # Should be end_of_data, not stop


class TestBreakevenNotLoss:
    """M-4: PnL=0 should not be counted as a loss."""

    def test_breakeven(self):
        from backtest.metrics_calculator import MetricsCalculator
        from backtest.trade_simulator import TradeResult
        calc = MetricsCalculator()

        trades = [
            TradeResult(
                ticker="TEST", grade="A", grade_source="html", score=85.0,
                report_date="2025-10-01", entry_date="2025-10-02",
                entry_price=100.0, exit_date="2025-10-10", exit_price=100.0,
                shares=100, invested=10000.0, pnl=0.0, return_pct=0.0,
                holding_days=8, exit_reason="end_of_data",
            ),
            TradeResult(
                ticker="WIN", grade="A", grade_source="html", score=90.0,
                report_date="2025-10-01", entry_date="2025-10-02",
                entry_price=100.0, exit_date="2025-10-10", exit_price=110.0,
                shares=100, invested=10000.0, pnl=1000.0, return_pct=10.0,
                holding_days=8, exit_reason="end_of_data",
            ),
        ]
        metrics = calc.calculate(trades, [])
        # PnL=0 trade is not a win, not a loss
        assert metrics.wins == 1
        assert metrics.losses == 0  # breakeven is NOT a loss


class TestAdjCloseZeroEntry:
    """C-1: adj_close=0.0 on entry bar → entry_price=0.0 → SkippedTrade."""

    def test_adj_close_zero_entry(self, simulator):
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100, adj_close=100),
            # Entry bar: close=100, adj_close=0.0 → adj_factor=0.0 → entry_price=0.0
            make_bar("2025-10-02", 100, 105, 95, 100, adj_close=0.0),
            make_bar("2025-10-03", 101, 103, 95, 100, adj_close=100),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        price_data = {"TEST": bars}
        trades, skipped = simulator.simulate_all([candidate], price_data)

        assert len(trades) == 0
        assert len(skipped) == 1
        assert skipped[0].skip_reason == "missing_ohlc"


class TestExitLogging:
    """M-5: Exit paths should emit debug log messages."""

    def test_stop_loss_logging(self, simulator, caplog):
        import logging
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry
            make_bar("2025-10-03", 95, 96, 85, 88),     # stop loss
        ]
        candidate = make_candidate(report_date="2025-10-01")
        price_data = {"TEST": bars}
        with caplog.at_level(logging.DEBUG, logger="backtest.trade_simulator"):
            simulator.simulate_all([candidate], price_data)
        assert any("stop_loss" in r.message for r in caplog.records)

    def test_end_of_data_logging(self, simulator, caplog):
        import logging
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),
            make_bar("2025-10-03", 101, 103, 95, 101),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        price_data = {"TEST": bars}
        with caplog.at_level(logging.DEBUG, logger="backtest.trade_simulator"):
            simulator.simulate_all([candidate], price_data)
        assert any("end_of_data" in r.message for r in caplog.records)

    def test_max_holding_logging(self, simulator, caplog):
        import logging
        from datetime import datetime, timedelta
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry
        ]
        base = datetime(2025, 10, 3)
        for i in range(100):
            d = (base + timedelta(days=i)).strftime('%Y-%m-%d')
            bars.append(make_bar(d, 102, 106, 95, 103))

        candidate = make_candidate(report_date="2025-10-01")
        price_data = {"TEST": bars}
        with caplog.at_level(logging.DEBUG, logger="backtest.trade_simulator"):
            simulator.simulate_all([candidate], price_data)
        assert any("max_holding" in r.message for r in caplog.records)
