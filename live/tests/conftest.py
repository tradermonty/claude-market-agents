"""Live-tests autouse fixtures.

The kill-switch alert path in live.executor shells out via subprocess to
scripts/send_report.py and would dispatch a real Gmail SMTP message during
unit tests. The 2026-04-30 incident sent a 'KILL SWITCH ACTIVATED (AAPL)'
email from a local pytest run because subprocess calls bypass standard
mocks. This autouse fixture intercepts the alert function in every live
test, regardless of whether the individual test remembers to patch it.
"""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _block_kill_switch_email():
    with patch("live.executor._send_kill_switch_alert") as mocked:
        yield mocked
