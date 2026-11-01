# Claude Code Configuration

## Pre-approved Tools and Actions

### Market Analysis & Reporting
- `mcp__finviz__*` - All Finviz market screening and analysis tools
- `mcp__alpaca__*` - All Alpaca trading data and market information tools
- `mcp__fmp-server__*` - All Financial Modeling Prep API tools
- `WebFetch` for market data sources (finance.yahoo.com, earningswhispers.com, etc.)
- `WebSearch` for market research and analysis

### Report Generation
- `Task` tool with `after-market-reporter` agent for daily market reports
- `Task` tool with `earnings-trade-analyst` agent for earnings analysis
- `Task` tool with `market-environment-strategist` agent for comprehensive market environment analysis
- `Task` tool with `fmp-stock-analyzer` agent for detailed stock fundamental and technical analysis
- `Task` tool with `earnings-analysis-reporter` agent for comprehensive earnings analysis
- HTML report generation in `/reports/` directory
- Social media post generation for market updates
- CSV/Excel export for trading data analysis

### File Operations for Reports
- `Write` operations in `/reports/` directory for market analysis outputs
- `Read` operations for analyzing existing market data files
- `Edit` operations for updating report templates and configurations

## X Post Policy
- All X posts **must be a single post**. Thread format (splitting into multiple posts) is prohibited
- **Reference template**: `reports/2026-05-26-after-market-x-post.md` (canonical structure for after-market posts)
- File naming: `reports/YYYY-MM-DD-after-market-x-post.md`
- X Premium long-form is allowed; do NOT artificially truncate to 280 chars — match the reference template's depth instead

### Required Section Order (after-market X post)
Follow this exact block order, separated by blank lines. Two blocks span multiple items with no blank line inside: header + ETF lines (items 1–2), and sector lines + stats line (item 5), matching the reference template:

1. **Header line**: `🇺🇸 US Market Close (Mon DD) — <short context tagline>`
   - Tagline examples: "Indexes Flat, Earnings Sweep", "Post-Memorial Day Return", "Risk-On Rally". Omit only if the day is genuinely uneventful.
2. **Major ETFs — 2 lines**:
   - Line 1: `$SPY ±X.XX% | $QQQ ±X.XX% | $DIA ±X.XX% | $IWM ±X.XX%`
   - Line 2: `$TLT ±X.XX% | $GLD ±X.XX%`
3. **🔥 Top Volume-Surge Movers**: header line + 2–3 lines listing 6–9 tickers with `$TICKER +XX.XX%` separated by ` | `. Add a short parenthetical only when the catalyst is verifiable from fetched data (e.g., `(guidance raise)`, `(crypto/AI infra)`). Use 🚀 on the standout. Do NOT invent catalysts.
4. **🌙 After-Hours Earnings** (skip entire block on Fridays or when no after-hours data):
   - Header `🌙 After-Hours Earnings (N/N beat EPS):` when applicable
   - Standout on its own line with EPS surprise + sales/QoQ: `$XXX +XX.XX% AH 🚀 (EPS +X.XX%, Sales +XX% QoQ)`
   - Remaining tickers on follow-up lines, pipe-separated, with brief surprise data where space allows
5. **📊 Sectors + stats — 3 lines, one block**:
   - Line 1: top 3 winning sectors with %
   - Line 2: bottom 2–3 losing sectors with %
   - Line 3 (stats, directly below line 2 with no blank line): `NN volume-surge stocks | NNN uptrend stocks | Avg move +X.X%`
6. **🗓️ Next-session earnings preview** (optional, only with verified tickers): `🗓️ Thu earnings: $XXX $YYY $ZZZ`
7. **Hashtags**: 5–7 tags, core set = `#StockMarket #MarketAnalysis #EarningsSeason` + situational (`#AfterHours`, `#AIStocks`, ticker tags like `#SNOW`).

### Quality Rules
- Parenthetical catalysts must trace to fetched data (EPS surprise %, sales QoQ, screener tag). Never guess.
- Counts (volume-surge, uptrend, avg move) must come from `finviz:get_market_overview` output, not estimated.
- After-hours block is omitted entirely on Fridays per the after-market-report prompt's Friday Rule.

## Development Practices
- Use `/tdd-developer` skill for all code implementation (Test-Driven Development)
- Follow the Red → Green → Refactor cycle: write tests first, then implement

## Date and Market Calendar Verification (MANDATORY)
- Always verify today's date with the `date` command at the start of every session
- Before mentioning holidays or market closures, ALWAYS verify with calendar calculation:
  ```bash
  python3 -c "import calendar; print(calendar.month(YYYY, MM))"
  ```
- US market holidays with variable dates (must calculate, never guess):
  - Presidents' Day: 3rd Monday of February
  - MLK Day: 3rd Monday of January
  - Memorial Day: Last Monday of May
  - Labor Day: 1st Monday of September
  - Thanksgiving: 4th Thursday of November
- Never assume a date is a holiday without verifying the day of week first

## Commands
- Market analysis commands can be run without explicit permission
- Report generation workflows are pre-approved for automation
- Always verify today's date with the `date` command before generating reports
