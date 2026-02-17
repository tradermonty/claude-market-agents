#!/usr/bin/env python3
"""Alpaca REST API client for paper trading.

Thin wrapper around Alpaca's REST API using requests.
Default base_url points to paper trading; non-paper URLs require explicit allow_live=True.
"""

import logging
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class AlpacaClient:
    """Alpaca REST API client with paper-trading safety guard."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        base_url: str = "https://paper-api.alpaca.markets",
        allow_live: bool = False,
    ):
        if "paper" not in base_url and not allow_live:
            raise ValueError(f"Non-paper URL requires --allow-live flag. Got: {base_url}")
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": secret_key,
            }
        )

    def _request(self, method: str, path: str, **kwargs) -> Any:
        """Make authenticated API request."""
        url = f"{self.base_url}{path}"
        resp = self.session.request(method, url, timeout=30, **kwargs)
        resp.raise_for_status()
        if resp.status_code == 204:
            return {}
        return resp.json()

    def get_account(self) -> dict:
        """Get account information including buying power."""
        return self._request("GET", "/v2/account")

    def get_positions(self) -> List[dict]:
        """Get all open positions."""
        return self._request("GET", "/v2/positions")

    def get_clock(self) -> dict:
        """Get market clock (is_open, next_open, next_close, timestamp)."""
        return self._request("GET", "/v2/clock")

    def place_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        type: str = "market",
        time_in_force: str = "day",
        client_order_id: Optional[str] = None,
        stop_price: Optional[float] = None,
        order_class: Optional[str] = None,
        stop_loss: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Place an order. Supports market, stop, and bracket orders."""
        payload: Dict[str, Any] = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": type,
            "time_in_force": time_in_force,
        }
        if client_order_id:
            payload["client_order_id"] = client_order_id
        if stop_price is not None:
            payload["stop_price"] = str(stop_price)
        if order_class:
            payload["order_class"] = order_class
        if stop_loss:
            payload["stop_loss"] = stop_loss
        return self._request("POST", "/v2/orders", json=payload)

    def place_bracket_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        time_in_force: str,
        stop_price: float,
        client_order_id: Optional[str] = None,
    ) -> dict:
        """Place a bracket order (OTO: buy + stop loss).

        Raises on failure so caller can fall back to separate orders.
        """
        return self.place_order(
            symbol=symbol,
            qty=qty,
            side=side,
            type="market",
            time_in_force=time_in_force,
            client_order_id=client_order_id,
            order_class="bracket",
            stop_loss={"stop_price": str(stop_price)},
        )

    def get_order(self, order_id: str) -> dict:
        """Get order by Alpaca order ID."""
        return self._request("GET", f"/v2/orders/{order_id}")

    def get_order_by_client_id(self, client_order_id: str) -> Optional[dict]:
        """Get order by client_order_id. Returns None if not found."""
        try:
            return self._request(
                "GET",
                "/v2/orders:by_client_order_id",
                params={"client_order_id": client_order_id},
            )
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an order by ID. Returns empty dict on success (204)."""
        return self._request("DELETE", f"/v2/orders/{order_id}")
