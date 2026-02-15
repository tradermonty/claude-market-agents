#!/usr/bin/env python3
"""Run manifest writer for experiment reproducibility.

Writes a JSON file capturing the exact configuration, environment,
and summary metrics for each backtest run.
"""

import json
import logging
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _git_sha() -> Optional[str]:
    """Get the current git commit SHA, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _git_dirty() -> Optional[bool]:
    """Check if the working tree has uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def write_manifest(
    output_dir: str,
    config: Dict[str, Any],
    summary_metrics: Dict[str, Any],
    candidate_count: int,
    trade_count: int,
    skipped_count: int,
) -> Path:
    """Write run_manifest.json to the output directory.

    Args:
        output_dir: Directory to write the manifest file.
        config: CLI configuration dict.
        summary_metrics: Key metrics from the backtest run.
        candidate_count: Number of candidates after filtering.
        trade_count: Number of executed trades.
        skipped_count: Number of skipped trades.

    Returns:
        Path to the written manifest file.
    """
    manifest = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "python_version": platform.python_version(),
        "config": config,
        "data": {
            "candidate_count": candidate_count,
            "trade_count": trade_count,
            "skipped_count": skipped_count,
        },
        "summary_metrics": summary_metrics,
    }

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    manifest_path = out_path / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    logger.info(f"Run manifest written to {manifest_path}")
    return manifest_path
