# Claude Code Instructions

This repository is a boat-race prediction MVP. Work conservatively and keep changes small.

## Read First

- `tasks/CURRENT_TASK.md`
- `.agents/rules/data-governance.md`
- `.agents/rules/modeling-discipline.md`
- `.agents/rules/reporting-discipline.md`
- `.agents/workflows/full_run.json`

## Operating Rules

- Prefer the smallest possible diff that solves the task.
- Do not change data contracts or model interfaces unless the task explicitly requires it.
- Keep `date`-based, leakage-safe evaluation intact.
- When evaluating strategies, preserve existing outputs in `reports/`.
- Use `PYTHONPATH='.'` for Python entry points when needed.
- If a task is ambiguous, inspect the current code and existing task files before editing.

## Safe Defaults

- Treat `data/raw/official/` as the primary source of truth.
- Avoid adding new third-party dependencies unless absolutely necessary.
- Keep commands reproducible on Windows PowerShell.
- Never assume offline backtests are valid if they use future information.

## Useful Entry Points

- Daily pipeline: `master_run.py`
- Ingest: `src/ingest/build_processed.py`
- Features: `src/features/build_features.py`
- Model: `src/models/train_win_model.py`
- Prediction: `src/models/predict_win_proba.py`
- Strategy: `src/strategy/generate_trifecta_candidates.py`
- EV / skip: `src/strategy/evaluate_ev_and_skip.py`
- Reporting: `src/report/build_daily_report.py`

## Expected Style

- Keep code changes explicit and easy to review.
- Add or update validation only when it improves trust in the pipeline.
- Prefer text or JSON reports for auditability.
