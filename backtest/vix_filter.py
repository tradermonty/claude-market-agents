#!/usr/bin/env python3
"""
VIX environment filter for earnings trade backtest.

Skips entries when VIX is elevated (default > 20), as high-volatility
environments degrade gap-up momentum continuation.

Based on 2026-03 week of 0-6 performance during VIX > 20 regime.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from backtest.trade_simulator import SkippedTrade

logger = logging.getLogger(__name__)

VIX_THRESHOLD_DEFAULT = 20.0
VIX_LOOKBACK_DAYS = 5  # business-day fallback window


def fetch_vix_data(fetcher, from_date: str, to_date: str) -> Dict[str, float]:
    """Fetch VIX daily close prices via FMP API.

    Args:
        fetcher: PriceFetcherProtocol instance.
        from_date: Start date YYYY-MM-DD (include lookback buffer).
        to_date: End date YYYY-MM-DD.

    Returns:
        {date_str: close_price} dict.
    """
    bars = fetcher.fetch_prices("^VIX", from_date, to_date)
    return {bar.date: bar.close for bar in bars}


def _resolve_vix(report_date: str, vix_data: Dict[str, float]) -> Optional[float]:
    """Return VIX close for report_date, falling back up to 5 days for holidays/weekends."""
    dt = datetime.strptime(report_date, "%Y-%m-%d")
    for offset in range(VIX_LOOKBACK_DAYS + 1):
        d = (dt - timedelta(days=offset)).strftime("%Y-%m-%d")
        if d in vix_data:
            return vix_data[d]
    return None


def should_skip_by_vix(
    report_date: str,
    vix_data: Dict[str, float],
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
    vix_data: Dict[str, float],
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
