#!/usr/bin/env python3
"""Tests for live.signal_generator with mocked dependencies."""

import json
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from backtest.html_parser import TradeCandidate
from backtest.price_fetcher import PriceBar
from backtest.tests.fake_price_fetcher import FakePriceFetcher
from live.config import LiveConfig
from live.signal_generator import (
    PriceValidationError,
    _derive_json_path,
    _filter_candidates,
    _recover_untracked_positions,
    _strict_parse_json,
    _sync_positions_from_alpaca,
    _validate_against_html,
    generate_signals,
)
from live.state_db import StateDB

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar(date, open_p, high, low, close, volume=1000):
    return PriceBar(
        date=date,
        open=open_p,
        high=high,
        low=low,
        close=close,
        adj_close=close,
        volume=volume,
    )


def _build_weekly_bars(weeks):
    """Build daily bars spanning multiple weeks (Mon-Fri each)."""
    bars = []
    for week_start_str, close_p in weeks:
        dt = datetime.strptime(week_start_str, "%Y-%m-%d")
        for day_offset in range(5):
            d = (dt + timedelta(days=day_offset)).strftime("%Y-%m-%d")
            if day_offset == 4:
                bars.append(_make_bar(d, close_p - 1, close_p + 2, close_p - 3, close_p))
            else:
                bars.append(_make_bar(d, close_p, close_p + 2, close_p - 3, close_p + 0.5))
    return bars


def _make_candidate(
    ticker, score=80.0, grade="B", price=100.0, report_date="2026-02-14", company_name=None
):
    return TradeCandidate(
        ticker=ticker,
        report_date=report_date,
        grade=grade,
        grade_source="html",
        score=score,
        price=price,
        gap_size=5.0,
        company_name=company_name or f"{ticker} Inc.",
    )


_ALPACA_SPEC_SET = [
    "get_account",
    "get_positions",
    "get_clock",
    "get_order",
    "get_order_by_client_id",
    "place_order",
    "place_bracket_order",
    "cancel_order",
]


def _mock_alpaca_client(positions=None, clock_date="2026-02-17"):
    """Create a mock AlpacaClient."""
    client = MagicMock(spec_set=_ALPACA_SPEC_SET)
    client.get_positions.return_value = positions or []
    client.get_clock.return_value = {
        "timestamp": f"{clock_date}T09:30:00-05:00",
        "is_open": True,
    }
    return client


def _write_fake_report(tmp_dir, report_date="2026-02-14", candidates=None):
    """Write a minimal HTML report that EarningsReportParser can parse."""
    if candidates is None:
        candidates = [("CRDO", 92, "A", 80.0), ("PLTR", 78, "B", 25.0)]

    cards = []
    for ticker, score, grade, price in candidates:
        cards.append(f"""
        <div class="stock-card {grade.lower()}-grade">
            <div class="stock-ticker"><span class="ticker-symbol">${ticker}</span></div>
            <div class="stock-company">{ticker} Inc.</div>
            <div class="score-value">{score}/100</div>
            <div class="stock-grade grade-{grade.lower()}">{grade}</div>
            <div class="metric-box">
                <div class="metric-label">Price</div>
                <div class="metric-value">${price}</div>
            </div>
            <div class="metric-box">
                <div class="metric-label">Gap Up</div>
                <div class="metric-value">5.0%</div>
            </div>
        </div>
        """)

    html = f"""<html><body>
    <section class="grade-section">
        {"".join(cards)}
    </section>
    </body></html>"""

    filename = f"earnings_trade_analysis_{report_date}.html"
    filepath = os.path.join(tmp_dir, filename)
    with open(filepath, "w") as f:
        f.write(html)
    return filepath


def _add_db_position(
    db,
    ticker,
    position_id=None,
    entry_price=150.0,
    shares=66,
    score=70.0,
    grade="B",
    entry_date="2026-02-10",
):
    """Add a position to DB and return position_id."""
    return db.add_position(
        ticker=ticker,
        entry_date=entry_date,
        entry_price=entry_price,
        target_shares=shares,
        actual_shares=shares,
        invested=entry_price * shares,
        stop_price=entry_price * 0.9,
        stop_order_id=f"stop-{ticker}",
        score=score,
        grade=grade,
        grade_source="html",
        report_date="2026-02-07",
        company_name=f"{ticker} Inc.",
        gap_size=3.0,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db():
    return StateDB(":memory:")


@pytest.fixture
def config():
    return LiveConfig(max_positions=3, daily_entry_limit=10)


@pytest.fixture
def price_fetcher():
    """Price fetcher with uptrending bars (no trailing stop trigger)."""
    bars = _build_weekly_bars(
        [
            ("2025-09-08", 100),
            ("2025-09-15", 105),
            ("2025-09-22", 110),
            ("2025-09-29", 115),
            ("2025-10-06", 120),
            ("2025-10-13", 125),
            ("2025-10-20", 130),
            ("2025-10-27", 135),
            ("2025-11-03", 140),
            ("2025-11-10", 145),
            ("2025-11-17", 150),
            ("2025-11-24", 155),
            ("2025-12-01", 160),
            ("2025-12-08", 165),
            ("2025-12-15", 170),
            ("2025-12-22", 175),
            ("2026-01-05", 180),
            ("2026-01-12", 185),
            ("2026-01-19", 190),
            ("2026-01-26", 195),
            ("2026-02-02", 200),
            ("2026-02-09", 205),
            ("2026-02-16", 210),
        ]
    )
    return FakePriceFetcher({"DEFAULT": bars})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestKillSwitch:
    def test_kill_switch_blocks(self, db, config, price_fetcher):
        """Kill switch ON should exit with code 3."""
        db.set_kill_switch(True)
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(tmp_dir)
            with pytest.raises(SystemExit) as exc_info:
                generate_signals(
                    config=config,
                    state_db=db,
                    alpaca_client=None,
                    price_fetcher=price_fetcher,
                    report_file=report,
                    output_dir=os.path.join(tmp_dir, "signals"),
                    trade_date="2026-02-17",
                    run_id="test-kill",
                )
            assert exc_info.value.code == 3


class TestReconciliation:
    def test_db_alpaca_mismatch_fails(self, db, config, price_fetcher):
        """Position mismatch without --force should exit code 4."""
        _add_db_position(db, "AAPL")
        _add_db_position(db, "MSFT")
        # Alpaca only has AAPL
        mock_alpaca = _mock_alpaca_client(
            positions=[{"symbol": "AAPL", "unrealized_pl": "10.0", "qty": "66"}]
        )
        mock_alpaca.get_order.side_effect = Exception("order not found")
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(tmp_dir)
            with pytest.raises(SystemExit) as exc_info:
                generate_signals(
                    config=config,
                    state_db=db,
                    alpaca_client=mock_alpaca,
                    price_fetcher=price_fetcher,
                    report_file=report,
                    output_dir=os.path.join(tmp_dir, "signals"),
                    trade_date="2026-02-17",
                    run_id="test-mismatch",
                )
            assert exc_info.value.code == 4

    def test_db_alpaca_mismatch_force(self, db, config, price_fetcher):
        """Position mismatch with --force should continue."""
        _add_db_position(db, "AAPL")
        _add_db_position(db, "MSFT")
        mock_alpaca = _mock_alpaca_client(
            positions=[{"symbol": "AAPL", "unrealized_pl": "10.0", "qty": "66"}]
        )
        mock_alpaca.get_order.side_effect = Exception("order not found")
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(tmp_dir)
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=mock_alpaca,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-17",
                run_id="test-force",
                force=True,
            )
            assert "ema_p10" in result
            assert "nwl_p4" in result


class TestTrailingStopExits:
    def test_generates_exit_on_trend_break(self, db):
        """Trailing stop trigger should produce an exit signal."""
        # Use EMA period 3 (smaller) to reduce warmup requirements
        config = LiveConfig(max_positions=3, primary_trailing_period=3, daily_entry_limit=10)
        _add_db_position(db, "FAIL", entry_date="2025-09-29", entry_price=115.0)

        # Build enough bars for EMA-3 warmup + transition (2 weeks) + drop
        bars = _build_weekly_bars(
            [
                ("2025-09-08", 100),  # EMA warmup 1
                ("2025-09-15", 105),  # EMA warmup 2
                ("2025-09-22", 110),  # EMA warmup 3 (SMA seed ready)
                ("2025-09-29", 115),  # Entry week
                ("2025-10-06", 120),  # Post-entry week 1
                ("2025-10-13", 125),  # Post-entry week 2 (transition met)
                ("2025-10-20", 80),  # Sharp drop below EMA -> trend break
            ]
        )
        fetcher = FakePriceFetcher({"FAIL": bars})

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(tmp_dir, candidates=[("NEWCO", 90, "A", 50.0)])
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2025-10-24",
                run_id="test-exit",
            )

        ema = result["ema_p10"]
        exit_tickers = [e["ticker"] for e in ema["exits"]]
        assert "FAIL" in exit_tickers
        assert ema["exits"][0]["reason"] == "trend_break"


class TestRotation:
    def test_rotation_logic(self, db):
        """Rotation should replace weakest position with better candidate."""
        config = LiveConfig(max_positions=2, daily_entry_limit=10)

        # Fill to max positions
        _add_db_position(db, "WEAK", score=50.0, entry_price=100.0)
        _add_db_position(db, "STRONG", score=90.0, entry_price=100.0)

        # Alpaca positions with WEAK having negative P&L
        mock_alpaca = _mock_alpaca_client(
            positions=[
                {"symbol": "WEAK", "unrealized_pl": "-500.0", "qty": "66"},
                {"symbol": "STRONG", "unrealized_pl": "200.0", "qty": "66"},
            ]
        )

        # Uptrending bars (no trailing stop trigger)
        bars = _build_weekly_bars(
            [
                ("2025-09-08", 100),
                ("2025-09-15", 105),
                ("2025-09-22", 110),
                ("2025-09-29", 115),
                ("2025-10-06", 120),
                ("2025-10-13", 125),
                ("2025-10-20", 130),
                ("2025-10-27", 135),
                ("2025-11-03", 140),
                ("2025-11-10", 145),
                ("2025-11-17", 150),
                ("2025-11-24", 155),
                ("2025-12-01", 160),
                ("2025-12-08", 165),
                ("2025-12-15", 170),
                ("2025-12-22", 175),
                ("2026-01-05", 180),
                ("2026-01-12", 185),
                ("2026-01-19", 190),
                ("2026-01-26", 195),
                ("2026-02-02", 200),
                ("2026-02-09", 205),
                ("2026-02-16", 210),
            ]
        )
        fetcher = FakePriceFetcher({"WEAK": bars, "STRONG": bars})

        with tempfile.TemporaryDirectory() as tmp_dir:
            # New candidate with higher score than WEAK
            report = _write_fake_report(
                tmp_dir,
                candidates=[("BETTER", 95, "A", 80.0)],
            )
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=mock_alpaca,
                price_fetcher=fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-17",
                run_id="test-rotation",
            )

        ema = result["ema_p10"]
        exit_tickers = [e["ticker"] for e in ema["exits"]]
        entry_tickers = [e["ticker"] for e in ema["entries"]]
        assert "WEAK" in exit_tickers
        assert "BETTER" in entry_tickers
        # Check rotation reason
        weak_exit = next(e for e in ema["exits"] if e["ticker"] == "WEAK")
        assert weak_exit["reason"] == "rotated_out"


class TestNewEntries:
    def test_new_entries_within_capacity(self, db, config, price_fetcher):
        """Should add entries up to max_positions."""
        # config.max_positions = 3, no existing positions
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(
                tmp_dir,
                candidates=[
                    ("AAA", 95, "A", 100.0),
                    ("BBB", 85, "A", 50.0),
                    ("CCC", 75, "B", 200.0),
                    ("DDD", 65, "C", 30.0),  # Should be skipped (capacity)
                ],
            )
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-17",
                run_id="test-entries",
            )

        ema = result["ema_p10"]
        assert len(ema["entries"]) == 3
        entry_tickers = [e["ticker"] for e in ema["entries"]]
        assert "AAA" in entry_tickers
        assert "BBB" in entry_tickers
        assert "CCC" in entry_tickers
        # DDD skipped due to capacity
        skipped_tickers = [s["ticker"] for s in ema["skipped"]]
        assert "DDD" in skipped_tickers

    def test_duplicate_ticker_skipped(self, db, config, price_fetcher):
        """Already-held tickers should be skipped."""
        _add_db_position(db, "CRDO")  # Already held

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(
                tmp_dir,
                candidates=[("CRDO", 92, "A", 80.0), ("PLTR", 78, "B", 25.0)],
            )
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-17",
                run_id="test-dup",
            )

        ema = result["ema_p10"]
        entry_tickers = [e["ticker"] for e in ema["entries"]]
        assert "CRDO" not in entry_tickers
        assert "PLTR" in entry_tickers
        skipped_tickers = [s["ticker"] for s in ema["skipped"]]
        assert "CRDO" in skipped_tickers


class TestShadow:
    def test_shadow_independent_calculation(self, db, config, price_fetcher):
        """Shadow path should use shadow_positions, not real positions."""
        # Real position: AAPL
        _add_db_position(db, "AAPL")
        # Shadow position: NVDA
        db.add_shadow_position(
            strategy="nwl_p4",
            ticker="NVDA",
            entry_date="2026-02-10",
            entry_price=300.0,
            shares=33,
            invested=9900.0,
            stop_price=270.0,
            report_date="2026-02-07",
            score=85.0,
            grade="A",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(
                tmp_dir,
                candidates=[("CRDO", 92, "A", 80.0)],
            )
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-17",
                run_id="test-shadow",
            )

        nwl = result["nwl_p4"]
        # Shadow should know about NVDA (shadow pos), not AAPL (real pos)
        assert nwl["summary"]["open_positions_before"] == 1
        # CRDO should be entered in shadow (capacity = 3 - 1 = 2 slots)
        entry_tickers = [e["ticker"] for e in nwl["entries"]]
        assert "CRDO" in entry_tickers


class TestSignalFormat:
    def test_signal_json_format(self, db, config, price_fetcher):
        """Output JSON should have all required fields."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(tmp_dir)
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-17",
                run_id="test-format",
            )

            for key in ("ema_p10", "nwl_p4"):
                sig = result[key]
                assert "trade_date" in sig
                assert "strategy" in sig
                assert "run_id" in sig
                assert "generated_at" in sig
                assert "exits" in sig
                assert "entries" in sig
                assert "skipped" in sig
                assert "summary" in sig

                summary = sig["summary"]
                assert "total_exits" in summary
                assert "total_entries" in summary
                assert "total_skipped" in summary
                assert "open_positions_before" in summary
                assert "open_positions_after" in summary
                assert "daily_entry_limit" in summary

            # Verify JSON file was written
            ema_file = os.path.join(tmp_dir, "signals", "trade_signals_2026-02-17_ema_p10.json")
            assert os.path.exists(ema_file)
            with open(ema_file) as f:
                loaded = json.load(f)
            assert loaded["strategy"] == "ema_p10"

            # Verify entry structure
            for entry in result["ema_p10"]["entries"]:
                assert "ticker" in entry
                assert "side" in entry
                assert "qty" in entry
                assert "score" in entry
                assert "grade" in entry
                assert "stop_price" in entry


class TestDryRun:
    def test_dry_run_no_db_write(self, db, config, price_fetcher):
        """Dry run should not write shadow positions to DB."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(
                tmp_dir,
                candidates=[("CRDO", 92, "A", 80.0)],
            )
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-17",
                run_id="test-dry",
                dry_run=True,
            )

        # Shadow entries generated but NOT written to DB
        nwl = result["nwl_p4"]
        assert len(nwl["entries"]) > 0

        # DB should have no shadow positions
        shadow = db.get_shadow_positions("nwl_p4")
        assert len(shadow) == 0

        # DB should have no shadow signals record
        with db._connect() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM shadow_signals").fetchone()
            assert row["cnt"] == 0


class TestFilterCandidates:
    def test_filter_by_grade(self):
        """Filter should respect min_grade."""
        candidates = [
            _make_candidate("A1", score=90, grade="A"),
            _make_candidate("B1", score=75, grade="B"),
            _make_candidate("C1", score=60, grade="C"),
            _make_candidate("D1", score=45, grade="D"),
        ]
        # min_grade B -> only A and B
        result = _filter_candidates(candidates, "B")
        tickers = [c.ticker for c in result]
        assert "A1" in tickers
        assert "B1" in tickers
        assert "C1" not in tickers
        assert "D1" not in tickers

    def test_filter_sorts_by_score_desc(self):
        """Filtered candidates should be sorted by score descending."""
        candidates = [
            _make_candidate("LOW", score=60, grade="B"),
            _make_candidate("HIGH", score=95, grade="A"),
            _make_candidate("MID", score=80, grade="B"),
        ]
        result = _filter_candidates(candidates, "D")
        assert result[0].ticker == "HIGH"
        assert result[1].ticker == "MID"
        assert result[2].ticker == "LOW"


class TestDailyEntryLimit:
    def test_daily_limit_caps_entries(self, db, price_fetcher):
        """daily_entry_limit=2, max_positions=10, 5 candidates -> 2 entries, 3 daily_limit skips."""
        config = LiveConfig(max_positions=10, daily_entry_limit=2)
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(
                tmp_dir,
                candidates=[
                    ("AAA", 95, "A", 100.0),
                    ("BBB", 85, "A", 50.0),
                    ("CCC", 75, "B", 200.0),
                    ("DDD", 65, "C", 30.0),
                    ("EEE", 55, "C", 40.0),
                ],
            )
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-17",
                run_id="test-daily-limit",
            )

        ema = result["ema_p10"]
        assert len(ema["entries"]) == 2
        entry_tickers = [e["ticker"] for e in ema["entries"]]
        assert "AAA" in entry_tickers
        assert "BBB" in entry_tickers
        # Remaining 3 should be skipped with daily_limit reason
        daily_skips = [s for s in ema["skipped"] if s["reason"] == "daily_limit"]
        assert len(daily_skips) == 3

    def test_capacity_binds_before_daily_limit(self, db, price_fetcher):
        """max_positions=1, daily_entry_limit=5, 3 candidates -> 1 entry, 2 capacity_full skips."""
        config = LiveConfig(max_positions=1, daily_entry_limit=5)
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(
                tmp_dir,
                candidates=[
                    ("AAA", 95, "A", 100.0),
                    ("BBB", 85, "A", 50.0),
                    ("CCC", 75, "B", 200.0),
                ],
            )
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-17",
                run_id="test-cap-binds",
            )

        ema = result["ema_p10"]
        assert len(ema["entries"]) == 1
        capacity_skips = [s for s in ema["skipped"] if s["reason"] == "capacity_full"]
        assert len(capacity_skips) == 2

    def test_rotation_counts_toward_daily_limit(self, db):
        """daily_entry_limit=1, rotation consumes it -> no additional entries."""
        config = LiveConfig(max_positions=2, daily_entry_limit=1)

        # Fill to max positions
        _add_db_position(db, "WEAK", score=50.0, entry_price=100.0)
        _add_db_position(db, "STRONG", score=90.0, entry_price=100.0)

        # Alpaca positions with WEAK having negative P&L
        mock_alpaca = _mock_alpaca_client(
            positions=[
                {"symbol": "WEAK", "unrealized_pl": "-500.0", "qty": "66"},
                {"symbol": "STRONG", "unrealized_pl": "200.0", "qty": "66"},
            ]
        )

        # Uptrending bars (no trailing stop trigger)
        bars = _build_weekly_bars(
            [
                ("2025-09-08", 100),
                ("2025-09-15", 105),
                ("2025-09-22", 110),
                ("2025-09-29", 115),
                ("2025-10-06", 120),
                ("2025-10-13", 125),
                ("2025-10-20", 130),
                ("2025-10-27", 135),
                ("2025-11-03", 140),
                ("2025-11-10", 145),
                ("2025-11-17", 150),
                ("2025-11-24", 155),
                ("2025-12-01", 160),
                ("2025-12-08", 165),
                ("2025-12-15", 170),
                ("2025-12-22", 175),
                ("2026-01-05", 180),
                ("2026-01-12", 185),
                ("2026-01-19", 190),
                ("2026-01-26", 195),
                ("2026-02-02", 200),
                ("2026-02-09", 205),
                ("2026-02-16", 210),
            ]
        )
        fetcher = FakePriceFetcher({"WEAK": bars, "STRONG": bars})

        with tempfile.TemporaryDirectory() as tmp_dir:
            # BETTER triggers rotation, EXTRA should be blocked by daily limit
            report = _write_fake_report(
                tmp_dir,
                candidates=[
                    ("BETTER", 95, "A", 80.0),
                    ("EXTRA", 70, "B", 60.0),
                ],
            )
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=mock_alpaca,
                price_fetcher=fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-17",
                run_id="test-rotation-daily",
            )

        ema = result["ema_p10"]
        # Rotation used the 1 daily slot: WEAK out, BETTER in
        entry_tickers = [e["ticker"] for e in ema["entries"]]
        assert "BETTER" in entry_tickers
        assert len(ema["entries"]) == 1
        # EXTRA should be skipped (both capacity and daily limit bind here)
        skipped_tickers = [s["ticker"] for s in ema["skipped"]]
        assert "EXTRA" in skipped_tickers

    def test_daily_limit_in_summary(self, db, price_fetcher):
        """summary should include daily_entry_limit."""
        config = LiveConfig(max_positions=3, daily_entry_limit=2)
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(tmp_dir)
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-17",
                run_id="test-summary",
            )

        assert result["ema_p10"]["summary"]["daily_entry_limit"] == 2
        assert result["nwl_p4"]["summary"]["daily_entry_limit"] == 2

    def test_shadow_daily_limit(self, db, price_fetcher):
        """Shadow path also enforces daily_entry_limit independently."""
        config = LiveConfig(max_positions=10, daily_entry_limit=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(
                tmp_dir,
                candidates=[
                    ("AAA", 95, "A", 100.0),
                    ("BBB", 85, "A", 50.0),
                ],
            )
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-17",
                run_id="test-shadow-daily",
            )

        nwl = result["nwl_p4"]
        assert len(nwl["entries"]) == 1
        daily_skips = [s for s in nwl["skipped"] if s["reason"] == "daily_limit"]
        assert len(daily_skips) == 1

    def test_rotation_does_not_exceed_max_positions(self, db):
        """After rotation, open_positions_after must not exceed max_positions."""
        config = LiveConfig(max_positions=2, daily_entry_limit=10)

        _add_db_position(db, "WEAK", score=50.0, entry_price=100.0)
        _add_db_position(db, "STRONG", score=90.0, entry_price=100.0)

        mock_alpaca = _mock_alpaca_client(
            positions=[
                {"symbol": "WEAK", "unrealized_pl": "-500.0", "qty": "66"},
                {"symbol": "STRONG", "unrealized_pl": "200.0", "qty": "66"},
            ]
        )

        bars = _build_weekly_bars(
            [
                ("2025-09-08", 100),
                ("2025-09-15", 105),
                ("2025-09-22", 110),
                ("2025-09-29", 115),
                ("2025-10-06", 120),
                ("2025-10-13", 125),
                ("2025-10-20", 130),
                ("2025-10-27", 135),
                ("2025-11-03", 140),
                ("2025-11-10", 145),
                ("2025-11-17", 150),
                ("2025-11-24", 155),
                ("2025-12-01", 160),
                ("2025-12-08", 165),
                ("2025-12-15", 170),
                ("2025-12-22", 175),
                ("2026-01-05", 180),
                ("2026-01-12", 185),
                ("2026-01-19", 190),
                ("2026-01-26", 195),
                ("2026-02-02", 200),
                ("2026-02-09", 205),
                ("2026-02-16", 210),
            ]
        )
        fetcher = FakePriceFetcher({"WEAK": bars, "STRONG": bars})

        with tempfile.TemporaryDirectory() as tmp_dir:
            # BETTER triggers rotation; EXTRA must NOT enter (capacity full)
            report = _write_fake_report(
                tmp_dir,
                candidates=[
                    ("BETTER", 95, "A", 80.0),
                    ("EXTRA", 70, "B", 60.0),
                ],
            )
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=mock_alpaca,
                price_fetcher=fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-17",
                run_id="test-rotation-cap",
            )

        ema = result["ema_p10"]
        assert ema["summary"]["open_positions_after"] <= config.max_positions
        entry_tickers = [e["ticker"] for e in ema["entries"]]
        assert "BETTER" in entry_tickers
        assert "EXTRA" not in entry_tickers
        cap_skips = [s for s in ema["skipped"] if s["reason"] == "capacity_full"]
        assert any(s["ticker"] == "EXTRA" for s in cap_skips)

    def test_negative_daily_limit_rejected(self):
        """daily_entry_limit < 0 should raise ValueError."""
        with pytest.raises(ValueError, match="daily_entry_limit"):
            LiveConfig(daily_entry_limit=-1)


class TestPositionSync:
    """Tests for _sync_positions_from_alpaca auto-close logic."""

    def _make_filled_order(
        self, filled_avg_price="140.00", filled_qty="66", filled_at="2026-02-16T15:30:00-05:00"
    ):
        return {
            "status": "filled",
            "filled_avg_price": filled_avg_price,
            "filled_qty": filled_qty,
            "filled_at": filled_at,
        }

    def test_sync_closes_position_when_stop_filled(self, db):
        """DB position not in Alpaca + stop order filled -> auto-close with correct pnl."""
        pos_id = _add_db_position(db, "AAPL", entry_price=150.0, shares=66)
        db_positions = db.get_open_positions()
        alpaca_positions = []  # AAPL not in Alpaca

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        mock_client.get_order.return_value = self._make_filled_order(
            filled_avg_price="140.00",
            filled_qty="66",
        )

        synced = _sync_positions_from_alpaca(
            db_positions,
            alpaca_positions,
            mock_client,
            db,
            "2026-02-17",
        )

        assert synced == 1
        # Verify position is closed
        open_positions = db.get_open_positions()
        assert len(open_positions) == 0

        # Verify exit details
        with db._connect() as conn:
            row = conn.execute(
                "SELECT exit_reason, exit_price, pnl, return_pct FROM positions WHERE position_id = ?",
                (pos_id,),
            ).fetchone()
        assert row["exit_reason"] == "stop_filled_sync"
        assert row["exit_price"] == 140.0
        # pnl = (140 - 150) * 66 = -660.0
        assert row["pnl"] == -660.0
        # return_pct = ((140/150) - 1) * 100 = -6.67
        assert row["return_pct"] == -6.67

    def test_sync_uses_filled_at_for_exit_date(self, db):
        """Exit date should come from filled_at timestamp, not trade_date."""
        pos_id = _add_db_position(db, "AAPL", entry_price=100.0, shares=10)
        db_positions = db.get_open_positions()

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        mock_client.get_order.return_value = self._make_filled_order(
            filled_avg_price="95.00",
            filled_qty="10",
            filled_at="2026-02-16T10:30:00-05:00",
        )

        _sync_positions_from_alpaca(
            db_positions,
            [],
            mock_client,
            db,
            "2026-02-17",
        )

        with db._connect() as conn:
            row = conn.execute(
                "SELECT exit_date FROM positions WHERE position_id = ?",
                (pos_id,),
            ).fetchone()
        assert row["exit_date"] == "2026-02-16"

    def test_sync_uses_filled_qty_for_pnl(self, db):
        """PnL should use filled_qty (60) not DB shares (66)."""
        _add_db_position(db, "AAPL", entry_price=100.0, shares=66)
        db_positions = db.get_open_positions()

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        mock_client.get_order.return_value = self._make_filled_order(
            filled_avg_price="90.00",
            filled_qty="60",
        )

        _sync_positions_from_alpaca(
            db_positions,
            [],
            mock_client,
            db,
            "2026-02-17",
        )

        with db._connect() as conn:
            row = conn.execute("SELECT pnl FROM positions WHERE ticker = 'AAPL'").fetchone()
        # pnl = (90 - 100) * 60 = -600.0
        assert row["pnl"] == -600.0

    def test_sync_skips_when_no_stop_order_id(self, db):
        """Position without stop_order_id should be skipped (not auto-closed)."""
        # Add position with no stop_order_id
        with db._connect() as conn:
            conn.execute(
                """INSERT INTO positions
                   (ticker, entry_date, entry_price, target_shares, actual_shares,
                    invested, stop_price, stop_order_id, score, grade, grade_source,
                    report_date, company_name, gap_size)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "AAPL",
                    "2026-02-10",
                    150.0,
                    66,
                    66,
                    9900.0,
                    135.0,
                    None,
                    70.0,
                    "B",
                    "html",
                    "2026-02-07",
                    "AAPL Inc.",
                    3.0,
                ),
            )
        db_positions = db.get_open_positions()

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)

        synced = _sync_positions_from_alpaca(
            db_positions,
            [],
            mock_client,
            db,
            "2026-02-17",
        )

        assert synced == 0
        mock_client.get_order.assert_not_called()
        assert len(db.get_open_positions()) == 1

    def test_sync_skips_when_order_lookup_fails(self, db):
        """API error on get_order should skip (not crash)."""
        _add_db_position(db, "AAPL")
        db_positions = db.get_open_positions()

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        mock_client.get_order.side_effect = Exception("API timeout")

        synced = _sync_positions_from_alpaca(
            db_positions,
            [],
            mock_client,
            db,
            "2026-02-17",
        )

        assert synced == 0
        assert len(db.get_open_positions()) == 1

    def test_sync_skips_when_stop_not_filled(self, db):
        """Stop order with status='new' should be skipped."""
        _add_db_position(db, "AAPL")
        db_positions = db.get_open_positions()

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        mock_client.get_order.return_value = {"status": "new"}

        synced = _sync_positions_from_alpaca(
            db_positions,
            [],
            mock_client,
            db,
            "2026-02-17",
        )

        assert synced == 0
        assert len(db.get_open_positions()) == 1

    def test_sync_skips_when_no_fill_price(self, db):
        """Filled order with no filled_avg_price should be skipped."""
        _add_db_position(db, "AAPL")
        db_positions = db.get_open_positions()

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        mock_client.get_order.return_value = {
            "status": "filled",
            "filled_avg_price": None,
            "filled_qty": "66",
            "filled_at": "2026-02-16T15:30:00-05:00",
        }

        synced = _sync_positions_from_alpaca(
            db_positions,
            [],
            mock_client,
            db,
            "2026-02-17",
        )

        assert synced == 0
        assert len(db.get_open_positions()) == 1

    def test_mixed_sync_and_unresolvable(self, db):
        """AAPL(stop filled) auto-closed, MSFT(no stop_order_id) left open."""
        # AAPL: has stop_order_id (from _add_db_position)
        _add_db_position(db, "AAPL", entry_price=150.0, shares=66)
        # MSFT: no stop_order_id
        with db._connect() as conn:
            conn.execute(
                """INSERT INTO positions
                   (ticker, entry_date, entry_price, target_shares, actual_shares,
                    invested, stop_price, stop_order_id, score, grade, grade_source,
                    report_date, company_name, gap_size)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "MSFT",
                    "2026-02-10",
                    400.0,
                    25,
                    25,
                    10000.0,
                    360.0,
                    None,
                    80.0,
                    "A",
                    "html",
                    "2026-02-07",
                    "MSFT Inc.",
                    4.0,
                ),
            )

        db_positions = db.get_open_positions()

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        # AAPL stop filled, MSFT get_order should not be called
        mock_client.get_order.return_value = self._make_filled_order(
            filled_avg_price="140.00",
            filled_qty="66",
        )

        synced = _sync_positions_from_alpaca(
            db_positions,
            [],
            mock_client,
            db,
            "2026-02-17",
        )

        assert synced == 1
        open_positions = db.get_open_positions()
        open_tickers = [p["ticker"] for p in open_positions]
        assert "AAPL" not in open_tickers
        assert "MSFT" in open_tickers


class TestE2EPipeline:
    """D1: End-to-end integration tests."""

    def test_json_to_signals_e2e(self, db, config, price_fetcher):
        """JSON candidates -> signal generation -> entries with qty > 0."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Write HTML report — tickers/prices must match JSON for cross-validation
            report = _write_fake_report(
                tmp_dir,
                report_date="2026-02-19",
                candidates=[("GRMN", 92, "A", 248.93), ("PLTR", 78, "B", 25.0)],
            )
            # Write JSON candidates (preferred source)
            json_data = {
                "report_date": "2026-02-19",
                "candidates": [
                    {"ticker": "GRMN", "grade": "A", "score": 92.5, "price": 248.93},
                    {"ticker": "PLTR", "grade": "B", "score": 78, "price": 25.0},
                ],
            }
            json_path = os.path.join(tmp_dir, "earnings_trade_candidates_2026-02-19.json")
            with open(json_path, "w") as f:
                json.dump(json_data, f)

            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-19",
                run_id="test-e2e-json",
            )

        ema = result["ema_p10"]
        assert len(ema["entries"]) >= 1
        for entry in ema["entries"]:
            assert entry["qty"] > 0
            assert entry["stop_price"] > 0
            assert entry["grade"] in ("A", "B", "C", "D")
        assert ema["price_validation_failed"] is False

    def test_html_to_signals_e2e(self, db, config, price_fetcher):
        """HTML report only (no JSON) -> candidate extraction -> entries with qty > 0."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(
                tmp_dir,
                report_date="2026-02-19",
                candidates=[
                    ("CRDO", 92, "A", 80.0),
                    ("PLTR", 78, "B", 25.0),
                ],
            )
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-19",
                run_id="test-e2e-html",
            )

        ema = result["ema_p10"]
        assert len(ema["entries"]) >= 1
        for entry in ema["entries"]:
            assert entry["qty"] > 0

    def test_executor_compatible_output(self, db, config, price_fetcher):
        """Generated signal JSON matches executor expected format."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(
                tmp_dir,
                report_date="2026-02-19",
                candidates=[("CRDO", 92, "A", 80.0)],
            )
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-19",
                run_id="test-e2e-compat",
            )

        for key in ("ema_p10", "nwl_p4"):
            entries = result[key]["entries"]
            for entry in entries:
                assert isinstance(entry["ticker"], str)
                assert isinstance(entry["qty"], int)
                assert entry["qty"] > 0
                assert isinstance(entry["stop_price"], float)
                assert entry["stop_price"] > 0
                assert entry["grade"] in ("A", "B", "C", "D")


class TestDeriveJsonPath:
    """Tests for _derive_json_path helper."""

    def test_standard_html_path(self):
        result = _derive_json_path("reports/earnings_trade_analysis_2026-02-19.html")
        assert result == "reports/earnings_trade_candidates_2026-02-19.json"

    def test_absolute_path(self):
        result = _derive_json_path("/home/user/reports/earnings_trade_analysis_2026-02-19.html")
        assert result == "/home/user/reports/earnings_trade_candidates_2026-02-19.json"

    def test_no_date_in_filename(self):
        result = _derive_json_path("reports/some_report.html")
        assert result is None

    def test_preserves_directory(self, tmp_path):
        result = _derive_json_path(str(tmp_path / "earnings_trade_analysis_2026-02-17.html"))
        assert result == str(tmp_path / "earnings_trade_candidates_2026-02-17.json")


class TestJsonPriorityParsing:
    """JSON file takes priority over HTML parsing."""

    def test_json_preferred_over_html(self, db, config, price_fetcher):
        """When JSON exists, candidates should come from JSON (grade_source='json')."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Write HTML report — price must match JSON (within $0.05)
            report = _write_fake_report(
                tmp_dir,
                report_date="2026-02-19",
                candidates=[("TS", 80, "B", 52.86)],
            )
            # Write JSON candidates (with higher score)
            json_data = {
                "report_date": "2026-02-19",
                "candidates": [{"ticker": "TS", "grade": "A", "score": 90, "price": 52.86}],
            }
            json_path = os.path.join(tmp_dir, "earnings_trade_candidates_2026-02-19.json")
            with open(json_path, "w") as f:
                json.dump(json_data, f)

            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-19",
                run_id="test-json-prio",
            )

        ema = result["ema_p10"]
        # Should use JSON data (score=90, grade=A) not HTML (score=80, grade=B)
        entries = ema["entries"]
        assert len(entries) >= 1
        ts_entry = next(e for e in entries if e["ticker"] == "TS")
        assert ts_entry["score"] == 90
        assert ts_entry["grade"] == "A"

    def test_html_fallback_when_no_json(self, db, config, price_fetcher):
        """When no JSON exists, HTML parsing still works."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(
                tmp_dir,
                report_date="2026-02-19",
                candidates=[("TS", 80, "B", 50.0)],
            )
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-19",
                run_id="test-html-fallback",
            )

        ema = result["ema_p10"]
        entries = ema["entries"]
        assert len(entries) >= 1
        ts_entry = next(e for e in entries if e["ticker"] == "TS")
        assert ts_entry["score"] == 80

    def test_json_empty_html_nonempty_blocks_entries(self, db, config, price_fetcher):
        """JSON empty + HTML non-empty = validation failure, entries blocked."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(
                tmp_dir,
                report_date="2026-02-19",
                candidates=[("TS", 80, "B", 50.0)],
            )
            # Write empty JSON candidates
            json_data = {"report_date": "2026-02-19", "candidates": []}
            json_path = os.path.join(tmp_dir, "earnings_trade_candidates_2026-02-19.json")
            with open(json_path, "w") as f:
                json.dump(json_data, f)

            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-19",
                run_id="test-empty-json",
            )

        ema = result["ema_p10"]
        assert ema["price_validation_failed"] is True
        assert len(ema["entries"]) == 0


# ---------------------------------------------------------------------------
# Recovery tests for _recover_untracked_positions
# ---------------------------------------------------------------------------


class TestRecoverUntrackedPositions:
    """Tests for _recover_untracked_positions function."""

    def test_recover_filled_order_from_previous_day(self, db):
        """Recover a position from a previous day's pending order (date-agnostic)."""
        # Pending order from 2026-02-23 (previous day)
        db.add_order(
            client_order_id="2026-02-23_LINC_entry_buy",
            ticker="LINC",
            side="buy",
            intent="entry",
            trade_date="2026-02-23",
            qty=10,
            alpaca_order_id="alp-linc-001",
            planned_stop_price=45.0,
        )

        db_positions = []  # No positions in DB
        alpaca_positions = [{"symbol": "LINC", "qty": "10"}]

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        # Alpaca says the order is filled
        mock_client.get_order.return_value = {
            "id": "alp-linc-001",
            "status": "filled",
            "filled_avg_price": "50.0",
            "filled_qty": "10",
            "legs": [],
        }
        mock_client.get_order_by_client_id.return_value = None
        mock_client.place_order.return_value = {"id": "alp-stop-linc-001"}

        result = _recover_untracked_positions(
            db_positions, alpaca_positions, mock_client, db, "2026-02-24"
        )

        # Should have recovered the position
        assert len(result) == 1
        assert result[0]["ticker"] == "LINC"
        assert result[0]["entry_price"] == 50.0
        assert result[0]["entry_date"] == "2026-02-23"  # Original trade_date
        assert result[0]["stop_order_id"] == "alp-stop-linc-001"

        # Verify DB order updated to filled
        order = db.get_order_by_client_id("2026-02-23_LINC_entry_buy")
        assert order["status"] == "filled"
        assert order["fill_price"] == 50.0

        # Verify stop was placed
        mock_client.place_order.assert_called_once()
        call_kwargs = mock_client.place_order.call_args.kwargs
        assert call_kwargs["type"] == "stop"
        assert call_kwargs["stop_price"] == 45.0

    def test_recover_skips_when_no_pending_order(self, db):
        """No pending order in DB for the ticker — skip, let reconcile handle."""
        db_positions = []
        alpaca_positions = [{"symbol": "UNKNOWN", "qty": "10"}]

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        result = _recover_untracked_positions(
            db_positions, alpaca_positions, mock_client, db, "2026-02-24"
        )

        # No recovery, positions unchanged
        assert result == []
        mock_client.get_order.assert_not_called()

    def test_recover_skips_when_still_pending(self, db):
        """Order is still pending on Alpaca — do not recover yet."""
        db.add_order(
            client_order_id="2026-02-23_VIV_entry_buy",
            ticker="VIV",
            side="buy",
            intent="entry",
            trade_date="2026-02-23",
            qty=5,
            alpaca_order_id="alp-viv-001",
            planned_stop_price=20.0,
        )

        db_positions = []
        alpaca_positions = [{"symbol": "VIV", "qty": "5"}]

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        mock_client.get_order.return_value = {
            "id": "alp-viv-001",
            "status": "accepted",
            "legs": [],
        }

        result = _recover_untracked_positions(
            db_positions, alpaca_positions, mock_client, db, "2026-02-24"
        )

        # No recovery
        assert result == []
        # Order status should not have been changed
        order = db.get_order_by_client_id("2026-02-23_VIV_entry_buy")
        assert order["status"] == "pending"

    def test_recover_does_not_double_place_stop_bracket(self, db):
        """Bracket order with active stop leg — reuse, don't place new stop."""
        db.add_order(
            client_order_id="2026-02-23_LINC_entry_buy",
            ticker="LINC",
            side="buy",
            intent="entry",
            trade_date="2026-02-23",
            qty=10,
            alpaca_order_id="alp-linc-001",
            planned_stop_price=45.0,
        )

        db_positions = []
        alpaca_positions = [{"symbol": "LINC", "qty": "10"}]

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        mock_client.get_order.return_value = {
            "id": "alp-linc-001",
            "status": "filled",
            "filled_avg_price": "50.0",
            "filled_qty": "10",
            "legs": [{"id": "alp-stop-leg-001", "status": "new"}],
        }

        result = _recover_untracked_positions(
            db_positions, alpaca_positions, mock_client, db, "2026-02-24"
        )

        assert len(result) == 1
        assert result[0]["stop_order_id"] == "alp-stop-leg-001"
        # place_order should NOT be called (stop already exists as bracket leg)
        mock_client.place_order.assert_not_called()

    def test_recover_does_not_double_place_stop_existing(self, db):
        """Existing stop order in DB — verified active on Alpaca, reuse it."""
        db.add_order(
            client_order_id="2026-02-23_LINC_entry_buy",
            ticker="LINC",
            side="buy",
            intent="entry",
            trade_date="2026-02-23",
            qty=10,
            alpaca_order_id="alp-linc-001",
            planned_stop_price=45.0,
        )
        # Existing stop order in DB
        db.add_order(
            client_order_id="2026-02-23_LINC_stop_sell",
            ticker="LINC",
            side="sell",
            intent="stop",
            trade_date="2026-02-23",
            qty=10,
            alpaca_order_id="alp-stop-existing-001",
        )

        db_positions = []
        alpaca_positions = [{"symbol": "LINC", "qty": "10"}]

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)

        def _get_order(order_id):
            if order_id == "alp-linc-001":
                return {
                    "id": "alp-linc-001",
                    "status": "filled",
                    "filled_avg_price": "50.0",
                    "filled_qty": "10",
                    "legs": [],
                }
            if order_id == "alp-stop-existing-001":
                # Step 2 verification: stop is still active on Alpaca
                return {"id": "alp-stop-existing-001", "status": "accepted"}
            raise ValueError(f"unexpected order_id: {order_id}")

        mock_client.get_order.side_effect = _get_order

        result = _recover_untracked_positions(
            db_positions, alpaca_positions, mock_client, db, "2026-02-24"
        )

        assert len(result) == 1
        assert result[0]["stop_order_id"] == "alp-stop-existing-001"
        # place_order should NOT be called (stop verified active on Alpaca)
        mock_client.place_order.assert_not_called()

    def test_recover_stale_db_stop_falls_through_to_step3(self, db):
        """Step 2: DB stop is stale (canceled on Alpaca) — falls through to Step 3."""
        db.add_order(
            client_order_id="2026-02-23_LINC_entry_buy",
            ticker="LINC",
            side="buy",
            intent="entry",
            trade_date="2026-02-23",
            qty=10,
            alpaca_order_id="alp-linc-001",
            planned_stop_price=45.0,
        )
        # Stale stop order in DB (pending in DB, but canceled on Alpaca)
        db.add_order(
            client_order_id="2026-02-23_LINC_stop_sell",
            ticker="LINC",
            side="sell",
            intent="stop",
            trade_date="2026-02-23",
            qty=10,
            alpaca_order_id="alp-stop-stale-001",
        )

        db_positions = []
        alpaca_positions = [{"symbol": "LINC", "qty": "10"}]

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)

        def _get_order(order_id):
            if order_id == "alp-linc-001":
                return {
                    "id": "alp-linc-001",
                    "status": "filled",
                    "filled_avg_price": "50.0",
                    "filled_qty": "10",
                    "legs": [],
                }
            if order_id == "alp-stop-stale-001":
                # Step 2 verification: stop is canceled on Alpaca (stale)
                return {"id": "alp-stop-stale-001", "status": "canceled"}
            raise ValueError(f"unexpected order_id: {order_id}")

        mock_client.get_order.side_effect = _get_order
        # Step 3: no existing stop on Alpaca either → new stop placed
        mock_client.get_order_by_client_id.return_value = None
        mock_client.place_order.return_value = {"id": "alp-stop-new-001"}

        result = _recover_untracked_positions(
            db_positions, alpaca_positions, mock_client, db, "2026-02-24"
        )

        assert len(result) == 1
        assert result[0]["stop_order_id"] == "alp-stop-new-001"
        # A new stop was placed because DB stop was stale
        mock_client.place_order.assert_called_once()

    # -- C1: stop placement failure → kill switch + position recorded ----------

    def test_recover_stop_failure_activates_kill_switch(self, db):
        """place_order raises for stop → kill switch ON, position still recorded."""
        db.add_order(
            client_order_id="2026-02-23_LINC_entry_buy",
            ticker="LINC",
            side="buy",
            intent="entry",
            trade_date="2026-02-23",
            qty=10,
            alpaca_order_id="alp-linc-001",
            planned_stop_price=45.0,
        )

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        mock_client.get_order.return_value = {
            "id": "alp-linc-001",
            "status": "filled",
            "filled_avg_price": "50.0",
            "filled_qty": "10",
            "legs": [],
        }
        mock_client.get_order_by_client_id.return_value = None
        mock_client.place_order.side_effect = Exception("network error")

        result = _recover_untracked_positions(
            [], [{"symbol": "LINC", "qty": "10"}], mock_client, db, "2026-02-24"
        )

        # Position must be recorded despite stop failure
        assert len(result) == 1
        assert result[0]["ticker"] == "LINC"
        # Kill switch must be ON
        assert db.is_kill_switch_on() is True

    # -- C2: Step 3 Alpaca lookup fails → skip new stop, kill switch ----------

    def test_recover_step3_alpaca_error_skips_new_stop_and_kill_switch(self, db):
        """Step 3 API error → no new stop placed, kill switch ON, position recorded."""
        db.add_order(
            client_order_id="2026-02-23_LINC_entry_buy",
            ticker="LINC",
            side="buy",
            intent="entry",
            trade_date="2026-02-23",
            qty=10,
            alpaca_order_id="alp-linc-001",
            planned_stop_price=45.0,
        )

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        mock_client.get_order.return_value = {
            "id": "alp-linc-001",
            "status": "filled",
            "filled_avg_price": "50.0",
            "filled_qty": "10",
            "legs": [],
        }
        # Step 2: no DB stop
        # Step 3: Alpaca lookup fails
        mock_client.get_order_by_client_id.side_effect = Exception("Alpaca API down")

        result = _recover_untracked_positions(
            [], [{"symbol": "LINC", "qty": "10"}], mock_client, db, "2026-02-24"
        )

        # Position must be recorded
        assert len(result) == 1
        assert result[0]["ticker"] == "LINC"
        # place_order should NOT be called (step 3 failed → skip new stop)
        mock_client.place_order.assert_not_called()
        # Kill switch must be ON
        assert db.is_kill_switch_on() is True

    # -- Step 3 reuses Alpaca stop -----------------------------------------------

    def test_recover_reuses_alpaca_stop_step3(self, db):
        """Step 3 finds an active stop on Alpaca → reuse it, no new placement."""
        db.add_order(
            client_order_id="2026-02-23_LINC_entry_buy",
            ticker="LINC",
            side="buy",
            intent="entry",
            trade_date="2026-02-23",
            qty=10,
            alpaca_order_id="alp-linc-001",
            planned_stop_price=45.0,
        )

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        mock_client.get_order.return_value = {
            "id": "alp-linc-001",
            "status": "filled",
            "filled_avg_price": "50.0",
            "filled_qty": "10",
            "legs": [],
        }
        # Step 2 uses state_db (real DB), not mock_client
        # Step 3 uses alpaca_client.get_order_by_client_id → return active stop
        mock_client.get_order_by_client_id.return_value = {
            "id": "alp-stop-from-alpaca",
            "status": "new",
        }

        result = _recover_untracked_positions(
            [], [{"symbol": "LINC", "qty": "10"}], mock_client, db, "2026-02-24"
        )

        assert len(result) == 1
        assert result[0]["stop_order_id"] == "alp-stop-from-alpaca"
        mock_client.place_order.assert_not_called()

    # -- M2: idempotent — no duplicate position -----------------------------------

    def test_recover_idempotent_no_duplicate_position(self, db):
        """M2: If position already recorded in DB, don't insert a duplicate.

        Pass db_positions=[] so LINC appears in 'untracked' and the function
        actually reaches the M2 idempotency check (get_open_positions inside
        the recovery loop).  Also add a stop order record so Steps 1-3 find
        it and skip new stop placement — isolating the M2 assertion.
        """
        db.add_order(
            client_order_id="2026-02-23_LINC_entry_buy",
            ticker="LINC",
            side="buy",
            intent="entry",
            trade_date="2026-02-23",
            qty=10,
            alpaca_order_id="alp-linc-001",
            planned_stop_price=45.0,
        )
        # Existing stop order record in DB (Step 2 will find this)
        db.add_order(
            client_order_id="2026-02-23_LINC_stop_sell",
            ticker="LINC",
            side="sell",
            intent="stop",
            trade_date="2026-02-23",
            qty=10,
            alpaca_order_id="alp-stop-linc-001",
        )
        # Position already exists in DB (but NOT passed in db_positions)
        db.add_position(
            ticker="LINC",
            entry_date="2026-02-23",
            entry_price=50.0,
            target_shares=10,
            actual_shares=10,
            invested=500.0,
            stop_price=45.0,
            stop_order_id="alp-stop-linc-001",
            score=None,
            grade=None,
            grade_source=None,
            report_date=None,
            company_name=None,
            gap_size=None,
        )

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)

        def _get_order(order_id):
            if order_id == "alp-linc-001":
                return {
                    "id": "alp-linc-001",
                    "status": "filled",
                    "filled_avg_price": "50.0",
                    "filled_qty": "10",
                    "legs": [],
                }
            if order_id == "alp-stop-linc-001":
                # Step 2 verification: stop is active on Alpaca
                return {"id": "alp-stop-linc-001", "status": "accepted"}
            raise ValueError(f"unexpected order_id: {order_id}")

        mock_client.get_order.side_effect = _get_order

        _recover_untracked_positions(
            [],  # Empty — LINC appears in 'untracked', reaches M2 check
            [{"symbol": "LINC", "qty": "10"}],
            mock_client,
            db,
            "2026-02-24",
        )

        # M2 path: position already exists → skip add_position, no duplicate
        positions = db.get_open_positions()
        assert len(positions) == 1  # Still just one
        # Stop already found in Step 2 — no new stop placed
        mock_client.place_order.assert_not_called()

    # -- M3: string/float qty handling -------------------------------------------

    def test_recover_handles_string_float_qty(self, db):
        """filled_qty='10.0' (string float) is parsed correctly."""
        db.add_order(
            client_order_id="2026-02-23_LINC_entry_buy",
            ticker="LINC",
            side="buy",
            intent="entry",
            trade_date="2026-02-23",
            qty=10,
            alpaca_order_id="alp-linc-001",
            planned_stop_price=45.0,
        )

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        mock_client.get_order.return_value = {
            "id": "alp-linc-001",
            "status": "filled",
            "filled_avg_price": "50.0",
            "filled_qty": "10.0",  # String float
            "legs": [],
        }
        mock_client.get_order_by_client_id.return_value = None
        mock_client.place_order.return_value = {"id": "alp-stop-001"}

        result = _recover_untracked_positions(
            [], [{"symbol": "LINC", "qty": "10"}], mock_client, db, "2026-02-24"
        )

        assert len(result) == 1
        assert result[0]["actual_shares"] == 10

    def test_recover_skips_zero_filled_qty(self, db):
        """filled_qty=0 → skip recovery, update_order_status NOT called."""
        db.add_order(
            client_order_id="2026-02-23_LINC_entry_buy",
            ticker="LINC",
            side="buy",
            intent="entry",
            trade_date="2026-02-23",
            qty=10,
            alpaca_order_id="alp-linc-001",
            planned_stop_price=45.0,
        )

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        mock_client.get_order.return_value = {
            "id": "alp-linc-001",
            "status": "filled",
            "filled_avg_price": "50.0",
            "filled_qty": "0",
            "legs": [],
        }

        result = _recover_untracked_positions(
            [], [{"symbol": "LINC", "qty": "10"}], mock_client, db, "2026-02-24"
        )

        # No recovery
        assert result == []
        # Order status should NOT have been updated
        order = db.get_order_by_client_id("2026-02-23_LINC_entry_buy")
        assert order["status"] == "pending"

    def test_recover_skips_invalid_fill_price(self, db):
        """filled_avg_price='N/A' → skip recovery, update_order_status NOT called."""
        db.add_order(
            client_order_id="2026-02-23_LINC_entry_buy",
            ticker="LINC",
            side="buy",
            intent="entry",
            trade_date="2026-02-23",
            qty=10,
            alpaca_order_id="alp-linc-001",
            planned_stop_price=45.0,
        )

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        mock_client.get_order.return_value = {
            "id": "alp-linc-001",
            "status": "filled",
            "filled_avg_price": "N/A",
            "filled_qty": "10",
            "legs": [],
        }

        result = _recover_untracked_positions(
            [], [{"symbol": "LINC", "qty": "10"}], mock_client, db, "2026-02-24"
        )

        # No recovery
        assert result == []
        # Order status should NOT have been updated
        order = db.get_order_by_client_id("2026-02-23_LINC_entry_buy")
        assert order["status"] == "pending"

    # -- M4: planned_stop=None → CRITICAL log, no kill switch -----------------

    def test_recover_null_planned_stop_logs_critical(self, db):
        """planned_stop=None → CRITICAL log, kill switch OFF, position recorded."""
        db.add_order(
            client_order_id="2026-02-23_LINC_entry_buy",
            ticker="LINC",
            side="buy",
            intent="entry",
            trade_date="2026-02-23",
            qty=10,
            alpaca_order_id="alp-linc-001",
            planned_stop_price=None,  # No planned stop
        )

        mock_client = MagicMock(spec_set=_ALPACA_SPEC_SET)
        mock_client.get_order.return_value = {
            "id": "alp-linc-001",
            "status": "filled",
            "filled_avg_price": "50.0",
            "filled_qty": "10",
            "legs": [],
        }
        mock_client.get_order_by_client_id.return_value = None

        with patch("live.signal_generator.logger") as mock_logger:
            result = _recover_untracked_positions(
                [], [{"symbol": "LINC", "qty": "10"}], mock_client, db, "2026-02-24"
            )

        # Position must be recorded
        assert len(result) == 1
        assert result[0]["ticker"] == "LINC"
        # No stop should be placed
        mock_client.place_order.assert_not_called()
        # Kill switch should remain OFF (design-level issue, not failure)
        assert db.is_kill_switch_on() is False
        # CRITICAL log emitted
        critical_calls = [c for c in mock_logger.critical.call_args_list if "UNPROTECTED" in str(c)]
        assert len(critical_calls) >= 1


# ---------------------------------------------------------------------------
# Strict JSON parse tests
# ---------------------------------------------------------------------------


def _write_json_file(tmp_dir, data, report_date="2026-02-19"):
    """Write a JSON candidates file and return the path."""
    path = os.path.join(tmp_dir, f"earnings_trade_candidates_{report_date}.json")
    with open(path, "w") as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f)
    return path


class TestStrictParseJson:
    """Tests for _strict_parse_json."""

    def test_invalid_json_raises(self, tmp_path):
        path = _write_json_file(str(tmp_path), "not json {{{")
        with pytest.raises(PriceValidationError, match="Invalid JSON"):
            _strict_parse_json(path)

    def test_root_not_dict_raises(self, tmp_path):
        path = _write_json_file(str(tmp_path), [1, 2, 3])
        with pytest.raises(PriceValidationError, match="not a dict"):
            _strict_parse_json(path)

    def test_no_candidates_key_raises(self, tmp_path):
        path = _write_json_file(str(tmp_path), {"report_date": "2026-02-19"})
        with pytest.raises(PriceValidationError, match="No 'candidates' list"):
            _strict_parse_json(path)

    def test_dropped_rows_raises(self, tmp_path):
        # One valid, one missing required fields -> dropped
        data = {
            "report_date": "2026-02-19",
            "candidates": [
                {"ticker": "AAPL", "grade": "A", "score": 90, "price": 100.0},
                {"bad_field": "no_ticker"},
            ],
        }
        path = _write_json_file(str(tmp_path), data)
        with pytest.raises(PriceValidationError, match="Dropped 1/2"):
            _strict_parse_json(path)


# ---------------------------------------------------------------------------
# Cross-validation tests
# ---------------------------------------------------------------------------


class TestValidateAgainstHtml:
    """Tests for _validate_against_html."""

    def test_both_empty_passes(self, tmp_path):
        report = _write_fake_report(str(tmp_path), candidates=[])
        _validate_against_html([], report)  # should not raise

    def test_all_match_passes(self, tmp_path):
        report = _write_fake_report(str(tmp_path), candidates=[("AAPL", 90, "A", 150.0)])
        json_candidates = [_make_candidate("AAPL", score=90, grade="A", price=150.0)]
        _validate_against_html(json_candidates, report)  # should not raise

    def test_price_mismatch_raises(self, tmp_path):
        report = _write_fake_report(str(tmp_path), candidates=[("AAPL", 90, "A", 150.0)])
        json_candidates = [_make_candidate("AAPL", score=90, grade="A", price=200.0)]
        with pytest.raises(PriceValidationError, match="AAPL"):
            _validate_against_html(json_candidates, report)

    def test_html_missing_ticker_raises(self, tmp_path):
        report = _write_fake_report(str(tmp_path), candidates=[("AAPL", 90, "A", 150.0)])
        json_candidates = [
            _make_candidate("AAPL", score=90, grade="A", price=150.0),
            _make_candidate("GOOG", score=80, grade="B", price=100.0),
        ]
        with pytest.raises(PriceValidationError, match="JSON-only"):
            _validate_against_html(json_candidates, report)

    def test_json_omission_raises(self, tmp_path):
        report = _write_fake_report(
            str(tmp_path),
            candidates=[("AAPL", 90, "A", 150.0), ("GOOG", 80, "B", 100.0)],
        )
        json_candidates = [_make_candidate("AAPL", score=90, grade="A", price=150.0)]
        with pytest.raises(PriceValidationError, match="JSON omission"):
            _validate_against_html(json_candidates, report)

    def test_html_no_price_raises(self, tmp_path):
        report = _write_fake_report(str(tmp_path), candidates=[("AAPL", 90, "A", 150.0)])
        json_candidates = [_make_candidate("AAPL", score=90, grade="A", price=150.0)]
        # Patch the HTML candidate to have None price
        with (
            patch(
                "live.signal_generator.EarningsReportParser.parse_single_report",
                return_value=[_make_candidate("AAPL", price=None)],
            ),
            pytest.raises(PriceValidationError, match="HTML price is None"),
        ):
            _validate_against_html(json_candidates, report)

    def test_json_nonempty_html_empty_raises(self, tmp_path):
        report = _write_fake_report(str(tmp_path), candidates=[])
        json_candidates = [_make_candidate("AAPL", score=90, grade="A", price=150.0)]
        with pytest.raises(PriceValidationError, match="HTML empty"):
            _validate_against_html(json_candidates, report)

    def test_json_empty_html_nonempty_raises(self, tmp_path):
        report = _write_fake_report(str(tmp_path), candidates=[("AAPL", 90, "A", 150.0)])
        with pytest.raises(PriceValidationError, match="JSON empty"):
            _validate_against_html([], report)

    def test_html_io_error_raises(self, tmp_path):
        json_candidates = [_make_candidate("AAPL", score=90, grade="A", price=150.0)]
        with (
            patch(
                "live.signal_generator.EarningsReportParser.parse_single_report",
                side_effect=FileNotFoundError("No such file"),
            ),
            pytest.raises(PriceValidationError, match="HTML parse failed"),
        ):
            _validate_against_html(json_candidates, "/nonexistent/report.html")

    def test_duplicate_ticker_in_json_raises(self, tmp_path):
        report = _write_fake_report(str(tmp_path), candidates=[("AAPL", 90, "A", 150.0)])
        json_candidates = [
            _make_candidate("AAPL", score=90, grade="A", price=150.0),
            _make_candidate("AAPL", score=85, grade="A", price=150.0),
        ]
        with pytest.raises(PriceValidationError, match="Duplicate tickers in JSON"):
            _validate_against_html(json_candidates, report)

    def test_duplicate_ticker_in_html_raises(self, tmp_path):
        report = _write_fake_report(
            str(tmp_path),
            candidates=[("AAPL", 90, "A", 150.0), ("AAPL", 85, "A", 150.0)],
        )
        json_candidates = [_make_candidate("AAPL", score=90, grade="A", price=150.0)]
        with pytest.raises(PriceValidationError, match="Duplicate tickers in HTML"):
            _validate_against_html(json_candidates, report)


# ---------------------------------------------------------------------------
# qty guard tests
# ---------------------------------------------------------------------------


def _simple_price_fetcher():
    """Create a FakePriceFetcher with uptrending bars (no trailing stop trigger)."""
    bars = _build_weekly_bars(
        [
            ("2025-09-08", 100),
            ("2025-09-15", 105),
            ("2025-09-22", 110),
            ("2025-09-29", 115),
            ("2025-10-06", 120),
            ("2025-10-13", 125),
            ("2025-10-20", 130),
            ("2025-10-27", 135),
            ("2025-11-03", 140),
            ("2025-11-10", 145),
            ("2025-11-17", 150),
            ("2025-11-24", 155),
            ("2025-12-01", 160),
            ("2025-12-08", 165),
            ("2025-12-15", 170),
            ("2025-12-22", 175),
            ("2026-01-05", 180),
            ("2026-01-12", 185),
            ("2026-01-19", 190),
            ("2026-01-26", 195),
            ("2026-02-02", 200),
            ("2026-02-09", 205),
            ("2026-02-16", 210),
        ]
    )
    return FakePriceFetcher({"DEFAULT": bars})


class TestQtyGuard:
    """Tests for qty=0 guard across all entry paths."""

    @pytest.fixture
    def db(self):
        return StateDB(":memory:")

    @pytest.fixture
    def config(self):
        return LiveConfig(max_positions=3, daily_entry_limit=10)

    @pytest.fixture
    def price_fetcher(self):
        return _simple_price_fetcher()

    def test_ema_entry_skipped_when_qty_zero(self, db, config, price_fetcher):
        """Candidate with price > position_size results in qty=0 and is skipped."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # price=99999 -> qty = int(10000/99999) = 0
            report = _write_fake_report(
                tmp_dir,
                report_date="2026-02-19",
                candidates=[("EXPENSIVE", 90, "A", 99999.0)],
            )
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-19",
                run_id="test-qty-zero",
            )

        ema = result["ema_p10"]
        assert len(ema["entries"]) == 0
        qty_zero_skipped = [s for s in ema["skipped"] if s.get("reason") == "qty_zero"]
        assert len(qty_zero_skipped) == 1
        assert qty_zero_skipped[0]["ticker"] == "EXPENSIVE"

    def test_ema_rotation_cancelled_when_qty_zero(self, db, config, price_fetcher):
        """Rotation with qty=0 candidate should not exit the weakest position."""
        config = LiveConfig(max_positions=1, daily_entry_limit=10, rotation=True)

        with tempfile.TemporaryDirectory() as tmp_dir:
            # Existing position (weakest)
            _add_db_position(db, "OLD", score=30, entry_price=50.0, shares=200)

            # New candidate with absurd price -> qty=0
            report = _write_fake_report(
                tmp_dir,
                report_date="2026-02-19",
                candidates=[("EXPENSIVE", 95, "A", 99999.0)],
            )
            # Need alpaca positions for rotation logic
            mock_alpaca = _mock_alpaca_client(
                positions=[
                    {
                        "symbol": "OLD",
                        "qty": "200",
                        "unrealized_pl": "-100.0",
                    }
                ]
            )

            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=mock_alpaca,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-19",
                run_id="test-rot-qty-zero",
                force=True,
            )

        ema = result["ema_p10"]
        # No rotation should have happened
        assert len(ema["entries"]) == 0
        # OLD should NOT have been exited
        rotated_exits = [e for e in ema["exits"] if e.get("reason") == "rotated_out"]
        assert len(rotated_exits) == 0

    def test_shadow_entry_skipped_when_qty_zero(self, db, config, price_fetcher):
        """Shadow path: candidate with price > position_size is skipped."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(
                tmp_dir,
                report_date="2026-02-19",
                candidates=[("EXPENSIVE", 90, "A", 99999.0)],
            )
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-19",
                run_id="test-shadow-qty-zero",
            )

        nwl = result["nwl_p4"]
        assert len(nwl["entries"]) == 0
        qty_zero_skipped = [s for s in nwl["skipped"] if s.get("reason") == "qty_zero"]
        assert len(qty_zero_skipped) == 1

    def test_shadow_rotation_cancelled_when_qty_zero(self, db, config, price_fetcher):
        """Shadow rotation with qty=0 candidate should not exit the weakest."""
        config = LiveConfig(max_positions=1, daily_entry_limit=10, rotation=True)

        with tempfile.TemporaryDirectory() as tmp_dir:
            # Add shadow position
            db.add_shadow_position(
                strategy="nwl_p4",
                ticker="OLD",
                entry_date="2026-02-10",
                entry_price=50.0,
                shares=200,
                invested=10000.0,
                stop_price=45.0,
                report_date="2026-02-10",
                score=30,
                grade="C",
            )

            report = _write_fake_report(
                tmp_dir,
                report_date="2026-02-19",
                candidates=[("EXPENSIVE", 95, "A", 99999.0)],
            )
            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-19",
                run_id="test-shadow-rot-qty-zero",
                dry_run=True,
            )

        nwl = result["nwl_p4"]
        assert len(nwl["entries"]) == 0
        rotated_exits = [e for e in nwl["exits"] if e.get("reason") == "rotated_out"]
        assert len(rotated_exits) == 0


# ---------------------------------------------------------------------------
# Fail-closed integration tests
# ---------------------------------------------------------------------------


class TestFailClosedIntegration:
    """Integration tests for fail-closed behavior."""

    @pytest.fixture
    def db(self):
        return StateDB(":memory:")

    @pytest.fixture
    def config(self):
        return LiveConfig(max_positions=3, daily_entry_limit=10)

    @pytest.fixture
    def price_fetcher(self):
        return _simple_price_fetcher()

    def test_json_broken_exits_continue(self, db, config, price_fetcher):
        """Broken JSON -> entry=0, exit continues, HTML parser not called for fallback."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(
                tmp_dir,
                report_date="2026-02-19",
                candidates=[("CRDO", 92, "A", 80.0)],
            )
            # Write broken JSON
            json_path = os.path.join(tmp_dir, "earnings_trade_candidates_2026-02-19.json")
            with open(json_path, "w") as f:
                f.write("{invalid json")

            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-19",
                run_id="test-broken-json",
            )

        ema = result["ema_p10"]
        assert ema["price_validation_failed"] is True
        assert len(ema["entries"]) == 0
        # Exits structure should still be present (empty since no positions)
        assert isinstance(ema["exits"], list)

    def test_price_mismatch_exits_continue(self, db, config, price_fetcher):
        """Price mismatch -> entries blocked, exit still works."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # HTML has price=80, JSON has price=200 -> mismatch
            report = _write_fake_report(
                tmp_dir,
                report_date="2026-02-19",
                candidates=[("CRDO", 92, "A", 80.0)],
            )
            json_data = {
                "report_date": "2026-02-19",
                "candidates": [{"ticker": "CRDO", "grade": "A", "score": 92, "price": 200.0}],
            }
            json_path = os.path.join(tmp_dir, "earnings_trade_candidates_2026-02-19.json")
            with open(json_path, "w") as f:
                json.dump(json_data, f)

            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-19",
                run_id="test-mismatch",
            )

        ema = result["ema_p10"]
        assert ema["price_validation_failed"] is True
        assert len(ema["entries"]) == 0

    def test_validation_failed_flag_in_both_signals(self, db, config, price_fetcher):
        """price_validation_failed=True appears in both ema_p10 and nwl_p4."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = _write_fake_report(
                tmp_dir,
                report_date="2026-02-19",
                candidates=[("CRDO", 92, "A", 80.0)],
            )
            # Write broken JSON to trigger validation failure
            json_path = os.path.join(tmp_dir, "earnings_trade_candidates_2026-02-19.json")
            with open(json_path, "w") as f:
                f.write("[]")  # root is list, not dict

            result = generate_signals(
                config=config,
                state_db=db,
                alpaca_client=None,
                price_fetcher=price_fetcher,
                report_file=report,
                output_dir=os.path.join(tmp_dir, "signals"),
                trade_date="2026-02-19",
                run_id="test-flag-both",
            )

        assert result["ema_p10"]["price_validation_failed"] is True
        assert result["nwl_p4"]["price_validation_failed"] is True
