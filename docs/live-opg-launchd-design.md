# OPG Order Support + launchd Automation Design

## Context

Paper Trading環境でOPG（Market On Open）注文を使い、寄り付きエントリーを自動化する。
OPGにより早朝に注文を置けば正確に寄り付き価格で約定する。
launchdでsignal_generator → executor(place) → executor(poll)を自動化する。

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| 1 process vs 2 processes | **2 launchd jobs** (place + poll) | Long-running processes are fragile. Two short jobs are robust |
| Bracket orders | **Skip in OPG mode** | bracket + OPG are incompatible. plain buy(opg) → fill → GTC stop |
| Exit sell | **No change (market + day)** | Exits execute during market hours |
| Existing tests | **Fixture changed to entry_tif="day"** | Preserve existing bracket tests. New tests for OPG |
| `--phase all` + OPG | **Error exit** | Timeout before fill → stop not placed → risk |

## Timing Flow

```
06:00 PT  earnings-trade-report (existing) → HTML report generation
06:15 PT  signal_generator                 → signals JSON generation
06:20 PT  executor --phase place           → exit sells + OPG buy orders
06:32 PT  executor --phase poll            → fill check + GTC stop placement (1st)
06:40 PT  executor --phase poll            → retry unfilled (2nd, idempotent)
06:50 PT  executor --phase poll            → final sweep (3rd, idempotent)
```

## Critical Issues

### C1: Phase C Slot Calculation vs Place Timing

**Problem**: At 06:20 PT (09:20 ET), exit sells (market+day) are not yet filled.
Alpaca position count hasn't decreased, so new OPG entries are skipped due to
insufficient slots.

**Solution**: In place phase, calculate available slots using DB open positions
minus actually-processed exits from Phase A.

```
real_available = max_positions - (db_open_count - exits_actually_processed)
```

**exits_actually_processed definition**:
- Sell orders successfully submitted to Alpaca (API success)
- stop_filled (stop already filled when canceling): **included** (position already closed)
- DB idempotent skip: **only non-terminal orders count**
  - Order status NOT IN TERMINAL_STATUSES → included
  - Order status IN TERMINAL_STATUSES → **not included** (already processed)
- Alpaca idempotent skip: **only non-terminal orders count**
  - Alpaca order status NOT IN TERMINAL_STATUSES → included
  - Alpaca order status IN TERMINAL_STATUSES → **not included**
- Sell order submission failure: **not included** (position remains)

### C2: `--phase all` + OPG Safety

**Problem**: Running `all` in OPG mode → place → immediate poll → timeout → stop not set.

**Solution**: When `entry_tif="opg"` and `--phase all`, error message + `sys.exit(6)`.

## Status Classification

Terminal statuses are defined once in `state_db.py`:

```python
TERMINAL_STATUSES = ('filled', 'canceled', 'expired', 'rejected', 'done_for_day', 'suspended')
```

All status queries reference this single constant. `get_pending_orders()` uses
`WHERE status NOT IN TERMINAL_STATUSES` to capture pending, new, accepted,
pending_new, and partially_filled orders.

## Poll Recovery Strategy (3 Runs)

- Poll phase timeout: 300s (5 minutes)
- `--phase poll` is idempotent: existing stops are not duplicated (client_order_id check)
  - If existing stop is in TERMINAL_STATUSES, it is re-placed with `_retry` suffix
- 3 poll jobs via launchd:
  - 06:32 PT (09:32 ET): 1st — captures most OPG fills
  - 06:40 PT (09:40 ET): 2nd — captures delayed fills
  - 06:50 PT (09:50 ET): 3rd (final) — manual intervention after this
- After 3rd poll, remaining unfilled: exit code 0 + CRITICAL log "UNFILLED ORDERS REMAIN"
  - Kill switch does NOT activate. Manual check required.

## Stop Price Source

- `orders.planned_stop_price` column (recorded at place time) is the **single source of truth**
- Poll phase reads ONLY from `orders.planned_stop_price` (no signals JSON / positions fallback)
- This allows stop recovery even for unfilled entries (no positions row yet)
- If `planned_stop_price` is NULL:
  - Stop order is NOT placed (dangerous to submit without price)
  - CRITICAL log: "UNPROTECTED POSITION: planned_stop_price is NULL for {ticker}"
  - Manual recovery required within 10 minutes of final poll

## OPG Time Guard

- OPG entries are blocked during market hours (9:28-19:00 ET)
- Time source: Alpaca clock API timestamp (converted to ET)
- Fallback: local system time if Alpaca clock unavailable

## Trade Date Resolution

- `--trade-date` argument is optional; defaults to `datetime.now(ET).strftime("%Y-%m-%d")`
- Alpaca clock is used ONLY for time guard, not for date determination
- Shell scripts omit `--trade-date` (Python resolves automatically)

## Schema Migration

`state_db.py` applies `ALTER TABLE orders ADD COLUMN planned_stop_price REAL` at
`StateDB.__init__()` for existing databases. The migration checks `PRAGMA table_info`
before altering.

## Files Changed

### live/config.py
- `LiveConfig.entry_tif: str = "opg"` field added

### live/state_db.py
- `TERMINAL_STATUSES` constant (single definition)
- `get_pending_orders(trade_date, intent, side)` method
- `planned_stop_price` column + `_migrate_schema()` migration
- `add_order()` accepts `planned_stop_price` parameter

### live/executor.py
- `--phase` argument (place / poll / all), default `all`
- OPG + all → `sys.exit(6)`
- Phase C slot calculation: DB-based with exit deduction
- Phase D time guard: Alpaca clock → 9:28-19:00 ET block
- Phase D orders: OPG mode skips bracket, uses `time_in_force="opg"`
- `skip_poll` flag: `--phase place` skips Phase E
- `execute_poll_phase()`: DB-driven fill check + GTC stop placement
- `--trade-date` argument (optional, for manual re-runs)
- `_poll_orders` accepts `poll_timeout` parameter
- Dry-run mode works without API keys

### live/tests/test_executor.py
- `config` fixture: `LiveConfig(entry_tif="day")`
- `opg_config` fixture: `LiveConfig(entry_tif="opg")`
- New test classes:
  - `TestOPGSkipsBracket`
  - `TestOPGBlockedDuringMarketHours`
  - `TestOPGAllPhaseRejected`
  - `TestSkipPoll`
  - `TestPlacePhaseSlotCalculation`
  - `TestPollPhaseIdempotent`
  - `TestPollPhasePendingOrders`
  - `TestPollPhaseNullStopPrice`
  - `TestIsMarketHoursET`
  - `TestExitIdempotentTerminalNotCounted`
  - `TestPollOrdersDoneForDay`
  - `TestPollPhaseReplacesTerminalStop`
  - `TestAlpacaIdempotentTerminalNotCounted`

### live/tests/test_state_db.py
- `TestPendingOrders` class (7 tests)
- `TestMigration` class

### scripts/ (3 shell scripts)
- `run_signal_generator.sh` — report lookup + `--report-file` argument
- `run_executor_place.sh` — today's signals file lookup + `--phase place`
- `run_executor_poll.sh` — DB-driven poll (no signals file needed)

### launchd/ (5 plist files)
- `com.trade-analysis.signal-generator.plist` — weekdays 06:15 PT
- `com.trade-analysis.executor-place.plist` — weekdays 06:20 PT
- `com.trade-analysis.executor-poll.plist` — weekdays 06:32 PT
- `com.trade-analysis.executor-poll-retry.plist` — weekdays 06:40 PT
- `com.trade-analysis.executor-poll-final.plist` — weekdays 06:50 PT

## Out of Scope

- Exit sells via OPG (trend_break at open price)
- Weekly trailing stop pre-market detection improvement

## Verification

```bash
# 1. All tests pass
.venv/bin/python -m pytest live/tests/ -v

# 2. Dry-run each phase (no API keys needed)
.venv/bin/python -m live.executor \
    --signals-file live/signals/trade_signals_YYYY-MM-DD_ema_p10.json \
    --state-db live/state.db --phase place --dry-run -v

.venv/bin/python -m live.executor \
    --state-db live/state.db --phase poll --dry-run -v

# 3. OPG + all rejection
.venv/bin/python -m live.executor \
    --signals-file live/signals/trade_signals_YYYY-MM-DD_ema_p10.json \
    --state-db live/state.db --phase all --dry-run -v
# → exit code 6

# 4. launchd registration
mkdir -p logs/
cp launchd/*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.trade-analysis.signal-generator.plist
launchctl load ~/Library/LaunchAgents/com.trade-analysis.executor-place.plist
launchctl load ~/Library/LaunchAgents/com.trade-analysis.executor-poll.plist
launchctl load ~/Library/LaunchAgents/com.trade-analysis.executor-poll-retry.plist
launchctl load ~/Library/LaunchAgents/com.trade-analysis.executor-poll-final.plist
```
