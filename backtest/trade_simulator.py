#!/usr/bin/env python3
"""
Trade simulator for earnings gap-up backtest.

Simulates long positions with:
- Configurable entry timing (report_open or next_day_open)
- Stop loss with slippage
- Max holding period (90 calendar days, optional)
- Weekly trailing stop (EMA or N-week low based)
- Fixed position sizing ($10,000)
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from backtest.price_fetcher import PriceBar

logger = logging.getLogger(__name__)


@dataclass
class TradeResult:
    ticker: str
    grade: str
    grade_source: str  # "html" | "inferred"
    score: Optional[float]
    report_date: str
    entry_date: str
    entry_price: float  # Open price
    exit_date: str
    exit_price: float
    shares: int
    invested: float  # shares * entry_price
    pnl: float
    return_pct: float
    holding_days: int  # Calendar days
    exit_reason: str  # "stop_loss" | "max_holding" | "end_of_data" | "trend_break"
    gap_size: Optional[float] = None
    company_name: Optional[str] = None


@dataclass
class SkippedTrade:
    ticker: str
    report_date: str
    grade: str
    score: Optional[float]
    skip_reason: str  # "no_price_data" | "zero_shares" | "missing_ohlc"


class TradeSimulator:
    """Simulate individual trades with stop loss and max holding period."""

    VALID_STOP_MODES = ("intraday", "close", "skip_entry_day", "close_next_open")
    VALID_ENTRY_MODES = ("report_open", "next_day_open")
    VALID_TRAILING_MODES = (None, "weekly_ema", "weekly_nweek_low")

    def __init__(
        self,
        position_size: float = 10000.0,
        stop_loss_pct: float = 10.0,
        slippage_pct: float = 0.5,
        max_holding_days: Optional[int] = 90,
        stop_mode: str = "intraday",
        daily_entry_limit: Optional[int] = None,
        entry_mode: str = "report_open",
        trailing_stop: Optional[str] = None,
        trailing_ema_period: int = 10,
        trailing_nweek_period: int = 4,
        trailing_transition_weeks: int = 3,
        data_end_date: Optional[str] = None,
    ):
        if stop_mode not in self.VALID_STOP_MODES:
            raise ValueError(
                f"Invalid stop_mode: {stop_mode}. Must be one of {self.VALID_STOP_MODES}"
            )
        if entry_mode not in self.VALID_ENTRY_MODES:
            raise ValueError(
                f"Invalid entry_mode: {entry_mode}. Must be one of {self.VALID_ENTRY_MODES}"
            )
        if trailing_stop not in self.VALID_TRAILING_MODES:
            raise ValueError(
                f"Invalid trailing_stop: {trailing_stop}. Must be one of {self.VALID_TRAILING_MODES}"
            )
        if trailing_stop is None and max_holding_days is None:
            raise ValueError(
                "Cannot disable both trailing_stop and max_holding_days (no exit mechanism)"
            )
        if trailing_ema_period < 2:
            raise ValueError(f"trailing_ema_period must be >= 2, got {trailing_ema_period}")
        if trailing_nweek_period < 2:
            raise ValueError(f"trailing_nweek_period must be >= 2, got {trailing_nweek_period}")
        if trailing_transition_weeks < 0:
            raise ValueError(
                f"trailing_transition_weeks must be >= 0, got {trailing_transition_weeks}"
            )
        if data_end_date is not None:
            try:
                datetime.strptime(data_end_date, "%Y-%m-%d")
            except ValueError as err:
                raise ValueError(f"data_end_date must be YYYY-MM-DD, got {data_end_date}") from err

        self.position_size = position_size
        self.stop_loss_pct = stop_loss_pct
        self.slippage_pct = slippage_pct
        self.max_holding_days = max_holding_days
        self.stop_mode = stop_mode
        self.daily_entry_limit = daily_entry_limit
        self.entry_mode = entry_mode
        self.trailing_stop = trailing_stop
        self.trailing_ema_period = trailing_ema_period
        self.trailing_nweek_period = trailing_nweek_period
        self.trailing_transition_weeks = trailing_transition_weeks
        self.data_end_date = data_end_date

    def _truncate_bars(self, bars: List[PriceBar]) -> List[PriceBar]:
        """Truncate bars to data_end_date (inclusive). Returns bars unchanged if no data_end_date.

        Called in BOTH simulate_all() and _simulate_single():
        - simulate_all(): ensures daily_entry_limit ranking excludes post-cutoff candidates
        - _simulate_single(): ensures all bar references (entry_idx, week_end, loop) are bounded
        Both calls are necessary; removing either creates subtle data leakage.
        """
        if not self.data_end_date:
            return bars
        return [b for b in bars if b.date <= self.data_end_date]

    def simulate_all(
        self,
        candidates: list,
        price_data: Dict[str, List[PriceBar]],
    ) -> Tuple[List[TradeResult], List[SkippedTrade]]:
        """
        Simulate trades for all candidates.

        When daily_entry_limit is set, candidates are grouped by entry date
        and only the top N (by score desc) are simulated per day.

        Args:
            candidates: List of TradeCandidate objects
            price_data: {ticker: [PriceBar sorted by date]}

        Returns:
            (trade_results, skipped_trades)
        """
        trades = []
        skipped = []

        # Phase 1: Pre-compute entry dates for daily limit filtering
        if self.daily_entry_limit is not None:
            simulatable = []
            for candidate in candidates:
                bars = price_data.get(candidate.ticker)
                if not bars:
                    skipped.append(
                        SkippedTrade(
                            ticker=candidate.ticker,
                            report_date=candidate.report_date,
                            grade=candidate.grade,
                            score=candidate.score,
                            skip_reason="no_price_data",
                        )
                    )
                    continue
                bars = self._truncate_bars(bars)
                if not bars:
                    skipped.append(
                        SkippedTrade(
                            ticker=candidate.ticker,
                            report_date=candidate.report_date,
                            grade=candidate.grade,
                            score=candidate.score,
                            skip_reason="no_price_data",
                        )
                    )
                    continue
                report_dt = datetime.strptime(candidate.report_date, "%Y-%m-%d")
                entry_idx = self._find_entry_index(bars, report_dt)
                if entry_idx is None:
                    skipped.append(
                        SkippedTrade(
                            ticker=candidate.ticker,
                            report_date=candidate.report_date,
                            grade=candidate.grade,
                            score=candidate.score,
                            skip_reason="no_price_data",
                        )
                    )
                    continue
                entry_date = bars[entry_idx].date
                simulatable.append((candidate, bars, entry_date))

            # Phase 2: Group by entry_date, pick top N by score
            by_date: Dict[str, list] = {}
            for item in simulatable:
                by_date.setdefault(item[2], []).append(item)

            filtered: List[Tuple[object, List[PriceBar]]] = []
            for entry_date in sorted(by_date.keys()):
                group = by_date[entry_date]
                # Sort by score descending; None scores go last
                group.sort(key=lambda x: x[0].score if x[0].score is not None else -1, reverse=True)
                for i, (cand, cand_bars, _) in enumerate(group):
                    if i < self.daily_entry_limit:
                        assert cand_bars is not None
                        filtered.append((cand, cand_bars))
                    else:
                        skipped.append(
                            SkippedTrade(
                                ticker=cand.ticker,
                                report_date=cand.report_date,
                                grade=cand.grade,
                                score=cand.score,
                                skip_reason="daily_limit",
                            )
                        )

            # Phase 3: Simulate filtered candidates
            for candidate, bars in filtered:
                result = self._simulate_single(candidate, bars)
                if isinstance(result, TradeResult):
                    trades.append(result)
                elif isinstance(result, SkippedTrade):
                    skipped.append(result)
        else:
            # No daily limit: original path
            for candidate in candidates:
                bars = price_data.get(candidate.ticker)
                if not bars:
                    skipped.append(
                        SkippedTrade(
                            ticker=candidate.ticker,
                            report_date=candidate.report_date,
                            grade=candidate.grade,
                            score=candidate.score,
                            skip_reason="no_price_data",
                        )
                    )
                    continue

                result = self._simulate_single(candidate, bars)
                if isinstance(result, TradeResult):
                    trades.append(result)
                elif isinstance(result, SkippedTrade):
                    skipped.append(result)

        logger.info(
            f"Simulated {len(trades)} trades, skipped {len(skipped)} "
            f"({self._skip_summary(skipped)})"
        )
        return trades, skipped

    def _simulate_single(self, candidate, bars: List[PriceBar]):
        """Simulate a single trade."""
        bars = self._truncate_bars(bars)
        if not bars:
            return SkippedTrade(
                ticker=candidate.ticker,
                report_date=candidate.report_date,
                grade=candidate.grade,
                score=candidate.score,
                skip_reason="no_price_data",
            )

        report_dt = datetime.strptime(candidate.report_date, "%Y-%m-%d")

        # Find entry day based on entry_mode
        entry_idx = self._find_entry_index(bars, report_dt)
        if entry_idx is None:
            return SkippedTrade(
                ticker=candidate.ticker,
                report_date=candidate.report_date,
                grade=candidate.grade,
                score=candidate.score,
                skip_reason="no_price_data",
            )

        entry_bar = bars[entry_idx]

        # Validate entry bar (all OHLC must be positive)
        if entry_bar.open <= 0 or entry_bar.low <= 0 or entry_bar.high <= 0 or entry_bar.close <= 0:
            return SkippedTrade(
                ticker=candidate.ticker,
                report_date=candidate.report_date,
                grade=candidate.grade,
                score=candidate.score,
                skip_reason="missing_ohlc",
            )

        entry_price = entry_bar.adjusted_open
        if entry_price <= 0:
            return SkippedTrade(
                ticker=candidate.ticker,
                report_date=candidate.report_date,
                grade=candidate.grade,
                score=candidate.score,
                skip_reason="missing_ohlc",
            )
        shares = int(self.position_size / entry_price)

        if shares == 0:
            return SkippedTrade(
                ticker=candidate.ticker,
                report_date=candidate.report_date,
                grade=candidate.grade,
                score=candidate.score,
                skip_reason="zero_shares",
            )

        invested = shares * entry_price
        stop_price = entry_price * (1 - self.stop_loss_pct / 100)
        entry_dt = datetime.strptime(entry_bar.date, "%Y-%m-%d")

        # Pre-compute weekly bars and indicators for trailing stop
        weekly_bars = []
        indicators: list = []
        if self.trailing_stop:
            from backtest.weekly_bars import (
                aggregate_daily_to_weekly,
                compute_weekly_ema,
                compute_weekly_nweek_low,
            )

            weekly_bars = aggregate_daily_to_weekly(bars)
            if self.trailing_stop == "weekly_ema":
                indicators = compute_weekly_ema(weekly_bars, self.trailing_ema_period)
            else:
                indicators = compute_weekly_nweek_low(weekly_bars, self.trailing_nweek_period)

        # Scan from entry day onward
        exit_price = None
        exit_date = None
        exit_reason = None
        pending_exit: Optional[str] = None  # None | "stop_loss" | "trend_break"

        for idx, bar in enumerate(bars[entry_idx:]):
            bar_dt = datetime.strptime(bar.date, "%Y-%m-%d")
            calendar_days = (bar_dt - entry_dt).days

            # Pending exit: previous day's signal -> exit at today's open
            if pending_exit is not None:
                exit_price = bar.adjusted_open * (1 - self.slippage_pct / 100)
                exit_date = bar.date
                exit_reason = pending_exit
                logger.debug(
                    f"{candidate.ticker} {entry_bar.date}: {exit_reason}(pending) at {bar.date} open, price={exit_price:.4f}"
                )
                break

            # Check stop loss (mode-dependent)
            stop_hit = False
            if self.stop_mode == "intraday":
                stop_hit = bar.low > 0 and bar.adjusted_low <= stop_price
            elif self.stop_mode == "close":
                adj_c = (
                    bar.adj_close
                    if (bar.adj_close is not None and bar.adj_close > 0)
                    else bar.close
                )
                stop_hit = adj_c > 0 and adj_c <= stop_price
            elif self.stop_mode == "skip_entry_day":
                stop_hit = idx > 0 and bar.low > 0 and bar.adjusted_low <= stop_price
            elif self.stop_mode == "close_next_open":
                adj_c = (
                    bar.adj_close
                    if (bar.adj_close is not None and bar.adj_close > 0)
                    else bar.close
                )
                if adj_c > 0 and adj_c <= stop_price:
                    pending_exit = "stop_loss"
                    # Don't break — execute at next bar's open

            if stop_hit:
                if self.stop_mode == "close":
                    adj_c = (
                        bar.adj_close
                        if (bar.adj_close is not None and bar.adj_close > 0)
                        else bar.close
                    )
                    exit_price = adj_c * (1 - self.slippage_pct / 100)
                else:
                    exit_price = stop_price * (1 - self.slippage_pct / 100)
                exit_date = bar.date
                exit_reason = "stop_loss"
                logger.debug(
                    f"{candidate.ticker} {entry_bar.date}: stop_loss({self.stop_mode}) at {bar.date}, price={exit_price:.4f}"
                )
                break

            # Trailing stop check (after stop_loss, before max_holding)
            if (
                self.trailing_stop is not None
                and pending_exit is None
                and self._is_week_end(bars, entry_idx + idx)
            ):
                completed = self._count_completed_weeks(weekly_bars, entry_bar.date, bar.date)
                if completed >= self.trailing_transition_weeks and self._is_trend_broken(
                    weekly_bars, indicators, bar.date
                ):
                    pending_exit = "trend_break"
                    # Don't break — exit at next bar's open

            # Check max holding period (ensure close is valid)
            if (
                pending_exit is None
                and self.max_holding_days is not None
                and calendar_days >= self.max_holding_days
                and bar.close > 0
            ):
                exit_price = (
                    bar.adj_close
                    if (bar.adj_close is not None and bar.adj_close > 0)
                    else bar.close
                )
                exit_date = bar.date
                exit_reason = "max_holding"
                logger.debug(
                    f"{candidate.ticker} {entry_bar.date}: max_holding at {bar.date}, days={calendar_days}"
                )
                break

        # If loop ended with pending_exit but no next bar: fallback to last bar's close
        if exit_price is None and pending_exit is not None:
            last_bar = bars[-1]
            adj_c = (
                last_bar.adj_close
                if (last_bar.adj_close is not None and last_bar.adj_close > 0)
                else last_bar.close
            )
            exit_price = adj_c * (1 - self.slippage_pct / 100)
            exit_date = last_bar.date
            exit_reason = pending_exit
            logger.debug(
                f"{candidate.ticker} {entry_bar.date}: {exit_reason}(pending) fallback at {last_bar.date}, price={exit_price:.4f}"
            )

        # If loop completed without exit (data runs out)
        if exit_price is None:
            last_bar = bars[-1]
            exit_price = (
                last_bar.adj_close
                if (last_bar.adj_close is not None and last_bar.adj_close > 0)
                else last_bar.close
            )
            exit_date = last_bar.date
            exit_reason = "end_of_data"
            logger.debug(f"{candidate.ticker} {entry_bar.date}: end_of_data at {last_bar.date}")

        assert exit_date is not None
        assert exit_reason is not None

        pnl = (exit_price - entry_price) * shares
        return_pct = ((exit_price / entry_price) - 1) * 100
        holding_days = (datetime.strptime(exit_date, "%Y-%m-%d") - entry_dt).days

        return TradeResult(
            ticker=candidate.ticker,
            grade=candidate.grade,
            grade_source=candidate.grade_source,
            score=candidate.score,
            report_date=candidate.report_date,
            entry_date=entry_bar.date,
            entry_price=round(entry_price, 4),
            exit_date=exit_date,
            exit_price=round(exit_price, 4),
            shares=shares,
            invested=round(invested, 2),
            pnl=round(pnl, 2),
            return_pct=round(return_pct, 2),
            holding_days=holding_days,
            exit_reason=exit_reason,
            gap_size=candidate.gap_size,
            company_name=candidate.company_name,
        )

    @staticmethod
    def _is_week_end(bars: List[PriceBar], idx: int) -> bool:
        """Current bar is the last trading day of its ISO week."""
        cur = datetime.strptime(bars[idx].date, "%Y-%m-%d")
        if idx + 1 >= len(bars):
            return True
        nxt = datetime.strptime(bars[idx + 1].date, "%Y-%m-%d")
        return cur.isocalendar()[:2] != nxt.isocalendar()[:2]

    @staticmethod
    def _count_completed_weeks(weekly_bars, entry_date: str, current_date: str) -> int:
        """Count weekly bars that started after entry_date and completed by current_date.

        Entry week is always excluded (even if entry is Monday = week_start).
        This ensures the transition period counts only FULL weeks after entry.
        """
        return sum(
            1 for wb in weekly_bars if wb.week_start > entry_date and wb.week_ending <= current_date
        )

    def _is_trend_broken(self, weekly_bars, indicators, current_date: str) -> bool:
        """Check if the most recent completed weekly bar broke the trend indicator."""
        wb_idx = None
        for i, wb in enumerate(weekly_bars):
            if wb.week_ending <= current_date:
                wb_idx = i
        if wb_idx is None or indicators[wb_idx] is None:
            return False

        if self.trailing_stop == "weekly_ema" or self.trailing_stop == "weekly_nweek_low":
            return weekly_bars[wb_idx].close < indicators[wb_idx]
        return False

    def _find_next_trading_day_index(
        self, bars: List[PriceBar], after_date: datetime
    ) -> Optional[int]:
        """Find index of first trading day strictly after after_date."""
        target = after_date.strftime("%Y-%m-%d")
        for i, bar in enumerate(bars):
            if bar.date > target:
                return i
        return None

    def _find_trading_day_index_on_or_after(
        self, bars: List[PriceBar], on_or_after_date: datetime
    ) -> Optional[int]:
        """Find index of first trading day on or after on_or_after_date."""
        target = on_or_after_date.strftime("%Y-%m-%d")
        for i, bar in enumerate(bars):
            if bar.date >= target:
                return i
        return None

    def _find_entry_index(self, bars: List[PriceBar], report_dt: datetime) -> Optional[int]:
        """Find entry bar index based on entry_mode."""
        if self.entry_mode == "report_open":
            idx = self._find_trading_day_index_on_or_after(bars, report_dt)
            if idx is not None:
                target = report_dt.strftime("%Y-%m-%d")
                if bars[idx].date != target:
                    logger.debug("report_open: no bar for %s, using %s", target, bars[idx].date)
            return idx
        else:
            return self._find_next_trading_day_index(bars, report_dt)

    @staticmethod
    def _skip_summary(skipped: List[SkippedTrade]) -> str:
        """Summarize skip reasons."""
        reasons: Dict[str, int] = {}
        for s in skipped:
            reasons[s.skip_reason] = reasons.get(s.skip_reason, 0) + 1
        return ", ".join(f"{reason}: {count}" for reason, count in sorted(reasons.items()))
