#!/usr/bin/env python3
"""Unit tests for the price fetcher."""

import pytest
from unittest.mock import patch, MagicMock
from backtest.price_fetcher import PriceBar, PriceFetcher


class TestPriceBarDateValidation:
    """M-7: PriceBar date must be YYYY-MM-DD format."""

    def test_valid_date(self):
        bar = PriceBar(
            date="2025-10-01", open=100, high=105,
            low=95, close=100, adj_close=100, volume=1000,
        )
        assert bar.date == "2025-10-01"

    def test_invalid_date_format(self):
        with pytest.raises(ValueError, match="Invalid date format"):
            PriceBar(
                date="10/01/2025", open=100, high=105,
                low=95, close=100, adj_close=100, volume=1000,
            )

    def test_invalid_date_partial(self):
        with pytest.raises(ValueError, match="Invalid date format"):
            PriceBar(
                date="2025-10", open=100, high=105,
                low=95, close=100, adj_close=100, volume=1000,
            )

    def test_invalid_date_empty(self):
        with pytest.raises(ValueError, match="Invalid date format"):
            PriceBar(
                date="", open=100, high=105,
                low=95, close=100, adj_close=100, volume=1000,
            )

    def test_invalid_date_extra_chars(self):
        with pytest.raises(ValueError, match="Invalid date format"):
            PriceBar(
                date="2025-10-01T00:00:00", open=100, high=105,
                low=95, close=100, adj_close=100, volume=1000,
            )


class TestOhlcConsistency:
    """M-6: high < low data should be skipped in fetch_prices."""

    @patch.object(PriceFetcher, '_resolve_api_key', return_value='test-key')
    @patch.object(PriceFetcher, '_make_request')
    def test_high_less_than_low_skipped(self, mock_request, mock_key):
        mock_request.return_value = {
            'historical': [
                # Valid bar
                {'date': '2025-10-01', 'open': 100, 'high': 105,
                 'low': 95, 'close': 100, 'adjClose': 100, 'volume': 1000},
                # Invalid: high < low
                {'date': '2025-10-02', 'open': 100, 'high': 90,
                 'low': 95, 'close': 100, 'adjClose': 100, 'volume': 1000},
                # Valid bar
                {'date': '2025-10-03', 'open': 101, 'high': 106,
                 'low': 96, 'close': 103, 'adjClose': 103, 'volume': 1200},
            ]
        }
        fetcher = PriceFetcher(api_key='test-key')
        bars = fetcher.fetch_prices('TEST', '2025-10-01', '2025-10-03')
        # Only 2 valid bars (high < low skipped)
        assert len(bars) == 2
        dates = [b.date for b in bars]
        assert '2025-10-02' not in dates


class TestRateLimitReset:
    """M-3: Rate limit flag should reset after successful request."""

    @patch.object(PriceFetcher, '_resolve_api_key', return_value='test-key')
    def test_rate_limit_resets(self, mock_key):
        fetcher = PriceFetcher(api_key='test-key')
        # Simulate rate-limited state
        fetcher._rate_limited = True
        assert fetcher._rate_limited is True

        # Mock a successful response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'historical': []}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(fetcher.session, 'get', return_value=mock_resp):
            fetcher._last_request_time = 0  # Skip rate limit wait
            result = fetcher._make_request('test-endpoint')

        assert fetcher._rate_limited is False
