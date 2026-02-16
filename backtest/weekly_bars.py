#!/usr/bin/env python3
"""Weekly bar aggregation and trend indicators for trailing stop.

Provides:
- Daily-to-weekly bar aggregation (ISO week grouping)
- Weekly EMA computation
- Weekly N-week low computation (current week excluded)
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from backtest.price_fetcher import PriceBar


@dataclass
class WeeklyBar:
    week_ending: str  # Last trading day of the week (YYYY-MM-DD)
    week_start: str  # First trading day of the week (YYYY-MM-DD)
    open: float  # Week open (adjusted)
    high: float  # Week high (adjusted)
    low: float  # Week low (adjusted)
    close: float  # Week close (adj_close)
    volume: int  # Total weekly volume


def aggregate_daily_to_weekly(bars: List[PriceBar]) -> List[WeeklyBar]:
    """Aggregate daily PriceBars into weekly bars using ISO week numbers.

    Partial weeks (holidays, short weeks) are valid bars.
    Uses adjusted prices: open=first bar's adjusted_open, high=max(adjusted_high),
    low=min(adjusted_low), close=last bar's adj_close.
    """
    if not bars:
        return []

    # Group bars by ISO (year, week)
    weeks: Dict[Tuple[int, int], List[PriceBar]] = {}
    week_order: List[Tuple[int, int]] = []
    for bar in bars:
        dt = datetime.strptime(bar.date, "%Y-%m-%d")
        iso = dt.isocalendar()
        key = (iso[0], iso[1])
        if key not in weeks:
            weeks[key] = []
            week_order.append(key)
        weeks[key].append(bar)

    result = []
    for key in week_order:
        week_bars = weeks[key]
        first = week_bars[0]
        last = week_bars[-1]

        adj_close = (
            last.adj_close if (last.adj_close is not None and last.adj_close > 0) else last.close
        )

        result.append(
            WeeklyBar(
                week_ending=last.date,
                week_start=first.date,
                open=first.adjusted_open,
                high=max(b.adjusted_high for b in week_bars),
                low=min(b.adjusted_low for b in week_bars),
                close=adj_close,
                volume=sum(b.volume for b in week_bars),
            )
        )

    return result


def compute_weekly_ema(weekly_bars: List[WeeklyBar], period: int) -> List[Optional[float]]:
    """Compute EMA of weekly close prices.

    First `period` bars use SMA as seed, then standard EMA formula.
    Returns None for indices < period (insufficient data).
    """
    if not weekly_bars or period < 1:
        return []

    result: List[Optional[float]] = []
    k = 2.0 / (period + 1)

    for i, wb in enumerate(weekly_bars):
        if i < period - 1:
            result.append(None)
        elif i == period - 1:
            # SMA seed
            sma = sum(weekly_bars[j].close for j in range(period)) / period
            result.append(round(sma, 6))
        else:
            prev = result[i - 1]
            if prev is None:
                result.append(None)
            else:
                ema = wb.close * k + prev * (1 - k)
                result.append(round(ema, 6))

    return result


def compute_weekly_nweek_low(weekly_bars: List[WeeklyBar], period: int) -> List[Optional[float]]:
    """Compute N-week low excluding the current week.

    For index i, returns min(low) of weekly_bars[max(0, i-period):i].
    The current week (index i) is excluded to avoid close < low being always false.
    Returns None when i < period (full-window required, same policy as EMA).
    """
    if not weekly_bars or period < 1:
        return []

    result: List[Optional[float]] = []
    for i in range(len(weekly_bars)):
        if i < period:
            result.append(None)
        else:
            window = weekly_bars[max(0, i - period) : i]
            result.append(min(wb.low for wb in window))

    return result
