#!/usr/bin/env python3
"""Tests for live.signal_generator with mocked dependencies."""

import json
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from backtest.html_parser import TradeCandidate
from backtest.price_fetcher import PriceBar
from backtest.tests.fake_price_fetcher import FakePriceFetcher
from live.config import LiveConfig
from live.signal_generator import (
    _filter_candidates,
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


def _mock_alpaca_client(positions=None, clock_date="2026-02-17"):
    """Create a mock AlpacaClient."""
    client = MagicMock()
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
    return LiveConfig(max_positions=3)


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
        config = LiveConfig(max_positions=3, primary_trailing_period=3)
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
        config = LiveConfig(max_positions=2)

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
