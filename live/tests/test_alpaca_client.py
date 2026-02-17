#!/usr/bin/env python3
"""Unit tests for the Alpaca REST API client."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from live.alpaca_client import AlpacaClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(**kwargs) -> AlpacaClient:
    """Create a client with dummy credentials for testing."""
    defaults = {
        "api_key": "test-key-id",
        "secret_key": "test-secret",
        "base_url": "https://paper-api.alpaca.markets",
    }
    defaults.update(kwargs)
    return AlpacaClient(**defaults)


def _mock_response(status_code: int = 200, json_data=None) -> MagicMock:
    """Build a fake requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Constructor safety guard
# ---------------------------------------------------------------------------


class TestConstructor:
    """Paper-trading safety guard in __init__."""

    def test_paper_url_default(self):
        client = _make_client()
        assert "paper" in client.base_url

    def test_live_url_blocked(self):
        with pytest.raises(ValueError, match="Non-paper URL requires --allow-live"):
            _make_client(base_url="https://api.alpaca.markets")

    def test_live_url_allowed(self):
        client = _make_client(
            base_url="https://api.alpaca.markets",
            allow_live=True,
        )
        assert client.base_url == "https://api.alpaca.markets"


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


class TestHeaders:
    """Session headers carry API credentials."""

    def test_headers_set(self):
        client = _make_client(api_key="MY-KEY", secret_key="MY-SECRET")
        assert client.session.headers["APCA-API-KEY-ID"] == "MY-KEY"
        assert client.session.headers["APCA-API-SECRET-KEY"] == "MY-SECRET"


# ---------------------------------------------------------------------------
# GET endpoints
# ---------------------------------------------------------------------------


class TestGetAccount:
    def test_get_account(self):
        client = _make_client()
        account_data = {"id": "acct-1", "buying_power": "50000.00"}
        mock_resp = _mock_response(json_data=account_data)

        with patch.object(client.session, "request", return_value=mock_resp) as mock_req:
            result = client.get_account()

        mock_req.assert_called_once_with(
            "GET",
            "https://paper-api.alpaca.markets/v2/account",
            timeout=30,
        )
        assert result == account_data


class TestGetPositions:
    def test_get_positions(self):
        client = _make_client()
        positions_data = [
            {"symbol": "AAPL", "qty": "10", "market_value": "1500.00"},
        ]
        mock_resp = _mock_response(json_data=positions_data)

        with patch.object(client.session, "request", return_value=mock_resp) as mock_req:
            result = client.get_positions()

        mock_req.assert_called_once_with(
            "GET",
            "https://paper-api.alpaca.markets/v2/positions",
            timeout=30,
        )
        assert result == positions_data


class TestGetClock:
    def test_get_clock(self):
        client = _make_client()
        clock_data = {
            "is_open": True,
            "next_open": "2026-02-17T09:30:00-05:00",
            "next_close": "2026-02-17T16:00:00-05:00",
        }
        mock_resp = _mock_response(json_data=clock_data)

        with patch.object(client.session, "request", return_value=mock_resp) as mock_req:
            result = client.get_clock()

        mock_req.assert_called_once_with(
            "GET",
            "https://paper-api.alpaca.markets/v2/clock",
            timeout=30,
        )
        assert result == clock_data


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------


class TestPlaceOrderMarket:
    def test_place_order_market(self):
        client = _make_client()
        order_resp = {"id": "order-1", "status": "accepted", "symbol": "AAPL"}
        mock_resp = _mock_response(json_data=order_resp)

        with patch.object(client.session, "request", return_value=mock_resp) as mock_req:
            result = client.place_order(
                symbol="AAPL",
                qty=10,
                side="buy",
                type="market",
                time_in_force="day",
            )

        mock_req.assert_called_once()
        call_kwargs = mock_req.call_args
        assert call_kwargs[0] == ("POST", "https://paper-api.alpaca.markets/v2/orders")
        payload = call_kwargs[1]["json"]
        assert payload["symbol"] == "AAPL"
        assert payload["qty"] == "10"
        assert payload["side"] == "buy"
        assert payload["type"] == "market"
        assert payload["time_in_force"] == "day"
        assert "stop_price" not in payload
        assert "order_class" not in payload
        assert result == order_resp


class TestPlaceOrderStop:
    def test_place_order_stop(self):
        client = _make_client()
        order_resp = {"id": "order-2", "status": "accepted"}
        mock_resp = _mock_response(json_data=order_resp)

        with patch.object(client.session, "request", return_value=mock_resp) as mock_req:
            result = client.place_order(
                symbol="TSLA",
                qty=5,
                side="sell",
                type="stop",
                time_in_force="gtc",
                stop_price=180.50,
            )

        call_kwargs = mock_req.call_args
        payload = call_kwargs[1]["json"]
        assert payload["symbol"] == "TSLA"
        assert payload["type"] == "stop"
        assert payload["stop_price"] == "180.5"
        assert result == order_resp


class TestPlaceBracketOrder:
    def test_place_bracket_order(self):
        client = _make_client()
        order_resp = {"id": "order-3", "status": "accepted", "order_class": "bracket"}
        mock_resp = _mock_response(json_data=order_resp)

        with patch.object(client.session, "request", return_value=mock_resp) as mock_req:
            result = client.place_bracket_order(
                symbol="NVDA",
                qty=20,
                side="buy",
                time_in_force="day",
                stop_price=95.00,
                client_order_id="my-bracket-1",
            )

        call_kwargs = mock_req.call_args
        payload = call_kwargs[1]["json"]
        assert payload["symbol"] == "NVDA"
        assert payload["qty"] == "20"
        assert payload["side"] == "buy"
        assert payload["type"] == "market"
        assert payload["order_class"] == "bracket"
        assert payload["stop_loss"] == {"stop_price": "95.0"}
        assert payload["client_order_id"] == "my-bracket-1"
        assert result == order_resp


# ---------------------------------------------------------------------------
# Get order
# ---------------------------------------------------------------------------


class TestGetOrder:
    def test_get_order(self):
        client = _make_client()
        order_data = {"id": "order-abc", "status": "filled", "symbol": "GOOG"}
        mock_resp = _mock_response(json_data=order_data)

        with patch.object(client.session, "request", return_value=mock_resp) as mock_req:
            result = client.get_order("order-abc")

        mock_req.assert_called_once_with(
            "GET",
            "https://paper-api.alpaca.markets/v2/orders/order-abc",
            timeout=30,
        )
        assert result == order_data


class TestGetOrderByClientId:
    def test_get_order_by_client_id(self):
        client = _make_client()
        order_data = {"id": "order-xyz", "client_order_id": "my-id-1"}
        mock_resp = _mock_response(json_data=order_data)

        with patch.object(client.session, "request", return_value=mock_resp) as mock_req:
            result = client.get_order_by_client_id("my-id-1")

        mock_req.assert_called_once_with(
            "GET",
            "https://paper-api.alpaca.markets/v2/orders:by_client_order_id",
            timeout=30,
            params={"client_order_id": "my-id-1"},
        )
        assert result == order_data

    def test_get_order_by_client_id_not_found(self):
        client = _make_client()
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 404

        http_error = requests.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_error

        with patch.object(client.session, "request", return_value=mock_resp):
            result = client.get_order_by_client_id("nonexistent")

        assert result is None


# ---------------------------------------------------------------------------
# Cancel order
# ---------------------------------------------------------------------------


class TestCancelOrder:
    def test_cancel_order(self):
        client = _make_client()
        mock_resp = _mock_response(status_code=204)

        with patch.object(client.session, "request", return_value=mock_resp) as mock_req:
            result = client.cancel_order("order-to-cancel")

        mock_req.assert_called_once_with(
            "DELETE",
            "https://paper-api.alpaca.markets/v2/orders/order-to-cancel",
            timeout=30,
        )
        assert result == {}
