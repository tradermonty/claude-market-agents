#!/usr/bin/env python3
"""
Price data fetcher for backtest.

Wraps FMP API to fetch historical price data with:
- Ticker-level period aggregation (one API call per ticker)
- Rate limiting and retry logic
- Split-adjusted price calculation
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from tqdm import tqdm

logger = logging.getLogger(__name__)


@dataclass
class PriceBar:
    date: str  # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    adj_close: Optional[float]
    volume: int

    def __post_init__(self):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", self.date):
            raise ValueError(f"Invalid date format: {self.date}, expected YYYY-MM-DD")

    @property
    def adj_factor(self) -> float:
        if self.adj_close is not None and self.close != 0:
            return self.adj_close / self.close
        return 1.0

    @property
    def adjusted_open(self) -> float:
        return self.open * self.adj_factor

    @property
    def adjusted_high(self) -> float:
        return self.high * self.adj_factor

    @property
    def adjusted_low(self) -> float:
        return self.low * self.adj_factor


try:
    from typing import Protocol, runtime_checkable
except ImportError:
    from typing_extensions import Protocol, runtime_checkable  # type: ignore[assignment]


@runtime_checkable
class PriceFetcherProtocol(Protocol):
    """Protocol for price data fetchers (real and fake)."""

    def fetch_prices(self, symbol: str, from_date: str, to_date: str) -> List[PriceBar]: ...

    def bulk_fetch(self, ticker_periods: Dict[str, tuple]) -> Dict[str, List[PriceBar]]: ...


class PriceFetcher:
    """FMP-based historical price data fetcher with caching."""

    BASE_URL = "https://financialmodelingprep.com/api/v3"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or self._resolve_api_key()
        if not self.api_key:
            raise ValueError(
                "FMP API key required. Pass --fmp-api-key, set FMP_API_KEY env var, "
                "or configure .mcp.json"
            )
        self.session = requests.Session()
        self._last_request_time = 0.0
        self._min_interval = 0.1  # 10 req/sec baseline
        self._rate_limited = False

    @staticmethod
    def _resolve_api_key() -> Optional[str]:
        """Resolve API key: .env -> env var -> .mcp.json."""
        load_dotenv()
        key = os.getenv("FMP_API_KEY")
        if key:
            return key

        # Try .mcp.json in current dir or project root
        for mcp_path in [".mcp.json", "../.mcp.json"]:
            p = Path(mcp_path)
            if p.exists():
                try:
                    data = json.loads(p.read_text())
                    # Navigate: mcpServers -> fmp-server -> env -> FMP_API_KEY
                    servers = data.get("mcpServers", data)
                    fmp = servers.get("fmp-server", {})
                    key = fmp.get("env", {}).get("FMP_API_KEY")
                    if key:
                        logger.info(f"Loaded FMP API key from {p}")
                        return str(key)
                except (json.JSONDecodeError, KeyError) as e:
                    logger.debug(f"Failed to read FMP key from {p}: {e}")
        return None

    def _rate_limit(self):
        """Simple rate limiter."""
        now = time.time()
        elapsed = now - self._last_request_time
        interval = 0.3 if self._rate_limited else self._min_interval
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_request_time = time.time()

    def _make_request(
        self, endpoint: str, params: Optional[Dict] = None, max_retries: int = 3
    ) -> Optional[Any]:
        """Make FMP API request with retry logic."""
        if params is None:
            params = {}
        params["apikey"] = self.api_key
        url = f"{self.BASE_URL}/{endpoint}"

        for attempt in range(max_retries + 1):
            self._rate_limit()
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 429:
                    self._rate_limited = True
                    delay = 5 * (2**attempt)
                    logger.warning(f"Rate limited on {endpoint}, waiting {delay}s")
                    time.sleep(delay)
                    continue
                if resp.status_code in (404, 403):
                    return None
                resp.raise_for_status()
                self._rate_limited = False  # Reset after successful request
                data = resp.json()
                if isinstance(data, dict) and data.get("Error Message"):
                    return None
                return data
            except requests.exceptions.RequestException as e:
                if attempt < max_retries:
                    time.sleep(2**attempt)
                    continue
                logger.warning(f"Request failed for {endpoint}: {e}")
                return None
            except json.JSONDecodeError as e:
                logger.warning(f"JSON decode error for {endpoint}: {e}")
                return None
        return None

    def _normalize_symbol(self, symbol: str) -> str:
        """Normalize symbol for FMP API (BRK.B -> BRK-B)."""
        return symbol.replace(".", "-").replace("/", "-") if symbol else symbol

    def fetch_prices(self, symbol: str, from_date: str, to_date: str) -> List[PriceBar]:
        """Fetch historical daily price bars for a symbol."""
        norm = self._normalize_symbol(symbol)
        data = self._make_request(
            f"historical-price-full/{norm}", {"from": from_date, "to": to_date}
        )

        if data is None:
            return []

        # Handle response format
        records = []
        if isinstance(data, dict) and "historical" in data:
            records = data["historical"]
        elif isinstance(data, list):
            records = data
        else:
            return []

        bars = []
        for rec in records:
            try:
                o = float(rec.get("open", 0))
                h = float(rec.get("high", 0))
                lo = float(rec.get("low", 0))
                c = float(rec.get("close", 0))
                if o <= 0 or h <= 0 or lo <= 0 or c <= 0:
                    logger.debug(f"Skipping {symbol} {rec.get('date')}: zero/missing OHLC")
                    continue
                if h < lo:
                    logger.debug(f"Skipping {symbol} {rec.get('date')}: high({h}) < low({lo})")
                    continue
                bars.append(
                    PriceBar(
                        date=rec["date"],
                        open=o,
                        high=h,
                        low=lo,
                        close=c,
                        adj_close=float(rec["adjClose"])
                        if rec.get("adjClose") is not None
                        else None,
                        volume=int(rec.get("volume", 0)),
                    )
                )
            except (KeyError, ValueError, TypeError) as e:
                logger.debug(f"Skipping malformed price record for {symbol}: {e}")
                continue

        # Sort chronologically (FMP returns newest first)
        bars.sort(key=lambda b: b.date)
        return bars

    def bulk_fetch(self, ticker_periods: Dict[str, tuple]) -> Dict[str, List[PriceBar]]:
        """
        Fetch prices for multiple tickers with aggregated date ranges.

        Args:
            ticker_periods: {ticker: (min_date, max_date)} pre-aggregated ranges

        Returns:
            {ticker: [PriceBar, ...]}
        """
        results = {}
        failed = []

        for ticker, (min_date, max_date) in tqdm(
            ticker_periods.items(), desc="Fetching price data", unit="ticker"
        ):
            bars = self.fetch_prices(ticker, min_date, max_date)
            if bars:
                results[ticker] = bars
            else:
                failed.append(ticker)
                logger.debug(f"No price data for {ticker}")

        if failed:
            logger.warning(f"Failed to fetch prices for {len(failed)} tickers: {failed[:20]}...")

        logger.info(f"Fetched price data for {len(results)}/{len(ticker_periods)} tickers")
        return results


def aggregate_ticker_periods(
    candidates: list,
    buffer_days: int = 120,
) -> Dict[str, tuple]:
    """
    Aggregate required date ranges per ticker from trade candidates.

    Each ticker needs data from report_date to report_date + buffer_days.
    Merges overlapping ranges per ticker.

    Returns:
        {ticker: (min_date_str, max_date_str)}
    """
    from collections import defaultdict

    ranges = defaultdict(list)

    for c in candidates:
        rd = datetime.strptime(c.report_date, "%Y-%m-%d")
        start = rd - timedelta(days=5)  # A few days before for entry
        end = rd + timedelta(days=buffer_days)
        ranges[c.ticker].append((start, end))

    result = {}
    for ticker, periods in ranges.items():
        min_date = min(p[0] for p in periods)
        max_date = max(p[1] for p in periods)
        result[ticker] = (
            min_date.strftime("%Y-%m-%d"),
            max_date.strftime("%Y-%m-%d"),
        )

    return result
