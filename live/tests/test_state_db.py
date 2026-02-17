#!/usr/bin/env python3
"""Tests for live.state_db using in-memory SQLite."""

import json
import sqlite3

import pytest

from live.state_db import TERMINAL_STATUSES, StateDB


@pytest.fixture
def db() -> StateDB:
    """Create a fresh in-memory StateDB for each test."""
    return StateDB(":memory:")


class TestTableCreation:
    """Verify all tables exist after initialization."""

    def test_tables_created(self, db: StateDB) -> None:
        with db._connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            table_names = sorted(
                row["name"] for row in rows if not row["name"].startswith("sqlite_")
            )
        expected = sorted(
            [
                "orders",
                "positions",
                "run_log",
                "shadow_positions",
                "shadow_signals",
                "system_config",
            ]
        )
        assert table_names == expected

    def test_system_config_defaults(self, db: StateDB) -> None:
        with db._connect() as conn:
            row = conn.execute(
                "SELECT value FROM system_config WHERE key = 'kill_switch'"
            ).fetchone()
        assert row is not None
        assert row["value"] == "false"


class TestKillSwitch:
    """Test kill switch behavior."""

    def test_default_off(self, db: StateDB) -> None:
        assert db.is_kill_switch_on() is False

    def test_set_on(self, db: StateDB) -> None:
        db.set_kill_switch(True)
        assert db.is_kill_switch_on() is True

    def test_set_off_after_on(self, db: StateDB) -> None:
        db.set_kill_switch(True)
        db.set_kill_switch(False)
        assert db.is_kill_switch_on() is False


class TestPositions:
    """Test position CRUD operations."""

    def _add_sample_position(self, db: StateDB, ticker: str = "AAPL") -> int:
        return db.add_position(
            ticker=ticker,
            entry_date="2026-02-16",
            entry_price=150.0,
            target_shares=66,
            actual_shares=66,
            invested=9900.0,
            stop_price=135.0,
            stop_order_id="stop-001",
            score=85.0,
            grade="A",
            grade_source="backtest",
            report_date="2026-02-15",
            company_name="Apple Inc.",
            gap_size=3.5,
        )

    def test_add_position_returns_id(self, db: StateDB) -> None:
        pid = self._add_sample_position(db)
        assert pid == 1

    def test_get_open_positions(self, db: StateDB) -> None:
        self._add_sample_position(db, "AAPL")
        self._add_sample_position(db, "MSFT")
        positions = db.get_open_positions()
        assert len(positions) == 2
        tickers = [p["ticker"] for p in positions]
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    def test_close_position(self, db: StateDB) -> None:
        pid = self._add_sample_position(db)
        db.close_position(
            position_id=pid,
            exit_date="2026-02-20",
            exit_price=160.0,
            exit_reason="trailing_stop",
            pnl=660.0,
            return_pct=6.67,
        )
        open_positions = db.get_open_positions()
        assert len(open_positions) == 0

    def test_close_position_preserves_data(self, db: StateDB) -> None:
        pid = self._add_sample_position(db)
        db.close_position(pid, "2026-02-20", 160.0, "trailing_stop", 660.0, 6.67)
        with db._connect() as conn:
            row = conn.execute("SELECT * FROM positions WHERE position_id = ?", (pid,)).fetchone()
        pos = dict(row)
        assert pos["exit_date"] == "2026-02-20"
        assert pos["exit_price"] == 160.0
        assert pos["exit_reason"] == "trailing_stop"
        assert pos["pnl"] == 660.0
        assert pos["return_pct"] == 6.67

    def test_update_position_shares(self, db: StateDB) -> None:
        pid = self._add_sample_position(db)
        db.update_position_shares(pid, 60)
        positions = db.get_open_positions()
        assert positions[0]["actual_shares"] == 60

    def test_update_stop_order_id(self, db: StateDB) -> None:
        pid = self._add_sample_position(db)
        db.update_stop_order_id(pid, "new-stop-002")
        positions = db.get_open_positions()
        assert positions[0]["stop_order_id"] == "new-stop-002"

    def test_open_excludes_closed(self, db: StateDB) -> None:
        pid1 = self._add_sample_position(db, "AAPL")
        self._add_sample_position(db, "MSFT")
        db.close_position(pid1, "2026-02-20", 160.0, "stop_loss", -100.0, -1.0)
        open_positions = db.get_open_positions()
        assert len(open_positions) == 1
        assert open_positions[0]["ticker"] == "MSFT"


class TestOrders:
    """Test order CRUD operations."""

    def test_add_order_returns_id(self, db: StateDB) -> None:
        oid = db.add_order(
            client_order_id="entry-AAPL-20260216",
            ticker="AAPL",
            side="buy",
            intent="entry",
            trade_date="2026-02-16",
            qty=66,
            run_id="run-001",
        )
        assert oid == 1

    def test_get_order_by_client_id(self, db: StateDB) -> None:
        db.add_order(
            client_order_id="entry-AAPL-20260216",
            ticker="AAPL",
            side="buy",
            intent="entry",
            trade_date="2026-02-16",
            qty=66,
        )
        order = db.get_order_by_client_id("entry-AAPL-20260216")
        assert order is not None
        assert order["ticker"] == "AAPL"
        assert order["side"] == "buy"
        assert order["intent"] == "entry"
        assert order["status"] == "pending"

    def test_get_order_by_client_id_not_found(self, db: StateDB) -> None:
        result = db.get_order_by_client_id("nonexistent")
        assert result is None

    def test_update_order_status(self, db: StateDB) -> None:
        oid = db.add_order(
            client_order_id="entry-AAPL-20260216",
            ticker="AAPL",
            side="buy",
            intent="entry",
            trade_date="2026-02-16",
            qty=66,
        )
        db.update_order_status(
            order_id=oid,
            status="filled",
            fill_price=150.50,
            filled_qty=66,
            remaining_qty=0,
        )
        order = db.get_order_by_client_id("entry-AAPL-20260216")
        assert order["status"] == "filled"
        assert order["fill_price"] == 150.50
        assert order["filled_qty"] == 66
        assert order["remaining_qty"] == 0

    def test_update_order_rejected(self, db: StateDB) -> None:
        oid = db.add_order(
            client_order_id="entry-AAPL-20260216",
            ticker="AAPL",
            side="buy",
            intent="entry",
            trade_date="2026-02-16",
            qty=66,
        )
        db.update_order_status(
            order_id=oid, status="rejected", reject_reason="insufficient buying power"
        )
        order = db.get_order_by_client_id("entry-AAPL-20260216")
        assert order["status"] == "rejected"
        assert order["reject_reason"] == "insufficient buying power"

    def test_unique_client_order_id(self, db: StateDB) -> None:
        db.add_order(
            client_order_id="entry-AAPL-20260216",
            ticker="AAPL",
            side="buy",
            intent="entry",
            trade_date="2026-02-16",
            qty=66,
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.add_order(
                client_order_id="entry-AAPL-20260216",
                ticker="AAPL",
                side="buy",
                intent="entry",
                trade_date="2026-02-16",
                qty=66,
            )

    def test_daily_order_count_all(self, db: StateDB) -> None:
        db.add_order("e1", "AAPL", "buy", "entry", "2026-02-16", 66)
        db.add_order("e2", "MSFT", "buy", "entry", "2026-02-16", 50)
        db.add_order("x1", "TSLA", "sell", "exit", "2026-02-16", 30)
        db.add_order("e3", "GOOG", "buy", "entry", "2026-02-17", 40)
        assert db.get_daily_order_count("2026-02-16") == 3
        assert db.get_daily_order_count("2026-02-17") == 1

    def test_daily_order_count_by_intent(self, db: StateDB) -> None:
        db.add_order("e1", "AAPL", "buy", "entry", "2026-02-16", 66)
        db.add_order("e2", "MSFT", "buy", "entry", "2026-02-16", 50)
        db.add_order("x1", "TSLA", "sell", "exit", "2026-02-16", 30)
        assert db.get_daily_order_count("2026-02-16", intent="entry") == 2
        assert db.get_daily_order_count("2026-02-16", intent="exit") == 1
        assert db.get_daily_order_count("2026-02-16", intent="stop") == 0

    def test_add_order_with_alpaca_id(self, db: StateDB) -> None:
        db.add_order(
            client_order_id="entry-AAPL-20260216",
            ticker="AAPL",
            side="buy",
            intent="entry",
            trade_date="2026-02-16",
            qty=66,
            alpaca_order_id="alp-abc-123",
        )
        order = db.get_order_by_client_id("entry-AAPL-20260216")
        assert order["alpaca_order_id"] == "alp-abc-123"


class TestRunLog:
    """Test run log operations."""

    def test_add_and_complete_run(self, db: StateDB) -> None:
        db.add_run_log(
            run_id="run-20260216-001",
            run_date="2026-02-16",
            phase="morning",
            signals_file="/tmp/signals.json",
        )
        db.complete_run_log(
            run_id="run-20260216-001",
            status="completed",
            exits_count=2,
            entries_count=5,
            skipped_count=1,
        )
        with db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM run_log WHERE run_id = ?", ("run-20260216-001",)
            ).fetchone()
        run = dict(row)
        assert run["status"] == "completed"
        assert run["exits_count"] == 2
        assert run["entries_count"] == 5
        assert run["skipped_count"] == 1
        assert run["completed_at"] is not None

    def test_run_log_failed(self, db: StateDB) -> None:
        db.add_run_log("run-fail", "2026-02-16", "morning")
        db.complete_run_log("run-fail", status="failed", error_message="Connection timeout")
        with db._connect() as conn:
            row = conn.execute("SELECT * FROM run_log WHERE run_id = ?", ("run-fail",)).fetchone()
        assert dict(row)["error_message"] == "Connection timeout"


class TestShadowPositions:
    """Test shadow position CRUD operations."""

    def _add_shadow(
        self,
        db,
        strategy="nweek_low",
        ticker="NVDA",
        entry_date="2026-02-16",
        entry_price=300.0,
        shares=33,
        invested=9900.0,
        stop_price=270.0,
        report_date="2026-02-14",
        score=90.0,
        grade="A",
    ):
        return db.add_shadow_position(
            strategy=strategy,
            ticker=ticker,
            entry_date=entry_date,
            entry_price=entry_price,
            shares=shares,
            invested=invested,
            stop_price=stop_price,
            report_date=report_date,
            score=score,
            grade=grade,
        )

    def test_add_shadow_position(self, db: StateDB) -> None:
        sid = self._add_shadow(db)
        assert sid == 1

    def test_get_shadow_positions_by_strategy(self, db: StateDB) -> None:
        self._add_shadow(db, strategy="nweek_low", ticker="NVDA")
        self._add_shadow(
            db,
            strategy="ema",
            ticker="AAPL",
            entry_price=150.0,
            shares=66,
            invested=9900.0,
            stop_price=135.0,
        )
        self._add_shadow(
            db,
            strategy="nweek_low",
            ticker="MSFT",
            entry_price=400.0,
            shares=25,
            invested=10000.0,
            stop_price=360.0,
        )
        nwl = db.get_shadow_positions("nweek_low")
        ema = db.get_shadow_positions("ema")
        assert len(nwl) == 2
        assert len(ema) == 1
        assert ema[0]["ticker"] == "AAPL"

    def test_close_shadow_position(self, db: StateDB) -> None:
        sid = self._add_shadow(db)
        db.close_shadow_position(
            shadow_id=sid,
            exit_date="2026-02-20",
            exit_price=320.0,
            exit_reason="trailing_stop",
            pnl=20.0,
            return_pct=6.67,
        )
        open_positions = db.get_shadow_positions("nweek_low")
        assert len(open_positions) == 0

    def test_add_shadow_signals(self, db: StateDB) -> None:
        signals = [{"ticker": "NVDA", "score": 90}, {"ticker": "AAPL", "score": 85}]
        sig_id = db.add_shadow_signals(
            trade_date="2026-02-16",
            strategy="nweek_low",
            signals_json=json.dumps(signals),
        )
        assert sig_id == 1
        with db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM shadow_signals WHERE signal_id = ?", (sig_id,)
            ).fetchone()
        stored = json.loads(dict(row)["signals_json"])
        assert len(stored) == 2
        assert stored[0]["ticker"] == "NVDA"


class TestPendingOrders:
    """Test get_pending_orders with various statuses."""

    def test_pending_statuses_returned(self, db: StateDB) -> None:
        """pending, new, accepted, partially_filled are returned."""
        for status, cid in [
            ("pending", "o1"),
            ("new", "o2"),
            ("accepted", "o3"),
            ("partially_filled", "o4"),
        ]:
            oid = db.add_order(
                client_order_id=cid,
                ticker="AAPL",
                side="buy",
                intent="entry",
                trade_date="2026-02-17",
                qty=10,
            )
            db.update_order_status(oid, status=status)
        results = db.get_pending_orders("2026-02-17")
        assert len(results) == 4

    def test_terminal_statuses_excluded(self, db: StateDB) -> None:
        """Terminal statuses are excluded from pending orders."""
        for status in TERMINAL_STATUSES:
            oid = db.add_order(
                client_order_id=f"t-{status}",
                ticker="AAPL",
                side="buy",
                intent="entry",
                trade_date="2026-02-17",
                qty=10,
            )
            db.update_order_status(oid, status=status)
        results = db.get_pending_orders("2026-02-17")
        assert len(results) == 0

    def test_filter_by_intent(self, db: StateDB) -> None:
        db.add_order("e1", "AAPL", "buy", "entry", "2026-02-17", 10)
        db.add_order("s1", "AAPL", "sell", "stop", "2026-02-17", 10)
        entries = db.get_pending_orders("2026-02-17", intent="entry")
        assert len(entries) == 1
        assert entries[0]["intent"] == "entry"

    def test_filter_by_side(self, db: StateDB) -> None:
        db.add_order("b1", "AAPL", "buy", "entry", "2026-02-17", 10)
        db.add_order("s1", "MSFT", "sell", "exit", "2026-02-17", 10)
        buys = db.get_pending_orders("2026-02-17", side="buy")
        assert len(buys) == 1
        assert buys[0]["side"] == "buy"

    def test_filter_by_trade_date(self, db: StateDB) -> None:
        db.add_order("d1", "AAPL", "buy", "entry", "2026-02-17", 10)
        db.add_order("d2", "MSFT", "buy", "entry", "2026-02-18", 10)
        results = db.get_pending_orders("2026-02-17")
        assert len(results) == 1

    def test_planned_stop_price_stored(self, db: StateDB) -> None:
        db.add_order(
            client_order_id="ps1",
            ticker="AAPL",
            side="buy",
            intent="entry",
            trade_date="2026-02-17",
            qty=10,
            planned_stop_price=135.0,
        )
        order = db.get_order_by_client_id("ps1")
        assert order["planned_stop_price"] == 135.0

    def test_planned_stop_price_default_null(self, db: StateDB) -> None:
        db.add_order(
            client_order_id="pn1",
            ticker="AAPL",
            side="buy",
            intent="entry",
            trade_date="2026-02-17",
            qty=10,
        )
        order = db.get_order_by_client_id("pn1")
        assert order["planned_stop_price"] is None


class TestMigration:
    """Test schema migration for planned_stop_price column."""

    def test_migration_adds_column(self) -> None:
        """StateDB adds planned_stop_price to old schema via migration."""
        # Create a DB with the old schema (no planned_stop_price)
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_order_id TEXT NOT NULL UNIQUE,
                alpaca_order_id TEXT,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL,
                intent TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                qty INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                fill_price REAL,
                filled_qty INTEGER,
                remaining_qty INTEGER,
                reject_reason TEXT,
                run_id TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

        # Wrap with StateDB — migration should run
        db = StateDB.__new__(StateDB)
        db.db_path = ":memory:"
        db._persistent_conn = conn
        conn.row_factory = sqlite3.Row
        db._init_db()

        # Verify column exists after migration
        cursor = conn.execute("PRAGMA table_info(orders)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "planned_stop_price" in columns

        # Verify add_order works with planned_stop_price
        db.add_order(
            client_order_id="mig-test",
            ticker="AAPL",
            side="buy",
            intent="entry",
            trade_date="2026-02-17",
            qty=10,
            planned_stop_price=90.0,
        )
        order = db.get_order_by_client_id("mig-test")
        assert order["planned_stop_price"] == 90.0
