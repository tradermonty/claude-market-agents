"""Report entry-point replay; Claude/provider and publish/email are offline fixtures."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backtest.json_parser import parse_candidates_json
from backtest.report_screening import ScreeningUnavailableError, validate_screening
from live.signal_generator import PriceValidationError, _strict_parse_json

ROOT = Path(__file__).resolve().parents[1]


def payload(state, source="earnings_trading_screener"):
    return {
        "report_date": "2026-11-16",
        "screening": {
            "status": state,
            "source": source,
            "evidence": "Authenticated same-filter result with explicit count"
            if state != "unavailable"
            else "Authentication failed; empty response unverifiable",
            "tickers": ["AAPL"] if state == "ok" else [],
        },
        "candidates": [{"ticker": "AAPL", "grade": "A", "score": 90, "price": 200}]
        if state == "ok"
        else (None if state == "unavailable" else []),
    }


@pytest.mark.parametrize("reader", [parse_candidates_json, _strict_parse_json])
def test_unavailable_is_not_zero_candidates(tmp_path, reader):
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(payload("unavailable")))
    with pytest.raises((ValueError, PriceValidationError), match="unavailable"):
        reader(str(path))


@pytest.mark.parametrize("report", ["earnings_trade", "after_market"])
@pytest.mark.parametrize(
    "state",
    [
        "ok",
        "empty",
        "unavailable",
        "missing",
        "malformed",
        "outside_universe",
        "friday",
        "invalid_friday",
    ],
)
def test_report_entrypoint(tmp_path, report, state):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "reports").mkdir()
    shutil.copy(ROOT / "scripts/lib_retry.sh", scripts)
    shutil.copytree(
        ROOT / "backtest",
        tmp_path / "backtest",
        ignore=shutil.ignore_patterns("tests", "__pycache__"),
    )
    validator = ROOT / "scripts/validate_report_screening.py"
    shutil.copy(validator, scripts)
    (scripts / "run_publish_reports.sh").write_text(
        "#!/bin/sh\ntouch '" + str(tmp_path / "run_publish_reports.sh") + "'\n"
    )
    (scripts / "run_publish_reports.sh").chmod(0o755)
    (scripts / "send_report.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        f"Path({str(tmp_path / 'email_args')!r}).write_text(repr(sys.argv))\n"
    )
    runner = (ROOT / f"scripts/run_{report}_report.sh").read_text()
    runner = runner.replace(
        "PROJECT_DIR=/Users/takueisaotome/PycharmProjects/claude-market-agents",
        f"PROJECT_DIR={tmp_path}",
    )
    runner = runner.replace("BACKOFF_SECS=30", "BACKOFF_SECS=0")
    runner = re.sub(r"TIMEOUT_SECS=\d+", "TIMEOUT_SECS=2", runner)
    runner = runner.replace("/opt/homebrew/bin/python3.11", sys.executable)
    (scripts / "runner.sh").write_text(runner)
    source = (
        "earnings_trading_screener"
        if report == "earnings_trade"
        else "earnings_afterhours_screener"
    )
    data = payload(
        "empty"
        if state in {"missing", "malformed"}
        else ("ok" if state == "outside_universe" else state),
        source,
    )
    report_date = "2026-11-13" if state == "friday" else "2026-11-16"
    if state in {"friday", "invalid_friday"}:
        data = payload("skipped_friday", source)
        data["report_date"] = report_date
        data["candidates"] = []
    if state == "outside_universe":
        data["screening"]["tickers"] = ["MSFT"]
        if report == "after_market":
            data["screening"]["source"] = "wrong_screener"
    (tmp_path / "fixture.json").write_text(json.dumps(data))
    # Stub generates the artifacts required by the real shell entry point.
    stub = f"""#!{sys.executable}
import json
from pathlib import Path
root = Path({str(tmp_path)!r})
data = json.loads((root / "fixture.json").read_text())
state = data["screening"]["status"]
text = {{"ok": "AAPL", "empty": {("No stocks available for earnings trading." if report == "earnings_trade" else "This Week's Remaining Earnings Calendar")!r}, "unavailable": {("Screening unavailable; candidate count unknown." if report == "earnings_trade" else "After-hours screening unavailable; matching count unknown.")!r}, "skipped_friday": "Next Week's Major Earnings Calendar"}}[state]
link = "https://elite.finviz.com/screener.ashx?v=211&t=AAPL" if state == "ok" else "https://github.com/tradermonty/claude-market-agents/issues/46"
for name in {["earnings_trade_analysis_2026-11-16.html", "earnings_trade_X_message_2026-11-16.md"] if report == "earnings_trade" else ["2026-11-16-after-market-report.html", "2026-11-16-after-market-x-post.md"]!r}:
    (root / "reports" / name).write_text(text + " " + link)
name = {"earnings_trade_candidates_2026-11-16.json" if report == "earnings_trade" else "2026-11-16-after-market-screening.json"!r}
if {state != "missing"!r}:
    (root / "reports" / name).write_text("{{" if {state == "malformed"!r} else json.dumps(data))
"""
    stub = stub.replace("2026-11-16", report_date)
    (scripts / "claude").write_text(stub)
    (scripts / "claude").chmod(0o755)
    (scripts / "date").write_text(
        '#!/bin/sh\ncase "$1" in +%Y-%m-%d) echo 2026-11-16;; *) /bin/date "$@";; esac\n'
    )
    (scripts / "date").write_text((scripts / "date").read_text().replace("2026-11-16", report_date))
    (scripts / "date").chmod(0o755)
    if state == "unavailable":
        (tmp_path / "logs" / f".{report}_{report_date}.success").touch()
    result = subprocess.run(
        ["bash", str(scripts / "runner.sh"), "--force"],
        env={**os.environ, "PATH": f"{scripts}:{os.environ['PATH']}"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        timeout=20,
    )
    success = state in {"ok", "empty"} or (state == "friday" and report == "after_market")
    assert (result.returncode == 0) == success
    assert (tmp_path / "run_publish_reports.sh").exists() == success
    assert bool(list((tmp_path / "logs").glob(".*.success"))) == success
    email_args = (tmp_path / "email_args").read_text()
    assert ("--report-html" in email_args) == success
    assert ("--alert-text" in email_args) != success
    if state in {"ok", "empty", "unavailable"}:
        for path in (tmp_path / "reports").glob("*"):
            if path.suffix in {".html", ".md"}:
                text = path.read_text()
                assert "https://" in text
                assert "SYMBOLS" not in text
                assert "{TICKER" not in text
                if state != "ok":
                    assert "t=" not in text
                if state == "empty" and report == "after_market":
                    assert "This Week's Remaining Earnings Calendar" in text
                assert ("screening unavailable" in text.lower()) == (state == "unavailable")
        if report == "earnings_trade":
            path = tmp_path / "reports/earnings_trade_candidates_2026-11-16.json"
            if state == "unavailable":
                with pytest.raises(PriceValidationError):
                    _strict_parse_json(str(path))
            else:
                assert len(_strict_parse_json(str(path))) == (1 if state == "ok" else 0)


@pytest.mark.parametrize(
    "screen",
    [
        None,
        {},
        {"status": "empty"},
        {"status": "ok", "tickers": []},
        {"status": "empty", "tickers": ["AAPL"]},
        {"status": "ok", "tickers": ["AAPL", "AAPL"]},
        {"status": "ok", "tickers": [{}]},
        {"status": "bogus", "tickers": []},
        {"status": "empty", "tickers": [], "evidence": ""},
    ],
)
def test_invalid_screening_is_not_zero(screen):
    data = payload("empty")
    if isinstance(screen, dict):
        screen = {"source": "earnings_trading_screener", "evidence": "verified", **screen}
    data["screening"] = screen
    with pytest.raises(ScreeningUnavailableError):
        validate_screening(data, "earnings_trading_screener")


@pytest.mark.parametrize("reader", [parse_candidates_json, _strict_parse_json])
@pytest.mark.parametrize("candidates", [None, [], [{"ticker": "AAPL"}]])
def test_explicit_unavailable_rejects_even_old_candidates(tmp_path, reader, candidates):
    data = payload("unavailable")
    data["candidates"] = candidates
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(data))
    with pytest.raises((ScreeningUnavailableError, PriceValidationError), match="unavailable"):
        reader(str(path))


def test_prompt_entrypoints_follow_availability_contract():
    for name in [
        "prompts/earnings-trade.md",
        "prompts/after-market-report.md",
        ".claude/commands/after-market-report.md",
        ".claude/agents/earnings-trade-analyst.md",
        ".claude/agents/after-market-reporter.md",
    ]:
        text = (ROOT / name).read_text()
        assert "docs/report-screening-contract.md" in text
        assert "unavailable" in text
        assert "get_moving_average_position(symbol=" not in text
        assert "get_stock_news(tickers=[" not in text
    for name in ["prompts/after-market-report.md", ".claude/commands/after-market-report.md"]:
        text = (ROOT / name).read_text()
        assert 'earnings_screener(earnings_date="today_after")' in text
        assert "Friday Rule" in text
        assert "This Week’s Remaining Earnings Calendar" in text
