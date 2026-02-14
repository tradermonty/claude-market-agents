#!/usr/bin/env python3
"""
Trade simulator for earnings gap-up backtest.

Simulates long positions with:
- Next business day open entry
- Stop loss with slippage
- Max holding period (90 calendar days)
- Fixed position sizing ($10,000)
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple

from backtest.price_fetcher import PriceBar

logger = logging.getLogger(__name__)


@dataclass
class TradeResult:
    ticker: str
    grade: str
    grade_source: str       # "html" | "inferred"
    score: Optional[float]
    report_date: str
    entry_date: str
    entry_price: float      # Open price
    exit_date: str
    exit_price: float
    shares: int
    invested: float         # shares * entry_price
    pnl: float
    return_pct: float
    holding_days: int       # Calendar days
    exit_reason: str        # "stop_loss" | "max_holding" | "end_of_data"
    gap_size: Optional[float] = None
    company_name: Optional[str] = None


@dataclass
class SkippedTrade:
    ticker: str
    report_date: str
    grade: str
    score: Optional[float]
    skip_reason: str        # "no_price_data" | "zero_shares" | "missing_ohlc"


class TradeSimulator:
    """Simulate individual trades with stop loss and max holding period."""

    def __init__(
        self,
        position_size: float = 10000.0,
        stop_loss_pct: float = 10.0,
        slippage_pct: float = 0.5,
        max_holding_days: int = 90,
    ):
        self.position_size = position_size
        self.stop_loss_pct = stop_loss_pct
        self.slippage_pct = slippage_pct
        self.max_holding_days = max_holding_days

    def simulate_all(
        self,
        candidates: list,
        price_data: Dict[str, List[PriceBar]],
    ) -> Tuple[List[TradeResult], List[SkippedTrade]]:
        """
        Simulate trades for all candidates.

        Args:
            candidates: List of TradeCandidate objects
            price_data: {ticker: [PriceBar sorted by date]}

        Returns:
            (trade_results, skipped_trades)
        """
        trades = []
        skipped = []

        for candidate in candidates:
            bars = price_data.get(candidate.ticker)
            if not bars:
                skipped.append(SkippedTrade(
                    ticker=candidate.ticker,
                    report_date=candidate.report_date,
                    grade=candidate.grade,
                    score=candidate.score,
                    skip_reason="no_price_data",
                ))
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
        report_dt = datetime.strptime(candidate.report_date, '%Y-%m-%d')

        # Find entry day: first trading day AFTER report_date
        entry_idx = self._find_next_trading_day_index(bars, report_dt)
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
        entry_dt = datetime.strptime(entry_bar.date, '%Y-%m-%d')

        # Scan from entry day onward
        exit_price = None
        exit_date = None
        exit_reason = None

        for bar in bars[entry_idx:]:
            bar_dt = datetime.strptime(bar.date, '%Y-%m-%d')
            calendar_days = (bar_dt - entry_dt).days

            # Check stop loss (using adjusted low, skip if low=0 to avoid false triggers)
            if bar.low > 0 and bar.adjusted_low <= stop_price:
                exit_price = stop_price * (1 - self.slippage_pct / 100)
                exit_date = bar.date
                exit_reason = "stop_loss"
                logger.debug(f"{candidate.ticker} {entry_bar.date}: stop_loss at {bar.date}, price={exit_price:.4f}")
                break

            # Check max holding period (ensure close is valid)
            if calendar_days >= self.max_holding_days and bar.close > 0:
                exit_price = bar.adj_close if (bar.adj_close is not None and bar.adj_close > 0) else bar.close
                exit_date = bar.date
                exit_reason = "max_holding"
                logger.debug(f"{candidate.ticker} {entry_bar.date}: max_holding at {bar.date}, days={calendar_days}")
                break

        # If loop completed without exit (data runs out)
        if exit_price is None:
            last_bar = bars[-1]
            exit_price = last_bar.adj_close if (last_bar.adj_close is not None and last_bar.adj_close > 0) else last_bar.close
            exit_date = last_bar.date
            exit_reason = "end_of_data"
            logger.debug(f"{candidate.ticker} {entry_bar.date}: end_of_data at {last_bar.date}")

        pnl = (exit_price - entry_price) * shares
        return_pct = ((exit_price / entry_price) - 1) * 100
        holding_days = (datetime.strptime(exit_date, '%Y-%m-%d') - entry_dt).days

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

    def _find_next_trading_day_index(
        self, bars: List[PriceBar], after_date: datetime
    ) -> Optional[int]:
        """Find index of first trading day strictly after after_date."""
        target = after_date.strftime('%Y-%m-%d')
        for i, bar in enumerate(bars):
            if bar.date > target:
                return i
        return None

    @staticmethod
    def _skip_summary(skipped: List[SkippedTrade]) -> str:
        """Summarize skip reasons."""
        reasons = {}
        for s in skipped:
            reasons[s.skip_reason] = reasons.get(s.skip_reason, 0) + 1
        return ', '.join(f"{reason}: {count}" for reason, count in sorted(reasons.items()))
