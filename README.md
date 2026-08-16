# boatrace-ai-mvp

競艇の公開データを使い、ローカルで日次予想、オッズ更新、結果照合、監査、Web 表示まで回す運用プロジェクトです。

このプロジェクトは「サンプルを見せるデモ」ではなく、実データがある日は実データだけを表示し、欠損がある日は欠損として見せる方針です。

## 原則

- 予想ロジックを勝手に変更しない
- BUY 閾値を勝手に変更しない
- EV 計算を勝手に変更しない
- `hard_guard` を勝手に変更しない
- サンプル、ダミー、固定買い目を production 表示に混ぜない
- `source_not_ready` / `result_data_missing` / `future_date_not_ready` を miss 扱いしない
- 外部予想や consensus は表示、比較、検証用であり、BUY 判定には混ぜない
- 実賭け前提の自動購入処理は実装しない

## 主な機能

- 日次運用: `preflight -> morning -> evening -> monitor`
- 独自 AI 予想ビュー: `/boatrace/<venue>/<YYYYMMDD>`
- 紙上予想一覧: `/predictions`
- Ops Goal Board: `/ops-board`
- prediction sheet / frozen bets / prediction review の生成
- 外部予想との consensus 表示
- paper validation 進捗の集計
- health check と complete ops 判定

## ディレクトリ

- `src/pipeline/`: 日次処理、health check、Ops Goal Board
- `src/web/`: Flask API とローカル Web UI
- `src/web/static/suminoe_ai.html`: 場別の独自 AI 予想ビュー
- `scripts/`: 日次運用用 `.bat` と補助スクリプト
- `reports/daily/YYYY-MM-DD/`: canonical な日次成果物
- `reports/predictions/YYYY-MM-DD/`: prediction sheet / frozen bets / review
- `reports/consensus/YYYY-MM-DD/`: 外部予想一致スコア
- `data/ui/YYYYMMDD/`: UI 用 JSON
- `reports/repo_audit/`: 監査、進捗、修正結果

## ローカル起動

```powershell
Set-Location <repository-root>
py -m src.web.app --host 127.0.0.1 --port 5000
```

主な URL:

- `http://127.0.0.1:5000/boatrace/portal/?v=5`
- `http://127.0.0.1:5000/boatrace/hamanako/20260507?v=externalfetch`
- `http://127.0.0.1:5000/predictions`
- `http://127.0.0.1:5000/ops-board`
- `http://127.0.0.1:5000/api/venue-ai-yosou?date=20260507&jcd=06`
- `http://127.0.0.1:5000/api/ops-goal-board?date=2026-05-12`

## 日次運用

基本の実行順:

```powershell
scripts\run_paper_ops_preflight.bat YYYY-MM-DD
scripts\run_paper_ops_morning.bat YYYY-MM-DD
scripts\run_paper_ops_evening.bat YYYY-MM-DD
scripts\run_paper_ops_monitor.bat YYYY-MM-DD
```

例:

```powershell
scripts\run_paper_ops_preflight.bat 2026-05-12
scripts\run_paper_ops_morning.bat 2026-05-12
scripts\run_paper_ops_evening.bat 2026-05-12
scripts\run_paper_ops_monitor.bat 2026-05-12
```

日次レポートと Ops Goal Board:

```powershell
scripts\run_daily_report.bat 2026-05-12
scripts\run_ops_goal_board.bat 2026-05-12
```

paper validation の再集計:

```powershell
scripts\run_paper_validation_refresh.bat 2026-05-12
```

next action の半自動処理:

```powershell
py scripts\run_paper_validation_next_actions.py --start-date 2026-04-01 --end-date 2026-05-12 --max-actions 5 --dry-run
py scripts\run_paper_validation_next_actions.py --start-date 2026-04-01 --end-date 2026-05-12 --max-actions 5 --execute
```

## 予想関連

prediction sheet:

```powershell
scripts\run_prediction_sheet.bat 2026-05-12
```

prediction review:

```powershell
scripts\run_prediction_review.bat 2026-05-12
```

consensus sheet:

```powershell
py scripts\build_consensus_sheet.py --date 2026-05-12
```

## 重要な成果物

日次:

- `reports/daily/YYYY-MM-DD/preflight_source_check.json`
- `reports/daily/YYYY-MM-DD/pre_race_run.json`
- `reports/daily/YYYY-MM-DD/odds_refresh_run.json`
- `reports/daily/YYYY-MM-DD/post_race_run.json`
- `reports/daily/YYYY-MM-DD/daily_summary.json`
- `reports/daily/YYYY-MM-DD/daily_report.json`
- `reports/daily/YYYY-MM-DD/ops_board.json`
- `reports/daily/YYYY-MM-DD/ops_board.md`
- `reports/daily/YYYY-MM-DD/skip_decisions.csv`

予想:

- `reports/predictions/YYYY-MM-DD/prediction_sheet.json`
- `reports/predictions/YYYY-MM-DD/frozen_bets.json`
- `reports/predictions/YYYY-MM-DD/prediction_review.json`

UI:

- `data/ui/YYYYMMDD/raceyosou_<jcd>.json`
- `data/ui/YYYYMMDD/prediction_sheet.json`
- `data/ui/YYYYMMDD/consensus_sheet.json`
- `data/ui/YYYYMMDD/ops_board.json`

監査:

- `reports/monitoring/YYYYMMDD_health_check.json`
- `reports/repo_audit/final_goal_progress.json`
- `reports/monitoring/paper_validation_summary.json`
- `reports/monitoring/paper_validation_gate.json`

## Web 表示

### 場別 AI 予想ビュー

`/boatrace/<venue>/<YYYYMMDD>` は `src/web/static/suminoe_ai.html` が表示します。

表示するもの:

- `date`
- `venue`
- `event`
- `generatedAt`
- `dataStatus`
- `warnings`
- race 単位の BUY 件数
- 最高 EV
- 最高 prob
- `decision`
- `prob`
- `odds`
- `ev`
- `stake`
- `stopReason`

実データが無い場合、仮のレース、仮の選手、仮の買い目は表示しません。

### Predictions

`/predictions` は paper validation と consensus を確認する画面です。

見る指標:

- BUY / WATCH / PAPER / SKIP
- consensus grade
- consensus score
- `paperEligibleCandidateCount`
- `remainingPaperEligibleCandidateCount`
- `primaryBlocker`

### Ops Goal Board

`/ops-board?date=YYYY-MM-DD` は日次運用の進捗を Kanban 形式で表示します。

見るカード:

- `pre_race_prediction`
- `odds_refresh`
- `post_race_settlement`
- `daily_summary_generation`
- `health_check`
- `complete_ops`

## health check

```powershell
py -m src.pipeline.health_check --date 2026-05-12
```

主な確認値:

- `latestCompleteOpsDate`
- `completeOpsReady`
- `primaryBlocker`
- `nextAction`
- `paperEligibleCandidateCount`
- `remainingPaperEligibleCandidateCount`
- `liveSettledBetCount`
- `revenueValidationReady`
- `opsBoardExists`
- `opsBoardSchemaOk`

## complete ops の考え方

complete ops は、日次成果物が揃い、結果未取得や未公開日を誤って成功扱いしていない状態を指します。

complete ops に必要な代表ファイル:

- `pre_race_run.json`
- `odds_refresh_run.json`
- `post_race_run.json`
- `daily_summary.json`
- `skip_decisions.csv`

complete ops に含めないもの:

- `result_data_missing`
- `raw_missing`
- `future_date_not_ready`
- `source_not_ready`
- `predictions_missing`
- `schema_error`
- `pipeline_failure`

## 検証コマンド

最小構文チェック:

```powershell
py -m py_compile src\web\app.py src\pipeline\health_check.py src\pipeline\ops_goal_board.py scripts\generate_ops_goal_board.py
```

画面確認:

```powershell
py -m src.web.app --host 127.0.0.1 --port 5000
```

API 確認:

```powershell
py - <<'PY'
import urllib.request
for url in [
    "http://127.0.0.1:5000/predictions",
    "http://127.0.0.1:5000/ops-board?date=2026-05-12",
    "http://127.0.0.1:5000/api/venue-ai-yosou?date=20260507&jcd=06",
]:
    print(url, urllib.request.urlopen(url).status)
PY
```

## 現在の主な未達条件

収益検証は、十分な settled sample が貯まるまで完了扱いにしません。

代表的な blocker:

- `paper_eligible_candidate_count_too_low`
- `liveSettledBetCount_below_100`
- `result_data_missing`
- `source_not_ready`

## 運用上の注意

- `reports/daily/YYYY-MM-DD/` を canonical とする
- `data/ui/YYYYMMDD/` は UI 用出力として扱う
- legacy の `YYYYMMDD` ディレクトリは読み取り互換のみ
- 欠損を埋めるための daily summary や prediction sheet は作らない
- `frozen_bets` は保存済み予想の記録として扱い、結果取得後に都合よく書き換えない
