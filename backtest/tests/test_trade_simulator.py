#!/usr/bin/env python3
"""Unit tests for the trade simulator."""

import pytest

from backtest.html_parser import TradeCandidate
from backtest.price_fetcher import PriceBar
from backtest.trade_simulator import TradeResult, TradeSimulator


@pytest.fixture
def simulator():
    return TradeSimulator(
        position_size=10000,
        stop_loss_pct=10.0,
        slippage_pct=0.5,
        max_holding_days=90,
        entry_mode="next_day_open",
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
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
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
            make_bar("2025-10-04", 95, 96, 85, 88),  # low 85 < stop 90
        ]
        candidate = make_candidate(report_date="2025-10-01")
        price_data = {"TEST": bars}
        trades, _skipped = simulator.simulate_all([candidate], price_data)

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
        trades, _skipped = simulator.simulate_all([candidate], price_data)

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
        trades, _skipped = simulator.simulate_all([candidate], price_data)

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
        trades, _skipped = simulator.simulate_all([candidate], price_data)

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
        trades, _skipped = simulator.simulate_all([candidate], price_data)
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
            make_bar("2025-10-03", 101, 103, 0, 100),  # low=0, should NOT stop
            make_bar("2025-10-04", 101, 104, 95, 103),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        price_data = {"TEST": bars}
        trades, _skipped = simulator.simulate_all([candidate], price_data)
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason != "stop_loss"  # Should be end_of_data, not stop


class TestBreakevenNotLoss:
    """M-4: PnL=0 should not be counted as a loss."""

    def test_breakeven(self):
        from backtest.metrics_calculator import MetricsCalculator

        calc = MetricsCalculator()

        trades = [
            TradeResult(
                ticker="TEST",
                grade="A",
                grade_source="html",
                score=85.0,
                report_date="2025-10-01",
                entry_date="2025-10-02",
                entry_price=100.0,
                exit_date="2025-10-10",
                exit_price=100.0,
                shares=100,
                invested=10000.0,
                pnl=0.0,
                return_pct=0.0,
                holding_days=8,
                exit_reason="end_of_data",
            ),
            TradeResult(
                ticker="WIN",
                grade="A",
                grade_source="html",
                score=90.0,
                report_date="2025-10-01",
                entry_date="2025-10-02",
                entry_price=100.0,
                exit_date="2025-10-10",
                exit_price=110.0,
                shares=100,
                invested=10000.0,
                pnl=1000.0,
                return_pct=10.0,
                holding_days=8,
                exit_reason="end_of_data",
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
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            stop_mode="close",
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry at 100
            make_bar("2025-10-03", 95, 96, 85, 95),  # low=85 < stop=90, but close=95 > stop
            make_bar("2025-10-04", 96, 100, 94, 98),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        assert trades[0].exit_reason != "stop_loss"  # Close mode should NOT stop here

    def test_close_below_stop(self):
        """close <= stop → triggered, exit_price = close * (1 - slippage)."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            stop_mode="close",
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry at 100
            make_bar("2025-10-03", 95, 96, 80, 88),  # close=88 < stop=90
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
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            stop_mode="skip_entry_day",
            entry_mode="next_day_open",
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 100, 80, 95),  # entry day, low=80 < stop=90
            make_bar("2025-10-03", 96, 100, 94, 98),  # day 2, above stop
            make_bar("2025-10-04", 97, 101, 95, 100),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        # Should NOT be stopped on entry day
        assert trades[0].exit_reason != "stop_loss"

    def test_day2_stopped(self):
        """Day 2 low < stop → triggered normally."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            stop_mode="skip_entry_day",
            entry_mode="next_day_open",
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry day
            make_bar("2025-10-03", 95, 96, 85, 88),  # day 2, low=85 < stop=90
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
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            stop_mode="intraday",
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 100, 80, 85),  # entry day, low=80 < stop=90
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
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            daily_entry_limit=2,
        )
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
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            daily_entry_limit=1,
        )
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
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            daily_entry_limit=None,
        )
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
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            daily_entry_limit=1,
        )
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
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            stop_mode="close_next_open",
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),  # report day
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry at open=100
            make_bar("2025-10-03", 95, 96, 80, 88),  # close=88 < stop=90 → pending
            make_bar("2025-10-04", 92, 95, 90, 93),  # exit at open=92
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
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            stop_mode="close_next_open",
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry at 100
            make_bar("2025-10-03", 95, 96, 85, 95),  # low=85 < stop but close=95 > stop
            make_bar("2025-10-04", 96, 100, 94, 98),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        assert trades[0].exit_reason != "stop_loss"

    def test_data_ends_on_stop_day(self):
        """close < stop on last bar → fallback to close * (1 - slippage)."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            stop_mode="close_next_open",
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry at 100
            make_bar("2025-10-03", 95, 96, 80, 88),  # close=88 < stop=90, no next bar
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
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry at 100
            make_bar("2025-10-03", 95, 96, 80, 88),  # close=88 < stop=90
            make_bar("2025-10-04", 92, 95, 90, 93),  # next open=92
        ]
        candidate = make_candidate(report_date="2025-10-01")

        # close mode
        sim_close = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            stop_mode="close",
        )
        trades_close, _ = sim_close.simulate_all([candidate], {"TEST": bars[:]})

        # close_next_open mode
        sim_cno = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            stop_mode="close_next_open",
        )
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
            make_bar("2025-10-03", 95, 96, 85, 88),  # stop loss
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
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            bars.append(make_bar(d, 102, 106, 95, 103))

        candidate = make_candidate(report_date="2025-10-01")
        price_data = {"TEST": bars}
        with caplog.at_level(logging.DEBUG, logger="backtest.trade_simulator"):
            simulator.simulate_all([candidate], price_data)
        assert any("max_holding" in r.message for r in caplog.records)


class TestEntryModeReportOpen:
    """Entry mode 'report_open': enter at report_date open (or next available)."""

    def test_business_day_enters_same_day(self):
        """report_date is a trading day → entry on that day."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            entry_mode="report_open",
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 102),  # report day = entry
            make_bar("2025-10-02", 102, 106, 98, 104),
            make_bar("2025-10-03", 104, 108, 100, 106),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _skipped = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        assert trades[0].entry_date == "2025-10-01"
        assert trades[0].entry_price == 100.0

    def test_weekend_enters_next_monday(self):
        """report_date is Saturday → entry on Monday."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            entry_mode="report_open",
        )
        # 2025-10-04 is Saturday, 2025-10-06 is Monday
        bars = [
            make_bar("2025-10-03", 100, 105, 95, 102),
            make_bar("2025-10-06", 103, 107, 99, 105),  # Monday
            make_bar("2025-10-07", 105, 109, 101, 107),
        ]
        candidate = make_candidate(report_date="2025-10-04")
        trades, _skipped = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        assert trades[0].entry_date == "2025-10-06"

    def test_bar_gap_enters_next_available(self):
        """report_date has no bar → enters at next available bar."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            entry_mode="report_open",
        )
        bars = [
            make_bar("2025-09-30", 98, 102, 95, 100),
            # 2025-10-01 missing
            make_bar("2025-10-02", 101, 105, 98, 103),
            make_bar("2025-10-03", 103, 107, 100, 105),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _skipped = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        assert trades[0].entry_date == "2025-10-02"

    def test_no_bar_on_or_after(self):
        """No bars on or after report_date → skipped."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            entry_mode="report_open",
        )
        bars = [
            make_bar("2025-09-28", 98, 102, 95, 100),
            make_bar("2025-09-29", 99, 103, 96, 101),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, skipped = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 0
        assert len(skipped) == 1
        assert skipped[0].skip_reason == "no_price_data"


class TestEntryModeNextDayOpen:
    """Entry mode 'next_day_open': backward compatible, always enters after report_date."""

    def test_enters_next_day(self):
        """Even when report_date bar exists, enters the next day."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            entry_mode="next_day_open",
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 102),  # report day
            make_bar("2025-10-02", 102, 106, 98, 104),  # entry day
            make_bar("2025-10-03", 104, 108, 100, 106),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        assert trades[0].entry_date == "2025-10-02"


class TestEntryModeValidation:
    """Invalid entry_mode raises ValueError."""

    def test_invalid_entry_mode(self):
        with pytest.raises(ValueError, match="Invalid entry_mode"):
            TradeSimulator(entry_mode="invalid")

    def test_valid_modes_accepted(self):
        for mode in ("report_open", "next_day_open"):
            sim = TradeSimulator(entry_mode=mode)
            assert sim.entry_mode == mode


class TestDailyEntryLimitWithReportOpen:
    """daily_entry_limit works correctly with report_open mode."""

    def test_limit_groups_by_report_date_entry(self):
        """Two candidates with same report_date, limit=1 → top score wins."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            daily_entry_limit=1,
            entry_mode="report_open",
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 102),
            make_bar("2025-10-02", 102, 106, 98, 104),
            make_bar("2025-10-03", 104, 108, 100, 106),
        ]
        candidates = [
            make_candidate("AAA", "2025-10-01", "A", 90),
            make_candidate("BBB", "2025-10-01", "B", 70),
        ]
        price_data = {"AAA": bars[:], "BBB": bars[:]}
        trades, skipped = sim.simulate_all(candidates, price_data)

        assert len(trades) == 1
        assert trades[0].ticker == "AAA"
        # Both enter on 2025-10-01 (report_open), so they compete
        assert trades[0].entry_date == "2025-10-01"
        daily_skips = [s for s in skipped if s.skip_reason == "daily_limit"]
        assert len(daily_skips) == 1
        assert daily_skips[0].ticker == "BBB"


class TestTrailingStopEMA:
    """Trailing stop with weekly EMA."""

    @staticmethod
    def _make_weekly_bars(weeks, entry_date, report_date, sim):
        """Build bars for N weeks starting from Monday entry_date.

        weeks: list of (close_price, low_price) per week.
        Each week has Mon-Fri with simple price data.
        """
        from datetime import datetime, timedelta

        bars = []
        start = datetime.strptime(entry_date, "%Y-%m-%d")
        # Add report day (before entry)
        bars.append(make_bar(report_date, 100, 105, 95, 100))

        for week_idx, (week_close, week_low) in enumerate(weeks):
            monday = start + timedelta(weeks=week_idx)
            for day_offset in range(5):
                d = monday + timedelta(days=day_offset)
                ds = d.strftime("%Y-%m-%d")
                if day_offset == 4:
                    # Friday: use week_close and week_low
                    bars.append(make_bar(ds, week_close, week_close + 5, week_low, week_close))
                else:
                    bars.append(make_bar(ds, 100, 105, week_low, 100))
        return bars

    def test_trailing_ema_exits_on_trend_break(self):
        """EMA break after transition -> exit_reason='trend_break' at next open."""
        from datetime import datetime, timedelta

        # Build enough weekly data for 3-period EMA + transition
        # Entry: 2025-10-06 (Mon), report: 2025-10-03
        # Need: transition_weeks=3 completed weeks, then EMA break
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=50.0,  # Wide stop to avoid triggering
            slippage_pct=0.5,
            max_holding_days=None,
            trailing_stop="weekly_ema",
            trailing_ema_period=3,
            trailing_transition_weeks=3,
            entry_mode="next_day_open",
        )

        # Build bars: entry Mon 10/6, weeks of rising then crashing
        bars = [make_bar("2025-10-03", 100, 105, 95, 100)]  # report day

        base = datetime(2025, 10, 6)  # Monday
        # Weeks 1-3 (transition): rising prices
        for week_idx in range(3):
            for day_off in range(5):
                d = (base + timedelta(weeks=week_idx, days=day_off)).strftime("%Y-%m-%d")
                p = 100 + week_idx * 5
                bars.append(make_bar(d, p, p + 5, p - 3, p + 2))

        # Week 4: still above EMA (prices stay high)
        for day_off in range(5):
            d = (base + timedelta(weeks=3, days=day_off)).strftime("%Y-%m-%d")
            bars.append(make_bar(d, 115, 120, 112, 116))

        # Week 5: crash below EMA
        for day_off in range(5):
            d = (base + timedelta(weeks=4, days=day_off)).strftime("%Y-%m-%d")
            bars.append(make_bar(d, 80, 82, 75, 78))

        # Week 6: next week for exit execution
        for day_off in range(5):
            d = (base + timedelta(weeks=5, days=day_off)).strftime("%Y-%m-%d")
            bars.append(make_bar(d, 76, 80, 74, 77))

        candidate = make_candidate(report_date="2025-10-03")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "trend_break"
        assert t.entry_date == "2025-10-06"

    def test_trailing_holds_during_uptrend(self):
        """Price stays above EMA -> no trend_break, exits at end_of_data."""
        from datetime import datetime, timedelta

        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=50.0,
            slippage_pct=0.5,
            max_holding_days=None,
            trailing_stop="weekly_ema",
            trailing_ema_period=3,
            trailing_transition_weeks=2,
            entry_mode="next_day_open",
        )

        bars = [make_bar("2025-10-03", 100, 105, 95, 100)]
        base = datetime(2025, 10, 6)
        # 5 weeks of steadily rising prices
        for week_idx in range(5):
            for day_off in range(5):
                d = (base + timedelta(weeks=week_idx, days=day_off)).strftime("%Y-%m-%d")
                p = 100 + week_idx * 10
                bars.append(make_bar(d, p, p + 5, p - 3, p + 3))

        candidate = make_candidate(report_date="2025-10-03")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        assert trades[0].exit_reason == "end_of_data"

    def test_transition_period_uses_fixed_stop(self):
        """During transition weeks, trailing stop is inactive."""
        from datetime import datetime, timedelta

        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=50.0,
            slippage_pct=0.5,
            max_holding_days=None,
            trailing_stop="weekly_ema",
            trailing_ema_period=3,
            trailing_transition_weeks=3,
            entry_mode="next_day_open",
        )

        bars = [make_bar("2025-10-03", 100, 105, 95, 100)]
        base = datetime(2025, 10, 6)
        # Week 1: normal
        for day_off in range(5):
            d = (base + timedelta(days=day_off)).strftime("%Y-%m-%d")
            bars.append(make_bar(d, 100, 105, 97, 102))
        # Week 2: crash (would break EMA if transition was over)
        for day_off in range(5):
            d = (base + timedelta(weeks=1, days=day_off)).strftime("%Y-%m-%d")
            bars.append(make_bar(d, 60, 62, 55, 58))

        candidate = make_candidate(report_date="2025-10-03")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        # transition_weeks=3 not met (only 2 weeks total), so no trend_break
        assert trades[0].exit_reason == "end_of_data"


class TestTrailingWithDisableMaxHolding:
    """trailing_stop + max_holding=None."""

    def test_holds_until_trend_break(self):
        """max_holding=None -> holds until trend breaks (not 90 days)."""
        from datetime import datetime, timedelta

        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=50.0,
            slippage_pct=0.5,
            max_holding_days=None,
            trailing_stop="weekly_ema",
            trailing_ema_period=3,
            trailing_transition_weeks=2,
            entry_mode="next_day_open",
        )

        bars = [make_bar("2025-07-01", 100, 105, 95, 100)]
        base = datetime(2025, 7, 2)
        # 20 weeks (~140 days, well over 90): steadily rising
        for week_idx in range(20):
            for day_off in range(5):
                d = (base + timedelta(weeks=week_idx, days=day_off)).strftime("%Y-%m-%d")
                p = 100 + week_idx * 3
                bars.append(make_bar(d, p, p + 5, p - 3, p + 2))

        candidate = make_candidate(report_date="2025-07-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        t = trades[0]
        # Should NOT be max_holding (it's disabled)
        assert t.exit_reason == "end_of_data"
        assert t.holding_days > 90


class TestPendingExitPriority:
    """stop_loss pending takes priority over trend_break."""

    def test_stop_loss_priority(self):
        """Same day: close_next_open triggers stop + week_end triggers trend -> stop_loss wins."""
        from datetime import datetime, timedelta

        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=None,
            stop_mode="close_next_open",
            trailing_stop="weekly_ema",
            trailing_ema_period=3,
            trailing_transition_weeks=0,  # Immediate trailing
            entry_mode="next_day_open",
        )

        # Build bars where stop_loss and trend_break happen on same day
        bars = [make_bar("2025-10-03", 100, 105, 95, 100)]
        base = datetime(2025, 10, 6)

        # 4 weeks of data for EMA warmup
        for week_idx in range(4):
            for day_off in range(5):
                d = (base + timedelta(weeks=week_idx, days=day_off)).strftime("%Y-%m-%d")
                bars.append(make_bar(d, 100, 105, 97, 102))

        # Week 5 Friday: close below both stop and EMA
        for day_off in range(4):
            d = (base + timedelta(weeks=4, days=day_off)).strftime("%Y-%m-%d")
            bars.append(make_bar(d, 100, 105, 97, 102))
        fri = (base + timedelta(weeks=4, days=4)).strftime("%Y-%m-%d")
        # Close = 85 < stop (100*0.9=90)
        bars.append(make_bar(fri, 90, 92, 83, 85))

        # Next Monday for execution
        next_mon = (base + timedelta(weeks=5)).strftime("%Y-%m-%d")
        bars.append(make_bar(next_mon, 84, 86, 80, 83))

        candidate = make_candidate(report_date="2025-10-03")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        # stop_loss should win since it's checked before trailing
        assert trades[0].exit_reason == "stop_loss"


class TestDataEndDate:
    """data_end_date truncation and exit behavior."""

    def test_data_end_date_exits_at_close(self):
        """data_end_date on a trading day -> exit at that day's close."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            entry_mode="next_day_open",
            data_end_date="2025-10-04",
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry
            make_bar("2025-10-03", 101, 103, 95, 101),
            make_bar("2025-10-04", 101, 104, 95, 103),  # data_end_date
            make_bar("2025-10-05", 104, 108, 100, 106),  # should be excluded
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_date == "2025-10-04"
        assert t.exit_reason == "end_of_data"

    def test_data_end_date_weekend(self):
        """data_end_date on Saturday -> exits at Friday close."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            entry_mode="next_day_open",
            data_end_date="2025-10-11",  # Saturday
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),
            make_bar("2025-10-03", 101, 103, 95, 101),
            make_bar("2025-10-06", 102, 106, 98, 104),
            make_bar("2025-10-07", 104, 108, 100, 106),
            make_bar("2025-10-08", 106, 110, 102, 108),
            make_bar("2025-10-09", 108, 112, 104, 110),
            make_bar("2025-10-10", 110, 115, 108, 113),  # Friday (last before Saturday)
            make_bar("2025-10-13", 114, 118, 110, 116),  # Monday (excluded)
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        assert trades[0].exit_date == "2025-10-10"

    def test_data_end_date_before_entry(self):
        """data_end_date < entry_date -> skip."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            entry_mode="next_day_open",
            data_end_date="2025-09-30",  # Before report date
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, skipped = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 0
        assert len(skipped) == 1
        assert skipped[0].skip_reason == "no_price_data"

    def test_pending_stop_next_day_within_data(self):
        """Pending stop_loss -> exits at next day's open (within data range)."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            stop_mode="close_next_open",
            entry_mode="next_day_open",
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry at 100
            make_bar("2025-10-03", 95, 96, 80, 88),  # close=88 < stop=90 -> pending
            make_bar("2025-10-04", 92, 95, 90, 93),  # exit at open=92
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "stop_loss"
        assert t.exit_date == "2025-10-04"

    def test_pending_stop_last_bar_fallback(self):
        """Pending stop on last bar -> fallback to close * (1 - slippage)."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            stop_mode="close_next_open",
            entry_mode="next_day_open",
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry at 100
            make_bar("2025-10-03", 95, 96, 80, 88),  # close=88 < stop=90, no next bar
        ]
        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "stop_loss"
        assert t.exit_date == "2025-10-03"
        # fallback: adj_close=88 * (1 - 0.005) = 87.56
        assert abs(t.exit_price - 87.56) < 0.01


class TestNoTrailingBackwardCompatible:
    """trailing_stop=None preserves original behavior."""

    def test_backward_compatible(self):
        """No trailing stop -> identical to original simulator."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            entry_mode="next_day_open",
            trailing_stop=None,
        )
        from datetime import datetime, timedelta

        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),
        ]
        base = datetime(2025, 10, 3)
        for i in range(100):
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            bars.append(make_bar(d, 102, 106, 95, 103))

        candidate = make_candidate(report_date="2025-10-01")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "max_holding"
        assert t.holding_days >= 90


class TestTrailingStopNWeekLow:
    """Trailing stop with weekly N-week low mode."""

    def test_nweek_low_exits_on_trend_break(self):
        """Close below N-week low after transition -> exit_reason='trend_break'."""
        from datetime import datetime, timedelta

        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=50.0,  # Wide stop to avoid triggering
            slippage_pct=0.5,
            max_holding_days=None,
            trailing_stop="weekly_nweek_low",
            trailing_nweek_period=3,
            trailing_transition_weeks=2,
            entry_mode="next_day_open",
        )

        # Entry: Mon 2025-10-06, report: Fri 2025-10-03
        bars = [make_bar("2025-10-03", 100, 105, 95, 100)]
        base = datetime(2025, 10, 6)

        # Weeks 1-4: stable prices, low=97 each week
        for week_idx in range(4):
            for day_off in range(5):
                d = (base + timedelta(weeks=week_idx, days=day_off)).strftime("%Y-%m-%d")
                bars.append(make_bar(d, 100, 105, 97, 102))

        # Week 5: crash, close=90 < min(97,97,97)=97 -> trend_break
        for day_off in range(5):
            d = (base + timedelta(weeks=4, days=day_off)).strftime("%Y-%m-%d")
            bars.append(make_bar(d, 92, 95, 88, 90))

        # Week 6: next week for exit execution
        for day_off in range(5):
            d = (base + timedelta(weeks=5, days=day_off)).strftime("%Y-%m-%d")
            bars.append(make_bar(d, 89, 92, 86, 88))

        candidate = make_candidate(report_date="2025-10-03")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "trend_break"
        assert t.entry_date == "2025-10-06"

    def test_nweek_low_holds_above_support(self):
        """Close stays above N-week low -> no trend_break, exits end_of_data."""
        from datetime import datetime, timedelta

        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=50.0,
            slippage_pct=0.5,
            max_holding_days=None,
            trailing_stop="weekly_nweek_low",
            trailing_nweek_period=3,
            trailing_transition_weeks=2,
            entry_mode="next_day_open",
        )

        bars = [make_bar("2025-10-03", 100, 105, 95, 100)]
        base = datetime(2025, 10, 6)

        # 5 weeks: close always above weekly lows (rising trend)
        for week_idx in range(5):
            for day_off in range(5):
                d = (base + timedelta(weeks=week_idx, days=day_off)).strftime("%Y-%m-%d")
                p = 100 + week_idx * 5
                bars.append(make_bar(d, p, p + 5, p - 3, p + 2))

        candidate = make_candidate(report_date="2025-10-03")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        assert trades[0].exit_reason == "end_of_data"

    def test_nweek_low_warmup_no_signal(self):
        """During warmup (< nweek_period weeks), trailing stop does not fire."""
        from datetime import datetime, timedelta

        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=50.0,
            slippage_pct=0.5,
            max_holding_days=None,
            trailing_stop="weekly_nweek_low",
            trailing_nweek_period=4,
            trailing_transition_weeks=0,  # Immediate activation
            entry_mode="next_day_open",
        )

        bars = [make_bar("2025-10-03", 100, 105, 95, 100)]
        base = datetime(2025, 10, 6)

        # Week 1: normal
        for day_off in range(5):
            d = (base + timedelta(days=day_off)).strftime("%Y-%m-%d")
            bars.append(make_bar(d, 100, 105, 97, 102))

        # Week 2: drop below typical weekly low (but nweek_period=4, only 2 weeks,
        # indicators=None so trailing stop can't fire).
        # Prices must stay above stop_loss (100*0.5=50).
        for day_off in range(5):
            d = (base + timedelta(weeks=1, days=day_off)).strftime("%Y-%m-%d")
            bars.append(make_bar(d, 75, 78, 70, 72))

        candidate = make_candidate(report_date="2025-10-03")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        # Should be end_of_data because warmup not met (indicators are None)
        assert trades[0].exit_reason == "end_of_data"

    def test_nweek_low_transition_blocks_exit(self):
        """During transition period, nweek_low break does not trigger exit."""
        from datetime import datetime, timedelta

        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=50.0,
            slippage_pct=0.5,
            max_holding_days=None,
            trailing_stop="weekly_nweek_low",
            trailing_nweek_period=2,
            trailing_transition_weeks=5,  # Long transition
            entry_mode="next_day_open",
        )

        bars = [make_bar("2025-10-03", 100, 105, 95, 100)]
        base = datetime(2025, 10, 6)

        # Weeks 1-3: stable prices
        for week_idx in range(3):
            for day_off in range(5):
                d = (base + timedelta(weeks=week_idx, days=day_off)).strftime("%Y-%m-%d")
                bars.append(make_bar(d, 100, 105, 97, 102))

        # Week 4: drop below N-week low (but transition_weeks=5, only 4 completed).
        # Prices must stay above stop_loss (100*0.5=50).
        for day_off in range(5):
            d = (base + timedelta(weeks=3, days=day_off)).strftime("%Y-%m-%d")
            bars.append(make_bar(d, 75, 78, 70, 72))

        candidate = make_candidate(report_date="2025-10-03")
        trades, _ = sim.simulate_all([candidate], {"TEST": bars})
        assert len(trades) == 1
        assert trades[0].exit_reason == "end_of_data"


class TestDailyEntryLimitWithDataEndDate:
    """daily_entry_limit + data_end_date combination tests."""

    def test_cutoff_prevents_post_date_candidate_consuming_slot(self):
        """Candidates after data_end_date must not consume daily limit slots."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            daily_entry_limit=1,
            entry_mode="next_day_open",
            data_end_date="2025-10-05",
        )
        # AAA: report 10/01, entry 10/02 (within data_end_date)
        bars_a = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),
            make_bar("2025-10-03", 101, 103, 95, 101),
            make_bar("2025-10-04", 101, 104, 95, 103),
            make_bar("2025-10-05", 103, 106, 100, 105),
        ]
        # BBB: report 10/01, entry 10/02, higher score but bars extend past cutoff
        bars_b = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),
            make_bar("2025-10-03", 101, 103, 95, 101),
            make_bar("2025-10-04", 101, 104, 95, 103),
            make_bar("2025-10-05", 103, 106, 100, 105),
            make_bar("2025-10-06", 105, 108, 102, 107),  # past cutoff (truncated)
        ]
        candidates = [
            make_candidate("AAA", "2025-10-01", "A", 80),
            make_candidate("BBB", "2025-10-01", "A", 90),  # Higher score
        ]
        price_data = {"AAA": bars_a, "BBB": bars_b}
        trades, skipped = sim.simulate_all(candidates, price_data)

        # Both candidates have valid entry within data_end_date, limit=1 picks top score
        assert len(trades) == 1
        assert trades[0].ticker == "BBB"  # Higher score wins
        daily_skips = [s for s in skipped if s.skip_reason == "daily_limit"]
        assert len(daily_skips) == 1
        assert daily_skips[0].ticker == "AAA"

    def test_post_cutoff_candidate_skipped_not_counted(self):
        """Candidate whose entry is after data_end_date -> skipped, doesn't take slot."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            daily_entry_limit=1,
            entry_mode="next_day_open",
            data_end_date="2025-10-03",
        )
        # AAA: report 10/01, entry 10/02 (within data_end_date), lower score
        bars_a = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),
            make_bar("2025-10-03", 101, 103, 95, 101),
        ]
        # BBB: report 10/04, entry 10/05 -> after data_end_date, all bars truncated
        bars_b = [
            make_bar("2025-10-04", 100, 105, 95, 100),
            make_bar("2025-10-05", 100, 105, 95, 102),
        ]
        candidates = [
            make_candidate("AAA", "2025-10-01", "A", 70),
            make_candidate("BBB", "2025-10-04", "A", 95),  # Higher score but post-cutoff
        ]
        price_data = {"AAA": bars_a, "BBB": bars_b}
        trades, skipped = sim.simulate_all(candidates, price_data)

        # BBB should be skipped (no bars after truncation), AAA should trade
        assert len(trades) == 1
        assert trades[0].ticker == "AAA"
        no_data_skips = [s for s in skipped if s.skip_reason == "no_price_data"]
        assert len(no_data_skips) == 1
        assert no_data_skips[0].ticker == "BBB"

    def test_same_day_limit_with_truncation(self):
        """Two same-day candidates, data_end_date limits bars, limit=1 works correctly."""
        sim = TradeSimulator(
            position_size=10000,
            stop_loss_pct=10.0,
            slippage_pct=0.5,
            max_holding_days=90,
            daily_entry_limit=1,
            entry_mode="next_day_open",
            data_end_date="2025-10-10",
        )
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),
            make_bar("2025-10-03", 101, 103, 95, 101),
            make_bar("2025-10-10", 103, 106, 100, 105),
            make_bar("2025-10-15", 105, 108, 102, 107),  # truncated
        ]
        candidates = [
            make_candidate("AAA", "2025-10-01", "A", 85),
            make_candidate("BBB", "2025-10-01", "A", 75),
        ]
        price_data = {"AAA": bars[:], "BBB": bars[:]}
        trades, _skipped = sim.simulate_all(candidates, price_data)

        assert len(trades) == 1
        assert trades[0].ticker == "AAA"
        # trade exits at data_end_date boundary
        assert trades[0].exit_date <= "2025-10-10"


class TestDisableMaxHoldingWithoutTrailing:
    """max_holding=None without trailing -> ValueError."""

    def test_raises(self):
        with pytest.raises(ValueError, match="Cannot disable both"):
            TradeSimulator(max_holding_days=None, trailing_stop=None)
