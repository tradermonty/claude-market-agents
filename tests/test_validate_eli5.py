"""Tests for scripts/validate_eli5.py - the publish gate for ELI5 explainers."""

from __future__ import annotations

from scripts import validate_eli5 as v

DISCLAIMER = "This is an explanation of market moves, not investment advice."
SOURCE = "<p>SPY -0.51% QQQ -1.99% INSM +14.13% Sanofi $41B cash. 27 of 41 hits</p>"


def _page(body: str) -> str:
    return f"<!DOCTYPE html><html><head><style>.a{{width:50%}}</style></head><body>{body}</body></html>"


def test_valid_explainer_has_no_problems() -> None:
    page = _page(f"<p>▼ $SPY -0.51%</p><p>▲ +14.13% $41B, 27 of 41 stocks</p><p>{DISCLAIMER}</p>")
    assert v.find_problems(page, SOURCE) == []


def test_number_missing_from_source_is_reported() -> None:
    page = _page(f"<p>$SPY -0.52%</p><p>{DISCLAIMER}</p>")
    assert v.find_problems(page, SOURCE) == ["number not in source report: -0.52%"]


def test_missing_doctype_and_disclaimer_are_reported() -> None:
    problems = v.find_problems("<html><body>-0.51%</body></html>", SOURCE)
    assert "missing <!DOCTYPE html>" in problems
    assert "missing disclaimer" in problems


def test_css_percentages_are_ignored() -> None:
    page = _page(f'<div style="width:73%"></div><p>{DISCLAIMER}</p>')
    assert v.find_problems(page, SOURCE) == []
