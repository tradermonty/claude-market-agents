"""Tests for scripts/publish_reports.py GitHub Pages live-verification helpers."""

from __future__ import annotations

import pytest

from scripts import publish_reports as pr

# ---------------------------------------------------------------------------
# derive_site_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        (
            "git@github.com:tradermonty/claude-market-agents.git",
            "https://tradermonty.github.io/claude-market-agents/",
        ),
        (
            "https://github.com/tradermonty/claude-market-agents.git",
            "https://tradermonty.github.io/claude-market-agents/",
        ),
        (
            "https://github.com/tradermonty/claude-market-agents",
            "https://tradermonty.github.io/claude-market-agents/",
        ),
        (
            "ssh://git@github.com/Tradermonty/Claude-Market-Agents.git",
            "https://tradermonty.github.io/Claude-Market-Agents/",
        ),
        # SSH host alias from a multi-account ~/.ssh/config - what this repo uses.
        (
            "git@github.com-tradermonty:tradermonty/claude-market-agents.git",
            "https://tradermonty.github.io/claude-market-agents/",
        ),
        # User/organisation site: the repo itself is the site root.
        (
            "git@github.com:tradermonty/tradermonty.github.io.git",
            "https://tradermonty.github.io/",
        ),
    ],
)
def test_derive_site_url_supported_remotes(remote: str, expected: str) -> None:
    assert pr.derive_site_url(remote) == expected


@pytest.mark.parametrize(
    "remote",
    [
        "",
        "git@gitlab.com:tradermonty/claude-market-agents.git",
        "https://example.com/whatever",
    ],
)
def test_derive_site_url_returns_none_for_unsupported(remote: str) -> None:
    assert pr.derive_site_url(remote) is None


# ---------------------------------------------------------------------------
# parse_changed_reports
# ---------------------------------------------------------------------------


def test_parse_changed_reports_picks_added_and_modified_html() -> None:
    porcelain = (
        "A  2026-08-06-after-market-report.html\n"
        "M  earnings_trade_analysis_2026-08-06.html\n"
        "M  index.html\n"
        "D  2020-01-01-after-market-report.html\n"
        "A  .nojekyll\n"
    )
    assert pr.parse_changed_reports(porcelain) == [
        "2026-08-06-after-market-report.html",
        "earnings_trade_analysis_2026-08-06.html",
    ]


def test_parse_changed_reports_handles_renames_and_blank_input() -> None:
    assert pr.parse_changed_reports("R  old-report.html -> new-report.html\n") == [
        "new-report.html"
    ]
    assert pr.parse_changed_reports("") == []


# ---------------------------------------------------------------------------
# wait_until_live
# ---------------------------------------------------------------------------


class FakeClock:
    """Monotonic clock advanced only by the sleeper, so tests never really sleep."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_wait_until_live_returns_true_without_sleeping_when_already_live() -> None:
    clock = FakeClock()
    calls: list[str] = []

    def checker(url: str) -> bool:
        calls.append(url)
        return True

    assert pr.wait_until_live(
        "https://example.github.io/repo/x.html",
        timeout=60,
        interval=15,
        checker=checker,
        sleeper=clock.sleep,
        clock=clock,
    )
    assert len(calls) == 1
    assert clock.slept == []


def test_wait_until_live_retries_until_the_url_appears() -> None:
    clock = FakeClock()
    responses = [False, False, True]

    def checker(_url: str) -> bool:
        return responses.pop(0)

    assert pr.wait_until_live(
        "https://example.github.io/repo/x.html",
        timeout=60,
        interval=15,
        checker=checker,
        sleeper=clock.sleep,
        clock=clock,
    )
    assert clock.slept == [15, 15]


def test_wait_until_live_gives_up_after_the_timeout() -> None:
    clock = FakeClock()
    attempts = 0

    def checker(_url: str) -> bool:
        nonlocal attempts
        attempts += 1
        return False

    assert not pr.wait_until_live(
        "https://example.github.io/repo/x.html",
        timeout=45,
        interval=15,
        checker=checker,
        sleeper=clock.sleep,
        clock=clock,
    )
    # Bounded: one attempt per interval within the timeout, never unbounded.
    assert attempts == 4
    assert clock.slept == [15, 15, 15]


# ---------------------------------------------------------------------------
# verify_reports_live
# ---------------------------------------------------------------------------


def test_verify_reports_live_reports_the_files_that_never_appeared() -> None:
    clock = FakeClock()
    live = {"https://site/repo/a.html"}

    def checker(url: str) -> bool:
        return url.split("?")[0] in live

    missing = pr.verify_reports_live(
        "https://site/repo/",
        ["a.html", "b.html"],
        timeout=30,
        interval=15,
        checker=checker,
        sleeper=clock.sleep,
        clock=clock,
    )
    assert missing == ["b.html"]


def test_verify_reports_live_returns_empty_when_all_are_live() -> None:
    clock = FakeClock()

    missing = pr.verify_reports_live(
        "https://site/repo/",
        ["a.html", "b.html"],
        timeout=30,
        interval=15,
        checker=lambda _url: True,
        sleeper=clock.sleep,
        clock=clock,
    )
    assert missing == []


# ---------------------------------------------------------------------------
# classify: ELI5 explainers get their own category
# ---------------------------------------------------------------------------


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
