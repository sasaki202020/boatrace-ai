# Codex Context

## Project

`boatrace-ai-mvp` は、競艇の公開データを使って日次の予想、保存、照合、監査、Web 表示を回すローカル運用プロジェクトです。

この repo では、実データがある日は実データだけを表示し、欠損がある日は欠損として見せます。サンプル表示で成功したように見せることはしません。

## Main Areas

- `src/pipeline/`
  - 日次処理、health check、Ops Goal Board、予想シート、結果照合
- `src/web/`
  - Flask API とローカル Web UI
- `src/web/static/suminoe_ai.html`
  - 場別の独自 AI 予想ビュー
- `scripts/`
  - `run_paper_ops_*`, `run_daily_report.bat`, `run_ops_goal_board.bat` などの運用ラッパー
- `reports/daily/YYYY-MM-DD/`
  - canonical な日次成果物
- `reports/predictions/YYYY-MM-DD/`
  - `prediction_sheet`, `frozen_bets`, `prediction_review`
- `reports/consensus/YYYY-MM-DD/`
  - 外部予想との一致スコア
- `reports/repo_audit/`
  - 監査、進捗、修正結果

## Startup

ローカル Web を見る:

```powershell
py -m src.web.app --host 127.0.0.1 --port 5000
```

主に見る URL:

- `/boatrace/portal/?v=5`
- `/boatrace/hamanako/20260507?v=externalfetch`
- `/predictions`
- `/ops-board`

## Daily Flow

標準の順番:

```powershell
scripts\run_paper_ops_preflight.bat YYYY-MM-DD
scripts\run_paper_ops_morning.bat YYYY-MM-DD
scripts\run_paper_ops_evening.bat YYYY-MM-DD
scripts\run_paper_ops_monitor.bat YYYY-MM-DD
```

レポート生成:

```powershell
scripts\run_daily_report.bat YYYY-MM-DD
scripts\run_ops_goal_board.bat YYYY-MM-DD
scripts\run_paper_validation_refresh.bat YYYY-MM-DD
```

## Verification

最低限の構文確認:

```powershell
py -m py_compile src\web\app.py src\pipeline\health_check.py src\pipeline\ops_goal_board.py scripts\generate_ops_goal_board.py
```

見るべき指標:

- `latestCompleteOpsDate`
- `completeOpsReady`
- `primaryBlocker`
- `nextAction`
- `paperEligibleCandidateCount`
- `remainingPaperEligibleCandidateCount`
- `liveSettledBetCount`
- `revenueValidationReady`

## Non-goals

- BUY 判定の変更
- EV 計算の変更
- 予想ロジックの変更
- 実購入処理の追加
- サンプルやダミーの production 混入

## When Resuming

次回 Codex は、まずこの 3 つを読む。

1. `AGENTS.md`
2. `docs/CODEX_CONTEXT.md`
3. `docs/CODEX_HANDOFF.md`
