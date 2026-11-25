#!/bin/bash
# After-Market ELI5 - mobile explainer derived from today's after-market report.
# Called by run_after_market_report.sh after the main report succeeds and
# before publishing, so both files go out in the same gh-pages push.
# Can also be run by hand:  scripts/run_after_market_eli5.sh [YYYY-MM-DD]
#
# Non-critical by design: the caller ignores this script's exit status, so a
# failed explainer never blocks publishing or emailing the main report.
# Kept in the same minimal style as run_after_market_report.sh (see the
# 2026-04-28 note there about heavy setup breaking claude under launchd).

PROJECT_DIR=/Users/takueisaotome/PycharmProjects/claude-market-agents
SCRIPT_DIR="${PROJECT_DIR}/scripts"
LOG_DIR="${PROJECT_DIR}/logs"
DATE="${1:-$(date +%Y-%m-%d)}"
LOG_FILE="${LOG_DIR}/after_market_eli5_${DATE}.log"

SOURCE_HTML="${PROJECT_DIR}/reports/${DATE}-after-market-report.html"
SOURCE_XPOST="${PROJECT_DIR}/reports/${DATE}-after-market-x-post.md"
EXPECTED_ELI5="${PROJECT_DIR}/reports/${DATE}-after-market-eli5.html"

source "${SCRIPT_DIR}/lib_retry.sh"

cd "$PROJECT_DIR" || exit 1

if [ ! -f "$SOURCE_HTML" ]; then
    echo "[ELI5] Source report missing: ${SOURCE_HTML} - skipping" >> "$LOG_FILE"
    exit 2
fi

# Rewriting an existing report is much lighter than the main job (no MCP
# calls), so a shorter watchdog keeps the publish step from being delayed long.
# Remove any earlier explainer for this date (e.g. a --force re-run) so a
# failed attempt can never publish numbers from a previous run.
rm -f "$EXPECTED_ELI5"

TIMEOUT_SECS=480
MAX_ATTEMPTS=2
BACKOFF_SECS=20
LAST_EXIT_CODE=1

for ATTEMPT in 1 2; do
    echo "[ELI5 ${ATTEMPT}/${MAX_ATTEMPTS}] Starting at $(date)" >> "$LOG_FILE"
    LOG_OFFSET_BEFORE=$(wc -c < "$LOG_FILE" | tr -d ' ')
    ATTEMPT_START_TIME=$(date +%s)

    # --tools limits the built-in tools that exist at all (--allowedTools only
    # pre-approves, and settings.local.json already allows Bash/WebSearch), and
    # --strict-mcp-config with no --mcp-config starts no MCP servers. Together
    # they make "no new data" a hard limit instead of a prompt request.
    ( claude -p "Create today's mobile ELI5 explainer. Follow the instructions in prompts/after-market-eli5.md with {DATE} = ${DATE}. Read ${SOURCE_HTML} and ${SOURCE_XPOST}, then write the explainer to ${EXPECTED_ELI5} (this exact path). Use the eli5 skill's approach. Work directly in this session: no sub-agents, no background tasks, no waiting." \
        --tools "Read,Write,Edit,Glob,Skill" \
        --allowedTools "Read Write Edit Glob Skill" \
        --strict-mcp-config \
        >> "$LOG_FILE" 2>&1 ) &
    CMD_PID=$!

    ( sleep "$TIMEOUT_SECS" 2>/dev/null; touch "${LOG_DIR}/.timeout_flag.eli5.$$"; _kill_descendants "$CMD_PID" TERM; sleep 5 2>/dev/null; _kill_descendants "$CMD_PID" KILL ) &
    WATCHDOG_PID=$!

    wait "$CMD_PID" 2>/dev/null
    EC=$?
    kill "$WATCHDOG_PID" 2>/dev/null
    wait "$WATCHDOG_PID" 2>/dev/null

    if [ -f "${LOG_DIR}/.timeout_flag.eli5.$$" ]; then
        rm -f "${LOG_DIR}/.timeout_flag.eli5.$$"
        EC=124
    fi

    if [ "$EC" -eq 0 ] && _log_has_false_success "$LOG_FILE" "$LOG_OFFSET_BEFORE"; then
        EC=125
    fi

    # Same rule as the main job: a timeout after a fresh file was written counts.
    if [ "$EC" -eq 0 ] || [ "$EC" -eq 124 ]; then
        if [ -f "$EXPECTED_ELI5" ] && [ "$(_file_mtime "$EXPECTED_ELI5")" -ge "$ATTEMPT_START_TIME" ]; then
            EC=0
        elif [ "$EC" -eq 0 ]; then
            EC=126
        fi
    fi

    # Publish gate: DOCTYPE, disclaimer, and every %/$ figure traceable to the
    # source report. A failing file is deleted so it is never published.
    if [ "$EC" -eq 0 ]; then
        if ! /opt/homebrew/bin/python3.11 "${SCRIPT_DIR}/validate_eli5.py" \
                "$EXPECTED_ELI5" "$SOURCE_HTML" "$SOURCE_XPOST" >> "$LOG_FILE" 2>&1; then
            echo "[ELI5 ${ATTEMPT}/${MAX_ATTEMPTS}] Validation failed - removing output" >> "$LOG_FILE"
            rm -f "$EXPECTED_ELI5"
            EC=127
        fi
    fi

    LAST_EXIT_CODE=$EC
    if [ "$EC" -eq 0 ]; then
        echo "[ELI5 ${ATTEMPT}/${MAX_ATTEMPTS}] Succeeded at $(date)" >> "$LOG_FILE"
        break
    fi
    echo "[ELI5 ${ATTEMPT}/${MAX_ATTEMPTS}] FAILED exit=${EC} at $(date)" >> "$LOG_FILE"

    if [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; then
        sleep "$BACKOFF_SECS"
    fi
done

exit "$LAST_EXIT_CODE"
