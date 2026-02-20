#!/usr/bin/env python3
"""Unit tests for portfolio simulator."""

from datetime import datetime, timedelta

import pytest

from backtest.html_parser import TradeCandidate
from backtest.portfolio_simulator import (
    PortfolioSimulator,
    PriceDateIndex,
)
from backtest.price_fetcher import PriceBar

# ─── Helpers ──────────────────────────────────────────────────────────────────


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


def make_bars(start_date, days, base_price=100.0, trend=0.5):
    """Generate daily bars with a steady trend."""
    bars = []
    dt = datetime.strptime(start_date, "%Y-%m-%d")
    price = base_price
    for i in range(days):
        d = (dt + timedelta(days=i)).strftime("%Y-%m-%d")
        bars.append(make_bar(d, price, price + 5, price - 2, price + 1))
        price += trend
    return bars


def make_sim(max_positions=5, **kwargs):
    defaults = {
        "max_positions": max_positions,
        "position_size": 10000,
        "stop_loss_pct": 10.0,
        "slippage_pct": 0.5,
        "max_holding_days": 90,
        "stop_mode": "intraday",
        "entry_mode": "next_day_open",
        "enable_rotation": True,
    }
    defaults.update(kwargs)
    return PortfolioSimulator(**defaults)


# ─── PriceDateIndex ──────────────────────────────────────────────────────────


class TestPriceDateIndex:
    def test_get_bar(self):
        bars = [make_bar("2025-10-01", 100, 105, 95, 100)]
        index = PriceDateIndex({"TEST": bars})
        assert index.get_bar("TEST", "2025-10-01") is not None
        assert index.get_bar("TEST", "2025-10-02") is None
        assert index.get_bar("MISSING", "2025-10-01") is None

    def test_all_trading_dates(self):
        bars_a = [make_bar("2025-10-01", 100, 105, 95, 100)]
        bars_b = [make_bar("2025-10-02", 50, 55, 45, 50)]
        index = PriceDateIndex({"A": bars_a, "B": bars_b})
        dates = index.all_trading_dates()
        assert dates == ["2025-10-01", "2025-10-02"]

    def test_get_previous_close(self):
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 102, 107, 97, 103),
        ]
        index = PriceDateIndex({"TEST": bars})
        assert index.get_previous_close("TEST", "2025-10-02") == 100
        assert index.get_previous_close("TEST", "2025-10-01") is None

    def test_get_bars_up_to(self):
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 102, 107, 97, 103),
            make_bar("2025-10-03", 104, 109, 99, 105),
        ]
        index = PriceDateIndex({"TEST": bars})
        up_to = index.get_bars_up_to("TEST", "2025-10-02")
        assert len(up_to) == 2
        assert up_to[-1].date == "2025-10-02"


# ─── Capacity Enforcement ────────────────────────────────────────────────────


class TestCapacityEnforcement:
    def test_max_positions_enforced(self):
        sim = make_sim(max_positions=1, enable_rotation=False)
        c1 = make_candidate("AAA", "2025-10-01", score=90)
        c2 = make_candidate("BBB", "2025-10-01", score=80)
        bars_a = make_bars("2025-10-01", 100)
        bars_b = make_bars("2025-10-01", 100)
        price_data = {"AAA": bars_a, "BBB": bars_b}

        _trades, skipped = sim.simulate_portfolio([c1, c2], price_data)

        capacity_skips = [s for s in skipped if s.skip_reason == "capacity_full"]
        assert len(capacity_skips) >= 1

    def test_position_freed_allows_new_entry(self):
        """After a position exits (stop loss), capacity opens for new entries."""
        sim = make_sim(max_positions=1, enable_rotation=False)
        c1 = make_candidate("STOP", "2025-10-01", score=90)
        c2 = make_candidate("LATE", "2025-10-10", score=85)

        bars_stop = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),  # entry
            make_bar("2025-10-03", 95, 96, 85, 88),  # stop loss (low=85 < 90)
            *make_bars("2025-10-04", 90),
        ]

        bars_late = make_bars("2025-10-01", 100)

        price_data = {"STOP": bars_stop, "LATE": bars_late}
        trades, _skipped = sim.simulate_portfolio([c1, c2], price_data)

        tickers_traded = {t.ticker for t in trades}
        assert "STOP" in tickers_traded
        assert "LATE" in tickers_traded

    def test_peak_positions_limited(self):
        sim = make_sim(max_positions=3, enable_rotation=False)
        candidates = [make_candidate(f"T{i}", "2025-10-01", score=90 - i) for i in range(10)]
        price_data = {f"T{i}": make_bars("2025-10-01", 100) for i in range(10)}

        trades, _skipped = sim.simulate_portfolio(candidates, price_data)

        from collections import defaultdict

        open_delta = defaultdict(int)
        for t in trades:
            open_delta[t.entry_date] += 1
            open_delta[t.exit_date] -= 1
        open_count = 0
        peak = 0
        for d in sorted(open_delta.keys()):
            open_count += open_delta[d]
            peak = max(peak, open_count)
        assert peak <= 3


# ─── Same-Ticker Policy ─────────────────────────────────────────────────────


class TestSameTickerPolicy:
    def test_duplicate_ticker_skipped(self):
        sim = make_sim(max_positions=5, enable_rotation=False)
        c1 = make_candidate("DUP", "2025-10-01", score=90)
        c2 = make_candidate("DUP", "2025-10-03", score=85)
        bars = make_bars("2025-10-01", 100)
        price_data = {"DUP": bars}

        _trades, skipped = sim.simulate_portfolio([c1, c2], price_data)

        dup_skips = [s for s in skipped if s.skip_reason == "duplicate_ticker"]
        assert len(dup_skips) >= 1

    def test_different_tickers_allowed(self):
        sim = make_sim(max_positions=5, enable_rotation=False)
        c1 = make_candidate("AAA", "2025-10-01", score=90)
        c2 = make_candidate("BBB", "2025-10-01", score=85)
        bars_a = make_bars("2025-10-01", 100)
        bars_b = make_bars("2025-10-01", 100)
        price_data = {"AAA": bars_a, "BBB": bars_b}

        trades, _skipped = sim.simulate_portfolio([c1, c2], price_data)

        tickers = {t.ticker for t in trades}
        assert "AAA" in tickers
        assert "BBB" in tickers


# ─── Rotation ────────────────────────────────────────────────────────────────


class TestRotation:
    def test_rotation_replaces_weakest_loser(self):
        sim = make_sim(max_positions=1)
        c1 = make_candidate("WEAK", "2025-10-01", score=60)
        c2 = make_candidate("STRONG", "2025-10-05", score=95)

        bars_weak = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 100),  # entry
            make_bar("2025-10-03", 98, 100, 95, 96),
            make_bar("2025-10-04", 96, 98, 93, 94),  # above stop (90)
            make_bar("2025-10-05", 94, 96, 92, 93),  # prev_close=94 -> negative
            make_bar("2025-10-06", 93, 95, 91, 92),  # STRONG entry day
            *make_bars("2025-10-07", 90, base_price=92),
        ]

        bars_strong = make_bars("2025-10-01", 100, base_price=50)

        price_data = {"WEAK": bars_weak, "STRONG": bars_strong}
        trades, _skipped = sim.simulate_portfolio([c1, c2], price_data)

        rotated = [t for t in trades if t.exit_reason == "rotated_out"]
        assert len(rotated) >= 1
        assert rotated[0].ticker == "WEAK"

    def test_no_rotation_when_disabled(self):
        sim = make_sim(max_positions=1, enable_rotation=False)
        c1 = make_candidate("WEAK", "2025-10-01", score=60)
        c2 = make_candidate("STRONG", "2025-10-05", score=95)

        bars_weak = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 100),
            make_bar("2025-10-03", 98, 100, 95, 96),
            make_bar("2025-10-04", 96, 98, 93, 94),
            make_bar("2025-10-05", 94, 96, 92, 93),
            make_bar("2025-10-06", 93, 95, 91, 92),
            *make_bars("2025-10-07", 90, base_price=92),
        ]

        bars_strong = make_bars("2025-10-01", 100, base_price=50)
        price_data = {"WEAK": bars_weak, "STRONG": bars_strong}

        trades, skipped = sim.simulate_portfolio([c1, c2], price_data)

        rotated = [t for t in trades if t.exit_reason == "rotated_out"]
        assert len(rotated) == 0

        cap_skips = [s for s in skipped if s.skip_reason == "capacity_full"]
        assert len(cap_skips) >= 1

    def test_rotation_only_on_negative_pnl(self):
        """Rotation should only happen when weakest has negative P&L."""
        sim = make_sim(max_positions=1)
        c1 = make_candidate("WINNER", "2025-10-01", score=60)
        c2 = make_candidate("NEW", "2025-10-05", score=95)

        bars_winner = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 100),  # entry
            make_bar("2025-10-03", 102, 105, 100, 104),
            make_bar("2025-10-04", 104, 108, 102, 106),
            make_bar("2025-10-05", 106, 110, 104, 108),  # prev_close=106 -> positive
            make_bar("2025-10-06", 108, 112, 106, 110),
            *make_bars("2025-10-07", 90, base_price=110),
        ]

        bars_new = make_bars("2025-10-01", 100, base_price=50)
        price_data = {"WINNER": bars_winner, "NEW": bars_new}

        trades, _skipped = sim.simulate_portfolio([c1, c2], price_data)

        rotated = [t for t in trades if t.exit_reason == "rotated_out"]
        assert len(rotated) == 0

    def test_rotation_cascade_prevention(self):
        """Only one rotation per day."""
        sim = make_sim(max_positions=1)
        c1 = make_candidate("WEAK", "2025-10-01", score=50)
        c2 = make_candidate("MID", "2025-10-05", score=70)
        c3 = make_candidate("TOP", "2025-10-05", score=90)

        bars_weak = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 100),
            make_bar("2025-10-03", 98, 100, 95, 96),
            make_bar("2025-10-04", 96, 98, 93, 94),
            make_bar("2025-10-05", 94, 96, 92, 93),
            make_bar("2025-10-06", 93, 95, 91, 92),
            *make_bars("2025-10-07", 90, base_price=92),
        ]

        bars_mid = make_bars("2025-10-01", 100, base_price=50)
        bars_top = make_bars("2025-10-01", 100, base_price=30)

        price_data = {"WEAK": bars_weak, "MID": bars_mid, "TOP": bars_top}
        trades, _skipped = sim.simulate_portfolio([c1, c2, c3], price_data)

        rotated = [t for t in trades if t.exit_reason == "rotated_out"]
        assert len(rotated) <= 1


# ─── Event Ordering ──────────────────────────────────────────────────────────


class TestEventOrdering:
    def test_pending_exit_before_new_entry(self):
        """Pending exit (Phase 1) executes before new entries (Phase 2)."""
        sim = make_sim(max_positions=1, enable_rotation=False, stop_mode="close_next_open")

        c1 = make_candidate("EXIT", "2025-10-01", score=80)
        c2 = make_candidate("ENTER", "2025-10-03", score=90)

        bars_exit = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 100),  # entry
            make_bar("2025-10-03", 98, 100, 95, 85),  # close=85 < stop=90 -> pending
            make_bar("2025-10-04", 84, 86, 82, 83),  # exits at open
            *make_bars("2025-10-05", 90, base_price=83),
        ]

        bars_enter = make_bars("2025-10-01", 100, base_price=50)
        price_data = {"EXIT": bars_exit, "ENTER": bars_enter}

        trades, _skipped = sim.simulate_portfolio([c1, c2], price_data)

        tickers = {t.ticker for t in trades}
        assert "EXIT" in tickers
        assert "ENTER" in tickers

    def test_data_end_date_closes_positions(self):
        sim = make_sim(max_positions=5, data_end_date="2025-10-10")
        c = make_candidate("TEST", "2025-10-01", score=85)
        bars = make_bars("2025-10-01", 100)
        price_data = {"TEST": bars}

        trades, _skipped = sim.simulate_portfolio([c], price_data)
        assert len(trades) == 1
        assert trades[0].exit_date <= "2025-10-10"


# ─── Trailing Stop in Portfolio ──────────────────────────────────────────────


class TestTrailingStopInPortfolio:
    def test_trailing_ema_works(self):
        sim = make_sim(
            max_positions=5,
            trailing_stop="weekly_ema",
            trailing_ema_period=3,
            trailing_transition_weeks=1,
            max_holding_days=None,
        )
        c = make_candidate("TEST", "2025-06-01", score=85)
        bars = make_bars("2025-05-01", 30, base_price=100, trend=1.0)
        drop_start = datetime(2025, 5, 31) + timedelta(days=60)
        for i in range(30):
            d = (drop_start + timedelta(days=i)).strftime("%Y-%m-%d")
            bars.append(make_bar(d, 80 - i, 82 - i, 78 - i, 79 - i))

        price_data = {"TEST": bars}
        trades, _ = sim.simulate_portfolio([c], price_data)

        assert len(trades) >= 1


# ─── Edge Cases ──────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_max_positions_one(self):
        sim = make_sim(max_positions=1, enable_rotation=False)
        c = make_candidate("SOLO", "2025-10-01", score=85)
        bars = make_bars("2025-10-01", 100)
        price_data = {"SOLO": bars}

        trades, _ = sim.simulate_portfolio([c], price_data)
        assert len(trades) == 1

    def test_no_candidates(self):
        sim = make_sim(max_positions=5)
        trades, skipped = sim.simulate_portfolio([], {})
        assert len(trades) == 0
        assert len(skipped) == 0

    def test_no_price_data(self):
        sim = make_sim(max_positions=5)
        c = make_candidate("NODATA", "2025-10-01")
        trades, _skipped = sim.simulate_portfolio([c], {})
        assert len(trades) == 0

    def test_all_positions_exit_same_day(self):
        sim = make_sim(max_positions=3, enable_rotation=False)
        candidates = [make_candidate(f"T{i}", "2025-10-01", score=90 - i) for i in range(3)]
        price_data = {}
        for i in range(3):
            price_data[f"T{i}"] = [
                make_bar("2025-10-01", 100, 105, 95, 100),
                make_bar("2025-10-02", 100, 105, 95, 100),  # entry
                make_bar("2025-10-03", 85, 86, 80, 82),  # stop loss
                *make_bars("2025-10-04", 50, base_price=82),
            ]

        trades, _ = sim.simulate_portfolio(candidates, price_data)
        stop_trades = [t for t in trades if t.exit_reason == "stop_loss"]
        assert len(stop_trades) == 3

    def test_missing_bar_for_ticker(self):
        """Position survives when its ticker has no bar on a given date."""
        sim = make_sim(max_positions=5)
        c = make_candidate("SPARSE", "2025-10-01", score=85)
        bars = [
            make_bar("2025-10-01", 100, 105, 95, 100),
            make_bar("2025-10-02", 100, 105, 95, 102),
            make_bar("2025-10-06", 103, 108, 98, 105),
            *make_bars("2025-10-07", 90, base_price=105),
        ]

        price_data = {"SPARSE": bars}
        trades, _ = sim.simulate_portfolio([c], price_data)
        assert len(trades) == 1

    def test_constructor_validation(self):
        with pytest.raises(ValueError, match="max_positions"):
            PortfolioSimulator(max_positions=0)

        with pytest.raises(ValueError, match="stop_mode"):
            PortfolioSimulator(max_positions=5, stop_mode="invalid")

        with pytest.raises(ValueError, match="trailing_stop"):
            PortfolioSimulator(max_positions=5, trailing_stop="invalid")

        with pytest.raises(ValueError, match="Cannot disable both"):
            PortfolioSimulator(max_positions=5, trailing_stop=None, max_holding_days=None)


# ─── Metrics Consistency ─────────────────────────────────────────────────────


class TestMetricsConsistency:
    def test_trade_result_fields(self):
        sim = make_sim(max_positions=5)
        c = make_candidate("FLD", "2025-10-01", score=85)
        bars = make_bars("2025-10-01", 100)
        price_data = {"FLD": bars}

        trades, _ = sim.simulate_portfolio([c], price_data)
        assert len(trades) == 1
        t = trades[0]

        assert t.ticker == "FLD"
        assert t.grade == "A"
        assert t.grade_source == "html"
        assert t.score == 85.0
        assert t.report_date == "2025-10-01"
        assert t.entry_date is not None
        assert t.exit_date is not None
        assert t.shares > 0
        assert t.invested > 0
        assert isinstance(t.pnl, float)
        assert isinstance(t.return_pct, float)
        assert t.holding_days >= 0
        assert t.exit_reason in (
            "stop_loss",
            "max_holding",
            "end_of_data",
            "trend_break",
            "rotated_out",
        )

    def test_skip_reasons(self):
        sim = make_sim(max_positions=1, enable_rotation=False)
        c1 = make_candidate("FIRST", "2025-10-01", score=90)
        c2 = make_candidate("DUP", "2025-10-03", score=80)
        c2_dup = make_candidate("FIRST", "2025-10-03", score=80)
        c3 = make_candidate("CAP", "2025-10-01", score=70)

        bars = make_bars("2025-10-01", 100)
        price_data = {
            "FIRST": bars,
            "DUP": bars,
            "CAP": make_bars("2025-10-01", 100),
        }

        _trades, skipped = sim.simulate_portfolio([c1, c2, c2_dup, c3], price_data)

        skip_reasons = {s.skip_reason for s in skipped}
        assert skip_reasons & {"capacity_full", "duplicate_ticker"}

    def test_compatible_with_metrics_calculator(self):
        """Results should be compatible with MetricsCalculator."""
        from backtest.metrics_calculator import MetricsCalculator

        sim = make_sim(max_positions=5)
        c = make_candidate("CALC", "2025-10-01", score=85)
        bars = make_bars("2025-10-01", 100)
        price_data = {"CALC": bars}

        trades, skipped = sim.simulate_portfolio([c], price_data)

        calc = MetricsCalculator()
        metrics = calc.calculate(trades, skipped, position_size=10000)
        assert metrics.total_trades >= 1
        assert metrics.win_rate >= 0
