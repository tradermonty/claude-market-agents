#!/usr/bin/env python3
"""
Report generator for earnings trade backtest.

Generates:
- HTML report with Plotly charts and interactive tables
- CSV files for trades and skipped trades
"""

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List

import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

from backtest.trade_simulator import TradeResult, SkippedTrade
from backtest.metrics_calculator import BacktestMetrics

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generate HTML + CSV backtest reports."""

    def generate(
        self,
        metrics: BacktestMetrics,
        trades: List[TradeResult],
        skipped: List[SkippedTrade],
        output_dir: str,
        config: dict = None,
    ):
        """Generate all output files."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        self._write_trades_csv(trades, out / "earnings_trade_backtest_trades.csv")
        self._write_skipped_csv(skipped, out / "earnings_trade_backtest_skipped.csv")
        self._write_html_report(metrics, trades, out / "earnings_trade_backtest_result.html", config)

        logger.info(f"Reports written to {out}")

    # ------------------------------------------------------------------ CSV
    def _write_trades_csv(self, trades: List[TradeResult], path: Path):
        if not trades:
            return
        fields = [
            'ticker', 'grade', 'grade_source', 'score', 'report_date',
            'entry_date', 'entry_price', 'exit_date', 'exit_price',
            'shares', 'invested', 'pnl', 'return_pct', 'holding_days',
            'exit_reason', 'gap_size', 'company_name',
        ]
        with open(path, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for t in sorted(trades, key=lambda x: x.entry_date):
                row = {k: getattr(t, k) for k in fields}
                row = {k: (v if v is not None else '') for k, v in row.items()}
                w.writerow(row)
        logger.info(f"Wrote {len(trades)} trades to {path}")

    def _write_skipped_csv(self, skipped: List[SkippedTrade], path: Path):
        if not skipped:
            return
        fields = ['ticker', 'report_date', 'grade', 'score', 'skip_reason']
        with open(path, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for s in sorted(skipped, key=lambda x: x.report_date):
                row = {k: getattr(s, k) for k in fields}
                row = {k: (v if v is not None else '') for k, v in row.items()}
                w.writerow(row)
        logger.info(f"Wrote {len(skipped)} skipped trades to {path}")

    # ------------------------------------------------------------------ HTML
    def _write_html_report(self, m: BacktestMetrics, trades: List[TradeResult], path: Path, config: dict = None):
        cfg = config or {}
        generated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Build chart JSONs
        cumulative_chart = self._cumulative_pnl_chart(trades)
        grade_chart = self._grade_bar_chart(m)
        scatter_chart = self._score_return_scatter(trades)
        gap_chart = self._gap_size_chart(m)
        monthly_chart = self._monthly_chart(m)
        distribution_chart = self._return_distribution(trades)

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Earnings Trade Backtest Results</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
:root {{
    --bg: #0d1117; --bg2: #161b22; --bg3: #21262d;
    --text: #e6edf3; --text2: #8b949e; --border: #30363d;
    --green: #3fb950; --red: #f85149; --blue: #58a6ff;
    --yellow: #d29922; --purple: #bc8cff;
    --grade-a: #3fb950; --grade-b: #58a6ff;
    --grade-c: #d29922; --grade-d: #f85149;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 20px; }}
.container {{ max-width: 1400px; margin: 0 auto; }}
h1 {{ font-size: 1.8em; margin-bottom: 8px; }}
.subtitle {{ color: var(--text2); margin-bottom: 24px; }}
.kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }}
.kpi {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 16px; text-align: center; }}
.kpi .label {{ font-size: 0.75em; color: var(--text2); text-transform: uppercase; letter-spacing: 0.5px; }}
.kpi .value {{ font-size: 1.6em; font-weight: 700; margin-top: 4px; }}
.kpi .value.positive {{ color: var(--green); }}
.kpi .value.negative {{ color: var(--red); }}
.section {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 20px; }}
.section h2 {{ font-size: 1.2em; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
.chart {{ width: 100%; min-height: 400px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.85em; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
th {{ background: var(--bg3); color: var(--text2); font-weight: 600; position: sticky; top: 0; cursor: pointer; }}
th:hover {{ color: var(--text); }}
tr:hover {{ background: var(--bg3); }}
.positive {{ color: var(--green); }}
.negative {{ color: var(--red); }}
.grade-a {{ color: var(--grade-a); font-weight: 700; }}
.grade-b {{ color: var(--grade-b); font-weight: 700; }}
.grade-c {{ color: var(--grade-c); font-weight: 700; }}
.grade-d {{ color: var(--grade-d); font-weight: 700; }}
.stat-test {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
.stat-box {{ background: var(--bg3); border-radius: 8px; padding: 16px; }}
.stat-box .label {{ font-size: 0.8em; color: var(--text2); }}
.stat-box .value {{ font-size: 1.2em; font-weight: 600; margin-top: 4px; }}
.config {{ font-size: 0.8em; color: var(--text2); margin-top: 20px; padding: 12px; background: var(--bg3); border-radius: 8px; }}
.scrollable {{ max-height: 600px; overflow-y: auto; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75em; font-weight: 600; }}
.badge-stop {{ background: rgba(248,81,73,0.2); color: var(--red); }}
.badge-hold {{ background: rgba(63,185,80,0.2); color: var(--green); }}
.badge-eod {{ background: rgba(210,153,34,0.2); color: var(--yellow); }}
</style>
</head>
<body>
<div class="container">
<h1>Earnings Trade Backtest Results</h1>
<p class="subtitle">Generated: {generated} | Period: {self._trade_period(trades)} | Independent Trade Model</p>

<!-- KPI Dashboard -->
<div class="kpi-grid">
  <div class="kpi"><div class="label">Total Trades</div><div class="value">{m.total_trades}</div></div>
  <div class="kpi"><div class="label">Win Rate</div><div class="value {'positive' if m.win_rate >= 50 else 'negative'}">{m.win_rate:.1f}%</div></div>
  <div class="kpi"><div class="label">Total P&L</div><div class="value {'positive' if m.total_pnl >= 0 else 'negative'}">${m.total_pnl:,.0f}</div></div>
  <div class="kpi"><div class="label">Profit Factor</div><div class="value {'positive' if m.profit_factor >= 1 else 'negative'}">{m.profit_factor:.2f}</div></div>
  <div class="kpi"><div class="label">Trade Sharpe*</div><div class="value">{m.trade_sharpe:.2f}</div></div>
  <div class="kpi"><div class="label">Max Drawdown</div><div class="value negative">${m.max_drawdown:,.0f}</div></div>
  <div class="kpi"><div class="label">Avg Return</div><div class="value {'positive' if m.avg_return >= 0 else 'negative'}">{m.avg_return:.1f}%</div></div>
  <div class="kpi"><div class="label">Skipped</div><div class="value">{m.total_skipped}</div></div>
</div>
<p style="font-size:0.75em; color:var(--text2); margin-bottom:16px;">*Trade Sharpe = mean(returns)/std(returns) per trade. Independent trade assumption; not annualized.</p>

<!-- Cumulative PnL -->
<div class="section">
  <h2>Cumulative P&L Curve</h2>
  <div id="cumulative-chart" class="chart"></div>
</div>

<!-- Grade Performance -->
<div class="section">
  <h2>Grade Performance (HTML-sourced grades only)</h2>
  <div id="grade-chart" class="chart"></div>
  {self._grade_table_html(m.grade_metrics_html_only, "Primary (HTML grade_source)")}
  <br>
  <details><summary style="cursor:pointer; color:var(--text2);">Show All Grades (including inferred)</summary>
  {self._grade_table_html(m.grade_metrics, "All Grades")}
  </details>
</div>

<!-- Statistical Test -->
{self._stat_test_html(m.ab_vs_cd_test)}

<!-- Score vs Return Scatter -->
<div class="section">
  <h2>Score vs Return (Scoring Effectiveness)</h2>
  <p style="color:var(--text2); font-size:0.85em; margin-bottom:12px;">
    Pearson r = {m.score_return_correlation:.4f} (p = {m.score_return_p_value:.4f})
  </p>
  <div id="scatter-chart" class="chart"></div>
</div>

<!-- Score Range Breakdown -->
<div class="section">
  <h2>Score Range Performance</h2>
  {self._score_range_table_html(m.score_range_metrics)}
</div>

<!-- Gap Size Breakdown -->
<div class="section">
  <h2>Gap Size Performance</h2>
  <div id="gap-chart" class="chart"></div>
  {self._gap_size_table_html(m.gap_size_metrics)}
</div>

<!-- Monthly Returns -->
<div class="section">
  <h2>Monthly Returns</h2>
  <div id="monthly-chart" class="chart"></div>
  {self._monthly_table_html(m.monthly_metrics)}
</div>

<!-- Return Distribution -->
<div class="section">
  <h2>Return Distribution</h2>
  <div id="dist-chart" class="chart"></div>
</div>

<!-- Stop Loss Analysis -->
<div class="section">
  <h2>Stop Loss Analysis</h2>
  <p>Overall Stop Rate: <strong class="negative">{m.stop_loss_rate:.1f}%</strong> ({m.stop_loss_total}/{m.total_trades})</p>
  {self._stop_loss_grade_table(m.grade_metrics_html_only)}
</div>

<!-- Skip Breakdown -->
<div class="section">
  <h2>Skipped Trades ({m.total_skipped})</h2>
  {self._skip_table_html(m.skip_reasons)}
</div>

<!-- All Trades Table -->
<div class="section">
  <h2>All Trades ({m.total_trades})</h2>
  <div class="scrollable">
  {self._trades_table_html(trades)}
  </div>
</div>

<!-- Config -->
<div class="config">
  <strong>Configuration:</strong>
  Position Size: ${cfg.get('position_size', 10000):,} |
  Stop Loss: {cfg.get('stop_loss', 10)}% |
  Slippage: {cfg.get('slippage', 0.5)}% |
  Max Holding: {cfg.get('max_holding', 90)} days |
  Min Grade: {cfg.get('min_grade', 'D')}
</div>

</div>

<script>
{cumulative_chart}
{grade_chart}
{scatter_chart}
{gap_chart}
{monthly_chart}
{distribution_chart}
{self._sortable_table_js()}
</script>
</body>
</html>"""
        path.write_text(html, encoding='utf-8')
        logger.info(f"HTML report written to {path}")

    # ------------------------------------------------------------------ Charts
    def _cumulative_pnl_chart(self, trades: List[TradeResult]) -> str:
        sorted_t = sorted(trades, key=lambda t: t.entry_date)
        dates = []
        cum_pnl = []
        running = 0
        for t in sorted_t:
            running += t.pnl
            dates.append(t.entry_date)
            cum_pnl.append(round(running, 2))

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=dates, y=cum_pnl, mode='lines',
            line=dict(color='#58a6ff', width=2),
            fill='tozeroy',
            fillcolor='rgba(88,166,255,0.1)',
            name='Cumulative P&L',
        ))
        fig.update_layout(
            template='plotly_dark', paper_bgcolor='#161b22', plot_bgcolor='#0d1117',
            margin=dict(l=60, r=20, t=20, b=40),
            xaxis_title='Entry Date', yaxis_title='Cumulative P&L ($)',
            yaxis_tickprefix='$', height=400,
        )
        return f"Plotly.newPlot('cumulative-chart', {fig.to_json()});"

    def _grade_bar_chart(self, m: BacktestMetrics) -> str:
        grades = [g for g in m.grade_metrics_html_only if g.count > 0]
        colors = {'A': '#3fb950', 'B': '#58a6ff', 'C': '#d29922', 'D': '#f85149'}

        fig = make_subplots(rows=1, cols=2, subplot_titles=('Win Rate by Grade', 'Avg Return by Grade'))
        for g in grades:
            fig.add_trace(go.Bar(
                x=[g.grade], y=[g.win_rate], name=g.grade,
                marker_color=colors.get(g.grade, '#8b949e'),
                text=[f'{g.win_rate:.1f}%'], textposition='auto',
                showlegend=False,
            ), row=1, col=1)
            fig.add_trace(go.Bar(
                x=[g.grade], y=[g.avg_return], name=g.grade,
                marker_color=colors.get(g.grade, '#8b949e'),
                text=[f'{g.avg_return:.1f}%'], textposition='auto',
                showlegend=False,
            ), row=1, col=2)
        fig.update_layout(
            template='plotly_dark', paper_bgcolor='#161b22', plot_bgcolor='#0d1117',
            margin=dict(l=60, r=20, t=40, b=40), height=350,
        )
        return f"Plotly.newPlot('grade-chart', {fig.to_json()});"

    def _score_return_scatter(self, trades: List[TradeResult]) -> str:
        colors = {'A': '#3fb950', 'B': '#58a6ff', 'C': '#d29922', 'D': '#f85149'}
        fig = go.Figure()
        for grade in ['A', 'B', 'C', 'D']:
            group = [t for t in trades if t.grade == grade and t.score is not None]
            if group:
                fig.add_trace(go.Scatter(
                    x=[t.score for t in group],
                    y=[t.return_pct for t in group],
                    mode='markers', name=f'Grade {grade}',
                    marker=dict(color=colors[grade], size=6, opacity=0.7),
                    text=[f'{t.ticker} ({t.report_date})' for t in group],
                    hovertemplate='%{text}<br>Score: %{x:.1f}<br>Return: %{y:.1f}%<extra></extra>',
                ))
        fig.update_layout(
            template='plotly_dark', paper_bgcolor='#161b22', plot_bgcolor='#0d1117',
            margin=dict(l=60, r=20, t=20, b=40),
            xaxis_title='Score', yaxis_title='Return (%)',
            yaxis_ticksuffix='%', height=400,
        )
        return f"Plotly.newPlot('scatter-chart', {fig.to_json()});"

    def _gap_size_chart(self, m: BacktestMetrics) -> str:
        gaps = [g for g in m.gap_size_metrics if g.count > 0 and g.range_label != "Unknown"]
        if not gaps:
            return ""
        colors = ['#3fb950' if g.avg_return >= 0 else '#f85149' for g in gaps]
        fig = go.Figure(go.Bar(
            x=[g.range_label for g in gaps],
            y=[g.avg_return for g in gaps],
            marker_color=colors,
            text=[f'{g.avg_return:.1f}%' for g in gaps],
            textposition='auto',
        ))
        fig.update_layout(
            template='plotly_dark', paper_bgcolor='#161b22', plot_bgcolor='#0d1117',
            margin=dict(l=60, r=20, t=20, b=40),
            xaxis_title='Gap Size', yaxis_title='Avg Return (%)',
            yaxis_ticksuffix='%', height=350,
        )
        return f"Plotly.newPlot('gap-chart', {fig.to_json()});"

    def _monthly_chart(self, m: BacktestMetrics) -> str:
        months = [mm.month for mm in m.monthly_metrics]
        pnls = [mm.total_pnl for mm in m.monthly_metrics]
        colors = ['#3fb950' if p >= 0 else '#f85149' for p in pnls]
        fig = go.Figure(go.Bar(
            x=months, y=pnls, marker_color=colors,
            text=[f'${p:,.0f}' for p in pnls], textposition='auto',
        ))
        fig.update_layout(
            template='plotly_dark', paper_bgcolor='#161b22', plot_bgcolor='#0d1117',
            margin=dict(l=60, r=20, t=20, b=40),
            xaxis_title='Month', yaxis_title='P&L ($)',
            yaxis_tickprefix='$', height=350,
        )
        return f"Plotly.newPlot('monthly-chart', {fig.to_json()});"

    def _return_distribution(self, trades: List[TradeResult]) -> str:
        fig = go.Figure()
        colors = {'A': '#3fb950', 'B': '#58a6ff', 'C': '#d29922', 'D': '#f85149'}
        for grade in ['A', 'B', 'C', 'D']:
            rets = [t.return_pct for t in trades if t.grade == grade]
            if rets:
                fig.add_trace(go.Histogram(
                    x=rets, name=f'Grade {grade}',
                    marker_color=colors[grade], opacity=0.6,
                    nbinsx=30,
                ))
        fig.update_layout(
            template='plotly_dark', paper_bgcolor='#161b22', plot_bgcolor='#0d1117',
            margin=dict(l=60, r=20, t=20, b=40),
            xaxis_title='Return (%)', yaxis_title='Count',
            barmode='overlay', height=350,
        )
        return f"Plotly.newPlot('dist-chart', {fig.to_json()});"

    # ------------------------------------------------------------------ Tables
    def _grade_table_html(self, grades, title: str) -> str:
        rows = ""
        for g in grades:
            if g.count == 0:
                continue
            css = f'grade-{g.grade.lower()}'
            rows += f"""<tr>
<td class="{css}">{g.grade}</td><td>{g.count}</td>
<td>{g.win_rate:.1f}%</td>
<td class="{'positive' if g.avg_return >= 0 else 'negative'}">{g.avg_return:.1f}%</td>
<td>{g.median_return:.1f}%</td>
<td class="{'positive' if g.total_pnl >= 0 else 'negative'}">${g.total_pnl:,.0f}</td>
<td>{g.stop_loss_rate:.1f}%</td>
<td>{g.avg_holding_days_win:.0f} / {g.avg_holding_days_loss:.0f} / {g.avg_holding_days_stop:.0f}</td>
</tr>"""
        return f"""<table><thead><tr>
<th>Grade</th><th>Trades</th><th>Win Rate</th><th>Avg Return</th>
<th>Median Return</th><th>Total P&L</th><th>Stop Rate</th><th>Avg Days (W/L/S)</th>
</tr></thead><tbody>{rows}</tbody></table>"""

    def _stat_test_html(self, test) -> str:
        if test is None:
            return '<div class="section"><h2>A/B vs C/D Statistical Test</h2><p style="color:var(--text2);">Insufficient data for test</p></div>'
        sig_text = '<span class="positive">SIGNIFICANT</span>' if test.significant else '<span class="negative">NOT significant</span>'
        return f"""<div class="section">
<h2>A/B vs C/D Statistical Test ({test.test_name})</h2>
<div class="stat-test">
  <div class="stat-box"><div class="label">{test.group_a_label} (n={test.group_a_n})</div><div class="value">{test.group_a_mean:.2f}%</div></div>
  <div class="stat-box"><div class="label">{test.group_b_label} (n={test.group_b_n})</div><div class="value">{test.group_b_mean:.2f}%</div></div>
  <div class="stat-box"><div class="label">t-statistic</div><div class="value">{test.t_statistic:.4f}</div></div>
  <div class="stat-box"><div class="label">p-value</div><div class="value">{test.p_value:.4f} {sig_text}</div></div>
  <div class="stat-box"><div class="label">95% CI (mean diff)</div><div class="value">[{test.ci_lower:.2f}%, {test.ci_upper:.2f}%]</div></div>
</div>
</div>"""

    def _score_range_table_html(self, ranges) -> str:
        rows = ""
        for r in ranges:
            if r.count == 0:
                continue
            rows += f"""<tr>
<td>{r.range_label}</td><td>{r.count}</td><td>{r.win_rate:.1f}%</td>
<td class="{'positive' if r.avg_return >= 0 else 'negative'}">{r.avg_return:.1f}%</td>
<td class="{'positive' if r.total_pnl >= 0 else 'negative'}">${r.total_pnl:,.0f}</td>
</tr>"""
        return f"""<table><thead><tr>
<th>Score Range</th><th>Trades</th><th>Win Rate</th><th>Avg Return</th><th>Total P&L</th>
</tr></thead><tbody>{rows}</tbody></table>"""

    def _gap_size_table_html(self, gaps) -> str:
        rows = ""
        for g in gaps:
            if g.count == 0:
                continue
            rows += f"""<tr>
<td>{g.range_label}</td><td>{g.count}</td><td>{g.win_rate:.1f}%</td>
<td class="{'positive' if g.avg_return >= 0 else 'negative'}">{g.avg_return:.1f}%</td>
<td class="{'positive' if g.total_pnl >= 0 else 'negative'}">${g.total_pnl:,.0f}</td>
</tr>"""
        return f"""<table><thead><tr>
<th>Gap Size</th><th>Trades</th><th>Win Rate</th><th>Avg Return</th><th>Total P&L</th>
</tr></thead><tbody>{rows}</tbody></table>"""

    def _monthly_table_html(self, months) -> str:
        rows = ""
        for mm in months:
            rows += f"""<tr>
<td>{mm.month}</td><td>{mm.count}</td><td>{mm.win_rate:.1f}%</td>
<td class="{'positive' if mm.avg_return >= 0 else 'negative'}">{mm.avg_return:.1f}%</td>
<td class="{'positive' if mm.total_pnl >= 0 else 'negative'}">${mm.total_pnl:,.0f}</td>
</tr>"""
        return f"""<table><thead><tr>
<th>Month</th><th>Trades</th><th>Win Rate</th><th>Avg Return</th><th>Total P&L</th>
</tr></thead><tbody>{rows}</tbody></table>"""

    def _stop_loss_grade_table(self, grades) -> str:
        rows = ""
        for g in grades:
            if g.count == 0:
                continue
            rows += f'<tr><td class="grade-{g.grade.lower()}">{g.grade}</td><td>{g.stop_loss_count}</td><td>{g.count}</td><td>{g.stop_loss_rate:.1f}%</td></tr>'
        return f"""<table><thead><tr><th>Grade</th><th>Stops</th><th>Trades</th><th>Stop Rate</th></tr></thead><tbody>{rows}</tbody></table>"""

    def _skip_table_html(self, reasons: dict) -> str:
        if not reasons:
            return '<p style="color:var(--text2);">No skipped trades</p>'
        rows = ""
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            rows += f'<tr><td>{reason}</td><td>{count}</td></tr>'
        return f'<table><thead><tr><th>Reason</th><th>Count</th></tr></thead><tbody>{rows}</tbody></table>'

    def _trades_table_html(self, trades: List[TradeResult]) -> str:
        sorted_t = sorted(trades, key=lambda t: t.entry_date)
        rows = ""
        for t in sorted_t:
            exit_badge = {
                'stop_loss': '<span class="badge badge-stop">STOP</span>',
                'max_holding': '<span class="badge badge-hold">90D</span>',
                'end_of_data': '<span class="badge badge-eod">EOD</span>',
            }.get(t.exit_reason, t.exit_reason)

            rows += f"""<tr>
<td>{t.ticker}</td>
<td class="grade-{t.grade.lower()}">{t.grade}</td>
<td>{f'{t.score:.1f}' if t.score is not None else '-'}</td>
<td>{t.report_date}</td>
<td>{t.entry_date}</td>
<td>${t.entry_price:.2f}</td>
<td>{t.exit_date}</td>
<td>${t.exit_price:.2f}</td>
<td class="{'positive' if t.pnl >= 0 else 'negative'}">${t.pnl:,.0f}</td>
<td class="{'positive' if t.return_pct >= 0 else 'negative'}">{t.return_pct:.1f}%</td>
<td>{t.holding_days}</td>
<td>{exit_badge}</td>
</tr>"""
        return f"""<table class="sortable"><thead><tr>
<th>Ticker</th><th>Grade</th><th>Score</th><th>Report</th>
<th>Entry</th><th>Entry $</th><th>Exit</th><th>Exit $</th>
<th>P&L</th><th>Return</th><th>Days</th><th>Exit</th>
</tr></thead><tbody>{rows}</tbody></table>"""

    # ------------------------------------------------------------------ Helpers
    def _trade_period(self, trades: List[TradeResult]) -> str:
        if not trades:
            return "N/A"
        dates = [t.report_date for t in trades]
        return f"{min(dates)} to {max(dates)}"

    def _sortable_table_js(self) -> str:
        return """
document.querySelectorAll('.sortable th').forEach((th, i) => {
  th.addEventListener('click', () => {
    const table = th.closest('table');
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const asc = th.dataset.sort !== 'asc';
    th.dataset.sort = asc ? 'asc' : 'desc';
    rows.sort((a, b) => {
      let va = a.cells[i].textContent.replace(/[$,%]/g, '').trim();
      let vb = b.cells[i].textContent.replace(/[$,%]/g, '').trim();
      let na = parseFloat(va), nb = parseFloat(vb);
      if (!isNaN(na) && !isNaN(nb)) return asc ? na - nb : nb - na;
      return asc ? va.localeCompare(vb) : vb.localeCompare(va);
    });
    rows.forEach(r => tbody.appendChild(r));
  });
});
"""
