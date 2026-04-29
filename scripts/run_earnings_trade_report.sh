#!/bin/bash
# Earnings Trade Report - launchd / cron Script
# Schedule: weekday 06:00 PT (configured in plist)
# Flags:  --force   Skip success marker check and re-run
#
# IMPORTANT (2026-04-28): Heavy script setup (PATH export, brace-block log
# headers, mkdir, find cleanup, lock file, etc.) was found to break Claude
# CLI 2.1.117+ when claude is later spawned via subshell under launchd —
# claude returns "Execution error" + exit 0 within ~5-13s, or rejects valid
# CLI options (e.g. "unknown option '--debug-file'"). The minimal-version
# bypass test (run_earnings_trade_minimal.sh) confirmed claude works fine
# when the script is short. So this script is intentionally minimal.

PROJECT_DIR=/Users/takueisaotome/PycharmProjects/claude-market-agents
SCRIPT_DIR="${PROJECT_DIR}/scripts"
LOG_DIR="${PROJECT_DIR}/logs"
TODAY=$(date +%Y-%m-%d)
LOG_FILE="${LOG_DIR}/earnings_trade_${TODAY}.log"
SUCCESS_MARKER="${LOG_DIR}/.earnings_trade_${TODAY}.success"

EXPECTED_HTML="${PROJECT_DIR}/reports/earnings_trade_analysis_${TODAY}.html"
EXPECTED_JSON="${PROJECT_DIR}/reports/earnings_trade_candidates_${TODAY}.json"
EXPECTED_XPOST="${PROJECT_DIR}/reports/earnings_trade_X_message_${TODAY}.md"

# Idempotency: skip if already completed today.
if [ "${1:-}" != "--force" ] && [ -f "$SUCCESS_MARKER" ]; then
    exit 0
fi

# Source helpers (_kill_descendants, _file_mtime, _log_has_false_success).
source "${SCRIPT_DIR}/lib_retry.sh"

cd "$PROJECT_DIR" || exit 1

TIMEOUT_SECS=900
MAX_ATTEMPTS=3
BACKOFF_SECS=30
LAST_EXIT_CODE=1

for ATTEMPT in 1 2 3; do
    echo "[Attempt ${ATTEMPT}/${MAX_ATTEMPTS}] Starting at $(date)" >> "$LOG_FILE"

    LOG_OFFSET_BEFORE=0
    if [ -f "$LOG_FILE" ]; then
        LOG_OFFSET_BEFORE=$(wc -c < "$LOG_FILE" | tr -d ' ')
    fi
    ATTEMPT_START_TIME=$(date +%s)
    DEBUG_LOG="${LOG_DIR}/claude_debug_${TODAY}_attempt${ATTEMPT}.log"

    ( claude -p "Run the earnings trade analysis using the earnings-trade-analyst agent. Follow the instructions in prompts/earnings-trade.md. IMPORTANT: Save all output files directly in the reports/ folder (NOT in date subfolders). Required output paths (write to these exact paths): ${EXPECTED_HTML}, ${EXPECTED_JSON}, ${EXPECTED_XPOST}." \
        --allowedTools "Bash Read Write Edit Glob Grep Skill Agent WebSearch WebFetch TodoWrite mcp__finviz__* mcp__fmp-server__* mcp__alpaca__*" \
        --debug \
        --debug-file "$DEBUG_LOG" \
        >> "$LOG_FILE" 2>&1 ) &
    CMD_PID=$!

    ( sleep "$TIMEOUT_SECS" 2>/dev/null; touch "${LOG_DIR}/.timeout_flag.$$"; _kill_descendants "$CMD_PID" TERM; sleep 5 2>/dev/null; _kill_descendants "$CMD_PID" KILL ) &
    WATCHDOG_PID=$!

    wait "$CMD_PID" 2>/dev/null
    EC=$?
    kill "$WATCHDOG_PID" 2>/dev/null
    wait "$WATCHDOG_PID" 2>/dev/null

    if [ -f "${LOG_DIR}/.timeout_flag.$$" ]; then
        rm -f "${LOG_DIR}/.timeout_flag.$$"
        EC=124
    fi

    if [ "$EC" -eq 0 ] && _log_has_false_success "$LOG_FILE" "$LOG_OFFSET_BEFORE"; then
        echo "[Attempt ${ATTEMPT}/${MAX_ATTEMPTS}] DETECTED 'Execution error' in output despite exit 0" >> "$LOG_FILE"
        EC=125
    fi

    if [ "$EC" -eq 0 ]; then
        for REQ in "$EXPECTED_HTML" "$EXPECTED_JSON" "$EXPECTED_XPOST"; do
            if [ ! -f "$REQ" ]; then
                echo "[Attempt ${ATTEMPT}/${MAX_ATTEMPTS}] Required output file not found: $REQ" >> "$LOG_FILE"
                EC=126
                break
            fi
            MT=$(_file_mtime "$REQ")
            if [ "$MT" -lt "$ATTEMPT_START_TIME" ]; then
                echo "[Attempt ${ATTEMPT}/${MAX_ATTEMPTS}] Required output file stale: $REQ" >> "$LOG_FILE"
                EC=126
                break
            fi
        done
    fi

    LAST_EXIT_CODE=$EC

    if [ "$EC" -eq 0 ]; then
        echo "[Attempt ${ATTEMPT}/${MAX_ATTEMPTS}] Succeeded at $(date)" >> "$LOG_FILE"
        break
    fi

    case "$EC" in
        124) echo "[Attempt ${ATTEMPT}/${MAX_ATTEMPTS}] TIMEOUT after ${TIMEOUT_SECS}s at $(date)" >> "$LOG_FILE" ;;
        125) echo "[Attempt ${ATTEMPT}/${MAX_ATTEMPTS}] FAILED (false-success) at $(date)" >> "$LOG_FILE" ;;
        126) echo "[Attempt ${ATTEMPT}/${MAX_ATTEMPTS}] FAILED (output-file assertion) at $(date)" >> "$LOG_FILE" ;;
        *)   echo "[Attempt ${ATTEMPT}/${MAX_ATTEMPTS}] FAILED with exit code ${EC} at $(date)" >> "$LOG_FILE" ;;
    esac

    if [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; then
        sleep "$BACKOFF_SECS"
    fi
done

EXIT_STATUS=$LAST_EXIT_CODE

if [ "$EXIT_STATUS" -eq 0 ]; then
    "${SCRIPT_DIR}/run_publish_reports.sh" >> "$LOG_FILE" 2>&1
    PUBLISH_STATUS=$?
    if [ "$PUBLISH_STATUS" -eq 0 ]; then
        touch "$SUCCESS_MARKER"
        # Email the HTML report on success.
        /opt/homebrew/bin/python3.11 "${SCRIPT_DIR}/send_report.py" \
            --report-html "$EXPECTED_HTML" \
            --subject "Market Agents - Earnings Trade Report - ${TODAY}" \
            >> "$LOG_FILE" 2>&1 || true
    else
        EXIT_STATUS=$PUBLISH_STATUS
    fi
fi

# On any failure (claude failed, publish failed), send a plain-text alert.
if [ "$EXIT_STATUS" -ne 0 ]; then
    RECENT_LOG=$(tail -25 "$LOG_FILE" 2>/dev/null || echo "(log unavailable)")
    /opt/homebrew/bin/python3.11 "${SCRIPT_DIR}/send_report.py" \
        --alert-text "Earnings Trade job failed on ${TODAY} (exit=${EXIT_STATUS}). Recent log tail:

${RECENT_LOG}

Full log: ${LOG_FILE}" \
        --subject "Market Agents - Earnings Trade ALERT - ${TODAY}" \
        >> "$LOG_FILE" 2>&1 || true
fi

echo "Completed: $(date), exit=$EXIT_STATUS" >> "$LOG_FILE"

exit "$EXIT_STATUS"
