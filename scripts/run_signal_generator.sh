#!/bin/bash
# Signal Generator - launchd Script
# Schedule: Weekdays 06:15 PT via launchd
# Generates trade signals JSON from earnings HTML reports.
#
# Env overrides (defaults preserve production behavior):
#   TODAY=YYYY-MM-DD      Override "today" date (also drives log filename)
#   TRADE_DATE=YYYY-MM-DD Pass --trade-date to Python (test only)
#   STATE_DB=path         Override state DB path
#   OUTPUT_DIR=path       Override signal JSON output dir
#   LOG_DIR=path          Override log directory
#   DRY_RUN=1             Pass --dry-run to Python
#   NO_ALPACA=1           Pass --no-alpaca to Python (test only — never set in launchd)
#   SEND_ALERT=0          Suppress JSON-missing alert email

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"

# Env-injectable settings (must come before LOG_FILE so date override propagates)
TODAY="${TODAY:-$(date +%Y-%m-%d)}"
STATE_DB="${STATE_DB:-live/state.db}"
OUTPUT_DIR="${OUTPUT_DIR:-live/signals}"
LOG_DIR="${LOG_DIR:-${PROJECT_DIR}/logs}"
DRY_RUN="${DRY_RUN:-0}"
NO_ALPACA="${NO_ALPACA:-0}"
SEND_ALERT="${SEND_ALERT:-1}"
TRADE_DATE="${TRADE_DATE:-}"

LOG_FILE="${LOG_DIR}/signal_generator_${TODAY}.log"

mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}" || exit 1

echo "=======================================" >> "${LOG_FILE}"
echo "Signal Generator - Started: $(date)" >> "${LOG_FILE}"
echo "=======================================" >> "${LOG_FILE}"

# Resolve report path. JSON is the single source of truth, but Python's
# generate_signals() takes an HTML path and derives the JSON path from the
# filename's date (it never reads the HTML contents). So:
#   - If today's HTML exists, use it (logged for transparency).
#   - If it doesn't, fall back to a synthetic path with the canonical name.
# In either case Python is started so that exits-flow signal JSON gets
# written even on a no-report / no-JSON day. Aborting here would strand
# open positions.
#
# Loop avoids ls|head exit-1 under set -euo pipefail when the glob misses.
REPORT_DIR="${PROJECT_DIR}/reports"
REPORT_FILE=""
for f in "${REPORT_DIR}"/earnings_trade_analysis_"${TODAY}"*.html; do
    if [ -f "$f" ]; then
        REPORT_FILE="$f"
        break
    fi
done

if [ -z "${REPORT_FILE}" ]; then
    REPORT_FILE="${REPORT_DIR}/earnings_trade_analysis_${TODAY}.html"
    echo "WARNING: No HTML earnings report found for ${TODAY}" >> "${LOG_FILE}"
    echo "Using synthetic path so JSON-only flow + exits-flow can proceed: ${REPORT_FILE}" >> "${LOG_FILE}"
fi

# JSON precheck. Use exact filename to avoid matching .OLD.json — Python reads
# the same exact path via _derive_json_path(). On miss, send alert and continue:
# generate_signals() must still run because it produces exit-flow signal JSON
# even when entries are blocked. Aborting here would strand open positions.
JSON_FILE="${REPORT_DIR}/earnings_trade_candidates_${TODAY}.json"
if [ ! -f "${JSON_FILE}" ]; then
    echo "CRITICAL: JSON candidates file missing: ${JSON_FILE}" >> "${LOG_FILE}"
    echo "Entries will be blocked but exits will still run." >> "${LOG_FILE}"
    if [ "${SEND_ALERT}" = "1" ]; then
        "${PROJECT_DIR}/.venv/bin/python" "${PROJECT_DIR}/scripts/send_report.py" \
            --alert-text "JSON candidates file missing for ${TODAY}. Entries blocked, exits will still run. Expected: ${JSON_FILE}" \
            --subject "[CRITICAL] signal_generator: JSON candidates missing ${TODAY}" \
            2>>"${LOG_FILE}" || echo "alert send failed (continuing)" >> "${LOG_FILE}"
    else
        echo "(alert suppressed by SEND_ALERT=0)" >> "${LOG_FILE}"
    fi
fi

echo "Using report: ${REPORT_FILE}" >> "${LOG_FILE}"

MANIFEST="${MANIFEST:-reports/backtest/run_manifest.json}"

# Build optional flags from env
EXTRA_FLAGS=""
[ "${DRY_RUN}" = "1" ] && EXTRA_FLAGS="${EXTRA_FLAGS} --dry-run"
[ "${NO_ALPACA}" = "1" ] && EXTRA_FLAGS="${EXTRA_FLAGS} --no-alpaca"
[ -n "${TRADE_DATE}" ] && EXTRA_FLAGS="${EXTRA_FLAGS} --trade-date ${TRADE_DATE}"

# Run signal generator. Disable -e temporarily so we capture the exit code
# and always finish writing the trailer log lines.
set +e
.venv/bin/python -m live.signal_generator \
    --report-file "${REPORT_FILE}" \
    --state-db "${STATE_DB}" \
    --output-dir "${OUTPUT_DIR}" \
    --manifest "${MANIFEST}" \
    ${EXTRA_FLAGS} \
    -v \
    >> "${LOG_FILE}" 2>&1

EXIT_STATUS=$?
set -e

echo "" >> "${LOG_FILE}"
echo "Completed: $(date)" >> "${LOG_FILE}"
echo "Exit Status: ${EXIT_STATUS}" >> "${LOG_FILE}"
echo "=======================================" >> "${LOG_FILE}"

exit ${EXIT_STATUS}
