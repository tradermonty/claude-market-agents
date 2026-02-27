#!/bin/bash
# lib_retry.sh - Shared timeout/retry library for launchd report jobs
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/lib_retry.sh"
#
#   run_claude_with_retry --timeout 900 --retries 2 --backoff 30 \
#       --log-file "$LOG_FILE" -- claude -p "..." --dangerously-skip-permissions

# ---------------------------------------------------------------------------
# _run_with_timeout <seconds> <log_file> <command...>
#
# Runs <command> in a new process group (via Python os.setsid) and kills the
# entire group if it exceeds <seconds>.
#
# Returns:
#   0   - command succeeded
#   124 - timeout (mirrors GNU coreutils timeout)
#   *   - command's own exit code
# ---------------------------------------------------------------------------
_run_with_timeout() {
    local timeout_secs="$1"
    local log_file="$2"
    shift 2

    # Timeout flag file (not just exit code) to distinguish timeout from failure
    local timeout_flag
    timeout_flag=$(mktemp "${TMPDIR:-/tmp}/timeout_flag.XXXXXX")
    rm -f "$timeout_flag"

    # Start command in a new session/process group.
    # macOS lacks the setsid command; use Python os.setsid() + os.execvp().
    # After setsid, the child's PGID == PID == $!, so kill -- -$PID kills all
    # descendants.
    python3 -c "
import os, sys
os.setsid()
os.execvp(sys.argv[1], sys.argv[1:])
" "$@" >> "$log_file" 2>&1 &
    local cmd_pid=$!

    # Watchdog: SIGTERM -> 5 s grace -> SIGKILL, targeting the process group
    (
        sleep "$timeout_secs" 2>/dev/null
        # Signal that timeout fired (flag file, not just exit code)
        touch "$timeout_flag"
        kill -TERM -- -"$cmd_pid" 2>/dev/null
        sleep 5 2>/dev/null
        kill -KILL -- -"$cmd_pid" 2>/dev/null
    ) &
    local watchdog_pid=$!

    # Wait for the main command to finish (success, failure, or killed)
    wait "$cmd_pid"
    local exit_code=$?

    # Clean up the watchdog
    kill "$watchdog_pid" 2>/dev/null
    wait "$watchdog_pid" 2>/dev/null

    # Determine if timeout was the cause
    if [ -f "$timeout_flag" ]; then
        rm -f "$timeout_flag"
        return 124
    fi

    rm -f "$timeout_flag"
    return "$exit_code"
}

# ---------------------------------------------------------------------------
# run_claude_with_retry [options] -- <command...>
#
# Options:
#   --timeout <secs>   Per-attempt timeout (default: 900 = 15 min)
#   --retries <n>      Extra attempts after first failure (default: 2, so 3 total)
#   --backoff <secs>   Sleep between retries (default: 30)
#   --log-file <path>  Log file for status messages
#
# Does NOT create a success marker; the caller is responsible for that.
#
# Returns:
#   0   - command succeeded
#   124 - all attempts timed out (last failure was timeout)
#   *   - last attempt's exit code (propagated for caller diagnostics)
# ---------------------------------------------------------------------------
run_claude_with_retry() {
    local timeout=900
    local retries=2
    local backoff=30
    local log_file="/dev/null"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --timeout)  timeout="$2";   shift 2 ;;
            --retries)  retries="$2";   shift 2 ;;
            --backoff)  backoff="$2";   shift 2 ;;
            --log-file) log_file="$2";  shift 2 ;;
            --)         shift; break ;;
            *)          break ;;
        esac
    done

    local max_attempts=$((retries + 1))
    local last_exit_code=1

    for ((attempt = 1; attempt <= max_attempts; attempt++)); do
        echo "" >> "$log_file"
        echo "[Attempt ${attempt}/${max_attempts}] Starting at $(date)" >> "$log_file"

        _run_with_timeout "$timeout" "$log_file" "$@"
        last_exit_code=$?

        if [ "$last_exit_code" -eq 0 ]; then
            echo "[Attempt ${attempt}/${max_attempts}] Succeeded at $(date)" >> "$log_file"
            return 0
        fi

        if [ "$last_exit_code" -eq 124 ]; then
            echo "[Attempt ${attempt}/${max_attempts}] TIMEOUT after ${timeout}s at $(date)" >> "$log_file"
        else
            echo "[Attempt ${attempt}/${max_attempts}] FAILED with exit code ${last_exit_code} at $(date)" >> "$log_file"
        fi

        if [ "$attempt" -lt "$max_attempts" ]; then
            echo "[Retry] Waiting ${backoff}s before next attempt..." >> "$log_file"
            sleep "$backoff"
        fi
    done

    echo "[FAILED] All ${max_attempts} attempts exhausted at $(date)" >> "$log_file"
    return "$last_exit_code"
}

# ---------------------------------------------------------------------------
# acquire_lock <lock_dir>
#
# mkdir-based exclusive lock. Detects and reclaims stale locks from dead PIDs.
# Returns 0 on success, 1 if another live instance holds the lock.
# ---------------------------------------------------------------------------
acquire_lock() {
    local lock_dir="$1"

    if mkdir "$lock_dir" 2>/dev/null; then
        echo $$ > "${lock_dir}/pid"
        return 0
    fi

    # Lock exists - check if the holder is still alive
    local holder_pid
    holder_pid=$(cat "${lock_dir}/pid" 2>/dev/null)
    if [ -z "$holder_pid" ] || ! kill -0 "$holder_pid" 2>/dev/null; then
        # pid file missing/empty/corrupt, or holder is dead -> reclaim
        rm -rf "$lock_dir"
        if mkdir "$lock_dir" 2>/dev/null; then
            echo $$ > "${lock_dir}/pid"
            return 0
        fi
    fi

    return 1
}

# ---------------------------------------------------------------------------
# release_lock <lock_dir>
# ---------------------------------------------------------------------------
release_lock() {
    local lock_dir="$1"
    rm -rf "$lock_dir"
}

# ---------------------------------------------------------------------------
# cleanup_old_artifacts <dir> <days>
#
# Removes success markers and stale lock dirs older than <days>.
# ---------------------------------------------------------------------------
cleanup_old_artifacts() {
    local dir="$1"
    local days="$2"
    find "$dir" -maxdepth 1 -name ".*.success" -mtime +"$days" -delete 2>/dev/null
    find "$dir" -maxdepth 1 -name ".*.lock" -type d -mtime +"$days" -exec rm -rf {} + 2>/dev/null
}
