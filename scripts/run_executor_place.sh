#!/bin/bash
# Executor Place Phase - launchd Script
# Schedule: Weekdays 06:20 PT via launchd
# Places exit sells + OPG buy orders. Does NOT poll for fills.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"

LOG_DIR="${PROJECT_DIR}/logs"
LOG_FILE="${LOG_DIR}/executor_place_$(date +%Y-%m-%d).log"

mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}" || exit 1

echo "=======================================" >> "${LOG_FILE}"
echo "Executor Place - Started: $(date)" >> "${LOG_FILE}"
echo "=======================================" >> "${LOG_FILE}"

# Find today's signals file (ema_p10 strategy, today's date only)
SIGNALS_DIR="${PROJECT_DIR}/live/signals"
TODAY=$(date +%Y-%m-%d)
SIGNALS_FILE=$(ls -t "${SIGNALS_DIR}"/trade_signals_"${TODAY}"_ema_p10.json 2>/dev/null | head -1)

if [ -z "${SIGNALS_FILE}" ]; then
    echo "ERROR: No ema_p10 signals file found for ${TODAY}" >> "${LOG_FILE}"
    echo "Expected: ${SIGNALS_DIR}/trade_signals_${TODAY}_ema_p10.json" >> "${LOG_FILE}"
    echo "Completed: $(date)" >> "${LOG_FILE}"
    exit 1
fi

echo "Using signals file: ${SIGNALS_FILE}" >> "${LOG_FILE}"

# Run executor in place phase
# --trade-date is omitted; Python resolves via datetime.now(ET)
.venv/bin/python -m live.executor \
    --signals-file "${SIGNALS_FILE}" \
    --state-db live/state.db \
    --phase place \
    -v \
    >> "${LOG_FILE}" 2>&1

EXIT_STATUS=$?

echo "" >> "${LOG_FILE}"
echo "Completed: $(date)" >> "${LOG_FILE}"
echo "Exit Status: ${EXIT_STATUS}" >> "${LOG_FILE}"
echo "=======================================" >> "${LOG_FILE}"

exit ${EXIT_STATUS}
