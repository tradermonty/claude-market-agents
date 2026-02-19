#!/usr/bin/env python3
"""
JSON Candidates Parser

Parses structured JSON candidate files generated alongside HTML reports.
Preferred over HTML parsing for reliable score/price extraction.
"""

import json
import logging
import math
import os
import re
from typing import List

from backtest.html_parser import TradeCandidate

logger = logging.getLogger(__name__)

DATE_FROM_FILENAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
REQUIRED_FIELDS = ("ticker", "grade", "score", "price")
VALID_GRADES = {"A", "B", "C", "D"}
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9./-]{0,9}$")


def parse_candidates_json(filepath: str) -> List[TradeCandidate]:
    """Parse a JSON candidates file into TradeCandidate list.

    Returns empty list on any error (missing file, invalid JSON, etc.).
    Candidates missing required fields (ticker, grade, score, price) are skipped.
    """
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read JSON candidates file %s: %s", filepath, e)
        return []

    if not isinstance(data, dict):
        logger.warning("JSON root is not an object: %s", filepath)
        return []

    # Extract report_date from JSON or fallback to filename
    report_date = data.get("report_date")
    if not report_date:
        m = DATE_FROM_FILENAME_RE.search(os.path.basename(filepath))
        report_date = m.group(1) if m else "unknown"

    raw_candidates = data.get("candidates")
    if not isinstance(raw_candidates, list):
        logger.warning("No 'candidates' list in %s", filepath)
        return []

    candidates: List[TradeCandidate] = []
    for i, entry in enumerate(raw_candidates):
        if not isinstance(entry, dict):
            logger.debug("Skipping non-dict candidate at index %d", i)
            continue

        # Check required fields
        missing = [k for k in REQUIRED_FIELDS if entry.get(k) is None]
        if missing:
            logger.debug("Skipping candidate at index %d: missing %s", i, missing)
            continue

        try:
            ticker = str(entry["ticker"]).lstrip("$").strip()
            grade = str(entry["grade"]).upper()
            score = float(entry["score"])
            price = float(entry["price"])

            if not TICKER_RE.match(ticker):
                logger.debug("Skipping candidate at index %d: invalid ticker %r", i, ticker)
                continue
            if grade not in VALID_GRADES:
                logger.debug("Skipping candidate at index %d: invalid grade %r", i, grade)
                continue
            if not math.isfinite(score) or not (0 <= score <= 100):
                logger.debug("Skipping candidate at index %d: score %s out of range", i, score)
                continue
            if not math.isfinite(price) or price <= 0:
                logger.debug("Skipping candidate at index %d: price %s invalid", i, price)
                continue

            candidates.append(
                TradeCandidate(
                    ticker=ticker,
                    report_date=report_date,
                    grade=grade,
                    grade_source="json",
                    score=score,
                    price=price,
                    gap_size=float(entry["gap_size"])
                    if entry.get("gap_size") is not None
                    else None,
                    company_name=entry.get("company_name"),
                )
            )
        except (ValueError, TypeError) as e:
            logger.debug("Skipping candidate at index %d: %s", i, e)
            continue

    logger.info("Parsed %d candidates from JSON: %s", len(candidates), filepath)
    return candidates
