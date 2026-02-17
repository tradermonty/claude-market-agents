#!/usr/bin/env python3
"""
Entry quality filter for earnings trade backtest.

Excludes candidates with historically weak entry profiles:
1. Low-price stocks ($10-30): Win 40.6%, Stop 48.4%
2. High-gap + high-score combos (gap>=10% & score>=85): Win 33.3%, Stop 66.7%

Based on 517-trade analysis (2025-09 ~ 2026-02).
"""

from typing import List, Optional, Tuple

from backtest.trade_simulator import SkippedTrade

# Module constants — defaults from 517-trade analysis
EXCLUDE_PRICE_MIN = 10
EXCLUDE_PRICE_MAX = 30
RISK_GAP_THRESHOLD = 10
RISK_SCORE_THRESHOLD = 85


def should_skip_candidate(
    c,
    price_min: float = EXCLUDE_PRICE_MIN,
    price_max: float = EXCLUDE_PRICE_MAX,
    gap_threshold: float = RISK_GAP_THRESHOLD,
    score_threshold: float = RISK_SCORE_THRESHOLD,
) -> Tuple[bool, Optional[str]]:
    """Check if a candidate should be filtered out.

    Returns:
        (should_skip, reason) where reason is None if not skipped.
    """
    # Rule 1: Low-price exclusion [price_min, price_max)
    if c.price is not None and price_min <= c.price < price_max:
        return True, f"filter_low_price_{price_min}_{price_max}"

    # Rule 2: High-gap + High-score combo
    if (
        c.gap_size is not None
        and c.score is not None
        and c.gap_size >= gap_threshold
        and c.score >= score_threshold
    ):
        return True, f"filter_high_gap_score_{gap_threshold}_{score_threshold}"

    return False, None


def apply_entry_quality_filter(
    candidates: list,
    price_min: float = EXCLUDE_PRICE_MIN,
    price_max: float = EXCLUDE_PRICE_MAX,
    gap_threshold: float = RISK_GAP_THRESHOLD,
    score_threshold: float = RISK_SCORE_THRESHOLD,
) -> Tuple[list, List[SkippedTrade]]:
    """Apply entry quality filter to candidates.

    Returns:
        (passed_candidates, skipped_trades)
    """
    passed = []
    skipped = []
    for c in candidates:
        skip, reason = should_skip_candidate(
            c,
            price_min=price_min,
            price_max=price_max,
            gap_threshold=gap_threshold,
            score_threshold=score_threshold,
        )
        if skip:
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


def is_filter_active(args) -> bool:
    """Determine if entry quality filter is active (truth source).

    Active when the explicit flag is set OR any override parameter is provided.
    """
    return (
        getattr(args, "entry_quality_filter", False)
        or args.exclude_price_min is not None
        or args.exclude_price_max is not None
        or args.risk_gap_threshold is not None
        or args.risk_score_threshold is not None
    )


def validate_filter_args(args) -> List[str]:
    """Validate entry quality filter CLI args.

    Returns:
        List of error messages (empty if valid).
    """
    errors = []

    eff_min = args.exclude_price_min if args.exclude_price_min is not None else EXCLUDE_PRICE_MIN
    eff_max = args.exclude_price_max if args.exclude_price_max is not None else EXCLUDE_PRICE_MAX

    if eff_min < 0:
        errors.append(f"--exclude-price-min must be >= 0, got {eff_min}")
    if eff_max <= eff_min:
        errors.append(f"--exclude-price-max ({eff_max}) must be > --exclude-price-min ({eff_min})")

    if args.risk_gap_threshold is not None and args.risk_gap_threshold < 0:
        errors.append(f"--risk-gap-threshold must be >= 0, got {args.risk_gap_threshold}")

    if args.risk_score_threshold is not None and (
        args.risk_score_threshold < 0 or args.risk_score_threshold > 100
    ):
        errors.append(f"--risk-score-threshold must be 0-100, got {args.risk_score_threshold}")

    return errors
