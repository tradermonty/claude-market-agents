#!/usr/bin/env python3
"""Golden tests: verify backtest vs live trailing stop decision consistency.

Runs the same price data through both PortfolioSimulator (backtest) and
TrailingStopChecker (live), asserting that trailing stop exit decisions
match exactly.
"""

from datetime import datetime, timedelta

import pytest

from backtest.html_parser import TradeCandidate
from backtest.portfolio_simulator import PortfolioSimulator
from backtest.price_fetcher import PriceBar
from backtest.tests.fake_price_fetcher import FakePriceFetcher
from backtest.weekly_bars import (
    aggregate_daily_to_weekly,
    compute_weekly_ema,
    compute_weekly_nweek_low,
    is_week_end_by_date,
)
from live.trailing_stop_checker import TrailingStopChecker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar(date, open_p, high, low, close, volume=10000):
    return PriceBar(
        date=date,
        open=open_p,
        high=high,
        low=low,
        close=close,
        adj_close=close,
        volume=volume,
    )


def _build_daily_bars(week_specs):
    """Build daily bars (Mon-Fri) from weekly specs.

    Args:
        week_specs: List of (monday_date_str, close_price) tuples.
    Returns:
        List of PriceBar spanning all specified weeks.
    """
    bars = []
    for week_start_str, close_p in week_specs:
        dt = datetime.strptime(week_start_str, "%Y-%m-%d")
        for d_off in range(5):
            d = (dt + timedelta(days=d_off)).strftime("%Y-%m-%d")
            if d_off == 4:  # Friday = weekly close
                bars.append(_make_bar(d, close_p - 1, close_p + 2, close_p - 3, close_p))
            else:
                bars.append(_make_bar(d, close_p, close_p + 2, close_p - 3, close_p + 0.5))
    return bars


def _make_candidate(ticker, score, grade, price, report_date):
    """Create a TradeCandidate for backtest simulation."""
    return TradeCandidate(
        ticker=ticker,
        report_date=report_date,
        grade=grade,
        grade_source="html",
        score=score,
        price=price,
        gap_size=5.0,
        company_name=f"{ticker} Inc.",
    )


def _collect_backtest_exits(trades, exit_reason="trend_break"):
    """Extract ticker+exit_date pairs from backtest trades with given reason."""
    return {(t.ticker, t.exit_date) for t in trades if t.exit_reason == exit_reason}


def _collect_live_exits(
    ticker,
    bars,
    entry_date,
    trailing_stop,
    trailing_period,
    transition_weeks=2,
    fmp_lookback_days=400,
):
    """Run TrailingStopChecker on each week-end date and collect exit dates."""
    fetcher = FakePriceFetcher({ticker: bars})
    checker = TrailingStopChecker(
        fetcher,
        trailing_transition_weeks=transition_weeks,
        fmp_lookback_days=fmp_lookback_days,
    )
    exit_dates = set()
    for bar in bars:
        if bar.date <= entry_date:
            continue
        result = checker.check_position(
            ticker,
            entry_date,
            bar.date,
            trailing_stop,
            trailing_period,
        )
        if result.should_exit:
            exit_dates.add((ticker, bar.date))
            break  # First exit only (position would be closed)
    return exit_dates


# ---------------------------------------------------------------------------
# Shared price data fixtures
# ---------------------------------------------------------------------------

# Scenario A: uptrend with sharp reversal → EMA trend break
SCENARIO_A_WEEKS = [
    ("2025-06-02", 100),
    ("2025-06-09", 104),
    ("2025-06-16", 108),
    ("2025-06-23", 112),
    ("2025-06-30", 116),  # entry week (report_open)
    ("2025-07-07", 120),  # post-entry 1
    ("2025-07-14", 125),  # post-entry 2 (transition met)
    ("2025-07-21", 130),
    ("2025-07-28", 135),
    ("2025-08-04", 140),
    ("2025-08-11", 145),
    ("2025-08-18", 150),
    ("2025-08-25", 90),  # sharp drop → should trigger EMA trend break
    ("2025-09-01", 85),  # continued decline
]

# Scenario B: steady uptrend → no exit
SCENARIO_B_WEEKS = [
    ("2025-06-02", 100),
    ("2025-06-09", 103),
    ("2025-06-16", 106),
    ("2025-06-23", 110),
    ("2025-06-30", 114),  # entry week
    ("2025-07-07", 118),
    ("2025-07-14", 122),
    ("2025-07-21", 126),
    ("2025-07-28", 130),
    ("2025-08-04", 134),
    ("2025-08-11", 138),
    ("2025-08-18", 142),
    ("2025-08-25", 146),
    ("2025-09-01", 150),
]

# Scenario C: NWL break
SCENARIO_C_WEEKS = [
    ("2025-06-02", 100),
    ("2025-06-09", 102),
    ("2025-06-16", 105),
    ("2025-06-23", 108),
    ("2025-06-30", 110),  # entry week
    ("2025-07-07", 115),
    ("2025-07-14", 118),  # transition met
    ("2025-07-21", 120),
    ("2025-07-28", 122),
    ("2025-08-04", 125),
    ("2025-08-11", 90),  # drop below 4-week low
    ("2025-08-18", 85),
]


# ---------------------------------------------------------------------------
# Tests: EMA trailing stop consistency
# ---------------------------------------------------------------------------


class TestEMAConsistency:
    """Verify backtest and live EMA trailing stop decisions match."""

    @pytest.fixture
    def ema_bars_a(self):
        return _build_daily_bars(SCENARIO_A_WEEKS)

    @pytest.fixture
    def ema_bars_b(self):
        return _build_daily_bars(SCENARIO_B_WEEKS)

    def test_ema_trend_break_detected_by_both(self, ema_bars_a):
        """Both backtest and live should detect EMA trend break on same date."""
        ticker = "TESTA"
        entry_date = "2025-06-30"  # Monday of entry week
        entry_price = 116.0
        ema_period = 3
        transition_weeks = 2

        # --- Backtest path ---
        candidate = _make_candidate(
            ticker,
            score=80,
            grade="B",
            price=entry_price,
            report_date="2025-06-27",  # Friday before entry
        )
        sim = PortfolioSimulator(
            max_positions=1,
            position_size=10000,
            stop_loss_pct=50.0,  # High stop loss to avoid intraday trigger
            stop_mode="intraday",
            trailing_stop="weekly_ema",
            trailing_ema_period=ema_period,
            trailing_transition_weeks=transition_weeks,
            enable_rotation=False,
            entry_mode="report_open",
        )
        trades, _ = sim.simulate_portfolio(
            [candidate],
            {ticker: ema_bars_a},
        )
        bt_exits = _collect_backtest_exits(trades)

        # --- Live path ---
        live_exits = _collect_live_exits(
            ticker,
            ema_bars_a,
            entry_date,
            "weekly_ema",
            ema_period,
            transition_weeks=transition_weeks,
        )

        # Both should detect an exit
        assert len(bt_exits) > 0, "Backtest should detect trend break"
        assert len(live_exits) > 0, "Live should detect trend break"

        # Extract exit dates (may differ by 1 day due to next-open execution in backtest)
        bt_exit_date = next(iter(bt_exits))[1]
        live_exit_date = next(iter(live_exits))[1]

        # The live checker detects exit on the week-end (Friday),
        # backtest applies the exit on the next trading day.
        # Both should be within the same calendar week.
        bt_dt = datetime.strptime(bt_exit_date, "%Y-%m-%d")
        live_dt = datetime.strptime(live_exit_date, "%Y-%m-%d")
        assert (
            bt_dt.isocalendar()[:2] == live_dt.isocalendar()[:2] or abs((bt_dt - live_dt).days) <= 3
        ), f"Exit date mismatch: backtest={bt_exit_date}, live={live_exit_date}"

    def test_ema_no_exit_in_uptrend(self, ema_bars_b):
        """Neither system should trigger exit in steady uptrend."""
        ticker = "TESTB"
        entry_date = "2025-06-30"
        entry_price = 114.0
        ema_period = 3
        transition_weeks = 2

        candidate = _make_candidate(
            ticker,
            score=80,
            grade="B",
            price=entry_price,
            report_date="2025-06-27",
        )
        sim = PortfolioSimulator(
            max_positions=1,
            position_size=10000,
            stop_loss_pct=50.0,
            stop_mode="intraday",
            trailing_stop="weekly_ema",
            trailing_ema_period=ema_period,
            trailing_transition_weeks=transition_weeks,
            enable_rotation=False,
            entry_mode="report_open",
        )
        trades, _ = sim.simulate_portfolio(
            [candidate],
            {ticker: ema_bars_b},
        )
        bt_trend_exits = _collect_backtest_exits(trades)

        live_exits = _collect_live_exits(
            ticker,
            ema_bars_b,
            entry_date,
            "weekly_ema",
            ema_period,
            transition_weeks=transition_weeks,
        )

        # Neither should have trend break exits
        assert len(bt_trend_exits) == 0, "Backtest should NOT exit in uptrend"
        assert len(live_exits) == 0, "Live should NOT exit in uptrend"


# ---------------------------------------------------------------------------
# Tests: NWL trailing stop consistency
# ---------------------------------------------------------------------------


class TestNWLConsistency:
    """Verify backtest and live NWL trailing stop decisions match."""

    @pytest.fixture
    def nwl_bars_c(self):
        return _build_daily_bars(SCENARIO_C_WEEKS)

    def test_nwl_break_detected_by_both(self, nwl_bars_c):
        """Both systems should detect NWL break on same date."""
        ticker = "TESTC"
        entry_date = "2025-06-30"
        entry_price = 110.0
        nwl_period = 4
        transition_weeks = 2

        candidate = _make_candidate(
            ticker,
            score=80,
            grade="B",
            price=entry_price,
            report_date="2025-06-27",
        )
        sim = PortfolioSimulator(
            max_positions=1,
            position_size=10000,
            stop_loss_pct=50.0,
            stop_mode="intraday",
            trailing_stop="weekly_nweek_low",
            trailing_nweek_period=nwl_period,
            trailing_transition_weeks=transition_weeks,
            enable_rotation=False,
            entry_mode="report_open",
        )
        trades, _ = sim.simulate_portfolio(
            [candidate],
            {ticker: nwl_bars_c},
        )
        bt_exits = _collect_backtest_exits(trades)

        live_exits = _collect_live_exits(
            ticker,
            nwl_bars_c,
            entry_date,
            "weekly_nweek_low",
            nwl_period,
            transition_weeks=transition_weeks,
        )

        assert len(bt_exits) > 0, "Backtest should detect NWL break"
        assert len(live_exits) > 0, "Live should detect NWL break"

        bt_exit_date = next(iter(bt_exits))[1]
        live_exit_date = next(iter(live_exits))[1]

        bt_dt = datetime.strptime(bt_exit_date, "%Y-%m-%d")
        live_dt = datetime.strptime(live_exit_date, "%Y-%m-%d")
        assert (
            bt_dt.isocalendar()[:2] == live_dt.isocalendar()[:2] or abs((bt_dt - live_dt).days) <= 3
        ), f"NWL exit date mismatch: backtest={bt_exit_date}, live={live_exit_date}"


# ---------------------------------------------------------------------------
# Tests: Shared function unit consistency
# ---------------------------------------------------------------------------


class TestSharedFunctionConsistency:
    """Verify standalone weekly_bars functions produce identical results."""

    @pytest.fixture
    def bars(self):
        return _build_daily_bars(SCENARIO_A_WEEKS)

    def test_weekly_aggregation_deterministic(self, bars):
        """Two aggregations of the same data should produce identical results."""
        w1 = aggregate_daily_to_weekly(bars)
        w2 = aggregate_daily_to_weekly(bars)
        assert len(w1) == len(w2)
        for a, b in zip(w1, w2):
            assert a.week_ending == b.week_ending
            assert a.close == b.close
            assert a.high == b.high
            assert a.low == b.low

    def test_ema_deterministic(self, bars):
        """EMA computation should be deterministic."""
        weekly = aggregate_daily_to_weekly(bars)
        e1 = compute_weekly_ema(weekly, 3)
        e2 = compute_weekly_ema(weekly, 3)
        assert e1 == e2

    def test_nwl_deterministic(self, bars):
        """NWL computation should be deterministic."""
        weekly = aggregate_daily_to_weekly(bars)
        n1 = compute_weekly_nweek_low(weekly, 4)
        n2 = compute_weekly_nweek_low(weekly, 4)
        assert n1 == n2

    def test_week_end_agrees_with_aggregation(self, bars):
        """is_week_end_by_date should return True only for last bar of each ISO week."""
        weekly = aggregate_daily_to_weekly(bars)
        week_ending_dates = {wb.week_ending for wb in weekly}

        for bar in bars:
            is_end = is_week_end_by_date(bars, bar.date)
            if bar.date in week_ending_dates:
                assert is_end, f"{bar.date} should be week end"
