# Codex Tasks

## Current Focus

- 日次運用の安定化
- Ops Goal Board の可視化
- paper validation の蓄積とブロッカー切り分け
- 場別 AI 予想ビューの実運用化

## Active Rules

- 予想ロジックは変更しない
- BUY 閾値は変更しない
- EV 計算は変更しない
- `hard_guard` は変更しない
- 外部予想は BUY 判定に混ぜない
- 欠損は欠損として扱う
- サンプル、固定値、ダミーで成功扱いしない

## Frequent Actions

1. `run_paper_ops_preflight.bat`
2. `run_paper_ops_morning.bat`
3. `run_paper_ops_evening.bat`
4. `run_paper_ops_monitor.bat`
5. `run_daily_report.bat`
6. `run_ops_goal_board.bat`
7. `run_paper_validation_refresh.bat`

## Common Checks

- `pre_race_run.json`
- `odds_refresh_run.json`
- `post_race_run.json`
- `daily_summary.json`
- `skip_decisions.csv`
- `prediction_sheet.json`
- `frozen_bets.json`
- `prediction_review.json`
- `consensus_sheet.json`
- `ops_board.json`

## Working Style

- まず既存の文書と成果物を見る
- 小さい修正で終わるところから直す
- 終わったら、次の最小アクションを 1 つだけ残す

