#!/usr/bin/env python3
"""
Portfolio simulator with position limits and rotation.

Unlike TradeSimulator (which simulates each trade independently),
PortfolioSimulator manages all positions concurrently on a day-by-day
basis to enforce:
- Maximum concurrent positions (capacity)
- No duplicate tickers
- Position rotation (replace weakest losing position with stronger candidate)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from backtest.html_parser import TradeCandidate
from backtest.price_fetcher import PriceBar
from backtest.trade_simulator import SkippedTrade, TradeResult
from backtest.weekly_bars import (
    WeeklyBar,
    aggregate_daily_to_weekly,
    compute_weekly_ema,
    compute_weekly_nweek_low,
    count_completed_weeks,
    is_trend_broken,
    is_week_end_by_date,
)

logger = logging.getLogger(__name__)


@dataclass
class OpenPosition:
    ticker: str
    candidate: TradeCandidate
    entry_date: str
    entry_price: float
    shares: int
    invested: float
    stop_price: float
    weekly_bars: List[WeeklyBar] = field(default_factory=list)
    indicators: List[Optional[float]] = field(default_factory=list)
    pending_exit: Optional[str] = None  # None | "stop_loss" | "trend_break"


class PriceDateIndex:
    """O(1) date-based bar lookup for portfolio simulation."""

    def __init__(self, price_data: Dict[str, List[PriceBar]]):
        self._index: Dict[str, Dict[str, PriceBar]] = {}
        self._all_dates: set = set()

        for ticker, bars in price_data.items():
            ticker_idx: Dict[str, PriceBar] = {}
            for bar in bars:
                ticker_idx[bar.date] = bar
                self._all_dates.add(bar.date)
            self._index[ticker] = ticker_idx

        self._sorted_dates = sorted(self._all_dates)

    def get_bar(self, ticker: str, date: str) -> Optional[PriceBar]:
        return self._index.get(ticker, {}).get(date)

    def get_previous_close(self, ticker: str, date: str) -> Optional[float]:
        ticker_idx = self._index.get(ticker, {})
        prev_bar = None
        for d in self._sorted_dates:
            if d >= date:
                break
            if d in ticker_idx:
                prev_bar = ticker_idx[d]
        if prev_bar is None:
            return None
        adj_c = (
            prev_bar.adj_close
            if (prev_bar.adj_close is not None and prev_bar.adj_close > 0)
            else prev_bar.close
        )
        return adj_c

    def all_trading_dates(self) -> List[str]:
        return self._sorted_dates

    def get_bars_up_to(self, ticker: str, date: str) -> List[PriceBar]:
        """Get all bars for ticker up to and including date, sorted by date."""
        ticker_idx = self._index.get(ticker, {})
        return [ticker_idx[d] for d in self._sorted_dates if d in ticker_idx and d <= date]


class PortfolioSimulator:
    """Day-by-day portfolio simulator with position limits and rotation."""

    VALID_STOP_MODES = ("intraday", "close", "skip_entry_day", "close_next_open")
    VALID_TRAILING_MODES = (None, "weekly_ema", "weekly_nweek_low")

    def __init__(
        self,
        max_positions: int,
        position_size: float = 10000.0,
        stop_loss_pct: float = 10.0,
        slippage_pct: float = 0.5,
        max_holding_days: Optional[int] = 90,
        stop_mode: str = "intraday",
        entry_mode: str = "report_open",
        trailing_stop: Optional[str] = None,
        trailing_ema_period: int = 10,
        trailing_nweek_period: int = 4,
        trailing_transition_weeks: int = 3,
        data_end_date: Optional[str] = None,
        enable_rotation: bool = True,
    ):
        if max_positions < 1:
            raise ValueError(f"max_positions must be >= 1, got {max_positions}")
        if stop_mode not in self.VALID_STOP_MODES:
            raise ValueError(f"Invalid stop_mode: {stop_mode}")
        if trailing_stop not in self.VALID_TRAILING_MODES:
            raise ValueError(f"Invalid trailing_stop: {trailing_stop}")
        if trailing_stop is None and max_holding_days is None:
            raise ValueError("Cannot disable both trailing_stop and max_holding_days")

        self.max_positions = max_positions
        self.position_size = position_size
        self.stop_loss_pct = stop_loss_pct
        self.slippage_pct = slippage_pct
        self.max_holding_days = max_holding_days
        self.stop_mode = stop_mode
        self.entry_mode = entry_mode
        self.trailing_stop = trailing_stop
        self.trailing_ema_period = trailing_ema_period
        self.trailing_nweek_period = trailing_nweek_period
        self.trailing_transition_weeks = trailing_transition_weeks
        self.data_end_date = data_end_date
        self.enable_rotation = enable_rotation

    def simulate_portfolio(
        self,
        candidates: List[TradeCandidate],
        price_data: Dict[str, List[PriceBar]],
    ) -> Tuple[List[TradeResult], List[SkippedTrade]]:
        """Run day-by-day portfolio simulation."""
        index = PriceDateIndex(price_data)
        all_dates = index.all_trading_dates()

        if self.data_end_date:
            all_dates = [d for d in all_dates if d <= self.data_end_date]

        if not all_dates:
            return [], []

        # Build entry schedule: date -> list of candidates entering on that date
        entry_schedule = self._build_entry_schedule(candidates, price_data, index)

        open_positions: List[OpenPosition] = []
        trades: List[TradeResult] = []
        skipped: List[SkippedTrade] = []
        peak_positions = 0

        for date in all_dates:
            # Phase 1 [Open]: Process pending exits at today's open
            exits_today: List[Tuple[OpenPosition, str]] = []
            remaining: List[OpenPosition] = []
            for pos in open_positions:
                if pos.pending_exit is not None:
                    exits_today.append((pos, pos.pending_exit))
                else:
                    remaining.append(pos)

            for pos, exit_reason in exits_today:
                bar = index.get_bar(pos.ticker, date)
                if bar is None:
                    # No bar today; keep position, clear pending
                    pos.pending_exit = None
                    remaining.append(pos)
                    continue
                exit_price = bar.adjusted_open * (1 - self.slippage_pct / 100)
                trades.append(self._close_position(pos, date, exit_price, exit_reason))

            open_positions = remaining

            # Phase 2 [Open]: New entries
            day_candidates = entry_schedule.get(date, [])
            # Sort by score descending (higher score = higher priority)
            day_candidates.sort(key=lambda c: c.score if c.score is not None else -1, reverse=True)
            rotated_today = False

            for cand in day_candidates:
                # Duplicate ticker check
                if any(p.ticker == cand.ticker for p in open_positions):
                    skipped.append(
                        SkippedTrade(
                            ticker=cand.ticker,
                            report_date=cand.report_date,
                            grade=cand.grade,
                            score=cand.score,
                            skip_reason="duplicate_ticker",
                        )
                    )
                    continue

                bar = index.get_bar(cand.ticker, date)
                if bar is None or bar.open <= 0:
                    skipped.append(
                        SkippedTrade(
                            ticker=cand.ticker,
                            report_date=cand.report_date,
                            grade=cand.grade,
                            score=cand.score,
                            skip_reason="no_price_data",
                        )
                    )
                    continue

                entry_price = bar.adjusted_open
                if entry_price <= 0:
                    skipped.append(
                        SkippedTrade(
                            ticker=cand.ticker,
                            report_date=cand.report_date,
                            grade=cand.grade,
                            score=cand.score,
                            skip_reason="missing_ohlc",
                        )
                    )
                    continue

                shares = int(self.position_size / entry_price)
                if shares == 0:
                    skipped.append(
                        SkippedTrade(
                            ticker=cand.ticker,
                            report_date=cand.report_date,
                            grade=cand.grade,
                            score=cand.score,
                            skip_reason="zero_shares",
                        )
                    )
                    continue

                # Capacity check
                if len(open_positions) >= self.max_positions:
                    if self.enable_rotation and not rotated_today:
                        rotated = self._try_rotation(open_positions, cand, index, date, trades)
                        if rotated:
                            rotated_today = True
                        else:
                            skipped.append(
                                SkippedTrade(
                                    ticker=cand.ticker,
                                    report_date=cand.report_date,
                                    grade=cand.grade,
                                    score=cand.score,
                                    skip_reason="capacity_full",
                                )
                            )
                            continue
                    else:
                        skipped.append(
                            SkippedTrade(
                                ticker=cand.ticker,
                                report_date=cand.report_date,
                                grade=cand.grade,
                                score=cand.score,
                                skip_reason="capacity_full",
                            )
                        )
                        continue

                # Open new position
                new_pos = self._open_position(cand, date, entry_price, shares, price_data)
                open_positions.append(new_pos)

            # Phase 3 [Intraday]: Stop loss check
            remaining = []
            for pos in open_positions:
                bar = index.get_bar(pos.ticker, date)
                if bar is None:
                    remaining.append(pos)
                    continue

                entry_dt = datetime.strptime(pos.entry_date, "%Y-%m-%d")
                bar_dt = datetime.strptime(date, "%Y-%m-%d")
                days_held = (bar_dt - entry_dt).days

                stop_hit = self._check_stop_loss(pos, bar, days_held)

                if self.stop_mode == "close_next_open" and stop_hit:
                    pos.pending_exit = "stop_loss"
                    remaining.append(pos)
                elif stop_hit:
                    if self.stop_mode == "close":
                        adj_c = (
                            bar.adj_close
                            if (bar.adj_close is not None and bar.adj_close > 0)
                            else bar.close
                        )
                        exit_price = adj_c * (1 - self.slippage_pct / 100)
                    else:
                        exit_price = pos.stop_price * (1 - self.slippage_pct / 100)
                    trades.append(self._close_position(pos, date, exit_price, "stop_loss"))
                else:
                    remaining.append(pos)
            open_positions = remaining

            # Phase 4 [Close]: Trailing stop check
            if self.trailing_stop is not None:
                for pos in open_positions:
                    if pos.pending_exit is not None:
                        continue
                    bar = index.get_bar(pos.ticker, date)
                    if bar is None:
                        continue
                    bars_up_to = index.get_bars_up_to(pos.ticker, date)
                    if self._is_week_end(bars_up_to, date):
                        weekly = aggregate_daily_to_weekly(bars_up_to)
                        if self.trailing_stop == "weekly_ema":
                            indicators = compute_weekly_ema(weekly, self.trailing_ema_period)
                        else:
                            indicators = compute_weekly_nweek_low(
                                weekly, self.trailing_nweek_period
                            )

                        completed = self._count_completed_weeks(weekly, pos.entry_date, date)
                        if completed >= self.trailing_transition_weeks and self._is_trend_broken(
                            weekly, indicators, date
                        ):
                            pos.pending_exit = "trend_break"

            # Phase 5 [Close]: Max holding check
            if self.max_holding_days is not None:
                remaining = []
                for pos in open_positions:
                    if pos.pending_exit is not None:
                        remaining.append(pos)
                        continue
                    entry_dt = datetime.strptime(pos.entry_date, "%Y-%m-%d")
                    bar_dt = datetime.strptime(date, "%Y-%m-%d")
                    days_held = (bar_dt - entry_dt).days
                    bar = index.get_bar(pos.ticker, date)
                    if days_held >= self.max_holding_days and bar is not None and bar.close > 0:
                        adj_c = (
                            bar.adj_close
                            if (bar.adj_close is not None and bar.adj_close > 0)
                            else bar.close
                        )
                        trades.append(self._close_position(pos, date, adj_c, "max_holding"))
                    else:
                        remaining.append(pos)
                open_positions = remaining

            peak_positions = max(peak_positions, len(open_positions))

        # Close remaining positions at end of data
        last_date = all_dates[-1] if all_dates else None
        for pos in open_positions:
            if last_date:
                bar = index.get_bar(pos.ticker, last_date)
                if bar:
                    adj_c = (
                        bar.adj_close
                        if (bar.adj_close is not None and bar.adj_close > 0)
                        else bar.close
                    )
                    exit_reason = pos.pending_exit if pos.pending_exit else "end_of_data"
                    trades.append(self._close_position(pos, last_date, adj_c, exit_reason))
                else:
                    # Fallback: close at entry price (no data)
                    trades.append(
                        self._close_position(pos, pos.entry_date, pos.entry_price, "end_of_data")
                    )

        logger.info(
            f"Portfolio sim: {len(trades)} trades, {len(skipped)} skipped, "
            f"peak_positions={peak_positions}"
        )
        return trades, skipped

    def _build_entry_schedule(
        self,
        candidates: List[TradeCandidate],
        price_data: Dict[str, List[PriceBar]],
        index: PriceDateIndex,
    ) -> Dict[str, List[TradeCandidate]]:
        """Map each candidate to its entry date."""
        schedule: Dict[str, List[TradeCandidate]] = {}
        for cand in candidates:
            bars = price_data.get(cand.ticker)
            if not bars:
                continue
            if self.data_end_date:
                bars = [b for b in bars if b.date <= self.data_end_date]
            if not bars:
                continue

            report_dt = datetime.strptime(cand.report_date, "%Y-%m-%d")
            if self.entry_mode == "report_open":
                target = report_dt.strftime("%Y-%m-%d")
                entry_date = None
                for b in bars:
                    if b.date >= target:
                        entry_date = b.date
                        break
            else:  # next_day_open
                target = report_dt.strftime("%Y-%m-%d")
                entry_date = None
                for b in bars:
                    if b.date > target:
                        entry_date = b.date
                        break

            if entry_date:
                schedule.setdefault(entry_date, []).append(cand)

        return schedule

    def _open_position(
        self,
        cand: TradeCandidate,
        date: str,
        entry_price: float,
        shares: int,
        price_data: Dict[str, List[PriceBar]],
    ) -> OpenPosition:
        stop_price = entry_price * (1 - self.stop_loss_pct / 100)
        return OpenPosition(
            ticker=cand.ticker,
            candidate=cand,
            entry_date=date,
            entry_price=entry_price,
            shares=shares,
            invested=shares * entry_price,
            stop_price=stop_price,
        )

    def _close_position(
        self, pos: OpenPosition, date: str, exit_price: float, exit_reason: str
    ) -> TradeResult:
        pnl = (exit_price - pos.entry_price) * pos.shares
        return_pct = ((exit_price / pos.entry_price) - 1) * 100 if pos.entry_price > 0 else 0
        entry_dt = datetime.strptime(pos.entry_date, "%Y-%m-%d")
        exit_dt = datetime.strptime(date, "%Y-%m-%d")
        holding_days = (exit_dt - entry_dt).days

        return TradeResult(
            ticker=pos.ticker,
            grade=pos.candidate.grade,
            grade_source=pos.candidate.grade_source,
            score=pos.candidate.score,
            report_date=pos.candidate.report_date,
            entry_date=pos.entry_date,
            entry_price=round(pos.entry_price, 4),
            exit_date=date,
            exit_price=round(exit_price, 4),
            shares=pos.shares,
            invested=round(pos.invested, 2),
            pnl=round(pnl, 2),
            return_pct=round(return_pct, 2),
            holding_days=holding_days,
            exit_reason=exit_reason,
            gap_size=pos.candidate.gap_size,
            company_name=pos.candidate.company_name,
        )

    def _check_stop_loss(self, pos: OpenPosition, bar: PriceBar, days_held: int) -> bool:
        if self.stop_mode == "intraday":
            return bar.low > 0 and bar.adjusted_low <= pos.stop_price
        elif self.stop_mode == "close":
            adj_c = (
                bar.adj_close if (bar.adj_close is not None and bar.adj_close > 0) else bar.close
            )
            return adj_c > 0 and adj_c <= pos.stop_price
        elif self.stop_mode == "skip_entry_day":
            return days_held > 0 and bar.low > 0 and bar.adjusted_low <= pos.stop_price
        elif self.stop_mode == "close_next_open":
            adj_c = (
                bar.adj_close if (bar.adj_close is not None and bar.adj_close > 0) else bar.close
            )
            return adj_c > 0 and adj_c <= pos.stop_price
        return False

    def _try_rotation(
        self,
        open_positions: List[OpenPosition],
        new_candidate: TradeCandidate,
        index: PriceDateIndex,
        date: str,
        trades: List[TradeResult],
    ) -> bool:
        """Try to rotate out the weakest losing position for new_candidate.

        Returns True if rotation occurred (position was removed from open_positions).
        """
        # Find weakest position (most negative unrealized P&L)
        weakest = None
        weakest_pnl = 0.0

        for pos in open_positions:
            prev_close = index.get_previous_close(pos.ticker, date)
            if prev_close is None:
                continue
            unrealized = (prev_close - pos.entry_price) * pos.shares
            if unrealized < weakest_pnl:
                weakest_pnl = unrealized
                weakest = pos

        if weakest is None:
            return False  # No losing positions

        # Score comparison
        new_score = new_candidate.score if new_candidate.score is not None else -1
        weakest_score = weakest.candidate.score if weakest.candidate.score is not None else -1
        if new_score <= weakest_score:
            return False

        # Execute rotation: close weakest at today's open with slippage
        bar = index.get_bar(weakest.ticker, date)
        if bar is None:
            return False
        exit_price = bar.adjusted_open * (1 - self.slippage_pct / 100)
        trades.append(self._close_position(weakest, date, exit_price, "rotated_out"))
        open_positions.remove(weakest)
        return True

    @staticmethod
    def _is_week_end(bars: List[PriceBar], current_date: str) -> bool:
        """Check if current_date is the last trading day of its ISO week in the bar list."""
        return is_week_end_by_date(bars, current_date)

    @staticmethod
    def _count_completed_weeks(
        weekly_bars: List[WeeklyBar], entry_date: str, current_date: str
    ) -> int:
        return count_completed_weeks(weekly_bars, entry_date, current_date)

    def _is_trend_broken(
        self, weekly_bars: List[WeeklyBar], indicators: List[Optional[float]], current_date: str
    ) -> bool:
        return is_trend_broken(weekly_bars, indicators, current_date)
