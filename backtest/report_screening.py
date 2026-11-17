"""Screen availability contract shared by generation gates and candidate readers."""

from datetime import date
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
