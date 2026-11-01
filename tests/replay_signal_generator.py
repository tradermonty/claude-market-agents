"""Replay harness: re-run signal_generator against past JSON files (read-only).

Usage:
    uv run python tests/replay_signal_generator.py 2026-05-04 2026-05-05 ...

The harness invokes generate_signals() directly with:
  - alpaca_client=None (no Alpaca contact, no reconciliation)
  - FakePriceFetcher({}) (offline; trailing/exits won't trigger on empty DB)
  - tmpdir-backed StateDB (no live state mutation, WAL/SHM auto-cleaned)

Use this for past-date sanity checks and for diagnosing entry-pipeline
issues without touching production state.
"""

from __future__ import annotations

import os
import sys
import tempfile

from backtest.tests.fake_price_fetcher import FakePriceFetcher
from live.config import LiveConfig
from live.signal_generator import generate_signals
from live.state_db import StateDB


def replay(date: str) -> None:
    config = LiveConfig()
    price_fetcher = FakePriceFetcher({})

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_db = os.path.join(tmp_dir, "state.db")
        out_dir = os.path.join(tmp_dir, "signals")
        os.makedirs(out_dir, exist_ok=True)
        state_db = StateDB(tmp_db)

        report_file = f"reports/earnings_trade_analysis_{date}.html"
        if not os.path.exists(report_file):
            print(f"{date}: SKIP (report file not found: {report_file})")
            return

        result = generate_signals(
            config=config,
            state_db=state_db,
            alpaca_client=None,
            price_fetcher=price_fetcher,
            report_file=report_file,
            output_dir=out_dir,
            trade_date=date,
            run_id=f"replay-{date}",
            dry_run=True,
        )

        execution = result["execution"]
        print(
            f"{date}: entries={len(execution.get('entries', []))} "
            f"skipped={len(execution.get('skipped', []))} "
            f"exits={len(execution.get('exits', []))} "
            f"price_validation_failed={execution.get('price_validation_failed')}"
        )


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tests/replay_signal_generator.py YYYY-MM-DD [...]")
        return 2
    for d in sys.argv[1:]:
        replay(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
