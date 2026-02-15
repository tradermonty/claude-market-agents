#!/usr/bin/env python3
"""Fake price fetcher for deterministic testing without network access."""

from typing import Dict, List

from backtest.price_fetcher import PriceBar


class FakePriceFetcher:
    """Returns pre-loaded price data. No network calls."""

    def __init__(self, data: Dict[str, List[PriceBar]]):
        self._data = data

    def fetch_prices(self, symbol: str, from_date: str, to_date: str) -> List[PriceBar]:
        bars = self._data.get(symbol, [])
        return [b for b in bars if from_date <= b.date <= to_date]

    def bulk_fetch(self, ticker_periods: Dict[str, tuple]) -> Dict[str, List[PriceBar]]:
        results: Dict[str, List[PriceBar]] = {}
        for ticker, (from_date, to_date) in ticker_periods.items():
            bars = self.fetch_prices(ticker, from_date, to_date)
            if bars:
                results[ticker] = bars
        return results
