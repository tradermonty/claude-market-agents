"""Screen availability contract shared by generation gates and candidate readers."""

import json
from datetime import date
from pathlib import Path
from typing import Any


class ScreeningUnavailableError(ValueError):
    """A report cannot establish its screener universe; this is not zero matches."""


def validate_screening(data: dict[str, Any], source: str) -> None:
    """Require explicit evidence and consistent artifacts for a new report.

    Evidence is recorded by the reporter from provider responses. This validates
    the contract, not the authenticity of a remote provider's response.
    """
    screen = data.get("screening")
    if not isinstance(screen, dict) or screen.get("source") != source:
        raise ScreeningUnavailableError("Screening unavailable: missing or incorrect source")
    status = screen.get("status")
    if status == "unavailable":
        raise ScreeningUnavailableError(f"Screening unavailable: {screen.get('evidence', '')}")
    if not isinstance(screen.get("evidence"), str) or not screen["evidence"].strip():
        raise ScreeningUnavailableError("Screening unavailable: missing verification evidence")
    tickers = screen.get("tickers")
    if not isinstance(tickers, list) or any(not isinstance(t, str) or not t for t in tickers):
        raise ScreeningUnavailableError("Screening unavailable: invalid ticker universe")
    if len(set(tickers)) != len(tickers):
        raise ScreeningUnavailableError("Screening unavailable: duplicate tickers")
    if status == "skipped_friday":
        try:
            friday = date.fromisoformat(data["report_date"]).weekday() == 4
        except (KeyError, TypeError, ValueError):
            friday = False
        if source != "earnings_afterhours_screener" or not friday or tickers:
            raise ScreeningUnavailableError("Screening unavailable: invalid Friday skip")
    elif status == "empty":
        if tickers:
            raise ScreeningUnavailableError("Screening unavailable: empty screen has tickers")
    elif status == "ok":
        if not tickers:
            raise ScreeningUnavailableError("Screening unavailable: ok screen has no tickers")
    else:
        raise ScreeningUnavailableError("Screening unavailable: unknown status")

    if source == "earnings_trading_screener":
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or any(not isinstance(c, dict) for c in candidates):
            raise ScreeningUnavailableError("Screening unavailable: invalid candidates")
        candidate_tickers = [c.get("ticker") for c in candidates]
        if any(not isinstance(t, str) for t in candidate_tickers):
            raise ScreeningUnavailableError("Screening unavailable: invalid candidate tickers")
        if len(set(candidate_tickers)) != len(candidate_tickers) or set(candidate_tickers) != set(
            tickers
        ):
            raise ScreeningUnavailableError(
                "Screening unavailable: candidates differ from screener universe"
            )


def validate_html_report_screening(html_path: Path, report_date: str, source: str) -> str | None:
    """Honor an HTML report's sibling availability artifact, preserving legacy HTML.

    Historical HTML has no status artifact (or an earnings JSON without screening
    metadata). If a status artifact is present, reject malformed, wrong-date or
    unavailable metadata rather than treating its HTML as a successful empty day.
    """
    if source == "earnings_trading_screener":
        artifact = html_path.with_name(f"earnings_trade_candidates_{report_date}.json")
    elif source == "earnings_afterhours_screener":
        artifact = html_path.with_name(f"{report_date}-after-market-screening.json")
    else:
        raise ScreeningUnavailableError(f"Screening unavailable: unknown source {source}")
    try:
        content = artifact.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None  # Historical reports predate screening artifacts.
    except (OSError, UnicodeError) as exc:
        raise ScreeningUnavailableError(f"Screening unavailable: cannot read {artifact}") from exc
    try:
        data = json.loads(content)
    except ValueError as exc:
        raise ScreeningUnavailableError(
            f"Screening unavailable: invalid JSON in {artifact}"
        ) from exc
    if not isinstance(data, dict):
        raise ScreeningUnavailableError(f"Screening unavailable: invalid object in {artifact}")
    if source == "earnings_trading_screener" and "screening" not in data:
        return None  # Legacy candidate JSON also has no screening state.
    if data.get("report_date") != report_date:
        raise ScreeningUnavailableError(
            f"Screening unavailable: mismatched report_date in {artifact}"
        )
    validate_screening(data, source)
    return str(data["screening"]["status"])
