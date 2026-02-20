# Adaptive Theme Basket Discovery Design (2026-02-16)

## 1. 目的

「その時々の相場で、どのテーマに資金が継続流入しているか」を固定ルールではなく動的に推定し、
各エントリー候補がその資金フローに乗っている確率を `数値スコア` 化する。

本設計のゴールは以下。

- テーマを事前固定しない（AI/エネルギーなどを手で決め打ちしない）
- 相場環境が変わると、テーマバスケットも自動で入れ替わる
- エントリー時点で利用可能な情報のみで判定する（リーク防止）
- 既存バックテスト（`backtest/`）へ段階導入できる

---

## 2. 背景課題

現状の課題は「銘柄単体条件（score, gap, price）」中心で、
市場全体のテーマ資金流入を十分に反映できていない点にある。

問題設定:

- 同じ「好決算」でも、テーマ追い風がある銘柄はドリフトしやすい
- 追い風がない銘柄はギャップ後に失速しやすい
- 追い風テーマ自体が時期で変わるため、固定バスケットはすぐ陳腐化する

---

## 3. 設計方針

本設計は以下の2層で構成する。

1. `Theme Discovery Layer`
   相場データから「今効いているテーマ集合」を毎日再推定する
2. `Candidate Fit Layer`
   エントリー候補が発見テーマにどれだけ整合しているかを採点する

最終的に `Market Sentiment Fit Score (0-100)` を算出し、
フィルタ・ランキング・サイズ調整に使用する。

### 3.1 実行モード

本設計は以下3モードを持つ。

1. `programmatic`
   完全ルールベース。再現性が高い。
2. `agent`
   Agent SDK による裁量判定を主とする。
3. `hybrid`（推奨）
   ルールベースを土台に、エージェント判定を上書きではなく加点/減点として使う。

運用初期は `hybrid` を標準にする。

---

## 4. システム全体像

日次バッチ（寄り前想定）:

1. 市場・銘柄データ取得
2. 動的テーマ抽出（クラスタリング）
3. テーマの強さ評価（Theme Heat）
4. 候補銘柄のテーマ適合評価（Theme Fit）
5. Agent SDK によるテーマ裁量評価（任意）
6. ガードレール付きスコア統合（Sentiment Fit）
7. エントリー判定へ反映

出力:

- `theme_snapshot_YYYY-MM-DD.json`（当日のテーマ一覧）
- `candidate_sentiment_scores_YYYY-MM-DD.csv`（候補別スコア）
- `theme_agent_decision_YYYY-MM-DD.json`（エージェント判断ログ）
- バックテストHTML内のテーマ診断セクション

---

## 5. データ入力設計

## 5.1 MVPで使うデータ（現行に近い）

- 価格・出来高（日足）
- 候補銘柄の `score/grade/gap/price`
- 候補HTMLからの会社名やセクター文字列（`stock-sector` 由来）

## 5.2 拡張で追加するデータ

- 銘柄プロフィール（sector / industry）
- セクターETF価格
- テーマ代理ETF/インデックス（可能なら）
- ニュース見出し・アナリスト改定（将来拡張）

---

## 6. 動的テーマ抽出ロジック

### 6.1 Universe構築

各日 `T` で分析対象銘柄集合 `U(T)` を作る。

構成:

- バックテスト候補銘柄
- 当日〜直近20営業日の高出来高・高モメンタム銘柄
- 流動性フィルタ通過銘柄（出来高/価格下限）

### 6.2 特徴量生成（銘柄単位）

各銘柄 `i` で、リークなしで以下を計算。

- `r1, r5, r20`: 1/5/20日リターン
- `rs5, rs20`: 対SPY相対強度
- `vol_shock`: 出来高倍率（当日/20日平均）
- `range_expansion`: 当日値幅の平常比
- `gap_hold`: ギャップ後維持率（可能範囲）
- `trend_quality`: 直近高値更新頻度、押し目回数

### 6.3 類似度グラフ構築

銘柄間の類似度 `S(i,j)` を作る。

基本式（MVP）:

`S = w1*return_corr + w2*volume_corr + w3*rs_alignment + w4*metadata_similarity`

`metadata_similarity` は sector/industry/キーワード一致度を使う。

### 6.4 クラスタリング（テーマ候補）

重み付きグラフに Louvain/Leiden を適用し、クラスタ `Ck` を生成。

クラスタ採用条件:

- 最小銘柄数を満たす（例: 5以上）
- 直近5日で一定以上のアクティブ度
- 異常に分散が高すぎない

### 6.5 テーマ命名（ラベル）

クラスタ内部の以下頻度上位でラベル化。

- sector / industry
- 会社名キーワード
- （拡張）ニュース見出しキーワード

命名は表示用であり、売買判定は数値スコアで行う。

---

## 7. テーマ強度スコア（Theme Heat）

テーマ `k` の強さを 0-100 で算出。

`ThemeHeat_k = 0.30*Breadth + 0.25*Strength + 0.20*Persistence + 0.15*Leadership + 0.10*RiskAdj`

各要素:

- `Breadth`: クラスタ内で上昇銘柄が占める比率
- `Strength`: 平均相対強度、出来高加速
- `Persistence`: 5日・20日での継続性
- `Leadership`: 上位銘柄のトレンド品質
- `RiskAdj`: ボラ過熱/急反転リスクの減点

テーマ状態判定:

- `Emerging`: 立ち上がり（加速中）
- `Trending`: 持続中
- `Exhausting`: 拡散・鈍化

---

## 8. 候補銘柄スコア（Theme Fit / Sentiment Fit）

候補銘柄 `i` に対して:

1. 所属可能テーマを推定（最大2テーマ）
2. テーマ適合度 `ThemeFit_i` を計算
3. 市場レジームと合成して最終スコア化

`ThemeFit_i = 0.35*Exposure + 0.25*FlowAlignment + 0.20*EarningsQuality + 0.20*Structure`

- `Exposure`: テーマクラスタへの連動性
- `FlowAlignment`: 直近資金流入との整合
- `EarningsQuality`: score/guidance/gap維持など
- `Structure`: 上昇トレンドの壊れにくさ

定量スコア:

`QuantSentiment_i = 0.70*ThemeFit_i + 0.30*RegimeScore`

ハイブリッド統合（推奨）:

`SentimentFit_i = 0.75*QuantSentiment_i + 0.25*AgentThemeFit_i`

`AgentThemeFit_i` は 0-100 で返す。
エージェントは `理由` と `反証条件` を必須出力する。

---

## 9. 市場レジーム判定

テーマ判定の前提として `RegimeScore` を持つ。

入力例:

- SPY/QQQのトレンド
- VIX水準と変化率
- 市場騰落比率
- セクター間分散

効果:

- リスクオフ時はテーマスコアの有効性を減衰
- リスクオン時はテーマ追随を強める

### 9.1 Agentic判定レイヤー（裁量の仕組み化）

狙い:

- 「今の物語（narrative）」を都度解釈する
- ただし主観暴走を防ぐため、厳格な I/O 契約を持たせる

Agent SDK への入力:

- 当日テーマクラスタ要約（Heat, Breadth, リーダー銘柄）
- 市場レジーム要約（RiskOn/Off, ボラ, 地合い）
- 候補銘柄ごとの定量特徴（score, gap, price, relative strength）
- 直近の失敗パターン統計（即死/失速比率）

Agent SDK からの出力（JSONスキーマ固定）:

- `global_view`: 今日有効なテーマ上位3件
- `theme_state`: `emerging|trending|exhausting`
- `ticker_assessment[]`
- `ticker_assessment[].agent_theme_fit` (0-100)
- `ticker_assessment[].confidence` (0-1)
- `ticker_assessment[].rationale`（短文）
- `ticker_assessment[].invalidations`（無効化条件）

統合時ガードレール:

- スキーマ不整合時は `programmatic` にフォールバック
- `confidence < min_confidence` の評価は採用しない
- 日次でのスコア変化上限を制限（例: ±20pt）
- Agentスコア単独での採用禁止（必ず定量条件を通す）

---

## 10. 売買ルールへの反映

## 10.1 フィルタ

- `SentimentFit < threshold` を除外
- 例: `threshold=60`

## 10.2 ランキング

`daily_entry_limit` がある日は下式で順位付け。

`RankScore = 0.5*ExistingSignal + 0.5*SentimentFit`

## 10.3 サイズ調整

- `SentimentFit >= 75`: 1.2x
- `60 <= SentimentFit < 75`: 1.0x
- `<60`: 0x（見送り）

---

## 11. 実装モジュール設計

追加候補:

- `backtest/theme_feature_engine.py`
- `backtest/theme_graph_cluster.py`
- `backtest/theme_scoring.py`
- `backtest/market_regime.py`
- `backtest/theme_snapshot.py`
- `backtest/theme_agent_client.py`
- `backtest/theme_agent_schema.py`
- `backtest/theme_agent_guardrail.py`

既存修正候補:

- `backtest/html_parser.py`
  - `TradeCandidate` に `sector_hint` / `industry_hint` / `theme_keywords` 追加
- `backtest/main.py`
  - テーマスコア計算とフィルタ統合
- `backtest/report_generator.py`
  - テーマ診断セクション追加

---

## 12. CLI設計案

- `--enable-theme-discovery`
- `--theme-mode programmatic|agent|hybrid`
- `--theme-lookback-short 5`
- `--theme-lookback-mid 20`
- `--theme-min-cluster-size 5`
- `--theme-threshold 60`
- `--theme-weight 0.5`
- `--export-theme-snapshot`
- `--agent-min-confidence 0.60`
- `--agent-max-daily-score-shift 20`
- `--agent-decision-log`

---

## 13. 段階導入計画

## Phase 0: Hybrid PoC（推奨）

目的:

- Agent SDK の入出力契約を先に固める
- 裁量をログ化し、後から検証可能にする

## Phase 1: Price/Volumeのみで動的クラスタ

目的:

- 外部追加データなしで動作確認
- テーマクラスタの安定性と再現性を評価

## Phase 2: Sector/Industry統合

目的:

- クラスタ命名の品質向上
- テーマ適合度の説明性向上

## Phase 3: ニュース/改定情報統合（任意）

目的:

- 「テーマの語り（narrative）」を定量化
- 早期テーマ検知精度を改善

---

## 14. 検証フレーム

## 14.1 比較対象

- Baseline（既存ルール）
- Baseline + 静的テーマ辞書
- Baseline + 本設計（動的テーマ）

## 14.2 KPI

- Total P&L
- Avg Return
- Win Rate
- Stop Rate
- Profit Factor
- Sharpe
- Max Drawdown
- Trade Count（過剰削減を監視）

## 14.3 検証方式

- 月次Walk-forward
- Purged CV（イベント重複リーク抑制）
- 期間別安定性（2025Q3/Q4, 2026Q1）

---

## 15. リスクと対策

主要リスク:

- 擬似クラスタ（偶然の共振）をテーマと誤認
- 過学習で将来性能が再現しない
- テーマ回転が速い局面で追随遅延
- 欠損データ（gap/sector）で判定不安定
- エージェントの説明と定量実態の乖離

対策:

- 最小クラスタサイズ・持続日数の下限
- レジーム悪化時のスコア減衰
- 単純ルールへのフォールバック
- 欠損時の保守的補完と `unknown` 扱い
- JSONスキーマ検証と採用条件のハード制約
- エージェント判断ログを日次保存し、事後監査可能にする

---

## 16. 受け入れ基準（初版）

本設計を採用する条件:

- Baseline比で Stop Rate を 3pt 以上改善
- Avg Return を 0.5pt 以上改善
- Total P&L の悪化を -5% 以内に抑える
- 2四半期相当のWalk-forwardで方向性が一貫

---

## 17. 今回の提案まとめ

テーマバスケットは「事前定義」ではなく、
`銘柄間の同時性（価格・出来高・相対強度）` から毎日再構成することで仕組み化できる。

この方式なら、2020年のリモートワーク相場でも、戦争起点のエネルギー相場でも、
同じフレームでテーマ抽出が可能になる。

まずは Phase 1（価格・出来高のみ）で実装し、次に sector/industry を加える段階導入を推奨する。
