# Codex Working Rules

## Goal
- Read `AGENTS.md` first.
- Read the task file under `tasks/`.
- Implement only the requested task with minimal changes.

## Project scope
- Main project root: `boatrace-ai-mvp/`
- Primary pipeline:
  1. `src/ingest/build_processed.py`
  2. `src/features/build_features.py`
  3. `src/models/train_win_model.py`
  4. `src/models/predict_win_proba.py`
  5. `src/strategy/generate_trifecta_candidates.py`
  6. `src/strategy/evaluate_ev_and_skip.py`
  7. `src/report/build_daily_report.py`

## Implementation rules
- Do not broad-refactor.
- Do not change unrelated files.
- Preserve existing CSV column names unless the task explicitly allows additions.
- Prefer fixing the smallest upstream or analysis point that explains the issue.
- If a generated report is known to be wrong, fix the logic before interpreting the number.

## Verification rules
- Run the minimum commands needed to verify the task.
- If a task creates a report, save it under `reports/`.
- If a task creates a helper script, place it under `src/eval/` unless another location is clearly better.

## Final response format
- `変更ファイル`
- `変更理由`
- `実行コマンド`
- `確認結果`
- `次の1手`
