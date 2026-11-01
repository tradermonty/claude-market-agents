#!/usr/bin/env python3
"""Live trading configuration with frozen parameters matching run_manifest."""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
CANONICAL_MANIFEST_PATH = "reports/backtest/run_manifest.json"

MANIFEST_DEFAULTS: dict[str, Any] = {
    "entry_quality_filter": False,
    "exclude_price_min": None,
    "exclude_price_max": None,
    "risk_gap_threshold": None,
    "risk_score_threshold": None,
    "vix_filter": False,
    "vix_threshold": None,
}

MANIFEST_FIELD_MAP = {
    "position_size": "position_size",
    "stop_loss": "stop_loss_pct",
    "slippage": "slippage_pct",
    "max_holding": "max_holding_days",
    "min_grade": "min_grade",
    "stop_mode": "stop_mode",
    "entry_mode": "entry_mode",
    "daily_entry_limit": "daily_entry_limit",
    "max_positions": "max_positions",
    "trailing_transition_weeks": "trailing_transition_weeks",
    "entry_quality_filter": "entry_quality_filter",
    "exclude_price_min": "exclude_price_min",
    "exclude_price_max": "exclude_price_max",
    "risk_gap_threshold": "risk_gap_threshold",
    "risk_score_threshold": "risk_score_threshold",
    "vix_filter": "vix_filter",
    "vix_threshold": "vix_threshold",
}


def _read_manifest_config(manifest_path: str) -> dict[str, Any]:
    with open(manifest_path) as f:
        manifest = json.load(f)
    config: dict[str, Any] = manifest.get("config", manifest)
    return config


def strategy_id_for(trailing_stop: str, period: int) -> str:
    """Return the persisted strategy identifier for a trailing-stop rule."""
    if trailing_stop == "weekly_nweek_low":
        return f"nwl_p{period}"
    if trailing_stop == "weekly_ema":
        return f"ema_p{period}"
    return f"{trailing_stop}_p{period}"


@dataclass(frozen=True)
class LiveConfig:
    """Frozen configuration for live paper trading.

    All parameters must match run_manifest.json values exactly.
    Use verify_against_manifest() to confirm alignment.
    """

    # Must match run_manifest exactly
    max_positions: int = 20
    daily_entry_limit: int | None = None
    position_size: float = 10000.0
    stop_loss_pct: float = 10.0
    slippage_pct: float = 0.5
    stop_mode: str = "intraday"
    entry_mode: str = "report_open"
    max_holding_days: int | None = None  # disabled
    rotation: bool = True
    min_grade: str = "D"

    # Trailing stop. Primary execution must match reports/backtest/run_manifest.json.
    primary_trailing_stop: str = "weekly_nweek_low"
    primary_trailing_period: int = 4
    shadow_trailing_stop: str = "weekly_nweek_low"
    shadow_trailing_period: int = 4
    trailing_transition_weeks: int = 2

    # Optional filters. Defaults match reports/backtest/run_manifest.json.
    entry_quality_filter: bool = False
    exclude_price_min: float | None = None
    exclude_price_max: float | None = None
    risk_gap_threshold: float | None = None
    risk_score_threshold: float | None = None
    vix_filter: bool = False
    vix_threshold: float | None = None

    # Alpaca (paper default)
    alpaca_base_url: str = "https://paper-api.alpaca.markets"

    # Order timing
    # OPG復帰チェックリスト (Live Elite移行時):
    #   1. ここを "opg" に変更
    #   2. scripts/run_executor_place.sh: --phase all → --phase place
    entry_tif: str = "day"  # Paper: "day" / Live Elite: "opg" (Market On Open auction)

    # Safety
    max_daily_trade_orders: int = 40  # entry + exit
    max_daily_stop_orders: int = 20  # protective stop only
    entry_cutoff_minutes: int = 5  # report_open: block entry after open+5min
    min_buying_power: float = 5000.0
    fmp_lookback_days: int = 400

    def __post_init__(self) -> None:
        if self.daily_entry_limit is not None and self.daily_entry_limit < 0:
            raise ValueError(f"daily_entry_limit must be >= 0, got {self.daily_entry_limit}")

    @property
    def primary_strategy_id(self) -> str:
        return strategy_id_for(self.primary_trailing_stop, self.primary_trailing_period)

    @property
    def shadow_strategy_id(self) -> str:
        return strategy_id_for(self.shadow_trailing_stop, self.shadow_trailing_period)

    @property
    def shadow_signal_label(self) -> str:
        if self.shadow_strategy_id == self.primary_strategy_id:
            return f"shadow_{self.shadow_strategy_id}"
        return self.shadow_strategy_id

    @classmethod
    def from_manifest(cls, manifest_path: str) -> "LiveConfig":
        """Build a LiveConfig from the selected manifest.

        This keeps alternate manifests operable from the CLI while
        verify_against_manifest() remains the final guard.
        """
        config_dict = _read_manifest_config(manifest_path)
        kwargs: dict[str, Any] = {}

        for m_key, c_attr in MANIFEST_FIELD_MAP.items():
            if m_key in config_dict:
                kwargs[c_attr] = config_dict[m_key]
            elif m_key in MANIFEST_DEFAULTS:
                kwargs[c_attr] = MANIFEST_DEFAULTS[m_key]

        trailing_stop = config_dict.get("trailing_stop")
        if trailing_stop:
            kwargs["primary_trailing_stop"] = trailing_stop
            kwargs["shadow_trailing_stop"] = trailing_stop
            period_key = {
                "weekly_ema": "trailing_ema_period",
                "weekly_nweek_low": "trailing_nweek_period",
            }.get(trailing_stop)
            if period_key and period_key in config_dict:
                kwargs["primary_trailing_period"] = config_dict[period_key]
                kwargs["shadow_trailing_period"] = config_dict[period_key]

        if "no_rotation" in config_dict:
            kwargs["rotation"] = not bool(config_dict["no_rotation"])

        return cls(**kwargs)

    def verify_against_manifest(self, manifest_path: str) -> None:
        """Compare frozen values against run_manifest.json. Raise on mismatch."""
        config_dict = _read_manifest_config(manifest_path)
        mismatches = []
        for m_key, c_attr in MANIFEST_FIELD_MAP.items():
            m_val = config_dict.get(m_key, MANIFEST_DEFAULTS.get(m_key))
            c_val = getattr(self, c_attr)
            if m_val != c_val and not (m_val is None and c_val is None):
                mismatches.append(f"  {m_key}: manifest={m_val}, config={c_val}")

        manifest_trailing_stop = config_dict.get("trailing_stop")
        if manifest_trailing_stop != self.primary_trailing_stop:
            mismatches.append(
                "  trailing_stop: "
                f"manifest={manifest_trailing_stop}, config={self.primary_trailing_stop}"
            )

        period_key = {
            "weekly_ema": "trailing_ema_period",
            "weekly_nweek_low": "trailing_nweek_period",
        }.get(self.primary_trailing_stop)
        if period_key:
            manifest_period = config_dict.get(period_key)
            if manifest_period != self.primary_trailing_period:
                mismatches.append(
                    f"  {period_key}: "
                    f"manifest={manifest_period}, config={self.primary_trailing_period}"
                )

        if "no_rotation" in config_dict:
            manifest_rotation = not bool(config_dict["no_rotation"])
            if manifest_rotation != self.rotation:
                mismatches.append(
                    f"  no_rotation: manifest={config_dict['no_rotation']}, "
                    f"config.rotation={self.rotation}"
                )
        if mismatches:
            raise ValueError("LiveConfig does not match run_manifest:\n" + "\n".join(mismatches))


def resolve_api_key(key_name: str, mcp_server: str) -> str | None:
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
