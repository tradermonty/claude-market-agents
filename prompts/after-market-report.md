## Overview

This prompt automates the creation of a **visual infographic and an X post** after the U.S. stock market closes by analyzing the day’s market action, volume‑surge tickers, and **after‑hours moves driven by post‑close earnings**.

---

## Required screening availability contract

Read and follow `docs/report-screening-contract.md` first. Classify the after-hours
screen as verified `ok`, confirmed `empty`, `unavailable`, or `skipped_friday`.
Authentication/schema errors and unverifiable empty responses are `unavailable`,
not zero. Write `reports/YYYY-MM-DD-after-market-screening.json` with `report_date`
and the `screening` object (source `earnings_afterhours_screener`). Display a visible
availability warning in HTML and X when unavailable; never take the empty fallback.
Use real source links without placeholder tickers. On Friday preserve the skip rule.

## Execution Steps

### 1. Fetch Market Data

```text
Retrieve data in the following order:

[Core Market Data]
1. `finviz:get_market_overview` – high‑level market overview & key ETF data
2. `finviz:volume_surge_screener` – detect volume‑surge tickers
3. `finviz:get_sector_performance` – sector performance table
4. `alpaca:get_stock_snapshot` – detailed data for major ETFs (SPY, QQQ, DIA, IWM, TLT, GLD)
5. `alpaca:get_stock_snapshot` – detailed data for the top volume‑surge tickers

[Earnings‑Related Data]
6. `finviz:earnings_afterhours_screener` – tickers up after earnings in after‑hours
7. `finviz:earnings_screener(earnings_date="today_after")` – companies scheduled to report after today’s close
8. `finviz:get_stock_news` – news for earnings tickers
9. `finviz:upcoming_earnings_screener` – today's earnings calendar with details

[Claude Trading Skills – MANDATORY, see §4a]
10. `sector-analyst` skill – load it with the Skill tool, then run its script:
    python3 ~/.claude/skills/sector-analyst/scripts/analyze_sector_rotation.py --json
    (public uptrend-ratio CSV, no API key, a few seconds)
11. `earnings-calendar` skill – its FMP script is pre-fetched by the wrapper
    (it takes ~6 min, too long for one Bash call). Read the JSON path given in
    the task prompt. Do NOT re-run the script yourself.
```

### 2. Analyze After‑Hours Trading

```text
For each earnings ticker:

1. **After‑hours price action**
   • Compare regular‑session close vs. latest after‑hours price
   • Calculate % change in after‑hours
   • Check after‑hours volume

2. **Earnings surprise**
   • Actual EPS vs. consensus
   • Actual revenue vs. consensus
   • Guidance commentary

3. **News & catalysts**
   • Headlines tied to the earnings release
   • Analyst notes
   • Company press releases
```

### 3. Data Processing & Calculations

```text
[Regular‑Session Data]
• % change for major ETFs
• % change for volume‑surge tickers
• Top tickers ranked by % change
• Sector performance ranking
• Market stats (number of volume‑surge tickers, avg. % move, etc.)

[After‑Hours Data]
• % change for earnings tickers
• Tickers moving ±5 % in after‑hours
• Surprise percentages
• After‑hours volume analysis
```

### 4. Infographic Generation

#### Design Requirements

* **Responsive**: mobile & desktop
* **Color theme**: dark‑blue gradients
* **Visual effects**: glassmorphism, hover, animations
* **Readability**: high contrast, legible fonts
* **Dedicated earnings area** for after‑hours data

#### Mandatory Sections

1. **Header**

   * Title: “🇺🇸 U.S. Stock Market Analysis”
   * Date: “📅 YYYY‑MM‑DD – Final After‑Market Data”

2. **Major ETF Performance**

   * SPY, QQQ, DIA, IWM, TLT, GLD – price & % change
   * Six cards in a 3×2 grid

3. **Top Volume‑Surge Tickers**

   * Show top 5 (symbol, name, % change, volume) ordered by % change

4. **🆕 Post‑Close Earnings & After‑Hours**

   * **Friday Rule**: On Fridays, **skip this entire section** (and earnings‑related Steps 6‑8 in data fetching; still run Step 9, whose BMO/AMC times feed the calendar box). Very few companies report after Friday’s close, and the screener returns stale data from the previous day. Instead, include only a “Next Week’s Major Earnings Calendar” box built from the earnings-calendar skill JSON (Finviz `upcoming_earnings_screener` fallback; see 4a).
   * **Mon–Thu with after‑hours results**: Show after‑hours performance of today’s reporters, EPS/Revenue surprises, tickers moving ±10 %, and highlight related news.
   * **Mon–Thu with NO after‑hours results (fallback)**: Only when `earnings_afterhours_screener` is confirmed `empty` under the availability contract, do NOT jump to next week’s earnings. Instead:
     1. Note any BMO (before‑market‑open) earnings that reported TODAY and their market‑session impact
     2. Show **“This Week’s Remaining Earnings Calendar”** from the earnings-calendar skill JSON, which already covers tomorrow through Friday (Finviz `upcoming_earnings_screener` fallback; see 4a)
     3. Only show “Next Week’s Major Earnings” as a secondary section if space permits and the current week is nearly over (Thu/Fri)

4a. **🧭 Sector Rotation (sector-analyst skill)** – full-width card placed right after the Sector Performance card

   * From the script's JSON: risk regime and score (cyclical vs defensive), cycle phase and confidence, top 3 and bottom 3 sectors by uptrend ratio, any overbought/oversold flags
   * Show the data date from `meta.freshness.date` (the CSV usually lags 1–2 trading days; say so). If `is_fresh` is false, show the warning
   * This complements, not replaces, today's Finviz sector % table: uptrend ratio = share of stocks in each sector in an uptrend
   * If the script fails, omit the card and drop its footer link (do not fabricate)

   **Earnings calendar box (earnings-calendar skill)** – used for the Friday "Next Week" box and the Mon–Thu "This Week's Remaining" box in section 4
   * Primary source: the skill's JSON (symbol, companyName, date, timing BMO/AMC/TAS, marketCapFormatted, epsEstimated, revenueEstimated). Show the largest names per day (≈6–8), with EPS estimate where space allows
   * FMP often returns `TAS` (time not announced). Where Finviz `upcoming_earnings_screener` shows a BMO/AMC time for the same ticker, use the Finviz time
   * Fallback: if the task prompt says the skill output is not available, or the JSON is empty/unreadable, build the box from `finviz:upcoming_earnings_screener` as before and drop the earnings-calendar footer link

5. **Market Statistics**

   * # of volume‑surge tickers
   * # of up‑trend tickers
   * Avg. relative volume
   * Avg. price move
   * 🆕 # of earnings releases

6. **Sector Performance**

   * All sectors’ % change
   * Display top 6; include market‑cap & ticker count

7. **Today’s Key Points**

   * Hot sectors
   * Volume‑surge characteristics
   * 🆕 Earnings highlights
   * 🆕 After‑hours focal points
   * Broad market trend
   * Bonds & gold moves

8. **Footer**

   * Data sources
   * Last refreshed time
   * Note: “Final after‑market data + after‑hours info”
   * **Skills line (MANDATORY whenever at least one skill's output was used; last line of the footer)** – one centered line, list only the skills whose output was actually used today (drop a skill that failed or fell back; omit the line if both did):

     ```html
     <p class="skills-line">Built with Claude trading skills: <a href="https://tradermonty.github.io/claude-trading-skills/en/skills/sector-analyst/" target="_blank" rel="noopener">Sector Analyst</a> · <a href="https://tradermonty.github.io/claude-trading-skills/en/skills/earnings-calendar/" target="_blank" rel="noopener">Earnings Calendar</a> — try them in Claude →</p>
     ```

     Also add the skill name as a small source note under each card that used it (e.g. “Source: Sector Analyst skill · uptrend data as of YYYY‑MM‑DD”)

#### 🆕 After‑Hours Section Styles

```css
/* Earnings & After‑Hours styles */
.afterhours-section {
    background: linear-gradient(135deg, #ff6b6b 0%, #ffa500 100%);
    border-left: 5px solid #ffff00; /* after‑hours accent */
}

.earnings-card {
    background: rgba(255, 255, 255, 0.2);
    border: 1px solid rgba(255, 255, 255, 0.3);
    position: relative;
}

.afterhours-badge {
    position: absolute;
    top: -10px;
    right: -10px;
    background: #ff4444;
    color: #fff;
    padding: 5px 10px;
    border-radius: 15px;
    font-size: 0.8em;
    font-weight: bold;
}

.earnings-surprise {
    display: flex;
    justify-content: space-between;
    margin: 10px 0;
}

.surprise-positive { color: #00ff88; }
.surprise-negative { color: #ff6b6b; }
```

#### Styling Guidelines

```css
/* Color palette */
– Base: dark blue (#1e3c72 → #2a5298)
– Accent: gradient per section
– 🆕 After‑hours: orange‑red (#ff6b6b → #ffa500)
– Up: bright green #00ff88 + shadow
– Down: bright red #ff6b6b + shadow
– Card bg: rgba(255,255,255,0.15)

/* Layout */
– Main grid: 2 columns (1 column on mobile)
– 🆕 After‑hours: full‑width section
– Card gap: 30 px
– Inner padding: 30 px
– Border‑radius: 20 px
– Shadow: 0 10 px 30 px rgba(0,0,0,0.3)
```

### 5. X Post Generator (Single Post – MANDATORY)

> **IMPORTANT**: X投稿は必ず**1つのシングルポスト**にまとめること。スレッド形式（複数投稿への分割）は禁止。
> X Premium long-form を前提とするため 280 文字制限に切り詰めない。下記テンプレートの情報密度を維持すること。

#### Canonical Reference

* **正本テンプレート**: `reports/2026-05-26-after-market-x-post.md` （以降のX投稿はこの構造に合わせる）

#### Template (Single Combined Post)

```text
🇺🇸 US Market Close (Mon DD) — <Short Context Tagline>
$SPY ±X.XX% | $QQQ ±X.XX% | $DIA ±X.XX% | $IWM ±X.XX%
$TLT ±X.XX% | $GLD ±X.XX%

🔥 Top Volume-Surge Movers:
$SYM1 +XX.XX% 🚀 | $SYM2 +XX.XX% | $SYM3 +XX.XX%
$SYM4 +XX.XX% | $SYM5 +XX.XX% (verified catalyst)
$SYM6 +XX.XX% | $SYM7 +XX.XX% | $SYM8 +XX.XX%

🌙 After-Hours Earnings (N/N beat EPS):
$STAR +XX.XX% AH 🚀 (EPS +XX.XX%, Sales +XX% QoQ)
$ER2 +XX.XX% AH (EPS beat +X.XX%) | $ER3 +XX.XX% AH
$ER4 +XX.XX% AH | $ER5 +XX.XX% AH (EPS +XX.XX%)

📊 Sectors: SectorA +X.XX%, SectorB +X.XX%, SectorC +X.XX%
SectorX -X.XX%, SectorY -X.XX%, SectorZ -X.XX%
NN volume-surge stocks | NNN uptrend stocks | Avg move +X.X%

🗓️ <Next-session> earnings: $TICK1 $TICK2 $TICK3   ← optional, verified only

#StockMarket #MarketAnalysis #EarningsSeason #AfterHours #<TickerOrTheme>
```

#### Section-by-Section Rules

| Block | Rule |
|-------|------|
| Header tagline | Required when day has a clear narrative; one short phrase (≤6 words). Omit only on flat/quiet days. |
| ETFs line 1 | Exactly 4 indices in this order: SPY, QQQ, DIA, IWM. |
| ETFs line 2 | TLT + GLD. Add brief `(commentary)` only if move ≥1% and reason is verifiable. |
| 🔥 Volume-Surge | 6–9 tickers across 2–3 lines, pipe-separated. Add 🚀 only on the top mover. Parentheticals only when catalyst is verifiable from fetched data (sector tag, EPS surprise, news headline). **Never invent catalysts.** |
| 🌙 After-Hours | Omit entirely on Fridays or when `earnings_afterhours_screener` is confirmed `empty`; for `unavailable`, show the availability warning in this block. Standout on its own line with EPS surprise + (optional) Sales QoQ. Remaining tickers grouped on pipe-separated lines with short surprise data where space permits. |
| 📊 Sectors | Line 1 = top 3 winners; Line 2 = bottom 2–3 losers. Pull from `get_sector_performance`. |
| Stats | `NN volume-surge stocks | NNN uptrend stocks | Avg move +X.X%` — counts/avg from `get_market_overview`. |
| 🗓️ Next-session earnings | Optional; include only tickers verified through `upcoming_earnings_screener`. Skip if uncertain. |
| Hashtags | 5–7 total. Core set: `#StockMarket #MarketAnalysis #EarningsSeason`. Situational adds: `#AfterHours`, `#AIStocks`, `#Semiconductors`, ticker tags (`#SNOW`, `#MRVL`). |

#### Hashtags

* **Core (always include all 3)**: #StockMarket #MarketAnalysis #EarningsSeason
* **Situational**: #AfterHours #AIStocks #Semiconductors #VolumeAnalysis
* **Ticker / theme tags**: 1–2 max, only when truly relevant (e.g., #SNOW on a 30%+ AH day)

### 6. Quality Checklist (Earnings Edition)

**Data Accuracy**

* [ ] All % changes calculated vs. prior close
* [ ] 🆕 After‑hours % changes use regular‑close baseline
* [ ] 🆕 Earnings surprise % correct (actual vs. consensus)
* [ ] Volume data reflects post‑close snapshots
* [ ] Sector classifications correct
* [ ] 🆕 Earnings timestamps recorded accurately

**After‑Hours Data Integrity**

* [ ] 🆕 Prices reflect latest move post‑announcement
* [ ] 🆕 After‑hours volume separated from regular volume
* [ ] 🆕 EPS / revenue / guidance data accurate
* [ ] 🆕 News items truly related to earnings

**Visual Quality**

* [ ] Text readable (contrast OK)
* [ ] 🆕 After‑hours section visually distinct
* [ ] 🆕 Surprise metrics color‑coded clearly
* [ ] Mobile layout intact
* [ ] Hover effects work
* [ ] Color rules consistent (up = green, down = red)

**Post Quality** (canonical reference: `reports/2026-05-26-after-market-x-post.md`)

* [ ] Single post (no thread split); X Premium long-form OK — do NOT truncate to 280 chars
* [ ] Section order matches template: Header → ETFs(×2) → 🔥 Movers → 🌙 AH Earnings → 📊 Sectors → Stats → 🗓️ (opt) → Hashtags
* [ ] Header tagline reflects day's narrative (or omitted for genuinely flat days)
* [ ] ETF line 1 has SPY/QQQ/DIA/IWM in that order; line 2 has TLT/GLD
* [ ] 🔥 Movers block: 6–9 tickers across 2–3 lines, 🚀 only on top mover
* [ ] Parenthetical catalysts trace to verifiable data (no invented narratives)
* [ ] 🌙 After-hours omitted on Fridays / when screener is confirmed `empty`; unavailable must show a warning
* [ ] 📊 Sectors: 2 lines (top 3 winners + bottom 2–3 losers)
* [ ] Stats line includes volume-surge count, uptrend count, avg move
* [ ] All tickers prefixed with `$`
* [ ] Percentages correct (recomputed vs. prior close)
* [ ] Hashtags: 5–7 total with all 3 core tags
* [ ] 🆕 Regular vs. after‑hours clearly separated
* [ ] 🆕 EPS / Sales surprise data accurate vs. consensus

### 7. Error Handling (Earnings Edition)

| Common Issue                     | Mitigation                                                                                               |
| -------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Mixing daily & weekly data       | Always reference prior close; re‑fetch if uncertain                                                      |
| 🆕 After‑hours fetch failure     | Record `unavailable`, show the warning in HTML/X, and fail the success gate; do not infer zero from a schedule                  |
| 🆕 Earnings data mismatch        | Double‑check consensus figures; watch for early/late releases; distinguish preliminary vs. final numbers |
| Market holiday                   | Use last trading day’s data; match date labels                                                           |
| 🆕 Misreading after‑hours volume | Separate regular vs. after‑hours; flag abnormally low volume                                             |
| Layout breakage                  | Test media queries; adjust after‑hours section; truncate long company names                              |

### 8. Sample Invocation (Earnings Edition)

```text
Prompt example:
“Analyze today’s U.S. market after the close, including post‑earnings after‑hours moves, and generate both an infographic and X post.”

Expected output:
1. HTML infographic with final market data
   – Regular‑session summary
   – 🆕 After‑hours & earnings section
   – 🆕 Surprise metrics
2. Markdown X post with earnings info
3. 🆕 Commentary on biggest after‑hours movers
4. Narrative on key market trends
```

### 🆕 9. Key Earnings Metrics

**Surprise Calculations**

```text
EPS Surprise  = (Actual EPS  − Consensus EPS)  / Consensus EPS × 100
Revenue Surprise = (Actual Rev − Consensus Rev) / Consensus Rev × 100
Guidance Change  = % upward / downward revision for next quarter or year
```

**After‑Hours Reaction**

```text
Immediate: price move in first 30 min post‑release
Sustained: move over 2‑3 hours post‑release
Volume: after‑hours volume vs. normal after‑hours average
```

**Notable Patterns**

```text
Beat & Raise  = EPS beat + guidance raised
Miss & Lower  = EPS miss + guidance cut
Beat & Flat   = Good EPS but guidance flat
Mixed         = EPS strong, revenue soft (or vice‑versa)
```

## Notes (Earnings Edition)

* Use data **after 16:00 ET** (market close)
* 🆕 Earnings usually drop post‑16:00; time after‑hours fetch accordingly
* 🆕 After‑hours liquidity is thin; large moves may have low volume
* Keep real‑time and post‑close data clearly separated
* 🆕 Display regular vs. after‑hours data separately
* Provide information only (not financial advice)
* List data sources precisely
* 🆕 Flag that earnings figures may be preliminary

### 🆕 10. Implementation Snippets

```javascript
// After‑hours % change
const afterHoursChange = ((afterHoursPrice - regularClose) / regularClose * 100).toFixed(2);

// Earnings surprise
const epsSurprise      = ((actualEPS - consensusEPS) / consensusEPS * 100).toFixed(1);
const revenueSurprise  = ((actualRevenue - consensusRevenue) / consensusRevenue * 100).toFixed(1);

// After‑hours volume ratio
const afterHoursVolRatio = (afterHoursVolume / averageAfterHoursVolume).toFixed(1);
```

```html
<div class="afterhours-section">
    <h2>⏰ Post‑Close Earnings & After‑Hours</h2>
    <div class="earnings-grid">
        <div class="earnings-card">
            <div class="afterhours-badge">After‑Hours</div>
            <div class="symbol">$AAPL</div>
            <div class="afterhours-change positive">+5.2%</div>
            <div class="earnings-surprise">
                <span>EPS: <span class="surprise-positive">+8.3%</span></span>
                <span>Revenue: <span class="surprise-positive">+2.1%</span></span>
            </div>
            <div class="earnings-volume">After‑Hours Volume: 2.3 M</div>
        </div>
    </div>
</div>
```

### 🆕 11. Tool Usage Examples

```text
# Screen tickers up after earnings in after‑hours
finviz:earnings_afterhours_screener()

# Check today's earnings calendar
finviz:upcoming_earnings_screener()

# Fetch earnings news
finviz:get_stock_news(tickers="AAPL,MSFT", news_type="earnings")

# Latest snapshot including after‑hours
alpaca:get_stock_snapshot(symbol_or_symbols=["AAPL"])
```

With this expanded prompt you can generate a fully integrated **post‑close report** that covers earnings and after‑hours action end‑to‑end.
