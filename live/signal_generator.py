#!/usr/bin/env python3
"""Signal generator for live paper trading.

CLI module (python -m live.signal_generator) that generates trade signal
JSON files from earnings HTML reports. Handles both the primary ema_p10
execution path and the nwl_p4 shadow tracking path.
"""

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from backtest.html_parser import EarningsReportParser, TradeCandidate
from backtest.price_fetcher import PriceFetcherProtocol
from live.alpaca_client import AlpacaClient
from live.config import ET, LiveConfig, resolve_api_key
from live.state_db import StateDB
from live.trailing_stop_checker import TrailingStopChecker

logger = logging.getLogger(__name__)

GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


def _filter_candidates(candidates: List[TradeCandidate], min_grade: str) -> List[TradeCandidate]:
    """Filter candidates by minimum grade and sort by score descending."""
    min_rank = GRADE_ORDER.get(min_grade, 3)
    filtered = [
        c for c in candidates if c.grade is not None and GRADE_ORDER.get(c.grade, 99) <= min_rank
    ]
    filtered.sort(key=lambda c: c.score if c.score is not None else 0, reverse=True)
    return filtered


def _reconcile_positions(
    db_positions: List[Dict[str, Any]],
    alpaca_positions: List[Dict[str, Any]],
    force: bool,
) -> None:
    """Compare DB positions with Alpaca positions. Exit on mismatch unless forced.

    Checks both ticker presence and share quantity.
    """
    db_by_ticker = {p["ticker"]: p for p in db_positions}
    alpaca_by_ticker = {p["symbol"]: p for p in alpaca_positions}

    db_tickers = set(db_by_ticker)
    alpaca_tickers = set(alpaca_by_ticker)

    msg_parts: List[str] = []

    in_db_only = db_tickers - alpaca_tickers
    in_alpaca_only = alpaca_tickers - db_tickers
    if in_db_only:
        msg_parts.append(f"  In DB but not Alpaca: {sorted(in_db_only)}")
    if in_alpaca_only:
        msg_parts.append(f"  In Alpaca but not DB: {sorted(in_alpaca_only)}")

    # Check quantity mismatches for shared tickers
    for ticker in sorted(db_tickers & alpaca_tickers):
        db_qty = db_by_ticker[ticker].get("actual_shares", 0)
        alpaca_qty = int(alpaca_by_ticker[ticker].get("qty", 0))
        if db_qty != alpaca_qty:
            msg_parts.append(f"  Qty mismatch {ticker}: DB={db_qty}, Alpaca={alpaca_qty}")

    if not msg_parts:
        logger.info("Position reconciliation OK: %d positions match", len(db_tickers))
        return

    msg = "Position mismatch detected\n" + "\n".join(msg_parts)

    if not force:
        logger.error(msg)
        sys.exit(4)
    else:
        logger.warning("%s\n  Continuing with --force", msg)


def _find_weakest_position(
    db_positions: List[Dict[str, Any]],
    alpaca_positions: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Find the position with the most negative unrealized P&L."""
    alpaca_by_ticker = {p["symbol"]: p for p in alpaca_positions}
    worst = None
    worst_pnl = 0.0
    for pos in db_positions:
        alp = alpaca_by_ticker.get(pos["ticker"])
        if alp is None:
            continue
        unrealized = float(alp.get("unrealized_pl", 0))
        if unrealized < worst_pnl:
            worst_pnl = unrealized
            worst = pos
    return worst


def _find_weakest_shadow(
    shadow_positions: List[Dict[str, Any]],
    config: LiveConfig,
) -> Optional[Dict[str, Any]]:
    """Find the shadow position with the worst theoretical return."""
    worst = None
    worst_ret = 0.0
    for pos in shadow_positions:
        # Approximate return from entry price (no live data for shadow)
        ret = 0.0
        if pos.get("entry_price") and pos["entry_price"] > 0:
            # Use a simple heuristic: score-based ranking (lower score = weaker)
            score = pos.get("score") or 0
            ret = -(100 - score)  # Lower score -> more negative
        if ret < worst_ret:
            worst_ret = ret
            worst = pos
    return worst


def _calculate_qty(price: float, position_size: float) -> int:
    """Calculate number of shares for a given position size."""
    if price <= 0:
        return 0
    return int(position_size / price)


def _calculate_stop_price(price: float, stop_loss_pct: float) -> float:
    """Calculate stop price from entry price and stop loss percentage."""
    return round(price * (1 - stop_loss_pct / 100), 2)


def generate_signals(
    config: LiveConfig,
    state_db: StateDB,
    alpaca_client: Optional[AlpacaClient],
    price_fetcher: PriceFetcherProtocol,
    report_file: str,
    output_dir: str,
    trade_date: str,
    run_id: str,
    force: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Generate trade signals from an earnings report.

    Returns dict with keys 'ema_p10' and 'nwl_p4' signal dicts.
    """
    # 1. Kill switch check
    if state_db.is_kill_switch_on():
        logger.error("Kill switch is ON. Aborting signal generation.")
        sys.exit(3)

    # 2. Parse report
    parser = EarningsReportParser()
    candidates = parser.parse_single_report(report_file)
    logger.info("Parsed %d candidates from %s", len(candidates), report_file)

    # 3. Filter by min_grade
    candidates = _filter_candidates(candidates, config.min_grade)
    logger.info("After grade filter: %d candidates", len(candidates))

    generated_at = datetime.now(ET).isoformat()

    # === ema_p10 path (execution) ===
    ema_signals = _generate_ema_signals(
        config,
        state_db,
        alpaca_client,
        price_fetcher,
        candidates,
        trade_date,
        run_id,
        generated_at,
        force,
    )

    # Write ema signal file
    ema_path = os.path.join(output_dir, f"trade_signals_{trade_date}_ema_p10.json")
    os.makedirs(output_dir, exist_ok=True)
    with open(ema_path, "w") as f:
        json.dump(ema_signals, f, indent=2)
    logger.info("Wrote EMA signals to %s", ema_path)

    # === nwl_p4 path (shadow) ===
    nwl_signals = _generate_shadow_signals(
        config,
        state_db,
        price_fetcher,
        candidates,
        trade_date,
        run_id,
        generated_at,
        dry_run,
    )

    # Write shadow signal file
    nwl_path = os.path.join(output_dir, f"trade_signals_{trade_date}_nwl_p4.json")
    with open(nwl_path, "w") as f:
        json.dump(nwl_signals, f, indent=2)
    logger.info("Wrote NWL signals to %s", nwl_path)

    # Store shadow signals in DB
    if not dry_run:
        state_db.add_shadow_signals(trade_date, "nwl_p4", json.dumps(nwl_signals))

    return {"ema_p10": ema_signals, "nwl_p4": nwl_signals}


def _generate_ema_signals(
    config: LiveConfig,
    state_db: StateDB,
    alpaca_client: Optional[AlpacaClient],
    price_fetcher: PriceFetcherProtocol,
    candidates: List[TradeCandidate],
    trade_date: str,
    run_id: str,
    generated_at: str,
    force: bool,
) -> Dict[str, Any]:
    """Generate EMA trailing stop signals for execution."""
    # 4. Get open positions
    db_positions = state_db.get_open_positions()

    # 5-6. Reconcile with Alpaca
    alpaca_positions: List[Dict[str, Any]] = []
    if alpaca_client is not None:
        alpaca_positions = alpaca_client.get_positions()
        _reconcile_positions(db_positions, alpaca_positions, force)

    # 7. Check trailing stops
    checker = TrailingStopChecker(
        price_fetcher,
        trailing_transition_weeks=config.trailing_transition_weeks,
        fmp_lookback_days=config.fmp_lookback_days,
    )
    exits: List[Dict[str, Any]] = []
    for pos in db_positions:
        result = checker.check_position(
            pos["ticker"],
            pos["entry_date"],
            trade_date,
            config.primary_trailing_stop,
            config.primary_trailing_period,
        )
        if result.should_exit:
            exits.append(
                {
                    "ticker": pos["ticker"],
                    "position_id": pos["position_id"],
                    "reason": "trend_break",
                    "qty": pos["actual_shares"],
                    "entry_price": pos["entry_price"],
                    "stop_order_id": pos.get("stop_order_id"),
                }
            )
            logger.info("EMA exit signal: %s (trend_break)", pos["ticker"])

    # 8. Rotation check
    exit_tickers = {e["ticker"] for e in exits}
    open_after_exits = len(db_positions) - len(exits)
    held_tickers = {p["ticker"] for p in db_positions}
    entries: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    rotation_done = False

    if (
        config.rotation
        and len(db_positions) > 0
        and open_after_exits == config.max_positions
        and candidates
        and not rotation_done
    ):
        weakest = _find_weakest_position(db_positions, alpaca_positions)
        if weakest and weakest["ticker"] not in exit_tickers:
            best_candidate = None
            for c in candidates:
                if c.ticker not in held_tickers and c.ticker not in exit_tickers:
                    best_candidate = c
                    break

            if best_candidate:
                weakest_score = weakest.get("score") or 0
                candidate_score = best_candidate.score or 0
                alpaca_by_ticker = {p["symbol"]: p for p in alpaca_positions}
                weakest_alp = alpaca_by_ticker.get(weakest["ticker"], {})
                weakest_pnl = float(weakest_alp.get("unrealized_pl", 0))

                if candidate_score > weakest_score and weakest_pnl < 0:
                    exits.append(
                        {
                            "ticker": weakest["ticker"],
                            "position_id": weakest["position_id"],
                            "reason": "rotated_out",
                            "qty": weakest["actual_shares"],
                            "entry_price": weakest["entry_price"],
                            "stop_order_id": weakest.get("stop_order_id"),
                        }
                    )
                    exit_tickers.add(weakest["ticker"])
                    price = best_candidate.price or 0
                    qty = _calculate_qty(price, config.position_size)
                    stop_price = _calculate_stop_price(price, config.stop_loss_pct)
                    entries.append(
                        {
                            "ticker": best_candidate.ticker,
                            "side": "buy",
                            "qty": qty,
                            "score": candidate_score,
                            "grade": best_candidate.grade,
                            "report_date": best_candidate.report_date,
                            "company_name": best_candidate.company_name,
                            "stop_price": stop_price,
                        }
                    )
                    held_tickers.add(best_candidate.ticker)
                    rotation_done = True
                    logger.info(
                        "Rotation: exit %s (score=%.1f, pnl=%.2f) -> enter %s (score=%.1f)",
                        weakest["ticker"],
                        weakest_score,
                        weakest_pnl,
                        best_candidate.ticker,
                        candidate_score,
                    )

    # 9. New entries
    open_count = len(db_positions)
    exit_count = len(exits)
    available_slots = config.max_positions - (open_count - exit_count)
    entry_tickers = {e["ticker"] for e in entries}

    for c in candidates:
        if available_slots <= 0:
            break
        if c.ticker in held_tickers:
            skipped.append({"ticker": c.ticker, "reason": "already_held", "score": c.score or 0})
            continue
        if c.ticker in exit_tickers or c.ticker in entry_tickers:
            continue
        price = c.price or 0
        qty = _calculate_qty(price, config.position_size)
        stop_price = _calculate_stop_price(price, config.stop_loss_pct)
        entries.append(
            {
                "ticker": c.ticker,
                "side": "buy",
                "qty": qty,
                "score": c.score or 0,
                "grade": c.grade,
                "report_date": c.report_date,
                "company_name": c.company_name,
                "stop_price": stop_price,
            }
        )
        entry_tickers.add(c.ticker)
        available_slots -= 1

    # Remaining candidates that didn't fit
    for c in candidates:
        if (
            c.ticker not in entry_tickers
            and c.ticker not in held_tickers
            and c.ticker not in exit_tickers
            and c.ticker not in {s["ticker"] for s in skipped}
        ):
            skipped.append({"ticker": c.ticker, "reason": "capacity_full", "score": c.score or 0})

    open_after = open_count - exit_count + len(entries)

    return {
        "trade_date": trade_date,
        "strategy": "ema_p10",
        "run_id": run_id,
        "generated_at": generated_at,
        "exits": exits,
        "entries": entries,
        "skipped": skipped,
        "summary": {
            "total_exits": len(exits),
            "total_entries": len(entries),
            "total_skipped": len(skipped),
            "open_positions_before": open_count,
            "open_positions_after": open_after,
        },
    }


def _generate_shadow_signals(
    config: LiveConfig,
    state_db: StateDB,
    price_fetcher: PriceFetcherProtocol,
    candidates: List[TradeCandidate],
    trade_date: str,
    run_id: str,
    generated_at: str,
    dry_run: bool,
) -> Dict[str, Any]:
    """Generate NWL trailing stop signals for shadow tracking."""
    # 11. Get shadow positions
    shadow_positions = state_db.get_shadow_positions("nwl_p4")

    # 12. Check trailing stops
    checker = TrailingStopChecker(
        price_fetcher,
        trailing_transition_weeks=config.trailing_transition_weeks,
        fmp_lookback_days=config.fmp_lookback_days,
    )
    shadow_exits: List[Dict[str, Any]] = []
    for pos in shadow_positions:
        result = checker.check_position(
            pos["ticker"],
            pos["entry_date"],
            trade_date,
            config.shadow_trailing_stop,
            config.shadow_trailing_period,
        )
        if result.should_exit:
            shadow_exits.append(
                {
                    "ticker": pos["ticker"],
                    "shadow_id": pos["shadow_id"],
                    "reason": "trend_break",
                    "qty": pos["shares"],
                    "entry_price": pos["entry_price"],
                    "last_close": result.last_close,
                }
            )
            logger.info("Shadow exit signal: %s (trend_break)", pos["ticker"])

    # 13. Shadow rotation
    exit_tickers = {e["ticker"] for e in shadow_exits}
    held_tickers = {p["ticker"] for p in shadow_positions}
    shadow_entries: List[Dict[str, Any]] = []
    shadow_skipped: List[Dict[str, Any]] = []
    open_after_exits = len(shadow_positions) - len(shadow_exits)

    if (
        config.rotation
        and len(shadow_positions) > 0
        and open_after_exits == config.max_positions
        and candidates
    ):
        weakest = _find_weakest_shadow(shadow_positions, config)
        if weakest and weakest["ticker"] not in exit_tickers:
            best_candidate = None
            for c in candidates:
                if c.ticker not in held_tickers and c.ticker not in exit_tickers:
                    best_candidate = c
                    break
            if best_candidate:
                weakest_score = weakest.get("score") or 0
                candidate_score = best_candidate.score or 0
                if candidate_score > weakest_score:
                    shadow_exits.append(
                        {
                            "ticker": weakest["ticker"],
                            "shadow_id": weakest["shadow_id"],
                            "reason": "rotated_out",
                            "qty": weakest["shares"],
                            "entry_price": weakest["entry_price"],
                        }
                    )
                    exit_tickers.add(weakest["ticker"])
                    price = best_candidate.price or 0
                    qty = _calculate_qty(price, config.position_size)
                    stop_price = _calculate_stop_price(price, config.stop_loss_pct)
                    shadow_entries.append(
                        {
                            "ticker": best_candidate.ticker,
                            "side": "buy",
                            "qty": qty,
                            "score": candidate_score,
                            "grade": best_candidate.grade,
                            "report_date": best_candidate.report_date,
                            "company_name": best_candidate.company_name,
                            "stop_price": stop_price,
                        }
                    )
                    held_tickers.add(best_candidate.ticker)

    # 14. Shadow entries
    open_count = len(shadow_positions)
    exit_count = len(shadow_exits)
    available_slots = config.max_positions - (open_count - exit_count)
    entry_tickers = {e["ticker"] for e in shadow_entries}

    for c in candidates:
        if available_slots <= 0:
            break
        if c.ticker in held_tickers:
            shadow_skipped.append(
                {"ticker": c.ticker, "reason": "already_held", "score": c.score or 0}
            )
            continue
        if c.ticker in exit_tickers or c.ticker in entry_tickers:
            continue
        price = c.price or 0
        qty = _calculate_qty(price, config.position_size)
        stop_price = _calculate_stop_price(price, config.stop_loss_pct)
        shadow_entries.append(
            {
                "ticker": c.ticker,
                "side": "buy",
                "qty": qty,
                "score": c.score or 0,
                "grade": c.grade,
                "report_date": c.report_date,
                "company_name": c.company_name,
                "stop_price": stop_price,
            }
        )
        entry_tickers.add(c.ticker)
        available_slots -= 1

    # 15. Close shadow positions in DB
    if not dry_run:
        for ex in shadow_exits:
            entry_price = ex["entry_price"]
            # Use last_close from trailing stop check as theoretical exit price
            exit_price = ex.get("last_close") or entry_price
            shares = ex.get("qty", 0)
            pnl = round((exit_price - entry_price) * shares, 2)
            return_pct = round(((exit_price / entry_price) - 1) * 100, 2) if entry_price else 0.0
            state_db.close_shadow_position(
                shadow_id=ex["shadow_id"],
                exit_date=trade_date,
                exit_price=exit_price,
                exit_reason=ex["reason"],
                pnl=pnl,
                return_pct=return_pct,
            )

    # 16. Add shadow entries to DB
    if not dry_run:
        for en in shadow_entries:
            entry_price = (
                en.get("stop_price", 0) / (1 - config.stop_loss_pct / 100)
                if en.get("stop_price")
                else 0
            )
            # Approximate entry price from position size and qty
            if en["qty"] > 0:
                entry_price = config.position_size / en["qty"]
            shares = en["qty"]
            invested = entry_price * shares
            state_db.add_shadow_position(
                strategy="nwl_p4",
                ticker=en["ticker"],
                entry_date=trade_date,
                entry_price=entry_price,
                shares=shares,
                invested=invested,
                stop_price=en.get("stop_price", 0),
                report_date=en.get("report_date", trade_date),
                score=en.get("score"),
                grade=en.get("grade"),
            )

    # Remaining skipped
    for c in candidates:
        if (
            c.ticker not in entry_tickers
            and c.ticker not in held_tickers
            and c.ticker not in exit_tickers
            and c.ticker not in {s["ticker"] for s in shadow_skipped}
        ):
            shadow_skipped.append(
                {"ticker": c.ticker, "reason": "capacity_full", "score": c.score or 0}
            )

    open_after = open_count - exit_count + len(shadow_entries)

    return {
        "trade_date": trade_date,
        "strategy": "nwl_p4",
        "run_id": run_id,
        "generated_at": generated_at,
        "exits": shadow_exits,
        "entries": shadow_entries,
        "skipped": shadow_skipped,
        "summary": {
            "total_exits": len(shadow_exits),
            "total_entries": len(shadow_entries),
            "total_skipped": len(shadow_skipped),
            "open_positions_before": open_count,
            "open_positions_after": open_after,
        },
    }


def main() -> None:
    """CLI entry point for signal generation."""
    parser = argparse.ArgumentParser(
        description="Generate trade signals from earnings HTML reports"
    )
    parser.add_argument("--report-file", required=True, help="Path to earnings HTML report file")
    parser.add_argument(
        "--output-dir",
        default="live/signals/",
        help="Directory for signal JSON files (default: live/signals/)",
    )
    parser.add_argument(
        "--state-db",
        default="live/state.db",
        help="Path to SQLite state DB (default: live/state.db)",
    )
    parser.add_argument(
        "--manifest", default=None, help="Path to run_manifest.json for config verification"
    )
    parser.add_argument(
        "--force", action="store_true", help="Continue despite DB/Alpaca position mismatch"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="No DB writes (shadow updates still execute)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
    )

    config = LiveConfig()

    # Verify against manifest if provided
    if args.manifest:
        config.verify_against_manifest(args.manifest)
        logger.info("Config verified against %s", args.manifest)

    # Resolve API keys
    alpaca_key = resolve_api_key("ALPACA_API_KEY", "alpaca")
    alpaca_secret = resolve_api_key("ALPACA_SECRET_KEY", "alpaca")
    fmp_key = resolve_api_key("FMP_API_KEY", "fmp-server")

    # Create clients
    alpaca_client = None
    if alpaca_key and alpaca_secret:
        alpaca_client = AlpacaClient(alpaca_key, alpaca_secret, config.alpaca_base_url)
    else:
        logger.warning("Alpaca keys not found; skipping reconciliation")

    from backtest.price_fetcher import PriceFetcher

    price_fetcher = PriceFetcher(api_key=fmp_key or "")

    state_db = StateDB(args.state_db)

    # Get trade date from Alpaca clock or use today
    if alpaca_client:
        clock = alpaca_client.get_clock()
        # Alpaca clock timestamp is in ISO format
        trade_date = clock.get("timestamp", "")[:10]
        if not trade_date:
            trade_date = datetime.now(ET).strftime("%Y-%m-%d")
    else:
        trade_date = datetime.now(ET).strftime("%Y-%m-%d")

    run_id = f"sig_{trade_date.replace('-', '')}_{uuid.uuid4().hex[:6]}"

    generate_signals(
        config=config,
        state_db=state_db,
        alpaca_client=alpaca_client,
        price_fetcher=price_fetcher,
        report_file=args.report_file,
        output_dir=args.output_dir,
        trade_date=trade_date,
        run_id=run_id,
        force=args.force,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
