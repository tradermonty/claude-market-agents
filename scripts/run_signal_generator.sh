#!/bin/bash
# Signal Generator - launchd Script
# Schedule: Weekdays 06:15 PT via launchd
# Generates trade signals JSON from earnings HTML reports.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"

LOG_DIR="${PROJECT_DIR}/logs"
LOG_FILE="${LOG_DIR}/signal_generator_$(date +%Y-%m-%d).log"

mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}" || exit 1

echo "=======================================" >> "${LOG_FILE}"
echo "Signal Generator - Started: $(date)" >> "${LOG_FILE}"
echo "=======================================" >> "${LOG_FILE}"

# Find today's earnings report (required by --report-file)
REPORT_DIR="${PROJECT_DIR}/reports"
TODAY=$(date +%Y-%m-%d)
REPORT_FILE=$(ls -t "${REPORT_DIR}"/earnings_trade_analysis_"${TODAY}"*.html 2>/dev/null | head -1)

if [ -z "${REPORT_FILE}" ]; then
    echo "ERROR: No earnings report found for ${TODAY}" >> "${LOG_FILE}"
    echo "Expected: ${REPORT_DIR}/earnings_trade_analysis_${TODAY}*.html" >> "${LOG_FILE}"
    echo "Completed: $(date)" >> "${LOG_FILE}"
    exit 1
fi

echo "Using report: ${REPORT_FILE}" >> "${LOG_FILE}"

# Run signal generator with --report-file (required argument)
# --trade-date is omitted; Python resolves via datetime.now(ET)
.venv/bin/python -m live.signal_generator \
    --report-file "${REPORT_FILE}" \
    --state-db live/state.db \
    -v \
    >> "${LOG_FILE}" 2>&1

EXIT_STATUS=$?

echo "" >> "${LOG_FILE}"
echo "Completed: $(date)" >> "${LOG_FILE}"
echo "Exit Status: ${EXIT_STATUS}" >> "${LOG_FILE}"
echo "=======================================" >> "${LOG_FILE}"

exit ${EXIT_STATUS}
