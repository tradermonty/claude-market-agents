# Earnings Trade Backtest System

Backtests a post-earnings gap-up trading strategy. Parses HTML trade analysis reports, fetches historical prices from FMP, simulates trades with stop losses, and generates interactive reports.

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Run backtest (requires FMP API key)
export FMP_API_KEY=your_key_here
python -m backtest.main --reports-dir reports/ --output-dir reports/backtest/

# Parse only (no API key needed)
python -m backtest.main --parse-only
```

## CLI Reference

| Argument | Type | Default | Description |
|---|---|---|---|
| `--reports-dir` | str | `reports/` | Directory with earnings trade HTML reports |
| `--output-dir` | str | `reports/backtest/` | Output directory for results |
| `--position-size` | float | `10000` | Position size per trade ($) |
| `--stop-loss` | float | `10.0` | Stop loss percentage (0-100) |
| `--slippage` | float | `0.5` | Slippage percentage on stop exit (0-50) |
| `--max-holding` | int | `90` | Max holding period in calendar days |
| `--min-grade` | choice | `D` | Minimum grade to include (A/B/C/D) |
| `--min-score` | float | None | Minimum score filter, inclusive (0-100) |
| `--max-score` | float | None | Maximum score filter, exclusive (0-100) |
| `--min-gap` | float | None | Minimum gap-up % filter, inclusive |
| `--max-gap` | float | None | Maximum gap-up % filter, exclusive |
| `--stop-mode` | choice | `intraday` | Stop loss trigger mode (see below) |
| `--daily-entry-limit` | int | None | Max new entries per day |
| `--entry-mode` | choice | `report_open` | Entry timing: `report_open` or `next_day_open` |
| `--walk-forward` | flag | off | Run walk-forward validation |
| `--wf-folds` | int | `3` | Number of walk-forward folds |
| `--fmp-api-key` | str | None | FMP API key (overrides env/config) |
| `--parse-only` | flag | off | Parse HTML only, skip price fetch |
| `--verbose` / `-v` | flag | off | Enable debug logging |

## Stop Mode Comparison

| Mode | Trigger | Exit Price |
|---|---|---|
| `intraday` | Intraday low <= stop price | Stop price - slippage |
| `close` | Close <= stop price | Close - slippage |
| `skip_entry_day` | Same as intraday, but skips entry day | Stop price - slippage |
| `close_next_open` | Close <= stop price | Next day's open - slippage |

## Data Requirements

- **HTML reports**: Earnings trade analysis HTML files named `earnings_trade_analysis_YYYY-MM-DD*.html` in the reports directory.
- **FMP API key**: Required for price data. Set via `FMP_API_KEY` env var, `.env` file, or `--fmp-api-key` flag.

## Output Files

| File | Description |
|---|---|
| `earnings_trade_backtest_result.html` | Interactive HTML report with Plotly charts |
| `earnings_trade_backtest_trades.csv` | All executed trades |
| `earnings_trade_backtest_skipped.csv` | Skipped trades with reasons |
| `run_manifest.json` | Run configuration and environment for reproducibility |

## Metrics Definitions

| Metric | Definition |
|---|---|
| **Win Rate** | Percentage of trades with positive P&L |
| **Profit Factor** | Total profit / total loss (inf if no losses) |
| **Trade Sharpe** | mean(trade returns) / std(trade returns), per-trade basis |
| **Max Drawdown** | Largest peak-to-trough decline in cumulative P&L |
| **Max Drawdown %** | Max drawdown as percentage of peak equity |

## Grade / Score System

Grades (A-D) are extracted from HTML reports or inferred from scores:

| Grade | Score Range | Interpretation |
|---|---|---|
| A | 85-100 | Strong buy signal |
| B | 70-84 | Moderate buy signal |
| C | 55-69 | Weak signal |
| D | < 55 | Poor signal |

## Architecture

```
main.py           CLI entry point, orchestration
  |
  +-- html_parser.py       Parse HTML reports -> TradeCandidate list
  +-- price_fetcher.py      Fetch OHLCV data from FMP API
  +-- trade_simulator.py    Simulate trades with stop loss
  +-- metrics_calculator.py Calculate performance metrics
  +-- report_generator.py   Generate HTML + CSV reports
  +-- run_manifest.py       Write reproducibility manifest
  +-- walk_forward.py       Walk-forward cross-validation
```

## Development

```bash
make lint        # ruff check + format check
make test        # pytest with coverage (60% minimum)
make typecheck   # mypy strict type checking
make security    # bandit security scan
make all         # lint + test + typecheck
make golden      # regenerate golden test fixtures
make format      # auto-format code
make install     # install with dev dependencies + pre-commit
```
