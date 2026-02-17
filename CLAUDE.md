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
- Use `reports/2026-01-27-after-market-xpost-combined.md` as template reference
- Condense major indices, top movers, after-hours, sectors, and market stats into one post
- File naming: `reports/YYYY-MM-DD-after-market-x-post.md`

## Development Practices
- Use `/tdd-developer` skill for all code implementation (Test-Driven Development)
- Follow the Red → Green → Refactor cycle: write tests first, then implement

## Commands
- Market analysis commands can be run without explicit permission
- Report generation workflows are pre-approved for automation
- Always verify today's date with the `date` command before generating reports
