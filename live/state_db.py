#!/usr/bin/env python3
"""SQLite state management for live paper trading.

Tracks positions, orders, run history, and shadow positions.
Supports :memory: for testing.
"""

import logging
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = (
    "filled",
    "canceled",
    "expired",
    "rejected",
    "done_for_day",
    "suspended",
)

_SCHEMA_POSITIONS = """
CREATE TABLE IF NOT EXISTS positions (
    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    entry_price REAL NOT NULL,
    target_shares INTEGER NOT NULL,
    actual_shares INTEGER NOT NULL DEFAULT 0,
    invested REAL NOT NULL DEFAULT 0.0,
    stop_price REAL NOT NULL,
    stop_order_id TEXT,
    score REAL,
    grade TEXT,
    grade_source TEXT,
    report_date TEXT,
    company_name TEXT,
    gap_size REAL,
    exit_date TEXT,
    exit_price REAL,
    exit_reason TEXT,
    pnl REAL,
    return_pct REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_SCHEMA_ORDERS = """
CREATE TABLE IF NOT EXISTS orders (
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
    planned_stop_price REAL,
    run_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_SCHEMA_RUN_LOG = """
CREATE TABLE IF NOT EXISTS run_log (
    run_id TEXT PRIMARY KEY,
    run_date TEXT NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    signals_file TEXT,
    exits_count INTEGER,
    entries_count INTEGER,
    skipped_count INTEGER,
    error_message TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
)
"""

_SCHEMA_SHADOW_POSITIONS = """
CREATE TABLE IF NOT EXISTS shadow_positions (
    shadow_id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL DEFAULT 'nwl_p4',
    ticker TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    entry_price REAL NOT NULL,
    shares INTEGER NOT NULL,
    invested REAL NOT NULL,
    stop_price REAL NOT NULL,
    score REAL,
    grade TEXT,
    report_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    exit_date TEXT,
    exit_price REAL,
    exit_reason TEXT,
    pnl REAL,
    return_pct REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_SCHEMA_SHADOW_SIGNALS = """
CREATE TABLE IF NOT EXISTS shadow_signals (
    signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    strategy TEXT NOT NULL,
    signals_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_SCHEMA_SYSTEM_CONFIG = """
CREATE TABLE IF NOT EXISTS system_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_SYSTEM_CONFIG_DEFAULTS = """
INSERT OR IGNORE INTO system_config (key, value) VALUES ('kill_switch', 'false')
"""

ALL_SCHEMAS = [
    _SCHEMA_POSITIONS,
    _SCHEMA_ORDERS,
    _SCHEMA_RUN_LOG,
    _SCHEMA_SHADOW_POSITIONS,
    _SCHEMA_SHADOW_SIGNALS,
    _SCHEMA_SYSTEM_CONFIG,
    _SYSTEM_CONFIG_DEFAULTS,
]


class StateDB:
    """SQLite-backed state manager for live trading."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._persistent_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._persistent_conn = sqlite3.connect(":memory:")
            self._persistent_conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if they do not exist."""
        with self._connect() as conn:
            for schema in ALL_SCHEMAS:
                conn.execute(schema)
            conn.commit()
        self._migrate_schema()
        logger.info("StateDB initialized at %s", self.db_path)

    def _migrate_schema(self) -> None:
        """Apply schema migrations for existing databases."""
        with self._connect() as conn:
            # Check if planned_stop_price column exists in orders table
            cursor = conn.execute("PRAGMA table_info(orders)")
            columns = {row[1] for row in cursor.fetchall()}
            if "planned_stop_price" not in columns:
                conn.execute("ALTER TABLE orders ADD COLUMN planned_stop_price REAL")
                conn.commit()
                logger.info("Migrated orders table: added planned_stop_price column")

    @contextmanager
    def _connect(self):
        """Context manager for database connections with row_factory.

        For :memory: databases, reuses a persistent connection so tables
        survive across calls. For file-based databases, opens and closes
        a fresh connection each time.
        """
        if self._persistent_conn is not None:
            yield self._persistent_conn
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                yield conn
            finally:
                conn.close()

    # -- Kill switch ----------------------------------------------------------

    def is_kill_switch_on(self) -> bool:
        """Return True if kill switch is engaged."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM system_config WHERE key = 'kill_switch'"
            ).fetchone()
            return row is not None and row["value"] == "true"

    def set_kill_switch(self, on: bool) -> None:
        """Set the kill switch state."""
        val = "true" if on else "false"
        with self._connect() as conn:
            conn.execute(
                "UPDATE system_config SET value = ?, updated_at = datetime('now') "
                "WHERE key = 'kill_switch'",
                (val,),
            )
            conn.commit()
        logger.warning("Kill switch set to %s", val)

    # -- Positions ------------------------------------------------------------

    def add_position(
        self,
        ticker: str,
        entry_date: str,
        entry_price: float,
        target_shares: int,
        actual_shares: int,
        invested: float,
        stop_price: float,
        stop_order_id: Optional[str],
        score: Optional[float],
        grade: Optional[str],
        grade_source: Optional[str],
        report_date: Optional[str],
        company_name: Optional[str],
        gap_size: Optional[float],
    ) -> int:
        """Insert a new open position. Returns position_id."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO positions (
                    ticker, entry_date, entry_price, target_shares, actual_shares,
                    invested, stop_price, stop_order_id, score, grade,
                    grade_source, report_date, company_name, gap_size
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    entry_date,
                    entry_price,
                    target_shares,
                    actual_shares,
                    invested,
                    stop_price,
                    stop_order_id,
                    score,
                    grade,
                    grade_source,
                    report_date,
                    company_name,
                    gap_size,
                ),
            )
            conn.commit()
            position_id = cursor.lastrowid
        logger.info("Added position %s: %s @ %.2f", position_id, ticker, entry_price)
        return position_id

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Return all positions where exit_date is NULL."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM positions WHERE exit_date IS NULL ORDER BY entry_date"
            ).fetchall()
            return [dict(row) for row in rows]

    def close_position(
        self,
        position_id: int,
        exit_date: str,
        exit_price: float,
        exit_reason: str,
        pnl: float,
        return_pct: float,
    ) -> None:
        """Close an open position with exit details."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE positions
                SET exit_date = ?, exit_price = ?, exit_reason = ?,
                    pnl = ?, return_pct = ?, updated_at = datetime('now')
                WHERE position_id = ?
                """,
                (exit_date, exit_price, exit_reason, pnl, return_pct, position_id),
            )
            conn.commit()
        logger.info("Closed position %s: reason=%s pnl=%.2f", position_id, exit_reason, pnl)

    def update_position_shares(self, position_id: int, actual_shares: int) -> None:
        """Update actual_shares after fill confirmation."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE positions
                SET actual_shares = ?, updated_at = datetime('now')
                WHERE position_id = ?
                """,
                (actual_shares, position_id),
            )
            conn.commit()

    def update_stop_order_id(self, position_id: int, stop_order_id: str) -> None:
        """Update the stop order ID for a position."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE positions
                SET stop_order_id = ?, updated_at = datetime('now')
                WHERE position_id = ?
                """,
                (stop_order_id, position_id),
            )
            conn.commit()

    # -- Orders ---------------------------------------------------------------

    def add_order(
        self,
        client_order_id: str,
        ticker: str,
        side: str,
        intent: str,
        trade_date: str,
        qty: int,
        run_id: Optional[str] = None,
        alpaca_order_id: Optional[str] = None,
        planned_stop_price: Optional[float] = None,
    ) -> int:
        """Insert a new order record. Returns order_id."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO orders (
                    client_order_id, alpaca_order_id, ticker, side, intent,
                    trade_date, qty, run_id, planned_stop_price
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_order_id,
                    alpaca_order_id,
                    ticker,
                    side,
                    intent,
                    trade_date,
                    qty,
                    run_id,
                    planned_stop_price,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def update_order_status(
        self,
        order_id: int,
        status: str,
        fill_price: Optional[float] = None,
        filled_qty: Optional[int] = None,
        remaining_qty: Optional[int] = None,
        reject_reason: Optional[str] = None,
    ) -> None:
        """Update order status and optional fill details."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET status = ?, fill_price = COALESCE(?, fill_price),
                    filled_qty = COALESCE(?, filled_qty),
                    remaining_qty = COALESCE(?, remaining_qty),
                    reject_reason = COALESCE(?, reject_reason),
                    updated_at = datetime('now')
                WHERE order_id = ?
                """,
                (status, fill_price, filled_qty, remaining_qty, reject_reason, order_id),
            )
            conn.commit()

    def get_order_by_client_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        """Look up an order by client_order_id."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_daily_order_count(self, trade_date: str, intent: Optional[str] = None) -> int:
        """Count orders placed on a given trade_date, optionally filtered by intent."""
        with self._connect() as conn:
            if intent:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM orders WHERE trade_date = ? AND intent = ?",
                    (trade_date, intent),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM orders WHERE trade_date = ?",
                    (trade_date,),
                ).fetchone()
            return row["cnt"]

    def get_pending_orders(
        self,
        trade_date: str,
        intent: Optional[str] = None,
        side: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return non-terminal orders for a given trade_date.

        Terminal statuses (filled, canceled, expired, rejected,
        done_for_day, suspended) are excluded.
        """
        query = "SELECT * FROM orders WHERE trade_date = ? AND status NOT IN ({})".format(
            ", ".join("?" for _ in TERMINAL_STATUSES)
        )
        params: list = [trade_date, *TERMINAL_STATUSES]
        if intent:
            query += " AND intent = ?"
            params.append(intent)
        if side:
            query += " AND side = ?"
            params.append(side)
        query += " ORDER BY order_id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    # -- Run log --------------------------------------------------------------

    def add_run_log(
        self,
        run_id: str,
        run_date: str,
        phase: str,
        status: str = "running",
        signals_file: Optional[str] = None,
    ) -> None:
        """Record the start of a run."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO run_log (run_id, run_date, phase, status, signals_file)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, run_date, phase, status, signals_file),
            )
            conn.commit()

    def complete_run_log(
        self,
        run_id: str,
        status: str,
        exits_count: Optional[int] = None,
        entries_count: Optional[int] = None,
        skipped_count: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Mark a run as completed or failed."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE run_log
                SET status = ?, exits_count = ?, entries_count = ?,
                    skipped_count = ?, error_message = ?,
                    completed_at = datetime('now')
                WHERE run_id = ?
                """,
                (status, exits_count, entries_count, skipped_count, error_message, run_id),
            )
            conn.commit()

    # -- Shadow positions -----------------------------------------------------

    def add_shadow_position(
        self,
        strategy: str,
        ticker: str,
        entry_date: str,
        entry_price: float,
        shares: int,
        invested: float,
        stop_price: float,
        report_date: str,
        score: Optional[float] = None,
        grade: Optional[str] = None,
    ) -> int:
        """Insert a shadow (paper-only tracking) position. Returns shadow_id."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO shadow_positions (
                    strategy, ticker, entry_date, entry_price, shares, invested,
                    stop_price, report_date, score, grade
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy,
                    ticker,
                    entry_date,
                    entry_price,
                    shares,
                    invested,
                    stop_price,
                    report_date,
                    score,
                    grade,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_shadow_positions(self, strategy: str) -> List[Dict[str, Any]]:
        """Return open shadow positions for a given strategy."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM shadow_positions "
                "WHERE strategy = ? AND status = 'open' ORDER BY entry_date",
                (strategy,),
            ).fetchall()
            return [dict(row) for row in rows]

    def close_shadow_position(
        self,
        shadow_id: int,
        exit_date: str,
        exit_price: float,
        exit_reason: str,
        pnl: float,
        return_pct: float,
    ) -> None:
        """Close a shadow position with exit details."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE shadow_positions
                SET exit_date = ?, exit_price = ?, exit_reason = ?,
                    pnl = ?, return_pct = ?, status = 'closed',
                    updated_at = datetime('now')
                WHERE shadow_id = ?
                """,
                (exit_date, exit_price, exit_reason, pnl, return_pct, shadow_id),
            )
            conn.commit()

    def add_shadow_signals(self, trade_date: str, strategy: str, signals_json: str) -> int:
        """Store raw signals JSON for a shadow strategy. Returns signal_id."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO shadow_signals (trade_date, strategy, signals_json)
                VALUES (?, ?, ?)
                """,
                (trade_date, strategy, signals_json),
            )
            conn.commit()
            return cursor.lastrowid
