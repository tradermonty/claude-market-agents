#!/usr/bin/env python3
"""Live trading configuration with frozen parameters matching run_manifest."""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

MANIFEST_FIELD_MAP = {
    "position_size": "position_size",
    "stop_loss": "stop_loss_pct",
    "slippage": "slippage_pct",
    "max_holding": "max_holding_days",
    "stop_mode": "stop_mode",
    "entry_mode": "entry_mode",
    "max_positions": "max_positions",
    "trailing_transition_weeks": "trailing_transition_weeks",
}


@dataclass(frozen=True)
class LiveConfig:
    """Frozen configuration for live paper trading.

    All parameters must match run_manifest.json values exactly.
    Use verify_against_manifest() to confirm alignment.
    """

    # Must match run_manifest exactly
    max_positions: int = 20
    daily_entry_limit: int = 2
    position_size: float = 10000.0
    stop_loss_pct: float = 10.0
    slippage_pct: float = 0.5
    stop_mode: str = "intraday"
    entry_mode: str = "report_open"
    max_holding_days: Optional[int] = None  # disabled
    rotation: bool = True
    min_grade: str = "D"

    # Trailing stop (primary = ema, shadow = nwl)
    primary_trailing_stop: str = "weekly_ema"
    primary_trailing_period: int = 10
    shadow_trailing_stop: str = "weekly_nweek_low"
    shadow_trailing_period: int = 4
    trailing_transition_weeks: int = 2

    # Alpaca (paper default)
    alpaca_base_url: str = "https://paper-api.alpaca.markets"

    # Order timing
    entry_tif: str = "day"  # Paper: "day" / Live Elite: "opg" (Market On Open auction)

    # Safety
    max_daily_trade_orders: int = 40  # entry + exit
    max_daily_stop_orders: int = 20  # protective stop only
    entry_cutoff_minutes: int = 5  # report_open: block entry after open+5min
    min_buying_power: float = 5000.0
    fmp_lookback_days: int = 400

    def __post_init__(self) -> None:
        if self.daily_entry_limit < 0:
            raise ValueError(f"daily_entry_limit must be >= 0, got {self.daily_entry_limit}")

    def verify_against_manifest(self, manifest_path: str) -> None:
        """Compare frozen values against run_manifest.json. Raise on mismatch."""
        with open(manifest_path) as f:
            manifest = json.load(f)
        config_dict = manifest.get("config", manifest)
        mismatches = []
        for m_key, c_attr in MANIFEST_FIELD_MAP.items():
            m_val = config_dict.get(m_key)
            c_val = getattr(self, c_attr)
            if m_val != c_val and not (m_val is None and c_val is None):
                mismatches.append(f"  {m_key}: manifest={m_val}, config={c_val}")
        if mismatches:
            raise ValueError("LiveConfig does not match run_manifest:\n" + "\n".join(mismatches))


def resolve_api_key(key_name: str, mcp_server: str) -> Optional[str]:
    """Resolve API key: env var -> .mcp.json (same pattern as PriceFetcher)."""
    from dotenv import load_dotenv

    load_dotenv()
    key = os.getenv(key_name)
    if key:
        return key
    for mcp_path in [".mcp.json", "../.mcp.json"]:
        p = Path(mcp_path)
        if p.exists():
            try:
                data = json.loads(p.read_text())
                servers = data.get("mcpServers", data)
                srv = servers.get(mcp_server, {})
                val = srv.get("env", {}).get(key_name)
                if val:
                    logger.info("Loaded %s from %s", key_name, p)
                    return str(val)
            except (json.JSONDecodeError, KeyError) as e:
                logger.debug("Failed to read %s from %s: %s", key_name, p, e)
    return None
