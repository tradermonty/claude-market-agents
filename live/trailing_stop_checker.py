#!/usr/bin/env python3
"""Trailing stop checker for live trading.

Reuses backtest weekly_bars functions to determine if a position's
trailing stop has been triggered. Supports both weekly EMA and
weekly N-week low modes.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from backtest.price_fetcher import PriceFetcherProtocol
from backtest.weekly_bars import (
    aggregate_daily_to_weekly,
    compute_weekly_ema,
    compute_weekly_nweek_low,
    count_completed_weeks,
    is_trend_broken,
    is_week_end_by_date,
)

logger = logging.getLogger(__name__)


@dataclass
class TrailingStopResult:
    """Result of a trailing stop check for one position."""

    ticker: str
    is_week_end: bool
    completed_weeks: int
    transition_met: bool  # completed_weeks >= trailing_transition_weeks
    trend_broken: bool  # close < indicator
    should_exit: bool  # transition_met AND trend_broken AND is_week_end
    indicator_value: Optional[float] = None
    last_close: Optional[float] = None


class TrailingStopChecker:
    """Check trailing stop conditions using backtest-identical logic.

    Uses PriceFetcher to get historical data, then applies the same
    weekly bar aggregation and trend-break detection as the backtest.
    """

    def __init__(
        self,
        price_fetcher: PriceFetcherProtocol,
        trailing_transition_weeks: int = 2,
        fmp_lookback_days: int = 400,
    ):
        self.price_fetcher = price_fetcher
        self.trailing_transition_weeks = trailing_transition_weeks
        self.fmp_lookback_days = fmp_lookback_days

    def check_position(
        self,
        ticker: str,
        entry_date: str,
        as_of_date: str,
        trailing_stop: str,
        trailing_period: int,
    ) -> TrailingStopResult:
        """Check if a position's trailing stop is triggered.

        Args:
            ticker: Stock symbol.
            entry_date: Position entry date (YYYY-MM-DD).
            as_of_date: Date to evaluate (YYYY-MM-DD).
            trailing_stop: "weekly_ema" or "weekly_nweek_low".
            trailing_period: EMA period or N-week low lookback.

        Returns:
            TrailingStopResult with detailed status.
        """
        # Fetch price data with sufficient lookback for indicator warmup
        as_of_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
        from_date = (as_of_dt - timedelta(days=self.fmp_lookback_days)).strftime("%Y-%m-%d")
        bars = self.price_fetcher.fetch_prices(ticker, from_date, as_of_date)

        if not bars:
            logger.warning("No price data for %s, cannot check trailing stop", ticker)
            return TrailingStopResult(
                ticker=ticker,
                is_week_end=False,
                completed_weeks=0,
                transition_met=False,
                trend_broken=False,
                should_exit=False,
            )

        # Check if as_of_date is the last trading day of its ISO week
        week_end = is_week_end_by_date(bars, as_of_date)

        if not week_end:
            return TrailingStopResult(
                ticker=ticker,
                is_week_end=False,
                completed_weeks=0,
                transition_met=False,
                trend_broken=False,
                should_exit=False,
            )

        # Aggregate to weekly bars
        weekly_bars = aggregate_daily_to_weekly(bars)
        if not weekly_bars:
            return TrailingStopResult(
                ticker=ticker,
                is_week_end=True,
                completed_weeks=0,
                transition_met=False,
                trend_broken=False,
                should_exit=False,
            )

        # Compute indicators
        if trailing_stop == "weekly_ema":
            indicators = compute_weekly_ema(weekly_bars, trailing_period)
        elif trailing_stop == "weekly_nweek_low":
            indicators = compute_weekly_nweek_low(weekly_bars, trailing_period)
        else:
            raise ValueError(f"Unknown trailing_stop mode: {trailing_stop}")

        # Count completed weeks since entry
        completed = count_completed_weeks(weekly_bars, entry_date, as_of_date)
        transition_met = completed >= self.trailing_transition_weeks

        # Check trend break
        broken = False
        indicator_val = None
        last_close = None

        if transition_met:
            broken = is_trend_broken(weekly_bars, indicators, as_of_date)

        # Extract indicator and close values for logging
        for i, wb in enumerate(weekly_bars):
            if wb.week_ending <= as_of_date:
                last_close = wb.close
                if i < len(indicators):
                    indicator_val = indicators[i]

        should_exit = week_end and transition_met and broken

        if should_exit:
            logger.info(
                "%s trailing stop triggered: close=%.2f < indicator=%.2f "
                "(weeks=%d, mode=%s, period=%d)",
                ticker,
                last_close or 0,
                indicator_val or 0,
                completed,
                trailing_stop,
                trailing_period,
            )

        return TrailingStopResult(
            ticker=ticker,
            is_week_end=week_end,
            completed_weeks=completed,
            transition_met=transition_met,
            trend_broken=broken,
            should_exit=should_exit,
            indicator_value=indicator_val,
            last_close=last_close,
        )
