#!/usr/bin/env python3
"""Tests for live trading configuration/manifest parity."""

import json
from pathlib import Path

import pytest

from live.config import LiveConfig

CANONICAL_MANIFEST = "reports/backtest/run_manifest.json"
VIX20_MANIFEST = "reports/backtest/vix20/run_manifest.json"
MANIFEST_FIXTURES = Path(__file__).parent / "fixtures" / "manifests"

# Generated reports exist only on the machine that runs the backtests and
# launchd jobs. Tracked snapshots always run; only generated-file cases skip.
requires_canonical_manifest = pytest.mark.skipif(
    not Path(CANONICAL_MANIFEST).exists(), reason=f"{CANONICAL_MANIFEST} not present"
)
requires_vix20_manifest = pytest.mark.skipif(
    not Path(VIX20_MANIFEST).exists(), reason=f"{VIX20_MANIFEST} not present"
)


@pytest.mark.parametrize(
    "manifest_path",
    [
        pytest.param(str(MANIFEST_FIXTURES / "canonical.json"), id="tracked-snapshot"),
        pytest.param(CANONICAL_MANIFEST, marks=requires_canonical_manifest, id="generated"),
    ],
)
def test_default_config_matches_canonical_backtest_manifest(manifest_path: str) -> None:
    """Live defaults should match the manifest used by launchd scripts."""
    LiveConfig().verify_against_manifest(manifest_path)


@pytest.mark.parametrize(
    "manifest_path",
    [
        pytest.param(str(MANIFEST_FIXTURES / "canonical.json"), id="tracked-snapshot"),
        pytest.param(CANONICAL_MANIFEST, marks=requires_canonical_manifest, id="generated"),
    ],
)
def test_config_from_canonical_manifest_matches_manifest(manifest_path: str) -> None:
    config = LiveConfig.from_manifest(manifest_path)

    assert config.primary_strategy_id == "nwl_p4"
    assert config.shadow_signal_label == "shadow_nwl_p4"
    assert config.entry_quality_filter is False
    assert config.vix_filter is False
    config.verify_against_manifest(manifest_path)


def test_manifest_verification_catches_trailing_stop_mismatch(tmp_path: Path) -> None:
    manifest = {
        "config": {
            "position_size": 10000,
            "stop_loss": 10.0,
            "slippage": 0.5,
            "max_holding": None,
            "min_grade": "D",
            "stop_mode": "intraday",
            "entry_mode": "report_open",
            "daily_entry_limit": None,
            "max_positions": 20,
            "trailing_transition_weeks": 2,
            "trailing_stop": "weekly_ema",
            "trailing_ema_period": 10,
            "trailing_nweek_period": 4,
            "no_rotation": False,
        }
    }
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="trailing_stop"):
        LiveConfig().verify_against_manifest(str(path))


def test_manifest_verification_catches_daily_limit_mismatch(tmp_path: Path) -> None:
    manifest = {
        "config": {
            "position_size": 10000,
            "stop_loss": 10.0,
            "slippage": 0.5,
            "max_holding": None,
            "min_grade": "D",
            "stop_mode": "intraday",
            "entry_mode": "report_open",
            "daily_entry_limit": 2,
            "max_positions": 20,
            "trailing_transition_weeks": 2,
            "trailing_stop": "weekly_nweek_low",
            "trailing_ema_period": 10,
            "trailing_nweek_period": 4,
            "no_rotation": False,
        }
    }
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="daily_entry_limit"):
        LiveConfig().verify_against_manifest(str(path))


def test_manifest_verification_catches_entry_quality_filter_mismatch(tmp_path: Path) -> None:
    manifest = {
        "config": {
            "position_size": 10000,
            "stop_loss": 10.0,
            "slippage": 0.5,
            "max_holding": None,
            "min_grade": "D",
            "stop_mode": "intraday",
            "entry_mode": "report_open",
            "daily_entry_limit": None,
            "max_positions": 20,
            "trailing_transition_weeks": 2,
            "trailing_stop": "weekly_nweek_low",
            "trailing_ema_period": 10,
            "trailing_nweek_period": 4,
            "no_rotation": False,
            "entry_quality_filter": True,
            "exclude_price_min": 10,
            "exclude_price_max": 30,
            "risk_gap_threshold": 10,
            "risk_score_threshold": 85,
        }
    }
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="entry_quality_filter"):
        LiveConfig().verify_against_manifest(str(path))


def test_manifest_verification_catches_vix_filter_mismatch(tmp_path: Path) -> None:
    manifest = {
        "config": {
            "position_size": 10000,
            "stop_loss": 10.0,
            "slippage": 0.5,
            "max_holding": None,
            "min_grade": "D",
            "stop_mode": "intraday",
            "entry_mode": "report_open",
            "daily_entry_limit": None,
            "max_positions": 20,
            "trailing_transition_weeks": 2,
            "trailing_stop": "weekly_nweek_low",
            "trailing_ema_period": 10,
            "trailing_nweek_period": 4,
            "no_rotation": False,
            "vix_filter": True,
            "vix_threshold": 20.0,
        }
    }
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="vix_filter"):
        LiveConfig().verify_against_manifest(str(path))


@pytest.mark.parametrize(
    "manifest_path",
    [
        pytest.param(str(MANIFEST_FIXTURES / "vix20.json"), id="tracked-snapshot"),
        pytest.param(VIX20_MANIFEST, marks=requires_vix20_manifest, id="generated"),
    ],
)
def test_vix20_manifest_can_be_selected_explicitly(manifest_path: str) -> None:
    config = LiveConfig.from_manifest(manifest_path)

    assert config.primary_strategy_id == "nwl_p4"
    assert config.trailing_transition_weeks == 3
    assert config.entry_quality_filter is True
    assert config.exclude_price_min == 10
    assert config.exclude_price_max == 30
    assert config.vix_filter is True
    assert config.vix_threshold == 20.0
    config.verify_against_manifest(manifest_path)
