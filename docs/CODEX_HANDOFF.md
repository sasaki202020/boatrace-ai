# Codex Handoff

## Last Known State

- ローカル Web は `src/web.app` で起動する
- `suminoe_ai.html` が場別 AI 予想ビューの実体
- `ops-board` は日次運用の進捗表示に使う
- `daily_summary.json` は complete ops と health check の重要入力
- `prediction_sheet`, `prediction_review`, `consensus_sheet` は表示と検証の中心

## Current Constraints

- 予想ロジックは未変更
- BUY 閾値は未変更
- EV 計算は未変更
- settlement ロジックは未変更
- sample fallback は使わない
- `source_not_ready` / `result_data_missing` / `future_date_not_ready` は成功扱いしない

## Useful Commands

```powershell
py -m src.web.app --host 127.0.0.1 --port 5000
py -m src.pipeline.health_check --date 2026-05-12
scripts\run_daily_report.bat 2026-05-12
scripts\run_ops_goal_board.bat 2026-05-12
scripts\run_paper_validation_refresh.bat 2026-05-12
```

## If You Resume Here

1. `AGENTS.md` を読む
2. `docs/CODEX_CONTEXT.md` を読む
3. `docs/CODEX_HANDOFF.md` を読む
4. `reports/repo_audit/final_goal_progress.json` と `reports/repo_audit/paper_validation_next_dates.json` を見る
5. 直近の壊れ方があるなら、まずその 1 点だけ直す
