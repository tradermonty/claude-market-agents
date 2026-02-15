#!/usr/bin/env python3
"""Unit tests for the price fetcher."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backtest.price_fetcher import PriceBar, PriceFetcher, PriceFetcherProtocol
from backtest.tests.fake_price_fetcher import FakePriceFetcher


class TestPriceBarDateValidation:
    """M-7: PriceBar date must be YYYY-MM-DD format."""

    def test_valid_date(self):
        bar = PriceBar(
            date="2025-10-01",
            open=100,
            high=105,
            low=95,
            close=100,
            adj_close=100,
            volume=1000,
        )
        assert bar.date == "2025-10-01"

    def test_invalid_date_format(self):
        with pytest.raises(ValueError, match="Invalid date format"):
            PriceBar(
                date="10/01/2025",
                open=100,
                high=105,
                low=95,
                close=100,
                adj_close=100,
                volume=1000,
            )

    def test_invalid_date_partial(self):
        with pytest.raises(ValueError, match="Invalid date format"):
            PriceBar(
                date="2025-10",
                open=100,
                high=105,
                low=95,
                close=100,
                adj_close=100,
                volume=1000,
            )

    def test_invalid_date_empty(self):
        with pytest.raises(ValueError, match="Invalid date format"):
            PriceBar(
                date="",
                open=100,
                high=105,
                low=95,
                close=100,
                adj_close=100,
                volume=1000,
            )

    def test_invalid_date_extra_chars(self):
        with pytest.raises(ValueError, match="Invalid date format"):
            PriceBar(
                date="2025-10-01T00:00:00",
                open=100,
                high=105,
                low=95,
                close=100,
                adj_close=100,
                volume=1000,
            )


class TestOhlcConsistency:
    """M-6: high < low data should be skipped in fetch_prices."""

    @patch.object(PriceFetcher, "_resolve_api_key", return_value="test-key")
    @patch.object(PriceFetcher, "_make_request")
    def test_high_less_than_low_skipped(self, mock_request, mock_key):
        mock_request.return_value = {
            "historical": [
                # Valid bar
                {
                    "date": "2025-10-01",
                    "open": 100,
                    "high": 105,
                    "low": 95,
                    "close": 100,
                    "adjClose": 100,
                    "volume": 1000,
                },
                # Invalid: high < low
                {
                    "date": "2025-10-02",
                    "open": 100,
                    "high": 90,
                    "low": 95,
                    "close": 100,
                    "adjClose": 100,
                    "volume": 1000,
                },
                # Valid bar
                {
                    "date": "2025-10-03",
                    "open": 101,
                    "high": 106,
                    "low": 96,
                    "close": 103,
                    "adjClose": 103,
                    "volume": 1200,
                },
            ]
        }
        fetcher = PriceFetcher(api_key="test-key")
        bars = fetcher.fetch_prices("TEST", "2025-10-01", "2025-10-03")
        # Only 2 valid bars (high < low skipped)
        assert len(bars) == 2
        dates = [b.date for b in bars]
        assert "2025-10-02" not in dates


class TestRateLimitReset:
    """M-3: Rate limit flag should reset after successful request."""

    @patch.object(PriceFetcher, "_resolve_api_key", return_value="test-key")
    def test_rate_limit_resets(self, mock_key):
        fetcher = PriceFetcher(api_key="test-key")
        # Simulate rate-limited state
        fetcher._rate_limited = True
        assert fetcher._rate_limited is True

        # Mock a successful response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"historical": []}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(fetcher.session, "get", return_value=mock_resp):
            fetcher._last_request_time = 0  # Skip rate limit wait
            fetcher._make_request("test-endpoint")

        assert fetcher._rate_limited is False


class TestFakePriceFetcher:
    """FakePriceFetcher conforms to PriceFetcherProtocol."""

    def test_protocol_conformance(self):
        fake = FakePriceFetcher({})
        assert isinstance(fake, PriceFetcherProtocol)

    def test_fetch_prices_returns_filtered_bars(self):
        bars = [
            PriceBar("2025-10-01", 100, 105, 95, 100, 100, 1000),
            PriceBar("2025-10-02", 100, 106, 94, 102, 102, 1100),
            PriceBar("2025-10-03", 102, 108, 100, 105, 105, 1200),
        ]
        fake = FakePriceFetcher({"AAPL": bars})
        result = fake.fetch_prices("AAPL", "2025-10-01", "2025-10-02")
        assert len(result) == 2
        assert result[0].date == "2025-10-01"
        assert result[1].date == "2025-10-02"

    def test_fetch_prices_missing_ticker(self):
        fake = FakePriceFetcher({})
        result = fake.fetch_prices("AAPL", "2025-10-01", "2025-10-02")
        assert result == []

    def test_bulk_fetch(self):
        bars = [
            PriceBar("2025-10-01", 100, 105, 95, 100, 100, 1000),
            PriceBar("2025-10-02", 100, 106, 94, 102, 102, 1100),
        ]
        fake = FakePriceFetcher({"AAPL": bars})
        result = fake.bulk_fetch({"AAPL": ("2025-10-01", "2025-10-02")})
        assert "AAPL" in result
        assert len(result["AAPL"]) == 2

    def test_load_from_mock_prices_json(self):
        """Load mock_prices.json and verify it works with FakePriceFetcher."""
        fixture_path = Path(__file__).parent / "fixtures" / "mock_prices.json"
        raw = json.loads(fixture_path.read_text())
        data = {}
        for ticker, records in raw.items():
            data[ticker] = [
                PriceBar(
                    date=r["date"],
                    open=r["open"],
                    high=r["high"],
                    low=r["low"],
                    close=r["close"],
                    adj_close=r.get("adjClose"),
                    volume=r["volume"],
                )
                for r in records
            ]
        fake = FakePriceFetcher(data)
        aapl_bars = fake.fetch_prices("AAPL", "2025-10-01", "2025-10-31")
        assert len(aapl_bars) > 10
        tsla_bars = fake.fetch_prices("TSLA", "2025-10-01", "2025-10-31")
        assert len(tsla_bars) > 10
