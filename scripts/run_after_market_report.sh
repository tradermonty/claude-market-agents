#!/bin/bash
# After Market Report - Cron Script
# Schedule: Daily at 1:10 PM US Pacific Time (after market close at 1:00 PM PT)
# Cron entry: 10 13 * * 1-5 /path/to/run_after_market_report.sh
# Flags:  --force  Skip success marker check and re-run

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Load shared retry/lock library
source "${SCRIPT_DIR}/lib_retry.sh"

# Set PATH for cron environment (node, npm, homebrew, etc.)
# Adjust these paths based on your system configuration
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:${HOME}/.npm-global/bin:${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"

# Configuration
LOG_DIR="${PROJECT_DIR}/logs"
TODAY=$(date +%Y-%m-%d)
LOG_FILE="${LOG_DIR}/after_market_${TODAY}.log"
LOCK_DIR="${LOG_DIR}/.after_market.lock"
SUCCESS_MARKER="${LOG_DIR}/.after_market_${TODAY}.success"

# Parse flags
FORCE=false
[[ "${1:-}" == "--force" ]] && FORCE=true

# Create log directory if it doesn't exist
mkdir -p "${LOG_DIR}"

# Set working directory
cd "${PROJECT_DIR}" || exit 1

# --- Idempotency: skip if already completed today ---
if [ "$FORCE" = false ] && [ -f "$SUCCESS_MARKER" ]; then
    echo "[SKIP] Already completed for ${TODAY} (marker: ${SUCCESS_MARKER})" >> "${LOG_FILE}"
    exit 0
fi

# --- Exclusive lock: prevent overlapping runs ---
if ! acquire_lock "$LOCK_DIR"; then
    echo "[SKIP] Another instance is running (lock: ${LOCK_DIR})" >> "${LOG_FILE}"
    exit 0
fi
trap 'release_lock "$LOCK_DIR"' EXIT

# --- Cleanup old markers/locks (> 7 days) ---
cleanup_old_artifacts "${LOG_DIR}" 7

# Log start time
echo "=======================================" >> "${LOG_FILE}"
echo "After Market Report - Started: $(date)" >> "${LOG_FILE}"
echo "=======================================" >> "${LOG_FILE}"

# Run Claude Code with timeout and retry
# timeout: 600s (10 min), retries: 2 (3 total attempts), backoff: 30s
EXPECTED_HTML="${PROJECT_DIR}/reports/${TODAY}-after-market-report.html"
EXPECTED_XPOST="${PROJECT_DIR}/reports/${TODAY}-after-market-x-post.md"

run_claude_with_retry \
    --timeout 600 --retries 2 --backoff 30 \
    --log-file "${LOG_FILE}" \
    --require-output-file "${EXPECTED_HTML}" \
    --require-output-file "${EXPECTED_XPOST}" \
    -- \
    claude -p "Generate today's after-market report using the after-market-reporter agent. Follow the instructions in prompts/after-market-report.md and generate the HTML report and X post message in the reports folder. The HTML must be saved to ${EXPECTED_HTML} and the X post must be saved to ${EXPECTED_XPOST} (these exact paths)." \
        --allowedTools "Bash Read Write Edit Glob Grep Skill Agent WebSearch WebFetch TodoWrite mcp__finviz__* mcp__fmp-server__* mcp__alpaca__*"

# Capture exit status
EXIT_STATUS=$?

# Publish to GitHub Pages if report generation succeeded
if [ ${EXIT_STATUS} -eq 0 ]; then
    echo "" >> "${LOG_FILE}"
    echo "Publishing reports to GitHub Pages..." >> "${LOG_FILE}"
    "${SCRIPT_DIR}/run_publish_reports.sh" >> "${LOG_FILE}" 2>&1
    PUBLISH_STATUS=$?
    echo "Publish Exit Status: ${PUBLISH_STATUS}" >> "${LOG_FILE}"

    # Create success marker only after BOTH generation and publish succeed
    if [ ${PUBLISH_STATUS} -eq 0 ]; then
        touch "${SUCCESS_MARKER}"
        echo "Success marker created: ${SUCCESS_MARKER}" >> "${LOG_FILE}"
    else
        EXIT_STATUS=${PUBLISH_STATUS}
        echo "Publish failed (exit ${PUBLISH_STATUS}) - no success marker created (re-run possible)" >> "${LOG_FILE}"
    fi
fi

# Log completion
echo "" >> "${LOG_FILE}"
echo "Completed: $(date)" >> "${LOG_FILE}"
echo "Exit Status: ${EXIT_STATUS}" >> "${LOG_FILE}"
echo "=======================================" >> "${LOG_FILE}"

exit ${EXIT_STATUS}
