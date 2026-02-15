#!/usr/bin/env python3
"""Tests for run manifest generation."""

import json
import re

import pytest

from backtest.run_manifest import write_manifest


@pytest.fixture
def tmp_output(tmp_path):
    return str(tmp_path)


class TestRunManifest:
    """Run manifest generation tests."""

    def test_json_created(self, tmp_output):
        path = write_manifest(
            output_dir=tmp_output,
            config={"stop_loss": 10.0, "slippage": 0.5},
            summary_metrics={"win_rate": 55.0, "total_pnl": 1234.56},
            candidate_count=100,
            trade_count=80,
            skipped_count=20,
        )
        assert path.exists()
        data = json.loads(path.read_text())
        assert "timestamp" in data
        assert "config" in data
        assert "summary_metrics" in data
        assert data["data"]["candidate_count"] == 100
        assert data["data"]["trade_count"] == 80
        assert data["data"]["skipped_count"] == 20

    def test_sha_format(self, tmp_output):
        path = write_manifest(
            output_dir=tmp_output,
            config={},
            summary_metrics={},
            candidate_count=0,
            trade_count=0,
            skipped_count=0,
        )
        data = json.loads(path.read_text())
        sha = data.get("git_sha")
        if sha is not None:
            assert re.match(r"^[0-9a-f]{40}$", sha), f"Invalid SHA: {sha}"

    def test_config_roundtrip(self, tmp_output):
        config = {
            "position_size": 10000,
            "stop_loss": 10.0,
            "slippage": 0.5,
            "max_holding": 90,
            "stop_mode": "intraday",
            "min_score": None,
            "max_score": None,
        }
        path = write_manifest(
            output_dir=tmp_output,
            config=config,
            summary_metrics={},
            candidate_count=0,
            trade_count=0,
            skipped_count=0,
        )
        data = json.loads(path.read_text())
        assert data["config"] == config

    def test_non_git_environment(self, tmp_output, monkeypatch):
        """Should not crash when git is not available."""
        monkeypatch.setenv("PATH", "")
        path = write_manifest(
            output_dir=tmp_output,
            config={},
            summary_metrics={},
            candidate_count=0,
            trade_count=0,
            skipped_count=0,
        )
        data = json.loads(path.read_text())
        assert data["git_sha"] is None
        assert data["git_dirty"] is None
