"""Tests for the After-Market ELI5 category in scripts/publish_reports.py."""

from __future__ import annotations

import pytest

from scripts import publish_reports as pr


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("2026-11-25-after-market-eli5.html", "After-Market ELI5"),
        ("2026-11-25-after-market-report.html", "After-Market Reports"),
        ("earnings_trade_analysis_2026-11-25.html", "Earnings Trade Analysis"),
        ("2026-11-25-something-else.html", None),
    ],
)
def test_classify_separates_eli5_from_full_report(filename: str, expected: str | None) -> None:
    assert pr.classify(filename) == expected


def test_eli5_category_is_published() -> None:
    assert "After-Market ELI5" in pr.PUBLISHED_CATEGORIES


def test_index_lists_eli5_section_after_full_reports(tmp_path) -> None:
    for name in ("2026-11-25-after-market-report.html", "2026-11-25-after-market-eli5.html"):
        (tmp_path / name).write_text(f"<html><head><title>{name}</title></head></html>")
    index = pr.generate_index(pr.scan_reports(tmp_path))
    full = index.index('id="section-after-market-reports"')
    eli5 = index.index('id="section-after-market-eli5"')
    assert full < eli5
