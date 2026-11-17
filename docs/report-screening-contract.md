# Report screening availability

Read this contract before generating either daily report. It applies to HTML,
X copy, candidate JSON, and the after-market screening sidecar. Check the active
MCP tool schema before calling tools; do not invent parameters.

## Establish the state before choosing a report branch

- `ok`: the configured screener succeeded, returned valid current rows, and its
  response/schema and requested session/filter can be verified. Record every
  returned ticker, including lower grades, in `screening.tickers`.
- `empty`: a successful, authenticated, schema-valid response establishes an
  explicit zero count for the **same configured screen and session**. Record
  the evidence (source/tool, fetch timestamp, session, filters and explicit count).
  If the MCP text only says “No stocks found”, or is a bare empty list, confirm
  against the authenticated same-filter Finviz result showing zero, or provider
  metadata proving successful parsing and zero total rows. A working unrelated
  tool, a general earnings calendar, HTTP 200, or no visible rows does not prove
  this screen has zero matches. If verification is inaccessible, use `unavailable`.
- `unavailable`: authentication/login/403, schema/argument/parse errors, exceptions,
  timeout, stale/wrong-session results, or an unverifiable empty response. Record
  the reason and attempted verification. Never write zero counts, “No stocks
  available”, or “no after-hours earnings” for this state. Repeating the same
  ambiguous response does not establish zero. A bounded retry is allowed.
- `skipped_friday`: only the after-market after-hours screen, only on Fridays.
  Do not call the after-hours screen. This is an intentional skip, not zero.

Preserve the provider response/evidence in the job log and summarize it in the
artifact. Do not log cookies, API keys, login HTML, or credentials. Evidence is
reporter-supplied; the offline gate validates consistency, not remote authenticity.

## Artifact contract

All newly generated artifacts include `report_date` and this object:

```json
{
  "screening": {
    "source": "earnings_trading_screener",
    "status": "ok",
    "evidence": "Fetched <timestamp>; session <date>; configured filters; authenticated schema-valid response, 1 row",
    "tickers": ["AAPL"]
  }
}
```

For earnings, embed it in `earnings_trade_candidates_YYYY-MM-DD.json`. `source`
is exactly `earnings_trading_screener`. Candidates must be exactly the returned
universe, each once; never add replacements from calendars, news, other screeners
or web searches. `empty` has `tickers: []` and `candidates: []`. `unavailable` has
`tickers: []` and **`candidates: null`**, overwriting any older same-day candidate
file. Do not leave old actionable candidates in place. An unavailable run must
still write fresh HTML, JSON, and X warning artifacts; do not fabricate scores.

For after-market, write `reports/YYYY-MM-DD-after-market-screening.json` with
`report_date` and `screening` (no candidate list). `source` is exactly
`earnings_afterhours_screener`. Use `tickers: []` for empty, unavailable, or Friday
skip. On Friday, evidence must state the date and the intentional Friday rule.

## Human-facing behavior and links

- Earnings `ok`: score all screened candidates; ticker-list screener links contain
  only actual candidates. `empty`: HTML and X say “No stocks available for earnings
  trading.” State that zero was verified, with timestamp and filter scope.
- Earnings `unavailable`: HTML and X prominently say **“Screening unavailable;
  candidate count unknown.”** Include a concise reason. No picks or invented count.
- After-market `ok`: show verified after-hours results normally. `empty`: apply the
  existing Mon–Thu fallback: today's verified BMO impact plus **This Week's Remaining
  Earnings Calendar**, next week secondary only when appropriate on Thu/Fri. Zero
  qualifying gainers does not mean zero earnings releases.
- After-market `unavailable`: keep a visible **“After-hours screening unavailable;
  matching count unknown.”** notice in HTML and X. Do not enter the valid-empty
  fallback or silently omit this notice. Independent verified calendar information
  may be labeled separately; it cannot establish the missing screen's count.
- Friday: omit the after-hours section and X block; show only the verified Next
  Week's Major Earnings Calendar per the existing rule. Calendar fetch failures
  are labeled unavailable rather than represented as an empty calendar.
- For empty/unavailable, remove ticker-list URL templates (`t=`, `SYMBOLS`,
  `{TICKER1}`, etc.). Use a labeled [Finviz home](https://finviz.com/) source link
  and an [availability issue](https://github.com/tradermonty/claude-market-agents/issues/46)
  link for unavailable. These links are references, not candidate recommendations.

## Execution gate and compatibility

Both shell entry points require a fresh status artifact and validate it before
publishing, emailing a success report, or writing the success marker. Unavailable,
missing, wrong-date, or inconsistent metadata fails the attempt (exit 127), uses
the existing bounded retries, and ultimately enters the existing alert path.
Fresh files after a timeout pass the same validation; freshness alone is insufficient.
Forced regeneration clears the previous success marker before attempting new data.
Warning HTML/X artifacts remain local for diagnosis and are not auto-published.

Candidate readers accept legacy files with no screening metadata. When metadata
is present they reject unavailable/inconsistent states, so `candidates: null` or
an explicit unavailable state cannot become a successful zero-candidate run.

## Offline verification

Run `pytest tests/test_report_screening.py` for both shell entry points using
fixture-producing Claude stubs and local publish/email substitutes. This checks
gating and artifact consistency without sending mail, publishing, invoking the
LLM, or calling Finviz. Prompt wording, Friday/fallback policy, and links are
checked separately. Real provider/LLM behavior still requires a live acceptance
run after deployment; these fixtures do not prove provider availability.
