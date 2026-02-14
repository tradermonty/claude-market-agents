# Earnings Trade Backtest System - Implementation Plan (v3 Final)

## Context

`reports/` に蓄積された約94件のearnings trade HTMLレポート（2025-09〜2026-02）からトレード候補銘柄を抽出し、
バックテストを実施する。検証の主目的は2つ: (1) スコアリングシステムの有効性検証（A/B vs C/D）、(2) 90日保有戦略の期待値評価。
指標は独立トレード集計、損切りはStop価格+スリッページモデルで実施する。

## 確定仕様

- ポジションサイズ: $10,000固定/トレード
- 損切り: Stop価格 + 0.5%スリッページ（エントリー当日から有効）
- 保有期間: 最大90暦日
- 資金モデル: 独立トレード集計（無限資金前提）
- Ticker: UA/UAA等は別銘柄として扱う
- 重複排除: (report_date, ticker) で重複排除、スコアが取れた最初のものを優先

---

## 1. ファイル構成

```
backtest/
  main.py                 # エントリーポイント（CLI引数）
  html_parser.py           # HTMLレポート解析（マルチフォーマット対応）
  price_fetcher.py         # FMP API株価取得（FMPDataFetcherコピー+ラッパー）
  trade_simulator.py       # トレードシミュレーション
  metrics_calculator.py    # パフォーマンス指標計算
  report_generator.py      # HTML結果レポート + CSV出力
  requirements.txt         # 依存パッケージ
  tests/
    test_html_parser.py    # パーサの自動テスト（HTMLフィクスチャ付き）
    test_trade_simulator.py # シミュレーターの自動テスト
    test_smoke.py           # 実データ94件のスモークテスト
    fixtures/               # テスト用HTMLスニペット（各フォーマット）
```

出力先: `reports/backtest/` に分離（自己汚染防止）
- `reports/backtest/earnings_trade_backtest_result.html`
- `reports/backtest/earnings_trade_backtest_trades.csv`

## 2. 依存パッケージ (requirements.txt)

```
requests>=2.31.0
pandas>=2.0.0
numpy>=1.24.0
scipy>=1.10.0
python-dotenv>=1.0.0
tqdm>=4.65.0
plotly>=5.14.0
beautifulsoup4>=4.12.0
pytest>=7.0.0
```

## 3. HTMLパーサ設計（最重要モジュール）

### 3.1 実データで確認したフォーマットバリアント（7種以上）

| 日付例 | Ticker要素 | Score取得方法 | Grade取得方法 |
|--------|-----------|---------------|---------------|
| 2025-09-04 | `div.ticker` ("AEO - $17.28") | `div.score` ("88 pts") | card CSS class `a-grade` |
| 2025-10-14 | `h2` ("$FBK") | `div.score` ("67.0 / 100") | `span.grade` in grade-badge |
| 2025-12-02 | `h2` ("$CRDO") | grade-badge text ("88.5 pts") | grade-badge text "A-Grade" |
| 2025-12-10 | `span.ticker` ("PLAB") | `h4` ("Score: 69 pts") | `div.grade-badge.grade-c` |
| 2026-01-15 | `span.ticker` ("TSM") | grade-badge text ("78 pts") | grade-badge text "B-Grade" |
| 2026-02-04 | `div.stock-ticker` | `div.stock-score-value` ("84.0") | `div.stock-score-label` |
| 2026-02-11 | `div.stock-ticker` | score-breakdown "Total Score" row ("86.5/100") | `span.stock-grade.grade-a` |
| 2026-02-13 | `div.stock-ticker` | `div.score-value` ("88.5") | 親section `.grade-header.grade-a` |

### 3.2 パーサ戦略: フォールバックチェーン方式

カード単位で複数セレクタをフォールバック試行する設計。

```python
@dataclass
class TradeCandidate:
    ticker: str
    report_date: str       # YYYY-MM-DD (ファイル名から)
    grade: str             # A/B/C/D
    grade_source: str      # "html" | "inferred" (スコアから推定した場合)
    score: float           # 0-100
    price: Optional[float] # レポート時点の株価（取得できない場合はNone）
    gap_size: Optional[float]
    company_name: Optional[str]
```

#### `_extract_ticker(card)` フォールバック順:

1. `card.find(class_='stock-ticker')` -> text strip
2. `card.find('span', class_='ticker')` -> text strip
3. `card.find('div', class_='ticker')` -> split(" - ")[0] strip
4. `card.find('h2')` -> regex `r'\$?([A-Z][A-Z0-9.-]{0,6})'`

#### Ticker正規化:

- `$` プレフィックス除去
- 空白・改行除去
- バリデーション: `re.match(r'^[A-Z][A-Z0-9./-]{0,9}', ticker)`
  - （USB, BRK.B, UA, UAA, BRK/B 等に対応。1文字=先頭[A-Z]、2文字以上=先頭+残り0-9文字）
  - 例: UA, UAA, BRK.B, USB, CRDO

#### `_extract_score(card)` フォールバック順（要素点誤抽出防止）:

**最重要: x/5 パターン（要素点）は全て除外。/100 と Total Score を優先。**

1. score-breakdown内の "Total Score" ラベル隣接値 -> regex `r'(\d+\.?\d*)/100'` (Format G)
2. `div.score-value` -> text取得し、`r'\d+/5'` を含むならスキップ。残りをfloat化 (Format H)
3. `div.stock-score-value` -> float (Format F)
4. `div.score` -> regex `r'(\d+\.?\d*)\s*(?:pts|/\s*100)'` (Format A/B)
5. `h4` containing "Score" -> regex `r'(\d+\.?\d*)\s*pts'` (Format D)
6. grade-badge text -> regex `r'(\d+\.?\d*)\s*pts'` (Format B2/E)

#### バリデーション:

- x/5 除外: テキストが `r'\d+/5'` にマッチ -> 要素点なのでスキップ
- 数値範囲: `5 < score <= 100`（要素点最大値5を超える値のみ総合点として採用）
- float変換失敗 -> スキップ

#### `_extract_grade(card, section=None)` フォールバック順:

1. card CSS class -> `re.search(r'([abcd])-grade', ' '.join(card.get('class', [])))`
2. `span.stock-grade` class -> `re.search(r'grade-([abcd])', class)`
3. `div.grade-badge` class -> `re.search(r'grade-([abcd])', class)`
4. `div.stock-score-label` text -> regex `r'([ABCD])-GRADE'`
5. `div.grade` / `span.grade` text -> single char A/B/C/D
6. grade-badge text -> regex `r'([ABCD])-Grade'`
7. 親section.grade-section の `.grade-header` class -> `re.search(r'grade-([abcd])')`
8. 最終手段: スコアから推定（>=85->A, >=70->B, >=55->C, else->D）
   -> `grade_source = "inferred"`（本集計から除外可能にする）

バリデーション: `grade in {'A','B','C','D'}`

#### `_extract_price(card)` フォールバック順:

1. `div.metric-value` where `div.metric-label` = "Price"
2. `span.price-current`
3. `div.price-value`
4. `div.ticker` text -> regex `r'\$(\d+\.?\d*)'`
5. `div.metric-box div.value` where `div.label` = "Current Price"
6. `span.price-prev` の隣の値

バリデーション: `price > 0`

### 3.3 No-Stock / Upcoming / Summary 除外ロジック

```python
# ファイルレベル: no-stocks 判定（セレクタ + テキストフォールバック）
if (soup.find(class_='no-stocks-card') or
    soup.find(class_='no-stocks-title') or
    soup.find(string=re.compile(r'No.*Earnings.*Stocks.*Available', re.I))):
    return []

# DOM除去: upcoming / summary セクションを除外（section + div 両方）
for tag_name in ['section', 'div']:
    for el in soup.find_all(tag_name, class_=re.compile(r'upcoming|summary-section')):
        el.decompose()

# summary テーブル内の ticker も除外済み（decompose済み）
```

### 3.4 重複排除

```python
# (report_date, ticker) で重複排除。スコアが取れたものを優先。
seen = set()
deduped = []
for c in candidates:
    key = (c.report_date, c.ticker)
    if key not in seen:
        seen.add(key)
        deduped.append(c)
    else:
        # 既存のスコアがNoneで新しい方にスコアがあれば置換
        ...
```

## 4. 日付定義

| 用語 | 定義 |
|------|------|
| report_date | HTMLファイル名の日付 |
| entry_date | report_date の翌営業日（FMP価格データから自動判定） |
| exit_date | stop発動日 or entry_date + 90暦日の直近営業日 |

前提: レポートはpre-market/BMOに生成。翌営業日寄りエントリーが保守的。

## 5. トレードシミュレーション設計

### 5.1 データクラス

```python
@dataclass
class TradeResult:
    ticker: str
    grade: str
    grade_source: str       # "html" | "inferred"
    score: float
    report_date: str
    entry_date: str
    entry_price: float      # Open
    exit_date: str
    exit_price: float
    shares: int             # floor(10000 / entry_price)
    invested: float         # shares * entry_price
    pnl: float
    return_pct: float
    holding_days: int       # 暦日
    exit_reason: str        # "stop_loss" | "max_holding" | "end_of_data"
    gap_size: Optional[float]

@dataclass
class SkippedTrade:
    ticker: str
    report_date: str
    skip_reason: str        # "no_price_data" | "zero_shares" | "missing_ohlc" | "delisted"
```

### 5.2 損切りロジック（Stop価格 + スリッページ）

```python
stop_price = entry_price * (1 - stop_loss_pct / 100)  # entry * 0.90
slippage_pct = 0.5  # デフォルト 0.5%

# エントリー当日から判定開始（寄り付き後の日中安値）
for day in price_data[entry_day_index:]:
    if day.adjusted_low <= stop_price:
        exit_price = stop_price * (1 - slippage_pct / 100)
        exit_reason = "stop_loss"
        break
    if (day.date - entry_date).days >= 90:
        exit_price = day.adjusted_close
        exit_reason = "max_holding"
        break
# ループ完了 = データ不足
exit_reason = "end_of_data"
```

### 5.3 異常系ハンドリング

- `shares = 0` (高価格株で $10,000 < entry_price): -> `SkippedTrade(skip_reason="zero_shares")`
- Open/Low欠損: -> `SkippedTrade(skip_reason="missing_ohlc")`
- adjClose欠損: adjClose がない場合は未調整OHLCで代替（ログ警告）
- FMP データなし: -> `SkippedTrade(skip_reason="no_price_data")`

### 5.4 株式分割・調整価格

```python
if adjClose is not None and close != 0:
    adj_factor = adjClose / close
    adjusted_open  = open * adj_factor
    adjusted_high  = high * adj_factor
    adjusted_low   = low * adj_factor
else:
    # adjClose欠損時は未調整で代替
    adjusted_* = raw_*
```

## 6. 価格データ取得設計

### 6.1 FMPDataFetcher再利用

`earnings-trade-backtest/src/fmp_data_fetcher.py` の FMPDataFetcher クラスをコピー。
主要メソッド: `get_historical_price_data(symbol, from_date, to_date)`

### 6.2 キャッシュ戦略（期間欠落バグ防止）

```python
# Step 1: 全候補から ticker ごとの必要期間を事前集約
ticker_periods = {}  # {ticker: (min_date, max_date)}
for candidate in all_candidates:
    min_d = min(existing_min, candidate.report_date)
    max_d = max(existing_max, candidate.report_date + 120 days)

# Step 2: ticker ごとに1回だけ API コール
for ticker, (min_date, max_date) in ticker_periods.items():
    prices[ticker] = fetcher.get_historical_price_data(ticker, min_date, max_date)
```

### 6.3 API Key（優先順位: 明示性・再現性優先）

1. CLI引数 `--fmp-api-key`（最も明示的）
2. 環境変数 `FMP_API_KEY`
3. `.mcp.json` からの読み取り（最終フォールバック）

## 7. パフォーマンス指標

### 7.1 全体指標（独立トレード集計）

- 総トレード数 / 勝ち / 負け / 勝率
- スキップ数（理由別内訳）
- 総損益 ($) / 平均リターン (%) / 中央値リターン (%)
- プロフィットファクター (総利益 / |総損失|)
- 最大ドローダウン（累積損益の peak-to-trough）
- Trade Sharpe（参考値、mean/std of trade returns、独立トレード前提のため過信注意）

### 7.2 スコアリング有効性検証

- グレード別: A/B/C/D の勝率・平均リターン・総損益・トレード数
  -> `grade_source="html"` のみを本集計、`"inferred"` は参考欄に別記
- スコアレンジ別: 85+, 70-84, 55-69, <55
- スコア vs リターン相関係数 (Pearson) + 散布図
- A/B vs C/D の差の検定（Welch t-test）+ 95% CI 併記

### 7.3 戦略評価

- ギャップサイズ別パフォーマンス (0-5%, 5-10%, 10-20%, 20%+)
- 月次損益
- Stop Loss 発動率（全体 / グレード別）
- 平均保有日数（勝ち / 負け / Stop）

## 8. レポート生成

### 8.1 HTML レポート (`reports/backtest/earnings_trade_backtest_result.html`)

1. サマリーダッシュボード: KPIカード（総トレード数、勝率、総損益、PF、Trade Sharpe、最大DD、スキップ数）
2. 累積損益カーブ: Plotly折れ線（entry_date順）
3. グレード別パフォーマンス: 棒グラフ + テーブル（A/B/C/D比較、grade_source注記）
4. A/B vs C/D 統計検定結果: t値、p値、95% CI
5. スコア vs リターン散布図: スコアリング有効性の視覚化
6. ギャップサイズ別: 棒グラフ
7. 月次リターン: 棒グラフ
8. リターン分布: ヒストグラム（グレード別色分け）
9. Stop Loss 分析: 発動率テーブル
10. スキップ銘柄一覧: 理由別テーブル
11. 全トレード一覧: ソート可能テーブル

### 8.2 CSV（2ファイルに分離）

- `reports/backtest/earnings_trade_backtest_trades.csv` -- 全TradeResult
- `reports/backtest/earnings_trade_backtest_skipped.csv` -- 全SkippedTrade（skip_reason付き）

## 9. テスト設計

### 9.1 test_html_parser.py (ユニットテスト)

各フォーマットバリアントのHTMLスニペットをフィクスチャとして用意:
- Format A (Sept), B1 (Oct), B2 (Dec初), D (Dec中), E (Jan), F (Feb4), G (Feb11), H (Feb13)
- No-stock ページ, Upcoming セクション付きページ

テストケース:
- 各フォーマットからのticker/score/grade正常抽出
- No-stock ページ -> 空リスト
- Upcoming セクション内 ticker -> 非抽出
- score x/5 パターン -> 要素点として無視、総合点のみ抽出
- (report_date, ticker) 重複排除の動作確認
- grade_source が html/inferred で正しく設定されること

### 9.2 test_trade_simulator.py (ユニットテスト)

- 正常ケース: entry -> 90日保有 -> close exit
- Stop loss ケース: 途中で Low が stop_price 以下 -> stop_price * 0.995 で決済
- エントリー当日stop: 寄り付き後に即日Low到達
- データ不足ケース: end_of_data exit
- shares=0 ケース: SkippedTrade生成
- 株式分割ケース: adjClose != close の日 -> 調整OHLC使用

### 9.3 test_smoke.py (統合テスト)

```python
def test_parse_all_real_reports():
    """実データ94件の全件パース: クラッシュなし・重複なし・score範囲内"""
    parser = EarningsReportParser()
    candidates = parser.parse_all_reports("reports/")

    # クラッシュなし（ここまで到達）
    assert len(candidates) > 0

    # 重複なし
    keys = [(c.report_date, c.ticker) for c in candidates]
    assert len(keys) == len(set(keys))

    # score範囲内
    for c in candidates:
        assert 0 < c.score <= 100, f"{c.ticker} on {c.report_date}: score={c.score}"

    # grade範囲内
    for c in candidates:
        assert c.grade in {'A', 'B', 'C', 'D'}
```

## 10. 実装順序

1. `html_parser.py` + `tests/test_html_parser.py` + `fixtures/` + `test_smoke.py`
   -> 全HTMLを解析、抽出結果CSV出力で目視確認
2. `price_fetcher.py` (FMPDataFetcher コピー + ticker期間集約ラッパー + API key優先順位)
3. `trade_simulator.py` + `tests/test_trade_simulator.py`
4. `metrics_calculator.py` (Trade Sharpe注記、t検定、grade_source分離)
5. `report_generator.py`
6. `main.py` (全体結合 + CLI引数)
7. エンドツーエンド実行 -> 結果確認

## 11. CLI引数

```bash
python backtest/main.py \
  --reports-dir reports/ \
  --output-dir reports/backtest/ \
  --position-size 10000 \
  --stop-loss 10.0 \
  --slippage 0.5 \
  --max-holding 90 \
  --min-grade D \
  --fmp-api-key <optional>
```

## 12. 検証方法

1. `pytest backtest/tests/` でユニットテスト + スモークテスト実行
2. `python backtest/main.py` でフル実行
3. 出力CSVから数件を手動照合（FMP価格データと比較）
4. HTMLレポートのチャート・テーブルを目視確認

## 13. 重要ファイルパス

| ファイル | 用途 |
|----------|------|
| `reports/earnings_trade_analysis_*.html` (94件) | 入力データ |
| `earnings-trade-backtest/src/fmp_data_fetcher.py` | FMPクライアントをコピー再利用 |
| `earnings-trade-backtest/src/report_generator.py` | Plotlyチャートパターン参考 |
| `.mcp.json` | FMP API Key フォールバック読み取り |
| `reports/backtest/` | 出力先（新規作成） |
