#!/usr/bin/env python3
"""Reject missing, stale-date, invalid or unavailable screening artifacts."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest.report_screening import validate_screening


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact")
    parser.add_argument("--source", required=True)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    try:
        data = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("report_date") != args.date:
            raise ValueError("Screening unavailable: missing or mismatched report_date")
        validate_screening(data, args.source)
    except (OSError, ValueError) as exc:
        print(f"Report screening validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
