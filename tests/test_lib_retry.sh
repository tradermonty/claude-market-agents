#!/bin/bash
# tests/test_lib_retry.sh
#
# Bash tests for scripts/lib_retry.sh covering:
#   - existing retry behavior (success, real failure, timeout)
#   - NEW false-success detection ("Execution error" on exit 0)
#   - NEW --require-output-file assertion (missing / stale / fresh)
#
# Run: bash tests/test_lib_retry.sh
# Exit 0 iff all tests pass.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck source=/dev/null
source "${PROJECT_DIR}/scripts/lib_retry.sh"

tests_passed=0
tests_failed=0

assert() {
    local desc="$1"
    shift
    if "$@"; then
        tests_passed=$((tests_passed+1))
        echo "    PASS: $desc"
    else
        tests_failed=$((tests_failed+1))
        echo "    FAIL: $desc"
    fi
}

assert_eq() {
    local desc="$1"
    local expected="$2"
    local actual="$3"
    if [ "$expected" = "$actual" ]; then
        tests_passed=$((tests_passed+1))
        echo "    PASS: $desc"
    else
        tests_failed=$((tests_failed+1))
        echo "    FAIL: $desc (expected=<$expected> actual=<$actual>)"
    fi
}

# --------------------------------------------------------------------------
# Test: real success → rc=0, single attempt
# --------------------------------------------------------------------------
test_real_success_returns_zero() {
    echo "TEST: real_success_returns_zero"
    local log; log=$(mktemp)
    run_claude_with_retry --timeout 5 --retries 2 --backoff 0 --log-file "$log" \
        -- bash -c 'echo ok; exit 0'
    local rc=$?
    assert_eq "rc==0" 0 "$rc"
    local attempts; attempts=$(grep -c "\] Starting at" "$log")
    assert_eq "single attempt" 1 "$attempts"
    rm -f "$log"
}

# --------------------------------------------------------------------------
# Test: real failure → rc!=0, retries exhausted
# --------------------------------------------------------------------------
test_real_failure_retries_and_returns_nonzero() {
    echo "TEST: real_failure_retries_and_returns_nonzero"
    local log; log=$(mktemp)
    run_claude_with_retry --timeout 5 --retries 2 --backoff 0 --log-file "$log" \
        -- bash -c 'echo err; exit 7'
    local rc=$?
    assert "rc != 0" [ "$rc" -ne 0 ]
    local attempts; attempts=$(grep -c "\] Starting at" "$log")
    assert_eq "3 attempts (1+2 retries)" 3 "$attempts"
    rm -f "$log"
}

# --------------------------------------------------------------------------
# Test: timeout → rc=124
# --------------------------------------------------------------------------
test_timeout_returns_124() {
    echo "TEST: timeout_returns_124"
    local log; log=$(mktemp)
    run_claude_with_retry --timeout 1 --retries 0 --backoff 0 --log-file "$log" \
        -- bash -c 'sleep 30; exit 0'
    local rc=$?
    assert_eq "rc==124" 124 "$rc"
    rm -f "$log"
}

# --------------------------------------------------------------------------
# NEW: Execution error on exit 0 → treated as failure, retried
# --------------------------------------------------------------------------
test_execution_error_on_exit_zero_is_failure() {
    echo "TEST: execution_error_on_exit_zero_is_failure"
    local log; log=$(mktemp)
    run_claude_with_retry --timeout 5 --retries 2 --backoff 0 --log-file "$log" \
        -- bash -c 'echo "Execution error"; exit 0'
    local rc=$?
    assert "rc != 0 (false-success detected)" [ "$rc" -ne 0 ]
    local attempts; attempts=$(grep -c "\] Starting at" "$log")
    assert_eq "retried to full 3 attempts" 3 "$attempts"
    rm -f "$log"
}

# --------------------------------------------------------------------------
# NEW: Execution error first, then success on retry → rc=0
# --------------------------------------------------------------------------
test_execution_error_then_success_is_ok() {
    echo "TEST: execution_error_then_success_is_ok"
    local log; log=$(mktemp)
    local counter; counter=$(mktemp)
    echo 0 > "$counter"
    run_claude_with_retry --timeout 5 --retries 3 --backoff 0 --log-file "$log" \
        -- bash -c "
            n=\$(cat '$counter')
            n=\$((n+1))
            echo \$n > '$counter'
            if [ \$n -eq 1 ]; then
                echo 'Execution error'
                exit 0
            else
                echo 'ok'
                exit 0
            fi
        "
    local rc=$?
    assert_eq "rc==0 after retry" 0 "$rc"
    local final_n; final_n=$(cat "$counter")
    assert_eq "2 invocations" 2 "$final_n"
    rm -f "$log" "$counter"
}

# --------------------------------------------------------------------------
# NEW: --require-output-file missing → failure
# --------------------------------------------------------------------------
test_require_output_file_missing_is_failure() {
    echo "TEST: require_output_file_missing_is_failure"
    local log; log=$(mktemp)
    local out; out=$(mktemp -u)  # path only, file does not exist
    run_claude_with_retry --timeout 5 --retries 1 --backoff 0 \
        --log-file "$log" --require-output-file "$out" \
        -- bash -c 'echo "pretending to work"; exit 0'
    local rc=$?
    assert "rc != 0 (missing output file)" [ "$rc" -ne 0 ]
    rm -f "$log" "$out"
}

# --------------------------------------------------------------------------
# NEW: --require-output-file exists but stale (mtime before attempt start) → failure
# --------------------------------------------------------------------------
test_require_output_file_stale_is_failure() {
    echo "TEST: require_output_file_stale_is_failure"
    local log; log=$(mktemp)
    local out; out=$(mktemp)
    # Back-date file to 2020-01-01 00:00:00 (well before attempt start)
    touch -t 202001010000 "$out"
    run_claude_with_retry --timeout 5 --retries 1 --backoff 0 \
        --log-file "$log" --require-output-file "$out" \
        -- bash -c 'echo "pretending to work"; exit 0'
    local rc=$?
    assert "rc != 0 (stale output file)" [ "$rc" -ne 0 ]
    rm -f "$log" "$out"
}

# --------------------------------------------------------------------------
# NEW: --require-output-file exists and fresh → success
# --------------------------------------------------------------------------
test_require_output_file_fresh_is_success() {
    echo "TEST: require_output_file_fresh_is_success"
    local log; log=$(mktemp)
    local out; out=$(mktemp -u)
    # Ensure the fresh-write is AFTER the attempt-start wall-clock second
    # by introducing a tiny sleep in the command.
    run_claude_with_retry --timeout 5 --retries 0 --backoff 0 \
        --log-file "$log" --require-output-file "$out" \
        -- bash -c "sleep 1; echo written > '$out'; exit 0"
    local rc=$?
    assert_eq "rc==0" 0 "$rc"
    assert "output file exists" [ -f "$out" ]
    rm -f "$log" "$out"
}

# --------------------------------------------------------------------------
# NEW: --require-output-file repeatable; all-fresh -> success
# --------------------------------------------------------------------------
test_multiple_require_output_files_all_fresh_is_success() {
    echo "TEST: multiple_require_output_files_all_fresh_is_success"
    local log; log=$(mktemp)
    local out1; out1=$(mktemp -u)
    local out2; out2=$(mktemp -u)
    run_claude_with_retry --timeout 5 --retries 0 --backoff 0 \
        --log-file "$log" \
        --require-output-file "$out1" \
        --require-output-file "$out2" \
        -- bash -c "sleep 1; echo a > '$out1'; echo b > '$out2'; exit 0"
    local rc=$?
    assert_eq "rc==0 with both fresh" 0 "$rc"
    assert "out1 exists" [ -f "$out1" ]
    assert "out2 exists" [ -f "$out2" ]
    rm -f "$log" "$out1" "$out2"
}

# --------------------------------------------------------------------------
# NEW: --require-output-file repeatable; one missing -> failure
# --------------------------------------------------------------------------
test_multiple_require_output_files_one_missing_is_failure() {
    echo "TEST: multiple_require_output_files_one_missing_is_failure"
    local log; log=$(mktemp)
    local out1; out1=$(mktemp -u)
    local out2; out2=$(mktemp -u)
    # Write ONLY out2 (the LAST flag value). With the original scalar
    # implementation this incorrectly returns success, because only the
    # last --require-output-file gets stored. The fix must check ALL files.
    run_claude_with_retry --timeout 5 --retries 0 --backoff 0 \
        --log-file "$log" \
        --require-output-file "$out1" \
        --require-output-file "$out2" \
        -- bash -c "sleep 1; echo b > '$out2'; exit 0"
    local rc=$?
    assert "rc != 0 (first file missing must still fail)" [ "$rc" -ne 0 ]
    rm -f "$log" "$out1" "$out2"
}

# --------------------------------------------------------------------------
# NEW: timeout kills descendant processes (no orphaned children)
# --------------------------------------------------------------------------
test_timeout_kills_descendants() {
    echo "TEST: timeout_kills_descendants"
    local log; log=$(mktemp)
    local marker; marker=$(mktemp -u)

    # Spawn a grandchild that would create $marker after 8s if it survives.
    # We will time out the parent in 1s; if descendant cleanup works, the
    # grandchild dies before it can touch the marker.
    run_claude_with_retry --timeout 1 --retries 0 --backoff 0 --log-file "$log" \
        -- bash -c "( sleep 8; touch '$marker' ) & wait"
    local rc=$?
    assert_eq "rc==124 (timeout)" 124 "$rc"

    # Wait long enough that the (would-be) grandchild touch would fire.
    sleep 10

    assert "marker NOT created (descendant was killed)" [ ! -f "$marker" ]
    rm -f "$log" "$marker"
}

# --------------------------------------------------------------------------
# Backward compat: existing calls (without --require-output-file) still work
# --------------------------------------------------------------------------
test_backward_compat_no_require_flag_works() {
    echo "TEST: backward_compat_no_require_flag_works"
    local log; log=$(mktemp)
    run_claude_with_retry --timeout 5 --retries 0 --backoff 0 --log-file "$log" \
        -- bash -c 'echo ok; exit 0'
    local rc=$?
    assert_eq "rc==0 without new flag" 0 "$rc"
    rm -f "$log"
}

# --------------------------------------------------------------------------
# Run all tests
# --------------------------------------------------------------------------
test_real_success_returns_zero
test_real_failure_retries_and_returns_nonzero
test_timeout_returns_124
test_execution_error_on_exit_zero_is_failure
test_execution_error_then_success_is_ok
test_require_output_file_missing_is_failure
test_require_output_file_stale_is_failure
test_require_output_file_fresh_is_success
test_multiple_require_output_files_all_fresh_is_success
test_multiple_require_output_files_one_missing_is_failure
test_timeout_kills_descendants
test_backward_compat_no_require_flag_works

echo ""
echo "======================================="
echo "Results: ${tests_passed} passed, ${tests_failed} failed"
echo "======================================="
[ "$tests_failed" -eq 0 ]
