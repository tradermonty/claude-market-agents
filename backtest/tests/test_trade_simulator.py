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


class TestStopModeClose:
    """Stop mode 'close': only trigger stop when close <= stop_price."""

    def test_low_below_stop_but_close_above(self):
        """low < stop but close > stop → NOT triggered (intraday would trigger)."""
        sim = TradeSimulator(position_size=10000, stop_loss_pct=10.0, slippage_pct=0.5,
                             max_holding_days=90, stop_mode="close")
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry at 100
            make_bar("2025-10-03", 95, 96, 85, 95),     # low=85 < stop=90, but close=95 > stop
            make_bar("2025-10-04", 96, 100, 94, 98),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        assert trades[0].exit_reason != "stop_loss"  # Close mode should NOT stop here

    def test_close_below_stop(self):
        """close <= stop → triggered, exit_price = close * (1 - slippage)."""
        sim = TradeSimulator(position_size=10000, stop_loss_pct=10.0, slippage_pct=0.5,
                             max_holding_days=90, stop_mode="close")
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry at 100
            make_bar("2025-10-03", 95, 96, 80, 88),     # close=88 < stop=90
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "stop_loss"
        # exit_price = adj_close * (1 - 0.005) = 88 * 0.995 = 87.56
        assert abs(t.exit_price - 87.56) < 0.01


class TestStopModeSkipEntryDay:
    """Stop mode 'skip_entry_day': don't check stop on entry day (idx=0)."""

    def test_entry_day_not_stopped(self):
        """Entry day low < stop → NOT triggered."""
        sim = TradeSimulator(position_size=10000, stop_loss_pct=10.0, slippage_pct=0.5,
                             max_holding_days=90, stop_mode="skip_entry_day")
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 100, 80, 95),   # entry day, low=80 < stop=90
            make_bar("2025-10-03", 96, 100, 94, 98),    # day 2, above stop
            make_bar("2025-10-04", 97, 101, 95, 100),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        # Should NOT be stopped on entry day
        assert trades[0].exit_reason != "stop_loss"

    def test_day2_stopped(self):
        """Day 2 low < stop → triggered normally."""
        sim = TradeSimulator(position_size=10000, stop_loss_pct=10.0, slippage_pct=0.5,
                             max_holding_days=90, stop_mode="skip_entry_day")
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry day
            make_bar("2025-10-03", 95, 96, 85, 88),     # day 2, low=85 < stop=90
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "stop_loss"
        assert abs(t.exit_price - 89.55) < 0.01


class TestStopModeIntraday:
    """Confirm existing intraday mode (default) backward compatibility."""

    def test_intraday_entry_day_stop(self):
        """Intraday mode: entry day low < stop → triggered (unlike skip_entry_day)."""
        sim = TradeSimulator(position_size=10000, stop_loss_pct=10.0, slippage_pct=0.5,
                             max_holding_days=90, stop_mode="intraday")
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 100, 80, 85),   # entry day, low=80 < stop=90
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        assert trades[0].exit_reason == "stop_loss"

    def test_invalid_stop_mode(self):
        """Invalid stop_mode raises ValueError."""
        with pytest.raises(ValueError, match="Invalid stop_mode"):
            TradeSimulator(stop_mode="invalid")


class TestDailyEntryLimit:
    """Daily entry limit: only top-N candidates per entry date."""

    def test_limit_filters_by_score(self):
        """3 candidates same day, limit=2 → top 2 by score, 1 skipped."""
        sim = TradeSimulator(position_size=10000, stop_loss_pct=10.0, slippage_pct=0.5,
                             max_holding_days=90, daily_entry_limit=2)
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),
            make_bar("2025-10-03", 101, 103, 95, 101),
            make_bar("2025-10-04", 101, 104, 95, 103),
        ]
        candidates = [
            make_candidate("AAA", "2025-10-01", "A", 90),
            make_candidate("BBB", "2025-10-01", "B", 80),
            make_candidate("CCC", "2025-10-01", "C", 70),
        ]
        price_data = {"AAA": bars[:], "BBB": bars[:], "CCC": bars[:]}
        trades, skipped = sim.simulate_all(candidates, price_data)

        assert len(trades) == 2
        tickers_traded = {t.ticker for t in trades}
        assert "AAA" in tickers_traded
        assert "BBB" in tickers_traded
        assert "CCC" not in tickers_traded

        daily_limit_skips = [s for s in skipped if s.skip_reason == "daily_limit"]
        assert len(daily_limit_skips) == 1
        assert daily_limit_skips[0].ticker == "CCC"

    def test_different_days_not_limited(self):
        """Candidates on different days are independently limited."""
        sim = TradeSimulator(position_size=10000, stop_loss_pct=10.0, slippage_pct=0.5,
                             max_holding_days=90, daily_entry_limit=1)
        bars_day1 = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),
            make_bar("2025-10-03", 101, 103, 95, 101),
        ]
        bars_day2 = [
            make_bar("2025-10-02", 100, 105, 95, 100),
            make_bar("2025-10-03", 100, 105, 95, 102),
            make_bar("2025-10-04", 101, 103, 95, 101),
        ]
        candidates = [
            make_candidate("AAA", "2025-10-01", "A", 90),
            make_candidate("BBB", "2025-10-02", "A", 85),
        ]
        price_data = {"AAA": bars_day1, "BBB": bars_day2}
        trades, skipped = sim.simulate_all(candidates, price_data)

        assert len(trades) == 2
        assert len([s for s in skipped if s.skip_reason == "daily_limit"]) == 0

    def test_no_limit(self):
        """limit=None → no filtering."""
        sim = TradeSimulator(position_size=10000, stop_loss_pct=10.0, slippage_pct=0.5,
                             max_holding_days=90, daily_entry_limit=None)
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),
            make_bar("2025-10-03", 101, 103, 95, 101),
        ]
        candidates = [
            make_candidate("AAA", "2025-10-01", "A", 90),
            make_candidate("BBB", "2025-10-01", "B", 80),
            make_candidate("CCC", "2025-10-01", "C", 70),
        ]
        price_data = {"AAA": bars[:], "BBB": bars[:], "CCC": bars[:]}
        trades, skipped = sim.simulate_all(candidates, price_data)

        assert len(trades) == 3
        assert len([s for s in skipped if s.skip_reason == "daily_limit"]) == 0

    def test_none_score_ranked_last(self):
        """score=None candidates are ranked last (below any scored candidate)."""
        sim = TradeSimulator(position_size=10000, stop_loss_pct=10.0, slippage_pct=0.5,
                             max_holding_days=90, daily_entry_limit=1)
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),
            make_bar("2025-10-03", 101, 103, 95, 101),
        ]
        candidates = [
            make_candidate("AAA", "2025-10-01", "A", None),
            make_candidate("BBB", "2025-10-01", "B", 50),
        ]
        price_data = {"AAA": bars[:], "BBB": bars[:]}
        trades, skipped = sim.simulate_all(candidates, price_data)

        assert len(trades) == 1
        assert trades[0].ticker == "BBB"  # scored candidate wins over None
        daily_skips = [s for s in skipped if s.skip_reason == "daily_limit"]
        assert len(daily_skips) == 1
        assert daily_skips[0].ticker == "AAA"


class TestStopModeCloseNextOpen:
    """Stop mode 'close_next_open': close triggers stop, execute at next day's open."""

    def test_close_below_stop_exits_next_open(self):
        """close < stop on day 2 → exit at day 3 open * (1 - slippage)."""
        sim = TradeSimulator(position_size=10000, stop_loss_pct=10.0, slippage_pct=0.5,
                             max_holding_days=90, stop_mode="close_next_open")
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),      # report day
            make_bar("2025-10-02", 100, 105, 95, 102),       # entry at open=100
            make_bar("2025-10-03", 95, 96, 80, 88),          # close=88 < stop=90 → pending
            make_bar("2025-10-04", 92, 95, 90, 93),          # exit at open=92
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "stop_loss"
        assert t.exit_date == "2025-10-04"
        # exit_price = 92 * adj_factor(93/93=1.0) * (1 - 0.005) = 92 * 0.995 = 91.54
        assert abs(t.exit_price - 91.54) < 0.01

    def test_close_above_stop_no_trigger(self):
        """close > stop → NOT triggered (same as close mode)."""
        sim = TradeSimulator(position_size=10000, stop_loss_pct=10.0, slippage_pct=0.5,
                             max_holding_days=90, stop_mode="close_next_open")
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),       # entry at 100
            make_bar("2025-10-03", 95, 96, 85, 95),          # low=85 < stop but close=95 > stop
            make_bar("2025-10-04", 96, 100, 94, 98),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        assert trades[0].exit_reason != "stop_loss"

    def test_data_ends_on_stop_day(self):
        """close < stop on last bar → fallback to close * (1 - slippage)."""
        sim = TradeSimulator(position_size=10000, stop_loss_pct=10.0, slippage_pct=0.5,
                             max_holding_days=90, stop_mode="close_next_open")
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),       # entry at 100
            make_bar("2025-10-03", 95, 96, 80, 88),          # close=88 < stop=90, no next bar
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "stop_loss"
        assert t.exit_date == "2025-10-03"
        # fallback: adj_close=88 * (1 - 0.005) = 87.56
        assert abs(t.exit_price - 87.56) < 0.01

    def test_vs_close_mode_different_exit_price(self):
        """Same scenario: close exits at close, close_next_open exits at next open."""
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),       # entry at 100
            make_bar("2025-10-03", 95, 96, 80, 88),          # close=88 < stop=90
            make_bar("2025-10-04", 92, 95, 90, 93),          # next open=92
        ]
        candidate = make_candidate(report_date="2025-10-01")

        # close mode
        sim_close = TradeSimulator(position_size=10000, stop_loss_pct=10.0, slippage_pct=0.5,
                                   max_holding_days=90, stop_mode="close")
        trades_close, _ = sim_close.simulate_all([candidate], {"TEST": bars[:]})

        # close_next_open mode
        sim_cno = TradeSimulator(position_size=10000, stop_loss_pct=10.0, slippage_pct=0.5,
                                 max_holding_days=90, stop_mode="close_next_open")
        trades_cno, _ = sim_cno.simulate_all([candidate], {"TEST": bars[:]})

        assert len(trades_close) == 1
        assert len(trades_cno) == 1

        tc = trades_close[0]
        tn = trades_cno[0]

        # close mode exits on day3 close, close_next_open exits on day4 open
        assert tc.exit_date == "2025-10-03"
        assert tn.exit_date == "2025-10-04"

        # close: 88 * 0.995 = 87.56, close_next_open: 92 * 0.995 = 91.54
        assert abs(tc.exit_price - 87.56) < 0.01
        assert abs(tn.exit_price - 91.54) < 0.01
        assert tn.exit_price != tc.exit_price


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
