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


def is_week_end_by_date(bars: List[PriceBar], current_date: str) -> bool:
    """Check if current_date is the last trading day of its ISO week.

    Standalone version of PortfolioSimulator._is_week_end.
    Searches bars list for current_date, then checks if next bar is in a different week.
    """
    if not bars:
        return False
    idx = None
    for i, b in enumerate(bars):
        if b.date == current_date:
            idx = i
            break
    if idx is None:
        return False
    cur_dt = datetime.strptime(current_date, "%Y-%m-%d")
    if idx + 1 >= len(bars):
        return True
    nxt_dt = datetime.strptime(bars[idx + 1].date, "%Y-%m-%d")
    return cur_dt.isocalendar()[:2] != nxt_dt.isocalendar()[:2]


def is_week_end_by_index(bars: List[PriceBar], idx: int) -> bool:
    """Check if bar at idx is the last trading day of its ISO week.

    Standalone version of TradeSimulator._is_week_end.
    Index-based lookup (more efficient when index is already known).
    """
    cur = datetime.strptime(bars[idx].date, "%Y-%m-%d")
    if idx + 1 >= len(bars):
        return True
    nxt = datetime.strptime(bars[idx + 1].date, "%Y-%m-%d")
    return cur.isocalendar()[:2] != nxt.isocalendar()[:2]


def count_completed_weeks(weekly_bars: List[WeeklyBar], entry_date: str, current_date: str) -> int:
    """Count weekly bars that started after entry_date and completed by current_date.

    Entry week is always excluded (even if entry is Monday = week_start).
    This ensures the transition period counts only FULL weeks after entry.
    """
    return sum(
        1 for wb in weekly_bars if wb.week_start > entry_date and wb.week_ending <= current_date
    )


def is_trend_broken(
    weekly_bars: List[WeeklyBar],
    indicators: List[Optional[float]],
    current_date: str,
) -> bool:
    """Check if the most recent completed weekly bar broke the trend indicator.

    Returns True if weekly close < indicator value for the last completed week.
    Works with both EMA and N-week low indicators.
    """
    wb_idx = None
    for i, wb in enumerate(weekly_bars):
        if wb.week_ending <= current_date:
            wb_idx = i
    if wb_idx is None or wb_idx >= len(indicators) or indicators[wb_idx] is None:
        return False
    return weekly_bars[wb_idx].close < indicators[wb_idx]
