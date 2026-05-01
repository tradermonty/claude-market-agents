#!/usr/bin/env python3
"""Reconcile DB positions with actual Alpaca order history.

When the existing _sync_positions_from_alpaca logic in signal_generator skips
a position (stop order canceled, no stop_order_id, etc.), the DB and Alpaca
diverge and signal_generator hard-fails on _reconcile_positions.

This script searches Alpaca's closed-order history for sell-side filled
orders covering each open DB ticker, then closes the DB position with
exit_reason=manual_reconcile.

Usage:
    uv run python scripts/reconcile_positions_from_alpaca.py [--dry-run] [--ticker TICKER]
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from live.alpaca_client import AlpacaClient  # noqa: E402
from live.config import resolve_api_key  # noqa: E402
from live.state_db import StateDB  # noqa: E402

logger = logging.getLogger("reconcile")


def find_latest_sell_fill(
    client: AlpacaClient,
    ticker: str,
    after_date: str,
) -> Optional[Dict[str, Any]]:
    """Return the most recent filled sell order for `ticker` after `after_date`.

    after_date is YYYY-MM-DD; orders with filled_at strictly later than
    that date are considered. Returns the raw order dict, or None if not
    found.
    """
    orders = client._request(
        "GET",
        "/v2/orders",
        params={"status": "closed", "symbols": ticker, "limit": 100, "direction": "desc"},
    )
    for order in orders:
        if order.get("side") != "sell":
            continue
        if order.get("status") != "filled":
            continue
        filled_at = order.get("filled_at")
        if not filled_at:
            continue
        fill_date = filled_at[:10]
        if fill_date <= after_date:
            continue
        return order
    return None


def reconcile_position(
    pos: Dict[str, Any],
    fill: Dict[str, Any],
    state_db: StateDB,
    dry_run: bool,
) -> Dict[str, Any]:
    """Close a single DB position based on a discovered sell fill."""
    fill_price = float(fill["filled_avg_price"])
    filled_qty = int(float(fill["filled_qty"]))
    exit_date = fill["filled_at"][:10]

    qty = filled_qty if filled_qty > 0 else int(pos["actual_shares"])
    entry_price = float(pos["entry_price"])
    pnl = round((fill_price - entry_price) * qty, 2)
    return_pct = round(((fill_price / entry_price) - 1) * 100, 2) if entry_price else 0.0

    summary = {
        "ticker": pos["ticker"],
        "position_id": pos["position_id"],
        "exit_date": exit_date,
        "exit_price": fill_price,
        "qty": qty,
        "entry_price": entry_price,
        "pnl": pnl,
        "return_pct": return_pct,
        "alpaca_order_id": fill.get("id"),
    }

    if dry_run:
        logger.info("[DRY-RUN] would close %s: %s", pos["ticker"], summary)
    else:
        state_db.close_position(
            pos["position_id"],
            exit_date,
            fill_price,
            "manual_reconcile",
            pnl,
            return_pct,
        )
        logger.info(
            "Closed %s pid=%d @%.4f qty=%d pnl=%.2f (%+.2f%%)",
            pos["ticker"],
            pos["position_id"],
            fill_price,
            qty,
            pnl,
            return_pct,
        )

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Show planned closes without writing"
    )
    parser.add_argument("--ticker", help="Restrict to a single ticker")
    parser.add_argument("--db", default=str(REPO_ROOT / "live" / "state.db"))
    parser.add_argument(
        "--after-date",
        default=None,
        help="Only consider Alpaca fills strictly after this date (YYYY-MM-DD). "
        "Defaults to each position's entry_date.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    api_key = resolve_api_key("ALPACA_API_KEY", "alpaca")
    secret = resolve_api_key("ALPACA_SECRET_KEY", "alpaca")
    if not api_key or not secret:
        logger.error("Alpaca credentials not found")
        return 1

    client = AlpacaClient(api_key, secret)
    state_db = StateDB(args.db)

    alpaca_tickers = {p["symbol"] for p in client.get_positions()}
    db_positions = state_db.get_open_positions()
    if args.ticker:
        db_positions = [p for p in db_positions if p["ticker"] == args.ticker]

    candidates = [p for p in db_positions if p["ticker"] not in alpaca_tickers]
    logger.info(
        "DB open: %d, Alpaca open: %d, mismatch (DB-only): %d",
        len(db_positions),
        len(alpaca_tickers),
        len(candidates),
    )

    closed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    total_pnl = 0.0

    for pos in candidates:
        after = args.after_date or pos["entry_date"]
        fill = find_latest_sell_fill(client, pos["ticker"], after)
        if not fill:
            logger.warning(
                "No sell fill found for %s after %s — manual review", pos["ticker"], after
            )
            skipped.append({"ticker": pos["ticker"], "reason": "no_sell_fill"})
            continue
        result = reconcile_position(pos, fill, state_db, args.dry_run)
        closed.append(result)
        total_pnl += result["pnl"]

    logger.info("=" * 60)
    logger.info("Reconciliation summary")
    logger.info("  Closed: %d positions, total PnL=%+.2f", len(closed), total_pnl)
    logger.info("  Skipped: %d (need manual review)", len(skipped))
    if skipped:
        for s in skipped:
            logger.info("    - %s: %s", s["ticker"], s["reason"])
    logger.info("Generated at %s", datetime.utcnow().isoformat() + "Z")
    return 0 if not skipped else 2


if __name__ == "__main__":
    sys.exit(main())
