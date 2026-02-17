#!/usr/bin/env python3
"""Order executor for live paper trading via Alpaca API.

Processes trade signals JSON (entries/exits) through a phased pipeline:
  A: Cancel stops + sell exits
  B: Poll sell orders until filled
  C: Recount positions
  D: Buy entries + place protective stops
  E: Poll buy orders until filled

Supports two execution modes:
  --phase all   : Run A-E in one process (default, day TIF only)
  --phase place : Run A-D, skip polling (for OPG pre-market orders)
  --phase poll  : Run poll phase only (DB-driven, no signals file needed)

Usage:
  python -m live.executor --signals-file signals.json [--phase place] [--dry-run] [-v]
  python -m live.executor --state-db live/state.db --phase poll [--dry-run] [-v]
"""

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from live.alpaca_client import AlpacaClient
from live.config import ET, LiveConfig, resolve_api_key
from live.state_db import TERMINAL_STATUSES, StateDB

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5  # seconds between order status polls
POLL_TIMEOUT = 60  # max seconds to wait for order fill (phase B/E)
POLL_TIMEOUT_OPG = 300  # max seconds for poll phase (OPG fill waiting)


def _generate_run_id(trade_date: str) -> str:
    """Generate a unique run ID for this execution."""
    short_uuid = uuid.uuid4().hex[:8]
    return f"exec-{trade_date}-{short_uuid}"


def _poll_orders(
    alpaca_client: AlpacaClient,
    state_db: StateDB,
    order_ids: List[Dict[str, Any]],
    dry_run: bool = False,
    poll_timeout: int = POLL_TIMEOUT,
) -> Dict[str, Dict[str, Any]]:
    """Poll a list of orders until all are filled or timeout.

    Args:
        order_ids: list of dicts with keys: alpaca_order_id, db_order_id, ticker
        poll_timeout: max seconds to wait for fills
    Returns:
        dict mapping alpaca_order_id to final order status dict
    """
    if dry_run or not order_ids:
        return {}

    results = {}
    pending = list(order_ids)
    elapsed = 0

    while pending and elapsed < poll_timeout:
        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        still_pending = []

        for item in pending:
            alpaca_id = item["alpaca_order_id"]
            db_id = item["db_order_id"]
            ticker = item["ticker"]

            try:
                order = alpaca_client.get_order(alpaca_id)
            except Exception as e:
                logger.error("Failed to poll order %s (%s): %s", alpaca_id, ticker, e)
                still_pending.append(item)
                continue

            status = order.get("status", "unknown")
            if status == "filled":
                fill_price = float(order.get("filled_avg_price", 0))
                filled_qty = int(order.get("filled_qty", 0))
                state_db.update_order_status(
                    db_id,
                    status="filled",
                    fill_price=fill_price,
                    filled_qty=filled_qty,
                    remaining_qty=0,
                )
                results[alpaca_id] = order
                logger.info(
                    "Order filled: %s %s @ %.2f (qty=%d)",
                    ticker,
                    alpaca_id,
                    fill_price,
                    filled_qty,
                )
            elif status == "partially_filled":
                filled_qty = int(order.get("filled_qty", 0))
                remaining = int(order.get("qty", 0)) - filled_qty
                state_db.update_order_status(
                    db_id,
                    status="partially_filled",
                    filled_qty=filled_qty,
                    remaining_qty=remaining,
                )
                logger.warning(
                    "Partial fill: %s filled=%d remaining=%d",
                    ticker,
                    filled_qty,
                    remaining,
                )
                still_pending.append(item)
            elif status in (
                "canceled",
                "expired",
                "rejected",
                "done_for_day",
                "suspended",
            ):
                reason = order.get("reject_reason") or status
                state_db.update_order_status(
                    db_id,
                    status=status,
                    reject_reason=reason,
                )
                results[alpaca_id] = order
                logger.warning("Order %s: %s (%s)", status, ticker, alpaca_id)
            else:
                still_pending.append(item)

        pending = still_pending

    if pending:
        for item in pending:
            logger.warning(
                "Order poll timeout: %s (%s)",
                item["ticker"],
                item["alpaca_order_id"],
            )

    return results


def _is_market_hours_et(alpaca_client: Optional[AlpacaClient]) -> bool:
    """Check if current time is within market hours (9:28-19:00 ET).

    Uses Alpaca clock timestamp for accuracy, falls back to local time.
    Returns True if within market hours (OPG should be blocked).
    """
    try:
        if alpaca_client:
            clock = alpaca_client.get_clock()
            ts = clock.get("timestamp", "")
            if ts:
                # Alpaca returns ISO format timestamp
                clock_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                et_now = clock_dt.astimezone(ET)
            else:
                et_now = datetime.now(ET)
        else:
            et_now = datetime.now(ET)
    except Exception:
        et_now = datetime.now(ET)

    hour, minute = et_now.hour, et_now.minute
    # Block OPG between 9:28 ET and 19:00 ET
    after_928 = (hour > 9) or (hour == 9 and minute >= 28)
    before_1900 = hour < 19
    return after_928 and before_1900


def execute_signals(
    config: LiveConfig,
    state_db: StateDB,
    alpaca_client: AlpacaClient,
    signals: Dict[str, Any],
    trade_date: str,
    run_id: str,
    dry_run: bool = False,
    skip_time_check: bool = False,
    skip_poll: bool = False,
) -> Dict[str, int]:
    """Execute trade signals through the phased pipeline.

    Returns dict with counts: exits_executed, entries_executed, skipped.
    Calls sys.exit(3) if kill switch is on.
    """
    # Kill switch check
    if state_db.is_kill_switch_on():
        logger.critical("KILL SWITCH is ON. Aborting execution.")
        sys.exit(3)

    # Strategy guard: only execute ema_p10 signals
    strategy = signals.get("strategy", "")
    if strategy and strategy != "ema_p10":
        logger.error(
            "Refusing to execute non-ema_p10 signals (strategy=%s). "
            "Only ema_p10.json should be passed to executor.",
            strategy,
        )
        sys.exit(5)

    is_opg = config.entry_tif == "opg"

    state_db.add_run_log(
        run_id=run_id,
        run_date=trade_date,
        phase="place" if skip_poll else "execute",
        signals_file=str(signals.get("_source_file", "")),
    )

    exits = signals.get("exits", [])
    entries = signals.get("entries", [])
    counts = {"exits_executed": 0, "entries_executed": 0, "skipped": 0}

    # ── Phase A: Stop Cancel + Sell ─────────────────────────────────────
    sell_orders_to_poll: List[Dict[str, Any]] = []

    for ex in exits:
        ticker = ex["ticker"]
        position_id = ex.get("position_id")
        qty = ex.get("qty", ex.get("current_shares", ex.get("shares", 0)))
        stop_order_id = ex.get("stop_order_id")

        logger.info("Phase A: processing exit for %s (position_id=%s)", ticker, position_id)

        # A-1/A-2: Cancel existing stop order
        if stop_order_id:
            if not dry_run:
                try:
                    alpaca_client.cancel_order(stop_order_id)
                    logger.info("Canceled stop order %s for %s", stop_order_id, ticker)
                except Exception as e:
                    error_msg = str(e)
                    if "filled" in error_msg.lower() or "422" in error_msg:
                        logger.info(
                            "Stop already filled for %s, skipping market sell",
                            ticker,
                        )
                        if position_id:
                            state_db.close_position(
                                position_id=position_id,
                                exit_date=trade_date,
                                exit_price=ex.get("stop_price", 0),
                                exit_reason="stop_filled",
                                pnl=ex.get("pnl", 0),
                                return_pct=ex.get("return_pct", 0),
                            )
                        counts["exits_executed"] += 1
                        continue
                    else:
                        logger.error("Failed to cancel stop %s: %s", stop_order_id, e)
            else:
                logger.info("[DRY RUN] Would cancel stop %s for %s", stop_order_id, ticker)

        # A-3: Place market sell
        client_order_id = f"{trade_date}_{ticker}_exit_sell"

        # Idempotency: check local DB
        existing = state_db.get_order_by_client_id(client_order_id)
        if existing:
            logger.info("Sell order already exists for %s (idempotent skip)", ticker)
            # Only count toward exits_executed (slot release) if order is
            # still active (non-terminal). Terminal orders have already been
            # processed and should not inflate the slot calculation.
            if existing["status"] not in TERMINAL_STATUSES:
                counts["exits_executed"] += 1
            continue

        # Idempotency: check Alpaca
        if not dry_run:
            alpaca_existing = alpaca_client.get_order_by_client_id(client_order_id)
            if alpaca_existing:
                logger.info(
                    "Sell order already on Alpaca for %s (idempotent skip)",
                    ticker,
                )
                alpaca_status = alpaca_existing.get("status", "")
                if alpaca_status not in TERMINAL_STATUSES:
                    counts["exits_executed"] += 1
                continue

        if dry_run:
            logger.info("[DRY RUN] Would sell %d shares of %s", qty, ticker)
            counts["exits_executed"] += 1
            continue

        try:
            order = alpaca_client.place_order(
                symbol=ticker,
                qty=qty,
                side="sell",
                type="market",
                time_in_force="day",
                client_order_id=client_order_id,
            )
            alpaca_order_id = order["id"]
            db_order_id = state_db.add_order(
                client_order_id=client_order_id,
                ticker=ticker,
                side="sell",
                intent="exit",
                trade_date=trade_date,
                qty=qty,
                run_id=run_id,
                alpaca_order_id=alpaca_order_id,
            )
            sell_orders_to_poll.append(
                {
                    "alpaca_order_id": alpaca_order_id,
                    "db_order_id": db_order_id,
                    "ticker": ticker,
                    "position_id": position_id,
                    "entry_price": ex.get("entry_price", 0),
                    "exit_reason": ex.get("reason", "signal_exit"),
                }
            )
            counts["exits_executed"] += 1
            logger.info("Placed sell order for %s: %s", ticker, alpaca_order_id)
        except Exception as e:
            logger.error("Failed to place sell order for %s: %s", ticker, e)
            counts["skipped"] += 1

    # ── Phase B: Sell Polling ───────────────────────────────────────────
    sell_results = _poll_orders(alpaca_client, state_db, sell_orders_to_poll, dry_run)

    for item in sell_orders_to_poll:
        alpaca_id = item["alpaca_order_id"]
        position_id = item.get("position_id")
        if alpaca_id in sell_results and position_id:
            result = sell_results[alpaca_id]
            if result.get("status") == "filled":
                fill_price = float(result.get("filled_avg_price", 0))
                entry_price = item.get("entry_price", 0)
                qty = int(result.get("filled_qty", 0))
                pnl = (fill_price - entry_price) * qty if entry_price else 0
                return_pct = ((fill_price / entry_price) - 1) * 100 if entry_price else 0
                state_db.close_position(
                    position_id=position_id,
                    exit_date=trade_date,
                    exit_price=fill_price,
                    exit_reason=item.get("exit_reason", "signal_exit"),
                    pnl=pnl,
                    return_pct=return_pct,
                )

    # ── Phase C: Position Recount ───────────────────────────────────────
    if is_opg and skip_poll:
        # OPG place phase: DB-based count, subtract exits actually processed
        db_open_count = len(state_db.get_open_positions())
        real_open_count = db_open_count - counts["exits_executed"]
        real_open_count = max(real_open_count, 0)
    elif not dry_run:
        real_positions = alpaca_client.get_positions()
        real_open_count = len(real_positions)
    else:
        open_db_positions = state_db.get_open_positions()
        real_open_count = len(open_db_positions)

    real_available = config.max_positions - real_open_count
    logger.info(
        "Phase C: %d open positions, %d slots available",
        real_open_count,
        real_available,
    )

    # ── Phase D: Buy + Stop (with time guard) ───────────────────────────
    buy_orders_to_poll: List[Dict[str, Any]] = []

    for entry in entries:
        ticker = entry["ticker"]
        qty = entry.get("qty", entry.get("shares", 0))
        stop_price = entry.get("stop_price", 0)

        # Time guard
        if not skip_time_check and not dry_run:
            if is_opg:
                # OPG mode: block if market is open (9:28-19:00 ET)
                if _is_market_hours_et(alpaca_client):
                    logger.warning(
                        "OPG entry blocked for %s: market hours (9:28-19:00 ET)",
                        ticker,
                    )
                    counts["skipped"] += 1
                    continue
            else:
                try:
                    clock = alpaca_client.get_clock()
                    if clock.get("is_open"):
                        now_et = datetime.now(ET)
                        market_open_et = now_et.replace(
                            hour=9,
                            minute=30,
                            second=0,
                            microsecond=0,
                        )
                        minutes_since_open = (now_et - market_open_et).total_seconds() / 60
                        if minutes_since_open > config.entry_cutoff_minutes:
                            logger.warning(
                                "Entry blocked for %s: %d min after open (cutoff=%d)",
                                ticker,
                                int(minutes_since_open),
                                config.entry_cutoff_minutes,
                            )
                            counts["skipped"] += 1
                            continue
                except Exception as e:
                    logger.warning("Time guard check failed: %s (proceeding)", e)

        # Slot check
        if real_available <= 0:
            logger.warning("No slots available, skipping entry for %s", ticker)
            counts["skipped"] += 1
            continue

        # Buying power check
        if not dry_run:
            try:
                account = alpaca_client.get_account()
                buying_power = float(account.get("buying_power", 0))
                if buying_power < config.min_buying_power:
                    logger.warning(
                        "Insufficient buying power ($%.2f < $%.2f), skipping %s",
                        buying_power,
                        config.min_buying_power,
                        ticker,
                    )
                    counts["skipped"] += 1
                    continue
            except Exception as e:
                logger.error("Failed to check buying power: %s", e)
                counts["skipped"] += 1
                continue

        # Daily order limit check
        trade_order_count = state_db.get_daily_order_count(
            trade_date, intent="entry"
        ) + state_db.get_daily_order_count(trade_date, intent="exit")
        stop_order_count = state_db.get_daily_order_count(trade_date, intent="stop")

        if trade_order_count >= config.max_daily_trade_orders:
            logger.warning(
                "Daily trade order limit reached (%d/%d), skipping %s",
                trade_order_count,
                config.max_daily_trade_orders,
                ticker,
            )
            counts["skipped"] += 1
            continue

        if stop_order_count >= config.max_daily_stop_orders:
            logger.warning(
                "Daily stop order limit reached (%d/%d), skipping %s",
                stop_order_count,
                config.max_daily_stop_orders,
                ticker,
            )
            counts["skipped"] += 1
            continue

        # Idempotency
        client_order_id = f"{trade_date}_{ticker}_entry_buy"
        existing = state_db.get_order_by_client_id(client_order_id)
        if existing:
            logger.info("Buy order already exists for %s (idempotent skip)", ticker)
            counts["entries_executed"] += 1
            continue

        if not dry_run:
            alpaca_existing = alpaca_client.get_order_by_client_id(client_order_id)
            if alpaca_existing:
                logger.info(
                    "Buy order already on Alpaca for %s (idempotent skip)",
                    ticker,
                )
                counts["entries_executed"] += 1
                continue

        if dry_run:
            logger.info(
                "[DRY RUN] Would buy %d shares of %s (stop=%.2f, tif=%s)",
                qty,
                ticker,
                stop_price,
                config.entry_tif,
            )
            counts["entries_executed"] += 1
            real_available -= 1
            continue

        # Place order: OPG mode skips bracket, uses tif="opg"
        alpaca_order_id = None
        stop_leg_id = None
        used_bracket = False

        if is_opg:
            # OPG: plain buy with tif="opg", no bracket
            try:
                order = alpaca_client.place_order(
                    symbol=ticker,
                    qty=qty,
                    side="buy",
                    type="market",
                    time_in_force="opg",
                    client_order_id=client_order_id,
                )
                alpaca_order_id = order["id"]
                logger.info(
                    "Placed OPG buy order for %s: %s",
                    ticker,
                    alpaca_order_id,
                )
            except Exception as e:
                logger.error("Failed to place OPG buy order for %s: %s", ticker, e)
                counts["skipped"] += 1
                continue
        else:
            # Day mode: try bracket order first, fallback to separate orders
            try:
                order = alpaca_client.place_bracket_order(
                    symbol=ticker,
                    qty=qty,
                    side="buy",
                    time_in_force="day",
                    stop_price=stop_price,
                    client_order_id=client_order_id,
                )
                alpaca_order_id = order["id"]
                legs = order.get("legs", [])
                if legs:
                    stop_leg_id = legs[0]["id"]
                used_bracket = True
                logger.info("Placed bracket order for %s: %s", ticker, alpaca_order_id)
            except Exception as e:
                logger.warning(
                    "Bracket order failed for %s: %s, falling back",
                    ticker,
                    e,
                )
                try:
                    order = alpaca_client.place_order(
                        symbol=ticker,
                        qty=qty,
                        side="buy",
                        type="market",
                        time_in_force="day",
                        client_order_id=client_order_id,
                    )
                    alpaca_order_id = order["id"]
                    logger.info(
                        "Placed fallback buy order for %s: %s",
                        ticker,
                        alpaca_order_id,
                    )
                except Exception as e2:
                    logger.error("Failed to place buy order for %s: %s", ticker, e2)
                    counts["skipped"] += 1
                    continue

        db_order_id = state_db.add_order(
            client_order_id=client_order_id,
            ticker=ticker,
            side="buy",
            intent="entry",
            trade_date=trade_date,
            qty=qty,
            run_id=run_id,
            alpaca_order_id=alpaca_order_id,
            planned_stop_price=stop_price if stop_price else None,
        )

        buy_orders_to_poll.append(
            {
                "alpaca_order_id": alpaca_order_id,
                "db_order_id": db_order_id,
                "ticker": ticker,
                "qty": qty,
                "stop_price": stop_price,
                "entry": entry,
                "used_bracket": used_bracket,
                "stop_leg_id": stop_leg_id,
            }
        )

        real_available -= 1
        counts["entries_executed"] += 1

    # ── Phase E: Buy Polling ────────────────────────────────────────────
    if skip_poll:
        logger.info("Phase E skipped (--phase place)")
    else:
        buy_results = _poll_orders(
            alpaca_client,
            state_db,
            buy_orders_to_poll,
            dry_run,
        )

        for item in buy_orders_to_poll:
            alpaca_id = item["alpaca_order_id"]
            ticker = item["ticker"]
            entry = item["entry"]

            if alpaca_id in buy_results:
                result = buy_results[alpaca_id]
                if result.get("status") == "filled":
                    fill_price = float(result.get("filled_avg_price", 0))
                    filled_qty = int(result.get("filled_qty", 0))
                    actual_stop_order_id = item.get("stop_leg_id")

                    # If not bracket, place separate GTC stop
                    if not item["used_bracket"]:
                        stop_client_id = f"{trade_date}_{ticker}_stop_sell"
                        try:
                            stop_order = alpaca_client.place_order(
                                symbol=ticker,
                                qty=filled_qty,
                                side="sell",
                                type="stop",
                                time_in_force="gtc",
                                stop_price=item["stop_price"],
                                client_order_id=stop_client_id,
                            )
                            actual_stop_order_id = stop_order["id"]
                            state_db.add_order(
                                client_order_id=stop_client_id,
                                ticker=ticker,
                                side="sell",
                                intent="stop",
                                trade_date=trade_date,
                                qty=filled_qty,
                                run_id=run_id,
                                alpaca_order_id=actual_stop_order_id,
                            )
                            logger.info(
                                "Placed GTC stop for %s: %s @ %.2f",
                                ticker,
                                actual_stop_order_id,
                                item["stop_price"],
                            )
                        except Exception as e:
                            logger.critical(
                                "FAILED to place stop for %s: %s — ACTIVATING KILL SWITCH",
                                ticker,
                                e,
                            )
                            state_db.set_kill_switch(True)
                            actual_stop_order_id = None

                    # Record position
                    position_id = state_db.add_position(
                        ticker=ticker,
                        entry_date=trade_date,
                        entry_price=fill_price,
                        target_shares=item["qty"],
                        actual_shares=filled_qty,
                        invested=fill_price * filled_qty,
                        stop_price=item["stop_price"],
                        stop_order_id=actual_stop_order_id,
                        score=entry.get("score"),
                        grade=entry.get("grade"),
                        grade_source=entry.get("grade_source"),
                        report_date=entry.get("report_date"),
                        company_name=entry.get("company_name"),
                        gap_size=entry.get("gap_size"),
                    )
                    logger.info(
                        "Recorded position %d: %s %d shares @ %.2f",
                        position_id,
                        ticker,
                        filled_qty,
                        fill_price,
                    )

    # Complete run log
    state_db.complete_run_log(
        run_id=run_id,
        status="completed",
        exits_count=counts["exits_executed"],
        entries_count=counts["entries_executed"],
        skipped_count=counts["skipped"],
    )

    logger.info(
        "Execution complete: exits=%d entries=%d skipped=%d",
        counts["exits_executed"],
        counts["entries_executed"],
        counts["skipped"],
    )
    return counts


def execute_poll_phase(
    config: LiveConfig,
    state_db: StateDB,
    alpaca_client: AlpacaClient,
    trade_date: str,
    run_id: str,
    dry_run: bool = False,
    poll_timeout: int = POLL_TIMEOUT_OPG,
) -> Dict[str, int]:
    """Execute poll phase: check filled OPG orders and place GTC stops.

    DB-driven — no signals file needed. Idempotent: safe to run multiple times.
    Returns dict with counts: filled, stops_placed, unprotected, still_pending.
    """
    if state_db.is_kill_switch_on():
        logger.critical("KILL SWITCH is ON. Aborting poll phase.")
        sys.exit(3)

    state_db.add_run_log(
        run_id=run_id,
        run_date=trade_date,
        phase="poll",
    )

    pending_entries = state_db.get_pending_orders(
        trade_date=trade_date,
        intent="entry",
        side="buy",
    )

    counts = {
        "filled": 0,
        "stops_placed": 0,
        "unprotected": 0,
        "still_pending": 0,
    }

    if not pending_entries:
        logger.info("Poll phase: no pending entry orders for %s", trade_date)
        state_db.complete_run_log(run_id=run_id, status="completed")
        return counts

    logger.info("Poll phase: %d pending entry orders", len(pending_entries))

    # Poll each pending order
    orders_to_poll = []
    for order_row in pending_entries:
        alpaca_id = order_row.get("alpaca_order_id")
        if not alpaca_id:
            logger.warning(
                "No alpaca_order_id for order %d (%s), skipping",
                order_row["order_id"],
                order_row["ticker"],
            )
            continue
        orders_to_poll.append(
            {
                "alpaca_order_id": alpaca_id,
                "db_order_id": order_row["order_id"],
                "ticker": order_row["ticker"],
                "planned_stop_price": order_row.get("planned_stop_price"),
                "qty": order_row["qty"],
            }
        )

    if dry_run:
        logger.info("[DRY RUN] Would poll %d orders", len(orders_to_poll))
        state_db.complete_run_log(run_id=run_id, status="completed")
        return counts

    # Poll for fills
    fill_results = _poll_orders(
        alpaca_client,
        state_db,
        orders_to_poll,
        dry_run=False,
        poll_timeout=poll_timeout,
    )

    unprotected_tickers = []

    for item in orders_to_poll:
        alpaca_id = item["alpaca_order_id"]
        ticker = item["ticker"]
        planned_stop = item["planned_stop_price"]

        if alpaca_id not in fill_results:
            counts["still_pending"] += 1
            continue

        result = fill_results[alpaca_id]
        if result.get("status") != "filled":
            continue

        counts["filled"] += 1
        fill_price = float(result.get("filled_avg_price", 0))
        filled_qty = int(result.get("filled_qty", 0))

        # Check if GTC stop already exists (idempotent)
        stop_client_id = f"{trade_date}_{ticker}_stop_sell"
        existing_stop = state_db.get_order_by_client_id(stop_client_id)
        if existing_stop:
            existing_status = existing_stop.get("status", "")
            if existing_status not in TERMINAL_STATUSES:
                # Stop order is still active — no need to re-place
                logger.info(
                    "GTC stop already exists for %s (status=%s, idempotent skip)",
                    ticker,
                    existing_status,
                )
                counts["stops_placed"] += 1
                _ensure_position_recorded(
                    state_db,
                    ticker,
                    trade_date,
                    fill_price,
                    item["qty"],
                    filled_qty,
                    planned_stop,
                    stop_client_id,
                )
                continue
            else:
                # Stop was canceled/rejected/expired — need to re-place
                logger.warning(
                    "Existing stop for %s has terminal status '%s', re-placing",
                    ticker,
                    existing_status,
                )
                # Use a new client_order_id to avoid UNIQUE constraint
                stop_client_id = f"{trade_date}_{ticker}_stop_sell_retry"

        # Check planned_stop_price
        if planned_stop is None:
            logger.critical(
                "UNPROTECTED POSITION: planned_stop_price is NULL for %s",
                ticker,
            )
            counts["unprotected"] += 1
            unprotected_tickers.append(ticker)
            # Still record position but without stop
            _ensure_position_recorded(
                state_db,
                ticker,
                trade_date,
                fill_price,
                item["qty"],
                filled_qty,
                0.0,
                None,
            )
            continue

        # Place GTC stop
        try:
            stop_order = alpaca_client.place_order(
                symbol=ticker,
                qty=filled_qty,
                side="sell",
                type="stop",
                time_in_force="gtc",
                stop_price=planned_stop,
                client_order_id=stop_client_id,
            )
            stop_order_id = stop_order["id"]
            state_db.add_order(
                client_order_id=stop_client_id,
                ticker=ticker,
                side="sell",
                intent="stop",
                trade_date=trade_date,
                qty=filled_qty,
                run_id=run_id,
                alpaca_order_id=stop_order_id,
            )
            counts["stops_placed"] += 1
            logger.info(
                "Placed GTC stop for %s: %s @ %.2f",
                ticker,
                stop_order_id,
                planned_stop,
            )
        except Exception as e:
            logger.critical(
                "FAILED to place stop for %s: %s — ACTIVATING KILL SWITCH",
                ticker,
                e,
            )
            state_db.set_kill_switch(True)
            counts["unprotected"] += 1
            unprotected_tickers.append(ticker)

        # Record position
        _ensure_position_recorded(
            state_db,
            ticker,
            trade_date,
            fill_price,
            item["qty"],
            filled_qty,
            planned_stop,
            stop_client_id if counts["stops_placed"] > 0 else None,
        )

    # Final summary
    if counts["still_pending"] > 0:
        logger.warning(
            "Poll phase: %d orders still pending after timeout",
            counts["still_pending"],
        )

    if unprotected_tickers:
        logger.critical(
            "UNFILLED STOPS: %d UNPROTECTED POSITION(S): %s — manual stop placement required",
            len(unprotected_tickers),
            ", ".join(unprotected_tickers),
        )

    state_db.complete_run_log(
        run_id=run_id,
        status="completed",
        entries_count=counts["filled"],
    )

    logger.info(
        "Poll phase complete: filled=%d stops=%d unprotected=%d pending=%d",
        counts["filled"],
        counts["stops_placed"],
        counts["unprotected"],
        counts["still_pending"],
    )
    # TODO: Add Slack/email notification for unprotected positions
    return counts


def _ensure_position_recorded(
    state_db: StateDB,
    ticker: str,
    trade_date: str,
    fill_price: float,
    target_shares: int,
    actual_shares: int,
    stop_price: Optional[float],
    stop_client_id: Optional[str],
) -> None:
    """Record position if not already exists (idempotent)."""
    open_positions = state_db.get_open_positions()
    for pos in open_positions:
        if pos["ticker"] == ticker and pos["entry_date"] == trade_date:
            return  # Already recorded

    # Look up stop order ID if available
    stop_order_id = None
    if stop_client_id:
        stop_order = state_db.get_order_by_client_id(stop_client_id)
        if stop_order:
            stop_order_id = stop_order.get("alpaca_order_id")

    state_db.add_position(
        ticker=ticker,
        entry_date=trade_date,
        entry_price=fill_price,
        target_shares=target_shares,
        actual_shares=actual_shares,
        invested=fill_price * actual_shares,
        stop_price=stop_price or 0.0,
        stop_order_id=stop_order_id,
        score=None,
        grade=None,
        grade_source=None,
        report_date=None,
        company_name=None,
        gap_size=None,
    )
    logger.info(
        "Recorded position: %s %d shares @ %.2f",
        ticker,
        actual_shares,
        fill_price,
    )


def main() -> None:
    """CLI entry point for executor."""
    parser = argparse.ArgumentParser(
        description="Execute trade signals via Alpaca API",
    )
    parser.add_argument(
        "--signals-file",
        default=None,
        help="Path to signals JSON file (required for place/all phases)",
    )
    parser.add_argument(
        "--state-db",
        default="live/state.db",
        help="Path to SQLite state DB",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Path to run_manifest.json for verification",
    )
    parser.add_argument(
        "--phase",
        choices=["place", "poll", "all"],
        default="all",
        help="Execution phase: place (A-D), poll (fill check + stops), all (A-E)",
    )
    parser.add_argument(
        "--trade-date",
        default=None,
        help="Trade date (YYYY-MM-DD). Defaults to current ET date.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log actions without executing",
    )
    parser.add_argument(
        "--skip-time-check",
        action="store_true",
        help="Bypass entry time guard (for testing)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    config = LiveConfig()

    # OPG + all → error
    if config.entry_tif == "opg" and args.phase == "all":
        logger.error(
            "OPG mode is incompatible with --phase all. "
            "Use --phase place and --phase poll separately for OPG mode."
        )
        sys.exit(6)

    if args.manifest:
        config.verify_against_manifest(args.manifest)
        logger.info("Manifest verification passed: %s", args.manifest)

    # Resolve trade_date
    trade_date = args.trade_date or datetime.now(ET).strftime("%Y-%m-%d")

    # Resolve API keys (not required for dry-run)
    api_key = resolve_api_key("ALPACA_API_KEY", "alpaca")
    secret_key = resolve_api_key("ALPACA_SECRET_KEY", "alpaca")

    if not args.dry_run and (not api_key or not secret_key):
        logger.critical("Alpaca API keys not found")
        sys.exit(1)

    alpaca_client = None
    if api_key and secret_key:
        alpaca_client = AlpacaClient(
            api_key=api_key,
            secret_key=secret_key,
            base_url=config.alpaca_base_url,
        )

    state_db = StateDB(args.state_db)

    if args.phase == "poll":
        # Poll phase: DB-driven, no signals file needed
        if not alpaca_client and not args.dry_run:
            logger.critical("Alpaca API keys required for poll phase")
            sys.exit(1)
        run_id = _generate_run_id(trade_date)
        logger.info(
            "Starting execution: run_id=%s trade_date=%s phase=%s", run_id, trade_date, args.phase
        )
        if args.dry_run:
            logger.info("DRY RUN mode — no orders will be placed")
        execute_poll_phase(
            config=config,
            state_db=state_db,
            alpaca_client=alpaca_client,
            trade_date=trade_date,
            run_id=run_id,
            dry_run=args.dry_run,
        )
    else:
        # Place or all phase: signals file required
        if not args.signals_file:
            logger.critical("--signals-file is required for --phase %s", args.phase)
            sys.exit(1)

        with open(args.signals_file) as f:
            signals = json.load(f)
        signals["_source_file"] = args.signals_file

        # Use trade_date from signals if available, else from args
        if not args.trade_date:
            trade_date = signals.get("trade_date", trade_date)

        # Generate run_id AFTER trade_date is finalized
        run_id = _generate_run_id(trade_date)
        logger.info(
            "Starting execution: run_id=%s trade_date=%s phase=%s", run_id, trade_date, args.phase
        )
        if args.dry_run:
            logger.info("DRY RUN mode — no orders will be placed")

        execute_signals(
            config=config,
            state_db=state_db,
            alpaca_client=alpaca_client,
            signals=signals,
            trade_date=trade_date,
            run_id=run_id,
            dry_run=args.dry_run,
            skip_time_check=args.skip_time_check,
            skip_poll=(args.phase == "place"),
        )


if __name__ == "__main__":
    main()
