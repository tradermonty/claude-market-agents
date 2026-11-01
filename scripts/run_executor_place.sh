#!/bin/bash
# Executor Place Phase - launchd Script
# Schedule: Weekdays 06:30 PT via launchd
# Places exit sells + DAY bracket buy orders, polls for fills.
# To revert to OPG: change --phase all → --phase place

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"

TODAY="${TODAY:-$(date +%Y-%m-%d)}"
LOG_DIR="${LOG_DIR:-${PROJECT_DIR}/logs}"
LOG_FILE="${LOG_DIR}/executor_place_${TODAY}.log"
MANIFEST="${MANIFEST:-reports/backtest/run_manifest.json}"

mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}" || exit 1

echo "=======================================" >> "${LOG_FILE}"
echo "Executor Place - Started: $(date)" >> "${LOG_FILE}"
echo "=======================================" >> "${LOG_FILE}"

# Resolve the manifest-aligned primary strategy, then find today's execution signals file.
SIGNALS_DIR="${PROJECT_DIR}/live/signals"
if ! PRIMARY_STRATEGY=$(
    .venv/bin/python -c '
import sys
from live.config import LiveConfig

print(LiveConfig.from_manifest(sys.argv[1]).primary_strategy_id)
' "${MANIFEST}" 2>> "${LOG_FILE}"
); then
    echo "ERROR: Could not resolve primary strategy from manifest: ${MANIFEST}" >> "${LOG_FILE}"
    echo "Completed: $(date)" >> "${LOG_FILE}"
    exit 1
fi

SIGNALS_FILE=""
for f in "${SIGNALS_DIR}"/trade_signals_"${TODAY}"_"${PRIMARY_STRATEGY}".json; do
    if [ -f "$f" ]; then
        SIGNALS_FILE="$f"
        break
    fi
done

if [ -z "${SIGNALS_FILE}" ]; then
    echo "ERROR: No execution signals file found for ${TODAY}" >> "${LOG_FILE}"
    echo "Expected: ${SIGNALS_DIR}/trade_signals_${TODAY}_${PRIMARY_STRATEGY}.json" >> "${LOG_FILE}"
    echo "Completed: $(date)" >> "${LOG_FILE}"
    exit 1
fi

echo "Using signals file: ${SIGNALS_FILE}" >> "${LOG_FILE}"
echo "Using manifest: ${MANIFEST}" >> "${LOG_FILE}"

# Run executor in place phase
# --trade-date is omitted; Python resolves via datetime.now(ET)
.venv/bin/python -m live.executor \
    --signals-file "${SIGNALS_FILE}" \
    --state-db live/state.db \
    --manifest "${MANIFEST}" \
    --phase all \
    -v \
    >> "${LOG_FILE}" 2>&1

EXIT_STATUS=$?

echo "" >> "${LOG_FILE}"
echo "Completed: $(date)" >> "${LOG_FILE}"
echo "Exit Status: ${EXIT_STATUS}" >> "${LOG_FILE}"
echo "=======================================" >> "${LOG_FILE}"

exit ${EXIT_STATUS}
