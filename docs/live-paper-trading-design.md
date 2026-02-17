# Live Paper Trading System - Design Document

## 1. Overview

Alpaca paper trading account to run live validation of earnings trade strategies.

| Item | Value |
|------|-------|
| Primary strategy | `ema_p10` (weekly EMA period 10) - **executed** via Alpaca |
| Shadow strategy | `nwl_p4` (weekly N-week low period 4) - **tracked** in DB only |
| Backtest PF | ema_p10: 2.14, nwl_p4: 2.19 |
| Portfolio | max 20 positions, $10,000 per position |
| Switching criteria | 90 days or 100 trades, PF +0.10, MaxDD neutral, P&L +3% |

### Design Principles

1. **Backtest parity** - identical rules to `run_manifest.json`
2. **Generation/execution separation** - `signal_generator` -> JSON -> `executor`
3. **Idempotency** - deterministic `client_order_id` prevents double orders
4. **State management** - SQLite DB + Alpaca reconciliation (fail-fast)
5. **Safety first** - paper URL default, kill switch, bracket orders

---

## 2. Architecture

```
                            +--------------------+
                            | HTML Report File   |
                            | (EarningsReport)   |
                            +--------+-----------+
                                     |
                                     v
+------------------+     +---------------------+     +-----------------+
| backtest/        |     | live/               |     | live/           |
| weekly_bars.py   |<--->| signal_generator.py |---->| executor.py     |
| (shared funcs)   |     | (CLI module)        |     | (CLI module)    |
+------------------+     +--------+------------+     +-------+---------+
                                  |                          |
                           +------v------+            +------v------+
                           | state_db.py |            | alpaca_     |
                           | (SQLite)    |<---------->| client.py   |
                           +-------------+            +-------------+
                                                           |
                                                    +------v------+
                                                    | Alpaca API  |
                                                    | (Paper)     |
                                                    +-------------+
```

### Data Flow

```
signal_generator                          executor
     |                                         |
     |  1. Parse HTML report                   |
     |  2. Get DB positions                    |
     |  3. Reconcile with Alpaca               |
     |  4. Check trailing stops (EMA)          |
     |  5. Rotation check                      |
     |  6. Generate entries/exits              |
     |     -> ema_p10.json                     |
     |                                         |
     |  7. Shadow path (NWL, independent)      |
     |  8. Update shadow_positions DB          |
     |     -> nwl_p4.json                      |
     |                                         |
     |                                    1. Read ema_p10.json
     |                                    2. Phase A: Cancel stops + sell
     |                                    3. Phase B: Poll sells
     |                                    4. Phase C: Recount positions
     |                                    5. Phase D: Buy + bracket stop
     |                                    6. Phase E: Poll buys
     |                                    7. Update state DB
```

---

## 3. Module Specifications

### 3.1 `live/config.py` (102 lines)

Frozen dataclass holding all trading parameters. Matches `run_manifest.json` exactly.

```python
@dataclass(frozen=True)
class LiveConfig:
    max_positions: int = 20
    position_size: float = 10000.0
    stop_loss_pct: float = 10.0
    slippage_pct: float = 0.5
    stop_mode: str = "intraday"
    entry_mode: str = "report_open"
    max_holding_days: Optional[int] = None
    rotation: bool = True
    min_grade: str = "D"
    primary_trailing_stop: str = "weekly_ema"
    primary_trailing_period: int = 10
    shadow_trailing_stop: str = "weekly_nweek_low"
    shadow_trailing_period: int = 4
    trailing_transition_weeks: int = 2
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    max_daily_trade_orders: int = 40
    max_daily_stop_orders: int = 20
    entry_cutoff_minutes: int = 5
    min_buying_power: float = 5000.0
    fmp_lookback_days: int = 400
```

Key functions:
- `verify_against_manifest(path)` - raises `ValueError` on parameter mismatch
- `resolve_api_key(key_name, mcp_server)` - env -> `.mcp.json` lookup

### 3.2 `live/state_db.py` (499 lines)

SQLite state management with 6 tables:

| Table | Purpose |
|-------|---------|
| `positions` | Real trading positions (Alpaca-backed) |
| `orders` | All order records with intent, fill tracking |
| `run_log` | Execution audit trail |
| `shadow_positions` | Independent NWL virtual portfolio |
| `shadow_signals` | Shadow signal JSON archive |
| `system_config` | Kill switch and system flags |

Key methods:
- `is_kill_switch_on()` / `set_kill_switch(on)`
- `add_position()` / `get_open_positions()` / `close_position()`
- `add_order()` / `get_order_by_client_id()` / `update_order_status()`
- `get_daily_order_count(trade_date, intent=None)` - separate counters
- `add_shadow_position()` / `get_shadow_positions(strategy)` / `close_shadow_position()`

Supports `:memory:` for testing. Uses `contextmanager` for connection handling.

### 3.3 `live/alpaca_client.py` (131 lines)

Requests-based REST client for Alpaca Trading API.

```python
class AlpacaClient:
    def __init__(self, api_key, secret_key,
                 base_url="https://paper-api.alpaca.markets",
                 allow_live=False)
```

**Paper URL guard**: raises `ValueError` if `"paper" not in base_url` and `allow_live=False`.

Methods:
- `get_account()`, `get_positions()`, `get_clock()`
- `place_order(symbol, qty, side, ...)` - standard market/stop orders
- `place_bracket_order(symbol, qty, side, ...)` - OTO buy+stop
- `get_order(id)`, `get_order_by_client_id(client_id)` - returns `None` on 404
- `cancel_order(id)`

### 3.4 `live/trailing_stop_checker.py` (165 lines)

Reuses `backtest/weekly_bars.py` standalone functions for trailing stop evaluation.

```python
class TrailingStopChecker:
    def check_position(ticker, entry_date, as_of_date,
                       trailing_stop, trailing_period) -> TrailingStopResult
```

Pipeline:
1. `fetch_prices(ticker, lookback, as_of_date)`
2. `is_week_end_by_date(bars, as_of_date)` - early return if not week end
3. `aggregate_daily_to_weekly(bars)`
4. `compute_weekly_ema/nweek_low(weekly, period)`
5. `count_completed_weeks >= transition_weeks`
6. `is_trend_broken(weekly, indicators, as_of_date)`

`TrailingStopResult` dataclass contains: `is_week_end`, `completed_weeks`,
`transition_met`, `trend_broken`, `should_exit`, `indicator_value`, `last_close`.

### 3.5 `live/signal_generator.py` (624 lines)

CLI module generating dual-strategy signal JSON files.

```bash
python -m live.signal_generator \
    --report-file reports/earnings_trade_analysis_2026-02-17.html \
    --output-dir live/signals/ \
    --state-db live/state.db \
    --manifest reports/backtest/run_manifest.json \
    [--force] [--dry-run] [-v]
```

Outputs:
- `trade_signals_{date}_ema_p10.json` - execution target
- `trade_signals_{date}_nwl_p4.json` - shadow record only

**ema_p10 path** (execution):
1. Kill switch check (exit code 3)
2. Parse HTML report -> filter candidates by min_grade
3. Get DB positions + reconcile with Alpaca (exit code 4 on mismatch)
4. Check trailing stops (`TrailingStopChecker` with `weekly_ema`, period 10)
5. Rotation check (if at capacity: weakest unrealized P&L vs best candidate score)
6. Generate entries (up to `max_positions - open + exits`)

**nwl_p4 path** (shadow, independent):
1. Get `shadow_positions` from DB
2. Check trailing stops (`weekly_nweek_low`, period 4)
3. Independent rotation check
4. Independent entries
5. Update `shadow_positions` and `shadow_signals` in DB

### 3.6 `live/executor.py` (623 lines)

CLI module executing signal JSON via Alpaca API.

```bash
python -m live.executor \
    --signals-file live/signals/trade_signals_2026-02-17_ema_p10.json \
    --state-db live/state.db \
    --manifest reports/backtest/run_manifest.json \
    [--dry-run] [--skip-time-check] [-v]
```

**Phase A** - Stop cancel + Sell:
- A-1: Get `stop_order_id` from state DB
- A-2: `cancel_order()` -> check status (`filled` = stop already hit, skip sell)
- A-3: Market sell with `client_order_id = {date}_{ticker}_exit_sell`

**Phase B** - Sell polling (timeout 60s)

**Phase C** - Position recount from Alpaca (`real_available = max - real_open`)

**Phase D** - Buy + Stop (with time guard):
- Entry blocked after market open + 5 minutes (`entry_cutoff_minutes`)
- First choice: bracket order (buy + stop in single request)
- Fallback: market buy -> poll -> GTC stop (kill switch on stop failure)
- `client_order_id = {date}_{ticker}_entry_buy` / `{date}_{ticker}_stop_sell`

**Phase E** - Buy polling

---

## 4. Backtest Code Sharing

### Standalone Functions in `backtest/weekly_bars.py`

```python
is_week_end_by_date(bars, current_date) -> bool
is_week_end_by_index(bars, idx) -> bool
count_completed_weeks(weekly_bars, entry_date, current_date) -> int
is_trend_broken(weekly_bars, indicators, current_date) -> bool
```

Both `PortfolioSimulator` and `TradeSimulator` delegate to these functions.
`TrailingStopChecker` imports and uses them directly.
Golden tests verify decision consistency between backtest and live paths.

---

## 5. Database Schema

### positions
| Column | Type | Notes |
|--------|------|-------|
| position_id | INTEGER PK | Auto-increment |
| ticker | TEXT | Stock symbol |
| entry_date | TEXT | YYYY-MM-DD |
| entry_price | REAL | Fill price |
| target_shares | INTEGER | Intended qty |
| actual_shares | INTEGER | Filled qty (partial fill tracking) |
| invested | REAL | entry_price * actual_shares |
| stop_price | REAL | Protective stop level |
| stop_order_id | TEXT | Alpaca stop order ID |
| score, grade, grade_source | | From HTML report |
| report_date, company_name, gap_size | | Candidate metadata |
| status | TEXT | 'open' / 'closed' |
| exit_date, exit_price, exit_reason | | Set on close |
| pnl, return_pct | REAL | Calculated on close |

### orders
| Column | Type | Notes |
|--------|------|-------|
| client_order_id | TEXT UNIQUE | `{date}_{ticker}_{intent}_{side}` |
| intent | TEXT | 'entry', 'exit', 'stop' |
| trade_date | TEXT | US/Eastern trade date |
| filled_qty, remaining_qty | INTEGER | Partial fill tracking |

### shadow_positions
Same schema as positions (without stop_order_id), independently managed.
`strategy` column defaults to 'nwl_p4'.

---

## 6. Idempotency Design

### client_order_id Format

```
{trade_date}_{ticker}_{intent}_{side}
```

| intent | side | Example |
|--------|------|---------|
| entry | buy | `20260217_NVDA_entry_buy` |
| exit | sell | `20260217_NVDA_exit_sell` |
| stop | sell | `20260217_NVDA_stop_sell` |

Three-layer protection:
1. **State DB**: `UNIQUE` constraint on `client_order_id`
2. **Executor check**: `get_order_by_client_id()` before placing
3. **Alpaca server**: Rejects duplicate `client_order_id`

---

## 7. Safety Guards

| # | Guard | Implementation | Test |
|---|-------|---------------|------|
| 1 | Kill switch | `system_config` table, checked at CLI startup | test_kill_switch_blocks |
| 2 | Paper URL default | `AlpacaClient.__init__()` raises on non-paper URL | test_non_paper_url_rejected |
| 3 | Idempotency | Deterministic client_order_id + DB UNIQUE + Alpaca server-side | test_idempotent_order |
| 4 | DB/Alpaca reconciliation | Mismatch = exit code 4, `--force` to continue | test_db_alpaca_mismatch_fails |
| 5 | Max positions | signal_generator calculates + executor recounts after sells | test_recount_positions |
| 6 | Rotation | Same rules as PortfolioSimulator (1/day, worst P&L vs score) | test_rotation_logic |
| 7 | Intraday stop | Alpaca GTC stop order (always active) | test_bracket_order_preferred |
| 8 | Buying power | `get_account()` check before each buy | test_buying_power_check |
| 9 | Daily order limit | Separate counters: trade (entry+exit) vs stop orders | test_daily_order_limit |
| 10 | Partial fill | `filled_qty`/`remaining_qty` tracking, warning on partial | - |
| 11 | Stop cancel before sell | Cancel GTC stop first, check if already filled | test_cancel_stop_before_sell |
| 12 | Entry time guard | Block entries >5min after open (report_open mode) | test_entry_time_guard |
| 13 | Shadow independence | `shadow_positions` table, no Alpaca interaction | test_shadow_independent |
| 14 | Bracket order | OTO preferred, fallback + kill switch on stop failure | test_bracket_fallback |
| 15 | Shadow responsibility | signal_generator owns all shadow state, executor ignores | test_dry_run |
| 16 | Manifest verification | Compare LiveConfig vs run_manifest at startup | config.verify_against_manifest() |

---

## 8. Dual-Strategy Comparison

```
signal_generator.py generates 2 JSONs per run:

trade_signals_{date}_ema_p10.json  <- executor processes this
trade_signals_{date}_nwl_p4.json   <- shadow record only

ema_p10: positions table + Alpaca reconciliation -> executed
nwl_p4:  shadow_positions table (independent)    -> DB only
```

Both strategies share the same candidate list from the HTML report.
Shadow entry_price uses theoretical slippage calculation (backtest parity).
Executor never touches `shadow_positions`.

---

## 9. Test Coverage

| Test File | Tests | Scope |
|-----------|-------|-------|
| test_state_db.py | 27 | All DB operations, kill switch, shadow positions |
| test_alpaca_client.py | 14 | Paper URL guard, all API methods, error handling |
| test_trailing_stop_checker.py | 9 | EMA/NWL detection, edge cases, warmup |
| test_signal_generator.py | 12 | Kill switch, reconciliation, trailing exits, rotation, shadow |
| test_executor.py | 13 | All phases, bracket/fallback, time guard, kill switch |
| test_rule_consistency.py | 7 | Golden tests: backtest vs live decision consistency |
| **Total** | **82** | |

### Golden Tests (test_rule_consistency.py)

Verify that `PortfolioSimulator` (backtest) and `TrailingStopChecker` (live)
produce identical trailing stop decisions:

- **EMA trend break**: Both detect exit on the same ISO week
- **EMA no exit**: Neither triggers in a steady uptrend
- **NWL break**: Both detect exit on the same ISO week
- **Shared function determinism**: aggregation, EMA, NWL, week-end detection

---

## 10. CLI Usage

### Daily Workflow

```bash
# 1. Generate signals (after market close or before open)
python -m live.signal_generator \
    --report-file reports/earnings_trade_analysis_2026-02-17.html \
    --output-dir live/signals/ \
    --state-db live/state.db \
    --manifest reports/backtest/run_manifest.json -v

# 2. Execute signals (before market open for report_open entries)
python -m live.executor \
    --signals-file live/signals/trade_signals_2026-02-17_ema_p10.json \
    --state-db live/state.db \
    --manifest reports/backtest/run_manifest.json -v

# 3. Verify
python -m pytest live/tests/ -v
ruff check live/ backtest/
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 3 | Kill switch is ON |
| 4 | DB/Alpaca position mismatch (use `--force` to override) |

---

## 11. File Inventory

### New Files (4,361 lines total)

```
live/
  __init__.py                          0 lines
  config.py                          102 lines
  state_db.py                        499 lines
  alpaca_client.py                   131 lines
  trailing_stop_checker.py           165 lines
  signal_generator.py                624 lines
  executor.py                        623 lines
  tests/
    __init__.py                        0 lines
    test_state_db.py                 361 lines
    test_alpaca_client.py            286 lines
    test_trailing_stop_checker.py    231 lines
    test_signal_generator.py         564 lines
    test_executor.py                 407 lines
    test_rule_consistency.py         368 lines
```

### Modified Files

| File | Change |
|------|--------|
| `backtest/weekly_bars.py` | +65 lines: 4 standalone functions |
| `backtest/portfolio_simulator.py` | Delegated 3 methods to standalone functions |
| `backtest/trade_simulator.py` | Delegated 3 methods to standalone functions |
| `pyproject.toml` | Added `live*` packages, test paths, Python >=3.10 |
| `.gitignore` | Added `live/state.db`, `live/signals/` |

### Verification Results

```
backtest/tests/: 240 passed (regression-free)
live/tests/:      82 passed
ruff check:       All checks passed
Total:           322 passed, 0 failed
```
