#!/bin/bash
# Executor Poll Phase - launchd Script
# Schedule: Weekdays 06:32, 06:40, 06:50 PT via launchd (3 jobs)
# Polls OPG order fills and places GTC stop orders. Idempotent.
# No signals file needed — works from state DB.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"

LOG_DIR="${PROJECT_DIR}/logs"
LOG_FILE="${LOG_DIR}/executor_poll_$(date +%Y-%m-%d_%H%M).log"

mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}" || exit 1

echo "=======================================" >> "${LOG_FILE}"
echo "Executor Poll - Started: $(date)" >> "${LOG_FILE}"
echo "=======================================" >> "${LOG_FILE}"

# Run executor in poll phase
# --trade-date is omitted; Python resolves via datetime.now(ET)
# Idempotent: safe to run multiple times
.venv/bin/python -m live.executor \
    --state-db live/state.db \
    --phase poll \
    -v \
    >> "${LOG_FILE}" 2>&1

EXIT_STATUS=$?

echo "" >> "${LOG_FILE}"
echo "Completed: $(date)" >> "${LOG_FILE}"
echo "Exit Status: ${EXIT_STATUS}" >> "${LOG_FILE}"
echo "=======================================" >> "${LOG_FILE}"

# Check for critical issues in log
if grep -qE 'CRITICAL|UNPROTECTED' "${LOG_FILE}"; then
    echo "WARNING: Critical issues detected. Check log: ${LOG_FILE}" >&2
fi

exit ${EXIT_STATUS}
