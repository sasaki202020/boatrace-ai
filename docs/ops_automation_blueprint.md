# 運用自動化 設計図

最終更新: 2026-04-03

このドキュメントは、`boatrace-ai-mvp` を「予測コード」ではなく「運用システム」として回すための実装設計図です。  
今の repo に存在するスクリプトと、追加済みの `src/jobs` を前提にしています。

## 目的

毎日の運用を次の順で安全に自動化すること。

1. データ更新
2. 特徴量更新
3. 予測生成
4. バックテスト
5. 劣化判定
6. 条件付き再学習
7. 新旧比較
8. 採用

重要:

- 学習そのものより先に、検証とガードを固める
- 新モデルは即採用しない
- 未来データを使わない
- 実オッズ未取得時は `BUY` を出さない

## 現在の実装済み部品

### データ更新

- `src/data/fetch_race_results.py`
- `src/data/fetch_live_odds.py`
- `src/data/build_historical_races.py`
- `src/features/build_features.py`

### 学習 / 予測

- `src/models/train_win_model.py`
- `src/models/predict_win_proba.py`
- `src/eval/train_probability_calibrator.py`

### 戦略

- `src/strategy/generate_trifecta_candidates.py`
- `src/strategy/evaluate_ev_and_skip.py`

### レポート / 検証

- `src/eval/backtest_buy_skip.py`
- `src/eval/evaluate_experiments.py`
- `src/report/build_daily_report.py`
- `src/report/build_ops_dashboard.py`

### 追加済みの運用ジョブ

- `src/jobs/daily_pipeline.py`
- `src/jobs/backtest_runner.py`
- `src/jobs/model_guard.py`
- `src/jobs/retrain.py`
- `src/jobs/model_compare.py`

## 推奨ディレクトリ構成

既存 repo に寄せると、運用で触る中心はこの配置です。

```text
src/
  data/
  features/
  models/
  strategy/
  eval/
  report/
  jobs/

data/
  raw/
  processed/
  features/
  strategy_outputs/

models/
  current/
  candidate/
  probability_calibrator.json

reports/
  daily/
  experiments/
  ops/
```

## 1日の自動ループ

### フルループ

```text
① 結果取得
② historical 更新
③ 特徴量更新
④ 学習
⑤ 確率校正
⑥ 当日予測
⑦ 候補生成
⑧ 実オッズ取得
⑨ BUY/WATCH/SKIP 判定
⑩ バックテスト
⑪ 劣化判定
⑫ レポート更新
```

### 実際の入口

`src/jobs/daily_pipeline.py` が司令塔です。

対応モード:

- `update`
- `train`
- `predict`
- `backtest`
- `guard`
- `full`

## 実行コマンド

### 毎日の標準実行

```powershell
py -m src.jobs.daily_pipeline --mode full
```

### 軽い確認だけ

```powershell
py -m src.jobs.daily_pipeline --mode guard
```

### バックテスト単体

```powershell
py -m src.jobs.backtest_runner
```

### 採用ガード単体

```powershell
py -m src.jobs.model_guard
```

### 候補モデルの作成

```powershell
py -m src.jobs.retrain --promote-current-snapshot
```

### 候補モデルと現行の比較

```powershell
py -m src.jobs.model_compare
```

### ガード後に条件付きで候補モデルを作成して比較

```powershell
py -m src.jobs.daily_pipeline --mode full --conditional-retrain
```

## 各ジョブの役割

### `src/jobs/daily_pipeline.py`

役割:

- 毎日まわす司令塔
- 途中で失敗したら停止
- `reports/ops/daily_pipeline_report.json` に実行結果を書き出す

設計方針:

- いきなり再学習しない
- まず検証を回す
- 失敗時は続行しない

### `src/jobs/backtest_runner.py`

役割:

- 最新の `skip_decisions.csv` を使って運用バックテスト
- 既存の `src.eval.backtest_buy_skip` をラップ

出力:

- `reports/ops/backtest_summary_latest.json`
- `reports/ops/backtest_race_results_latest.csv`
- `reports/ops/backtest_daily_summary_latest.csv`
- `reports/ops/backtest_equity_curve_latest.csv`
- `reports/ops/backtest_runner_report.json`

主要指標:

- `buy_count`
- `hit_count`
- `hit_rate`
- `roi`
- `avg_odds`
- `max_drawdown`
- `max_consecutive_loss`

### `src/jobs/model_guard.py`

役割:

- 最新バックテストが採用条件を満たすか判定
- 条件を満たしたときだけ baseline 更新可能

出力:

- `reports/ops/model_guard_latest.json`

基本判定:

- `ROI >= 1.0`
- `buy_count >= 3`
- `max_drawdown >= -0.2`

現在は保守的に `HOLD` を返す設計です。

### `src/jobs/retrain.py`

役割:

- 現行モデルを壊さずに candidate モデルを作る
- 学習 / 校正 / 予測 / 候補生成 / 判定 / バックテストを一式実行
- 実行前の production artifact は最後に復元する

出力:

- `reports/ops/candidate_runs/<timestamp>/retrain_report.json`
- 同ディレクトリ配下に candidate backtest とモデル artifact 一式

### `src/jobs/model_compare.py`

役割:

- 最新 candidate run と current の運用成績を比較
- `models/candidate` を `models/current` と production `models/` に昇格できる

出力:

- `reports/ops/model_compare_latest.json`

基本方針:

- 比較だけを既定にする
- `--promote` を付けた時だけ採用する

## 再学習ポリシー

完全自動再学習は推奨しません。  
まずは条件付き再学習にします。

### 再学習トリガ候補

- 新規レースが 1000 件以上たまった
- 直近30日の ROI が 1.0 未満で 2回連続
- BUY 件数が長期間ゼロ
- 特定場だけ崩れた

### 再学習条件の例

```text
if ROI < 1.0 and buy_count >= 30:
    retrain candidate model
```

現在の `daily_pipeline.py` では、`--conditional-retrain` を付けると
`model_guard` が `HOLD` を返した時にだけ `retrain -> model_compare`
を続けて実行できます。

### 採用ルール

新モデルは必ず旧モデルと比較する。

採用条件の例:

- 新モデル ROI > 旧モデル ROI
- BUY件数が旧モデルの 80% 未満まで減っていない
- max_drawdown が悪化していない

## 安全装置

### 必須

- 学習 / 検証 / 運用期間を分離
- 締切後オッズを使わない
- 実オッズなしでは `BUY` を出さない
- 新モデルは即採用しない
- baseline との比較を必須にする

### 特に危険なもの

- 結果確定後にしか分からない値を特徴量に混ぜる
- ROI が少数サンプルで良かっただけのモデルを採用する
- 高オッズ案件だけで「勝っているように見える」状態

## 推奨の定期実行

### 毎日

```powershell
py -m src.jobs.daily_pipeline --mode predict
py -m src.jobs.daily_pipeline --mode backtest
py -m src.jobs.daily_pipeline --mode guard
```

### 週次

```powershell
py -m src.jobs.daily_pipeline --mode full
```

### 運用用 bat 入口

```powershell
ops_pipeline.bat predict
ops_pipeline.bat guard
ops_pipeline.bat weekly
```

理由:

- 毎日は軽く回す
- 学習を含むフル更新は週次に寄せる
- 毎日フル学習は過学習や事故の原因になりやすい

## ダッシュボード連携

Web ダッシュボードは以下をすでに表示できます。

- gate health
- strategy mode
- recent30 の KPI
- ops health
  - 採用ガード
  - 最新バックテスト
  - パイプライン状態

つまり、`reports/ops/*.json` が更新されれば、運用状態は画面でも確認できます。

## 今後の追加候補

### 優先度 高

1. `scripts/register_daily_pipeline.ps1`
   - Windows タスクスケジューラ登録
   - `ops_pipeline.bat` を daily / weekly で回す

### 優先度 中

4. pre-race 欠損の削減
5. 実オッズ取得安定化
6. approx_prob 校正レポート自動生成

## 段階的な完成条件

### Phase 1

- daily pipeline が止まらず動く
- backtest と guard が JSON を更新する

### Phase 2

- retrain が candidate 保存までできる
- model_compare で採用判定できる

### Phase 3

- Windows スケジューラで定期実行
- 毎日レポート自動更新

### Phase 4

- 直近劣化を自動検知
- 必要時だけ再学習

## 最後に

この repo で大事なのは、「全部自動」ではなく「壊れたら止める」ことです。  
今の構成なら、次にやるべきは大きなリファクタではなく、`retrain.py` と `model_compare.py` を追加して候補モデルの比較採用を完成させることです。
