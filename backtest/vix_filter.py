#!/usr/bin/env python3
"""
VIX environment filter for earnings trade backtest.

Skips entries when VIX is elevated (default > 20), as high-volatility
environments degrade gap-up momentum continuation.

Based on 2026-03 week of 0-6 performance during VIX > 20 regime.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from backtest.trade_simulator import SkippedTrade

logger = logging.getLogger(__name__)

VIX_THRESHOLD_DEFAULT = 20.0
VIX_LOOKBACK_DAYS = 5  # business-day fallback window


@dataclass
class VixDay:
    """VIX OHLC for a single trading day."""

    open: float
    close: float


def fetch_vix_data(fetcher, from_date: str, to_date: str) -> Dict[str, VixDay]:
    """Fetch VIX daily open/close via FMP API.

    Returns:
        {date_str: VixDay(open, close)} dict.
    """
    bars = fetcher.fetch_prices("^VIX", from_date, to_date)
    return {bar.date: VixDay(open=bar.open, close=bar.close) for bar in bars}


def _resolve_vix(report_date: str, vix_data: Dict[str, VixDay]) -> Optional[float]:
    """Return the VIX value observable at report_date open (entry time).

    Strategy:
    1. If report_date is a trading day, use its VIX open (observable at entry).
    2. Otherwise, fall back to the most recent prior trading day's close
       (up to VIX_LOOKBACK_DAYS).
    """
    # First: try report_date's open (same-moment as entry)
    if report_date in vix_data:
        return vix_data[report_date].open

    # Fallback: previous trading day's close
    dt = datetime.strptime(report_date, "%Y-%m-%d")
    for offset in range(1, VIX_LOOKBACK_DAYS + 1):
        d = (dt - timedelta(days=offset)).strftime("%Y-%m-%d")
        if d in vix_data:
            return vix_data[d].close
    return None


def should_skip_by_vix(
    report_date: str,
    vix_data: Dict[str, VixDay],
    vix_threshold: float = VIX_THRESHOLD_DEFAULT,
) -> Tuple[bool, Optional[str]]:
    """Check if trade should be skipped due to high VIX.

    Returns:
        (should_skip, reason). Fail-open on missing data.
    """
    vix = _resolve_vix(report_date, vix_data)
    if vix is None:
        logger.debug(f"VIX data missing for {report_date}, fail-open")
        return False, None
    if vix > vix_threshold:
        return True, f"filter_high_vix_{vix_threshold}"
    return False, None


def apply_vix_filter(
    candidates: list,
    vix_data: Dict[str, VixDay],
    vix_threshold: float = VIX_THRESHOLD_DEFAULT,
) -> Tuple[list, List[SkippedTrade]]:
    """Apply VIX filter to candidate list.

    Returns:
        (passed_candidates, skipped_trades)
    """
    passed = []
    skipped = []
    for c in candidates:
        skip, reason = should_skip_by_vix(c.report_date, vix_data, vix_threshold)
        if skip:
            assert reason is not None
            skipped.append(
                SkippedTrade(
                    ticker=c.ticker,
                    report_date=c.report_date,
                    grade=c.grade,
                    score=c.score,
                    skip_reason=reason,
                )
            )
        else:
            passed.append(c)
    return passed, skipped


def is_vix_filter_active(args) -> bool:
    """Determine if VIX filter is active. Safe for missing attributes."""
    return getattr(args, "vix_filter", False) or getattr(args, "vix_threshold", None) is not None


def validate_vix_filter_args(args) -> List[str]:
    """Validate VIX filter CLI args.

    Returns:
        List of error messages (empty if valid).
    """
    errors = []
    th = getattr(args, "vix_threshold", None)
    if th is not None and th <= 0:
        errors.append(f"--vix-threshold must be > 0, got {th}")
    return errors
