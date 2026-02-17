# Earnings Trade Entry Quality Filter Design (2026-02-16, implemented 2026-02-16)

## 1. 目的

`reports/backtest/earnings_trade_backtest_trades.csv` と `reports/backtest/charts/` の実績から、
「エントリー後に上がらない確率が高い銘柄」を事前に除外し、最終損益と安定性を改善する。

本設計は以下を満たす。

- エントリー前に取得可能な情報のみで判定する（リーク防止）
- 総損益を大きく落とさず、勝率・平均リターン・ストップ率を改善する
- 既存バックテスト実装（`backtest/`）に段階導入できる

---

## 2. 分析対象データ

- トレード実績: `reports/backtest/earnings_trade_backtest_trades.csv`
  - 件数: 517
  - 期間: 2025-09-04 ~ 2026-02-13
- チャート: `reports/backtest/charts/*.png`
  - 代表確認:
    - 即死型: `LZ_2025-11-06_B.png`, `FCEL_2025-12-18_C.png`
    - 失速型: `IDXX_2025-11-03_A.png`, `INSM_2025-10-30_A.png`
    - 素直上昇型: `LITE_2025-11-05_A.png`, `STX_2025-10-29_A.png`

ベースライン（517件）:

- Total P&L: 159,478.42
- Avg Return: 3.11%
- Win Rate: 54.5%
- Stop Rate: 35.0%

---

## 3. 負けパターンの定義と観測結果

### 3.1 パターン定義

- `immediate_dump`: `exit_reason=stop_loss` かつ `holding_days<=3`
- `fade_dump`: `exit_reason=stop_loss` かつ `holding_days>3`
- `smooth_up`: `pnl>0` かつ `holding_days>=20` かつ `exit_reason in {max_holding,end_of_data}`
- `other`: 上記以外

### 3.2 分布

- immediate_dump: 87件 (16.8%)
- fade_dump: 94件 (18.2%)
- smooth_up: 147件 (28.4%)
- other: 189件 (36.6%)

`stop_loss` は 181件で、0-1日ストップが70件（全ストップの38.7%）を占める。

---

## 4. 主要な傾向

### 4.1 Entry Price帯の偏り

`entry_price` 帯別では `10-30` が最も弱い。

- 10-30: 128件, Win 40.6%, Stop 48.4%, Avg +0.33%
- 30-60: 100件, Win 63.0%, Stop 29.0%, Avg +4.85%
- 60-100: 81件, Win 65.4%, Stop 23.5%, Avg +4.34%

### 4.2 Score帯の偏り

`85+` は平均リターンは悪くないが、ストップ率が高い。

- 85+: 85件, Win 47.1%, Stop 47.1%, Avg +3.51%
- 55-69: 123件, Win 60.2%, Stop 27.6%, Avg +3.44%
- 70-84: 190件, Win 58.4%, Stop 30.5%, Avg +3.92%

### 4.3 Gap×Scoreの高リスク組み合わせ

`gap_size>=10` かつ `score>=85`（gap既知分）は成績が悪い。

- 18件, Win 33.3%, Stop 66.7%, Avg -0.45%, P&L -767.48

---

## 5. フィルタ候補の比較（実測）

| ルール | 件数 | Total P&L | Avg Return | Win Rate | Stop Rate |
|---|---:|---:|---:|---:|---:|
| Baseline | 517 | 159,478.42 | 3.11% | 54.5% | 35.0% |
| `exclude entry_price 10-30` | 390 | 155,440.64 | 4.02% | 59.0% | 30.8% |
| `exclude known (gap>=10 & score>=85)` | 499 | 160,245.90 | 3.24% | 55.3% | 33.9% |
| `exclude entry_price 10-30 OR known (gap>=10 & score>=85)` | 375 | 156,628.77 | 4.21% | 60.0% | 29.3% |
| `score 55-85 & exclude entry_price 10-30` | 238 | 108,708.61 | 4.61% | 64.3% | 23.9% |

解釈:

- 品質改善と損益維持のバランスは、`exclude entry_price 10-30 OR known (gap>=10 & score>=85)` が最も実務的。
- リターン品質最大化は `score 55-85 & exclude entry_price 10-30` だが、取引数と総損益が大きく減る。

---

## 6. 採用方針（提案）

### 6.1 Unlimited運用向け（件数を維持したい場合）

優先導入ルール:

1. `entry_price` が `[10, 30)` の銘柄を除外
2. `gap_size` が既知の場合に限り `gap_size>=10 && score>=85` を除外

設計時の期待効果（上記2条件併用、CSV集計ベース）:

- Avg Return: +3.11% -> +4.21%
- Win Rate: 54.5% -> 60.0%
- Stop Rate: 35.0% -> 29.3%
- Total P&L: 159,478 -> 156,629（-1.8%）

**実装後バックテスト結果**（`--data-end-date 2026-02-14`, 2026-02-16実行）:

| Metric | Baseline | Filtered | Delta |
|---|---:|---:|---:|
| Trades | 517 | 417 | -100 |
| Win Rate | 54.5% | 58.3% | **+3.7pt** |
| Avg Return | 3.11% | 4.05% | **+0.94pt** |
| Stop Rate | 35.0% | 30.9% | **-4.1pt** |
| Total P&L | $159,478 | $167,523 | **+$8,045 (+5.0%)** |
| Profit Factor | 1.78 | 2.14 | **+0.36** |
| Trade Sharpe | 0.20 | 0.26 | **+0.06** |
| Max Drawdown | $10,672 | $10,851 | +$179 |

除外内訳: `filter_low_price_10_30`: 84件, `filter_high_gap_score_10_85`: 16件

設計時予測との差異: Avg Return は +4.21% 予測に対し +4.05%（微減）、Total P&L は -1.8% 予測に対し **+5.0%**（改善）。
差異の主因は設計時の CSV 集計（静的）と実装後のバックテスト（simulation 経由）でのデータ処理パスの違い。

### 6.2 Entry Limit=2運用向け

既存実験 `reports/backtest-limit2/entry_limit_optimization_report.html` より、
`--daily-entry-limit 2 --min-score 55 --max-score 85 --min-grade B` を第一候補とする。

実験レポート上の値:

- 35件, Win 68.6%, Stop 28.6%, Avg +6.28%, PF 3.07, Sharpe 6.49

---

## 7. 実装設計（実装済み）

### 7.1 モジュール構成

新規ファイル `backtest/entry_filter.py` に全ロジックを集約。

判定対象は `TradeCandidate` の既存列のみ:

- `price`（候補時点価格）
- `gap_size`
- `score`

Public API（4関数）:

```python
# backtest/entry_filter.py

# Module constants (defaults)
EXCLUDE_PRICE_MIN = 10
EXCLUDE_PRICE_MAX = 30
RISK_GAP_THRESHOLD = 10
RISK_SCORE_THRESHOLD = 85

def should_skip_candidate(c, price_min=10, price_max=30,
                          gap_threshold=10, score_threshold=85
                          ) -> tuple[bool, str | None]:
    """Rule 1: price in [min, max) -> skip with 'filter_low_price_{min}_{max}'
       Rule 2: gap>=threshold & score>=threshold -> skip with 'filter_high_gap_score_{gap}_{score}'
       Price filter takes precedence over combo filter."""

def apply_entry_quality_filter(candidates, ...) -> tuple[list, list[SkippedTrade]]:
    """Apply filter, returns (passed, skipped)."""

def is_filter_active(args) -> bool:
    """Truth source: --entry-quality-filter OR any override is not None."""

def validate_filter_args(args) -> list[str]:
    """Validate CLI args. Returns error messages."""
```

### 7.2 変更ファイル

| ファイル | 変更内容 |
|---|---|
| `backtest/entry_filter.py` | **NEW** フィルタロジック + バリデーション + `is_filter_active` |
| `backtest/tests/test_entry_filter.py` | **NEW** 41 unit tests |
| `backtest/main.py` | CLI 5フラグ追加、バリデーション統合、フィルタ適用、config dict、`filtered_candidates.csv` 出力、skipped マージ |
| `backtest/tests/test_cli_validation.py` | 新フラグの受理/拒否テスト7件 + 実験CLI回帰テスト4件 |
| `backtest/report_generator.py` | `_filter_config_html()` に Entry Quality Filter 表示追加 |
| `backtest/stop_loss_experiment.py` | CLI 5フラグ追加 + バリデーション + フィルタ適用 |
| `backtest/trailing_stop_experiment.py` | 同上 |

変更不要で自動対応したモジュール（設計通り）:

- `SkippedTrade` — `skip_reason` は自由文字列、新値が自動で動作
- `MetricsCalculator._skip_breakdown()` — `skip_reason` を自動集計
- `ReportGenerator._skip_table_html()` — `skip_reasons` dict を自動レンダリング
- `run_manifest.py` — config dict から自動保存

### 7.3 CLI（実装済み）

```
--entry-quality-filter           # bool flag, enables both filters with module defaults
--exclude-price-min FLOAT        # override: lower bound of exclusion range (default: 10)
--exclude-price-max FLOAT        # override: upper bound of exclusion range (default: 30)
--risk-gap-threshold FLOAT       # override: gap threshold for combo filter (default: 10)
--risk-score-threshold FLOAT     # override: score threshold for combo filter (default: 85)
```

設計時の提案からの変更点:

- `--exclude-entry-price-min/max` → `--exclude-price-min/max` に短縮
- `--exclude-gap-score-combo` (bool) → 不要化（`--entry-quality-filter` で両フィルタ同時有効）
- `--allow-missing-gap` → 初期実装では固定挙動（gap=None は除外しない）として省略
- Override flags 単独指定で暗黙的にフィルタ有効化（`is_filter_active()` が truth source）

### 7.4 skip_reason 命名

レビューを経て、固定文字列から動的文字列に変更:

- `"filter_low_price_{min}_{max}"` （例: `filter_low_price_10_30`）
- `"filter_high_gap_score_{gap}_{score}"` （例: `filter_high_gap_score_10_85`）

カスタム閾値使用時に理由名が実値を反映し、`filtered_candidates.csv` や skip 集計で誤解を防ぐ。

### 7.5 バリデーション

`validate_filter_args()` を共通関数化し、3箇所（`main.py`, `stop_loss_experiment.py`, `trailing_stop_experiment.py`）から呼び出し:

- `eff_min >= 0`
- `eff_max > eff_min`（片側 override 時も effective 値で検証）
- `risk_gap_threshold >= 0`
- `0 <= risk_score_threshold <= 100`

---

## 8. 検証計画

### 8.1 KPI

- Total P&L
- Avg Return
- Win Rate
- Stop Rate
- Profit Factor
- Sharpe

### 8.2 受け入れ基準（初期）と実績

| 基準 | 閾値 | 実績 | 判定 |
|---|---|---|---|
| Stop Rate 改善 | 3pt 以上 | **-4.1pt** (35.0%→30.9%) | PASS |
| Avg Return 改善 | 0.7pt 以上 | **+0.94pt** (3.11%→4.05%) | PASS |
| Total P&L 維持 | baseline 比 -3% 以内 | **+5.0%** ($159,478→$167,523) | PASS |

### 8.3 検証ステップ

1. ~~In-sample再計算（現期間）~~ → **完了** (2026-02-16)
2. 月次Walk-forward（rolling） → 未実施
3. `daily-entry-limit=2` 環境での再最適化 → 未実施
4. gap欠損の多い銘柄群を分離評価 → 未実施

---

## 9. リスクと注意点

- `gap_size` 欠損が多い（解析対象では約76%欠損）ため、Gap条件は補助ルール扱い。
- フィルタが強すぎると機会損失が増え、総損益を毀損する。
- 単一期間（2025-09~2026-02）に依存しており、将来相場で効果が反転する可能性がある。

---

## 10. 実装履歴と次アクション

### 10.1 完了（2026-02-16）

1. ~~`entry_price 10-30` 除外を先行実装~~ → `backtest/entry_filter.py`
2. ~~`gap>=10 && score>=85` 除外（gap既知時のみ）を併用実装~~ → 同上
3. ~~レポートに「フィルタ理由別の除外損益」を追加~~ → `report_generator.py` skip 集計 + HTML config 表示
4. ~~受け入れ基準の検証~~ → 全 3 基準 PASS

テスト: 285 passed, 6 skipped（全テストスイート）

### 10.2 残課題

1. Walk-forward 結果を追記して効果の頑健性を確認
2. `daily-entry-limit=2` 環境での再検証
3. gap 欠損が多い銘柄群の分離評価
4. Portfolio mode (`--max-positions`) との組み合わせ検証
