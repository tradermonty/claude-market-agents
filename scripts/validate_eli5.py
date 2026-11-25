#!/usr/bin/env python3
"""Publish gate for the after-market ELI5 explainer.

The explainer is a rewrite of the day's full report, so every percentage and
dollar figure it shows must already appear in that report. This catches a
model that rounded, mistyped or invented a number before it reaches the site.

Usage: validate_eli5.py <eli5.html> <source.html> [<source2> ...]
Exit 0 when clean; exit 1 and print one problem per line otherwise.
Stdlib only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DISCLAIMER = "not investment advice"

# Signed or unsigned percentages, and dollar amounts such as $41B / $1.2T.
_NUMBER_RE = re.compile(r"[+-]?\d+(?:\.\d+)?%|\$\d+(?:\.\d+)?[KMBT]?\b")


def visible_text(page: str) -> str:
    """Strip <style>/<script> blocks and tags (with their attributes)."""
    page = re.sub(r"<(style|script)\b.*?</\1>", " ", page, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", page)


def find_problems(page: str, source: str) -> list[str]:
    problems = []
    if not page.lstrip().lower().startswith("<!doctype html"):
        problems.append("missing <!DOCTYPE html>")
    if DISCLAIMER not in page:
        problems.append("missing disclaimer")
    source_text = visible_text(source)
    for number in dict.fromkeys(_NUMBER_RE.findall(visible_text(page))):
        # Compare unsigned: the source may show "+3.18%" or "3.18%" for the same move.
        if number.lstrip("+-") not in source_text:
            problems.append(f"number not in source report: {number}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__.strip().splitlines()[-3], file=sys.stderr)
        return 2
    page = Path(argv[1]).read_text(encoding="utf-8")
    source = "\n".join(Path(p).read_text(encoding="utf-8") for p in argv[2:] if Path(p).exists())
    problems = find_problems(page, source)
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
