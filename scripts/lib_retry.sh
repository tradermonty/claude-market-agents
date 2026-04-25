#!/bin/bash
# lib_retry.sh - Shared timeout/retry library for launchd report jobs
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/lib_retry.sh"
#
#   run_claude_with_retry --timeout 900 --retries 2 --backoff 30 \
#       --log-file "$LOG_FILE" \
#       --require-output-file "reports/today.html" \
#       --require-output-file "reports/today.json" \
#       -- claude -p "..." --allowedTools "Bash Read Write ..."
#
# History note: an earlier version started the wrapped command via Python
# os.setsid + os.execvp to put it in a new process group. That layer was
# implicated in a Claude CLI 2.1.117+ regression where sub-agent invocations
# crashed under setsid with "Execution error" + exit 0. We now run the
# command directly in the background and clean up descendants explicitly.

# ---------------------------------------------------------------------------
# _kill_descendants <pid> [signal]
#
# Recursively send <signal> (default TERM) to <pid> and all of its
# descendants, deepest-first so that grandchildren receive the signal before
# their parents disappear. Used by the watchdog to make sure MCP / sub-agent
# helper processes don't get orphaned when we time out the wrapped command.
# ---------------------------------------------------------------------------
_kill_descendants() {
    local pid="$1"
    local sig="${2:-TERM}"
    local child
    for child in $(pgrep -P "$pid" 2>/dev/null); do
        _kill_descendants "$child" "$sig"
    done
    kill -"$sig" "$pid" 2>/dev/null
}

# ---------------------------------------------------------------------------
# _run_with_timeout <seconds> <log_file> <command...>
#
# Runs <command> directly in the background (no setsid wrapper) and kills it
# plus all of its descendants if it exceeds <seconds>.
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

    # Start command directly in the background (no setsid/execvp wrapper).
    # The launchd-spawned parent already has its own session, so an extra
    # setsid layer was both unnecessary and suspected of interacting badly
    # with Claude CLI 2.1.117+ when invoking sub-agents. Running the command
    # directly matches the pattern used by weekly-trade-strategy, which has
    # been running reliably on the same CLI build.
    "$@" >> "$log_file" 2>&1 &
    local cmd_pid=$!

    # Watchdog: SIGTERM (with descendant cleanup) -> 5 s grace -> SIGKILL.
    # Without a process group, we walk the pid tree explicitly so MCP /
    # sub-agent helper processes don't get orphaned on timeout.
    (
        sleep "$timeout_secs" 2>/dev/null
        touch "$timeout_flag"
        _kill_descendants "$cmd_pid" TERM
        sleep 5 2>/dev/null
        _kill_descendants "$cmd_pid" KILL
    ) &
    local watchdog_pid=$!

    # Wait for the main command to finish (success, failure, or killed)
    wait "$cmd_pid" 2>/dev/null
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
# _file_mtime <path>
#
# Echo the mtime (epoch seconds) of <path>, or 0 if it doesn't exist.
# Works on both BSD (macOS) stat and GNU (Linux) stat.
# ---------------------------------------------------------------------------
_file_mtime() {
    local path="$1"
    stat -f %m "$path" 2>/dev/null \
        || stat -c %Y "$path" 2>/dev/null \
        || echo 0
}

# ---------------------------------------------------------------------------
# _log_has_false_success <log_file> <start_offset>
#
# Return 0 (true) iff the bytes written to <log_file> since <start_offset>
# contain a Claude CLI "Execution error" banner — the signature of a silent
# crash that nevertheless exits 0 (CLI 2.1.117 regression, 2026-04-22).
# ---------------------------------------------------------------------------
_log_has_false_success() {
    local log_file="$1"
    local start_offset="$2"
    [ -f "$log_file" ] || return 1
    [ "$log_file" = "/dev/null" ] && return 1
    tail -c +$((start_offset + 1)) "$log_file" 2>/dev/null \
        | grep -q "Execution error"
}

# ---------------------------------------------------------------------------
# run_claude_with_retry [options] -- <command...>
#
# Options:
#   --timeout <secs>           Per-attempt timeout (default: 900 = 15 min)
#   --retries <n>              Extra attempts after first failure (default: 2)
#   --backoff <secs>           Sleep between retries (default: 30)
#   --log-file <path>          Log file for status messages
#   --require-output-file <p>  (optional, repeatable) Assert <p> exists with
#                              mtime >= attempt start time. May be specified
#                              multiple times to require several artefacts; a
#                              run that exits 0 without producing every fresh
#                              <p> is treated as a failure (triggers retry).
#                              Useful for catching CLI crashes that exit 0
#                              but produce no artefact.
#
# Does NOT create a success marker; the caller is responsible for that.
#
# Returns:
#   0   - command succeeded
#   124 - all attempts timed out (last failure was timeout)
#   125 - all attempts produced a false-success ("Execution error" on exit 0)
#   126 - all attempts failed the --require-output-file assertion
#   *   - last attempt's exit code (propagated for caller diagnostics)
# ---------------------------------------------------------------------------
run_claude_with_retry() {
    local timeout=900
    local retries=2
    local backoff=30
    local log_file="/dev/null"
    local -a require_output_files=()

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --timeout)             timeout="$2";                       shift 2 ;;
            --retries)             retries="$2";                       shift 2 ;;
            --backoff)             backoff="$2";                       shift 2 ;;
            --log-file)            log_file="$2";                      shift 2 ;;
            --require-output-file) require_output_files+=("$2");       shift 2 ;;
            --)                    shift; break ;;
            *)                     break ;;
        esac
    done

    local max_attempts=$((retries + 1))
    local last_exit_code=1

    for ((attempt = 1; attempt <= max_attempts; attempt++)); do
        echo "" >> "$log_file"
        echo "[Attempt ${attempt}/${max_attempts}] Starting at $(date)" >> "$log_file"

        # Snapshot log size & wall-clock time BEFORE the attempt so we can
        # tell later whether (a) "Execution error" was printed in *this*
        # attempt, and (b) the required output file was written during it.
        local log_offset_before=0
        if [ -f "$log_file" ] && [ "$log_file" != "/dev/null" ]; then
            log_offset_before=$(wc -c < "$log_file" | tr -d ' ')
        fi
        local attempt_start_time
        attempt_start_time=$(date +%s)

        _run_with_timeout "$timeout" "$log_file" "$@"
        last_exit_code=$?

        # False-success detection (CLI crashed but exited 0)
        if [ "$last_exit_code" -eq 0 ] \
           && _log_has_false_success "$log_file" "$log_offset_before"; then
            echo "[Attempt ${attempt}/${max_attempts}] DETECTED 'Execution error' in output despite exit 0 - treating as failure" >> "$log_file"
            last_exit_code=125
        fi

        # Required output-file assertion (every file must exist and be fresh)
        if [ "$last_exit_code" -eq 0 ] && [ "${#require_output_files[@]}" -gt 0 ]; then
            local require_path
            for require_path in "${require_output_files[@]}"; do
                if [ ! -f "$require_path" ]; then
                    echo "[Attempt ${attempt}/${max_attempts}] Required output file not found: ${require_path}" >> "$log_file"
                    last_exit_code=126
                    break
                fi
                local file_mtime
                file_mtime=$(_file_mtime "$require_path")
                if [ "$file_mtime" -lt "$attempt_start_time" ]; then
                    echo "[Attempt ${attempt}/${max_attempts}] Required output file stale (mtime=${file_mtime} < attempt_start=${attempt_start_time}): ${require_path}" >> "$log_file"
                    last_exit_code=126
                    break
                fi
            done
        fi

        if [ "$last_exit_code" -eq 0 ]; then
            echo "[Attempt ${attempt}/${max_attempts}] Succeeded at $(date)" >> "$log_file"
            return 0
        fi

        case "$last_exit_code" in
            124) echo "[Attempt ${attempt}/${max_attempts}] TIMEOUT after ${timeout}s at $(date)" >> "$log_file" ;;
            125) echo "[Attempt ${attempt}/${max_attempts}] FAILED (false-success: Execution error on exit 0) at $(date)" >> "$log_file" ;;
            126) echo "[Attempt ${attempt}/${max_attempts}] FAILED (output-file assertion) at $(date)" >> "$log_file" ;;
            *)   echo "[Attempt ${attempt}/${max_attempts}] FAILED with exit code ${last_exit_code} at $(date)" >> "$log_file" ;;
        esac

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
