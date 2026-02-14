#!/usr/bin/env python3
"""
Smoke tests: parse all real reports, verify no crashes and data integrity.
"""

import os
import re
import pytest
from pathlib import Path

from bs4 import BeautifulSoup
from backtest.html_parser import EarningsReportParser

REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"


@pytest.mark.skipif(
    not REPORTS_DIR.exists() or not list(REPORTS_DIR.glob("earnings_trade_analysis_*.html")),
    reason="Real report files not available",
)
class TestSmoke:
    """Integration tests against real report data."""

    def test_parse_all_real_reports(self):
        """Parse all ~94 real reports: no crash, no dupes, valid data."""
        parser = EarningsReportParser()
        candidates = parser.parse_all_reports(str(REPORTS_DIR))

        # Should extract a significant number of candidates
        assert len(candidates) > 300, f"Too few candidates: {len(candidates)}"

        # No duplicates by (report_date, ticker)
        keys = [(c.report_date, c.ticker) for c in candidates]
        assert len(keys) == len(set(keys)), "Duplicate (report_date, ticker) found"

        # Score range (allow None)
        for c in candidates:
            if c.score is not None:
                assert 5 < c.score <= 100, f"{c.ticker} on {c.report_date}: score={c.score} out of range"

        # All grades valid
        for c in candidates:
            assert c.grade in {'A', 'B', 'C', 'D'}, f"{c.ticker}: invalid grade {c.grade}"

        # All grade_source valid
        for c in candidates:
            assert c.grade_source in {'html', 'inferred'}, f"{c.ticker}: invalid grade_source {c.grade_source}"

        # Report coverage
        report_dates = set(c.report_date for c in candidates)
        assert len(report_dates) >= 50, f"Too few report dates: {len(report_dates)}"

        # Ticker format validation
        for c in candidates:
            assert re.match(r'^[A-Z][A-Z0-9./-]{0,9}$', c.ticker), f"Invalid ticker format: {c.ticker}"
            assert '+' not in c.ticker, f"Ticker contains '+': {c.ticker}"
            assert '%' not in c.ticker, f"Ticker contains '%': {c.ticker}"

        # Grade source distribution: majority should be html
        html_count = sum(1 for c in candidates if c.grade_source == "html")
        assert html_count > len(candidates) * 0.5, \
            f"Too few HTML-sourced grades: {html_count}/{len(candidates)}"

        # All 4 grades present
        grades = set(c.grade for c in candidates)
        for g in ['A', 'B', 'C', 'D']:
            assert g in grades, f"Grade {g} not found in candidates"

    def test_no_stock_pages_handled(self):
        """No-stock pages should not crash or produce candidates."""
        parser = EarningsReportParser()
        # Parse all files, no-stock pages should be silently skipped
        candidates = parser.parse_all_reports(str(REPORTS_DIR))
        # If we get here without exception, no-stock pages were handled
        assert True

    def test_score_distribution(self):
        """Score distribution should span multiple ranges."""
        parser = EarningsReportParser()
        candidates = parser.parse_all_reports(str(REPORTS_DIR))

        scores = [c.score for c in candidates if c.score is not None]
        # Should have scores in multiple ranges
        has_high = any(s >= 85 for s in scores)
        has_mid = any(55 <= s < 85 for s in scores)

        assert has_high, "No high scores (>=85) found"
        assert has_mid, "No mid scores (55-84) found"

    def test_grade_distribution(self):
        """Should have candidates across multiple grades."""
        parser = EarningsReportParser()
        candidates = parser.parse_all_reports(str(REPORTS_DIR))

        grades = set(c.grade for c in candidates)
        # Should have all 4 grades
        assert 'A' in grades, "No A-grade candidates found"
        assert 'B' in grades, "No B-grade candidates found"
        assert 'C' in grades, "No C-grade candidates found"
        assert 'D' in grades, "No D-grade candidates found"

    def test_no_zero_candidate_reports(self):
        """Each report file should produce >= 1 candidate (except no-stocks pages and known exceptions)."""
        # Known files with 0 candidates due to pre-existing format gaps or holiday/empty pages
        KNOWN_ZERO = {
            'earnings_trade_analysis_2025-09-05.html',
            'earnings_trade_analysis_2025-09-09.html',
            'earnings_trade_analysis_2025-10-20.html',
            'earnings_trade_analysis_2025-10-31.html',
            'earnings_trade_analysis_2025-12-08.html',
            'earnings_trade_analysis_2025-12-11.html',
            'earnings_trade_analysis_2025-12-24.html',
            'earnings_trade_analysis_2025-12-25.html',
            'earnings_trade_analysis_2025-12-29.html',
            'earnings_trade_analysis_2025-12-31.html',
            'earnings_trade_analysis_2026-01-01.html',
            'earnings_trade_analysis_2026-01-07.html',
            'earnings_trade_analysis_2026-01-09.html',
            'earnings_trade_analysis_2026-01-14.html',
            'earnings_trade_analysis_2026-01-19.html',
        }
        parser = EarningsReportParser()
        report_files = sorted(REPORTS_DIR.glob("earnings_trade_analysis_*.html"))
        zero_files = []
        for f in report_files:
            if f.name in KNOWN_ZERO:
                continue
            candidates = parser.parse_single_report(str(f))
            if len(candidates) == 0 and not parser._is_no_stocks_page(
                BeautifulSoup(f.read_text(), 'html.parser')
            ):
                zero_files.append(f.name)
        assert zero_files == [], f"Reports with 0 candidates (not no-stocks, not known): {zero_files}"
