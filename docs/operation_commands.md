# Operation Commands

## TASK-016: race filter comparison

This is an offline / shadow evaluation only. Do not treat it as a production BUY rule.

```powershell
$env:PYTHONPATH='.'
py -3.12 -m src.eval.ablation_and_bottleneck
py -3.12 -m src.eval.write_t016_race_filter_decision
```

Outputs:
- `reports/race_filter_comparison.json`
- `reports/t016_race_filter_decision.md`
- `reports/t016_race_filter_decision.json`

Snapshot note:
- The default inputs are pinned to the `data/tmp/20260311_eval` offline evaluation snapshot.
- They are not the live `today_*` inputs used by the daily pipeline.

## TASK-017B: historical result txt validation

This is a shadow validation only. Do not fold it into the production BUY rule.

```powershell
py -m src.ingest.official_k_loader --date 20260404 --input-dir data/raw/official/results
py -m src.pipeline.collect_historical_inputs --start-date 20260401 --end-date 20260425 --jcd all --stages result_txt --input-dir data/raw/official/results
py -m src.evaluation.audit_historical_inputs --start-date 20260401 --end-date 20260425 --jcd all
py -m src.evaluation.backtest_range --start-date 20260401 --end-date 20260425 --jcd all --stake 100 --prediction-source backfill
py -m src.pipeline.debug_result --date 20260404 --jcd 22 --rno 1 --source txt --input-dir data/raw/official/results
```

Outputs:
- `data/normalized/YYYYMMDD/{jcd}/race_{rno}.json`
- `reports/backtest/20260401_20260425_historical_input_audit.csv`
- `reports/backtest/20260401_20260425_historical_input_audit.json`
- `reports/backtest/20260401_20260425_backfill_summary.json`
- `reports/backtest/20260401_20260425_backfill_coverage.json`
- `reports/backtest/20260401_20260425_backfill_tuning_readiness.json`

Snapshot note:
- `KYYMMDD.TXT` is used only for settlement and backtest.
- It does not change BUY thresholds or model weights.
- If HTML result is missing but K result exists, settlement can proceed from `official_txt_k`.
- If a date still cannot settle, check `reports/errors/` and the audit output first.

## TASK-017C: K result import and refresh

This is a shadow / offline maintenance flow only. Do not change the production BUY rule here.

```powershell
py -m src.pipeline.import_k_results --input-dir data/inbox/k_results --target-dir data/raw/official/results
py -m src.evaluation.export_missing_k_checklist --start-date 20260401 --end-date 20260425 --input-dir data/raw/official/results
py -m src.pipeline.import_and_refresh_k_results --input-dir data/inbox/k_results --start-date 20260401 --end-date 20260425 --jcd all --stake 100
```

Outputs:
- `reports/backtest/k_result_import_manifest.json`
- `reports/backtest/k_result_import_manifest.csv`
- `reports/backtest/20260401_20260425_missing_k_checklist.md`
- `reports/backtest/20260401_20260425_missing_k_checklist.csv`
- `reports/backtest/20260401_20260425_import_refresh_summary.json`

Snapshot note:
- `data/inbox/k_results/` is the dropbox for new K files.
- Missing K files remain `missing`; no sample補完 is allowed.
- Keep `BUY` thresholds and score weights unchanged while K coverage is still being built.

### K result coverage refresh

This is a shadow validation only. Do not fold it into the production BUY rule.

```powershell
py -m src.evaluation.audit_k_result_coverage --start-date 20260401 --end-date 20260425 --input-dir data/raw/official/results
py -m src.pipeline.refresh_k_backtest --start-date 20260401 --end-date 20260425 --jcd all --input-dir data/raw/official/results --stake 100
```

Outputs:
- `reports/backtest/20260401_20260425_k_result_coverage.json`
- `reports/backtest/20260401_20260425_k_result_coverage.csv`
- `reports/backtest/20260401_20260425_k_refresh_summary.json`

Snapshot note:
- This is only for missing-day diagnosis and backfill readiness.
- It does not change the production BUY rule.
- If missing dates remain, inspect `reports/backtest/20260401_20260425_k_result_coverage.json` before retrying.

## TASK-017B: race filter multiday validation

This is a shadow validation only. Do not fold it into the production BUY rule.

```powershell
$env:PYTHONPATH='.'
py -3.12 -m src.eval.ablation_and_bottleneck
py -3.12 -m src.eval.run_t017_race_filter_multiday
```

Outputs:
- `reports/t017_race_filter_multiday_validation.md`
- `reports/t017_race_filter_multiday_validation.json`
- `reports/t017_missing_snapshot_diagnostics.md`
- `reports/t017_missing_snapshot_diagnostics.json`

Snapshot note:
- This runner works on offline / shadow validation snapshots.
- It does not change the live daily pipeline or BUY rule.
- If a target date is missing, check `reports/t017_missing_snapshot_diagnostics.md` first.

## TASK-018: race filter promotion gate

This is a gate definition step only. Even if a filter ever reports PASS, keep `allow_production_adoption=false` until a later explicit approval task.

```powershell
$env:PYTHONPATH='.'
py -3.12 -m src.eval.run_t018_race_filter_promotion_gate
```

Inputs:
- `reports/t017_race_filter_multiday_validation.json`
- `reports/t017_race_filter_multiday_validation.md`
- `reports/t017_missing_snapshot_diagnostics.json`
- `reports/t017_missing_snapshot_diagnostics.md`
- `config/race_filter_promotion_gate.json`

Outputs:
- `reports/t018_race_filter_promotion_gate.md`
- `reports/t018_race_filter_promotion_gate.json`

Snapshot note:
- This is a shadow gate for adoption criteria only.
- It does not change the production BUY rule.
- `PASS` in this gate is not enough to adopt while `allow_production_adoption=false`.

## TASK-018B: live shadow operation

This is the live shadow operation phase. It does not change the production BUY rule.

```powershell
py -m src.pipeline.health_check --date today
py -m src.evaluation.live_operation_summary --start-date 20260425 --end-date today
py -m src.evaluation.tuning_gate --start-date 20260425 --end-date today
```

Daily ops:

```powershell
scripts/run_daily_freeze.bat
scripts/run_evening_settle.bat
scripts/run_daily_report.bat
```

K files:

```powershell
scripts/check_k_inbox.bat
scripts/import_k_results.bat
scripts/import_and_refresh_k_results.bat
```

Snapshot note:
- This phase keeps daily frozen bets, settlement, and reporting flowing.
- `canTuneWithLiveOnly=false` means the live sample is still too small for BUY threshold tuning.
- `canTuneWithBackfill=true` still does not allow production adoption without live confirmation.
- Keep BUY thresholds and model weights unchanged until the live gate passes.

## TASK-019: Windows Task Scheduler registration and daily operation confirmation

This is a live shadow operation support step only. It does not change the production BUY rule.

Task registration:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/register_tasks.ps1
```

Registered tasks:
- `Boatrace_DailyFreeze` at `07:00`
- `Boatrace_HealthCheck` at `07:30`
- `Boatrace_EveningSettle` at `21:30`
- `Boatrace_DailyReport` at `22:00`

Task status:

```powershell
py -m src.pipeline.task_status
py -m src.pipeline.health_check --date today
```

Manual task runs:

```powershell
scripts/run_daily_freeze.bat
scripts/run_evening_settle.bat
scripts/run_daily_report.bat
scripts/health_check.bat
```

Task removal:

```powershell
Unregister-ScheduledTask -TaskName Boatrace_DailyFreeze -Confirm:$false
Unregister-ScheduledTask -TaskName Boatrace_EveningSettle -Confirm:$false
Unregister-ScheduledTask -TaskName Boatrace_HealthCheck -Confirm:$false
Unregister-ScheduledTask -TaskName Boatrace_DailyReport -Confirm:$false
```

Logs and checks:

- Logs are written under `logs/tasks/`.
- Check the latest `logs/tasks/*_YYYYMMDD_*.log` when a task fails.
- If `health_check` warns, review `reports/monitoring/task_status.json` and `reports/monitoring/task_status.md` first.
- Keep BUY thresholds unchanged while this phase is still building live settlement sample size.

## TASK-019: race filter snapshot coverage expansion

This is a shadow data-prep step only. Do not fold it into the production BUY rule.

```powershell
$env:PYTHONPATH='.'
py -3.12 -m src.eval.run_t019_snapshot_coverage
```

Inputs:
- `data/raw/official/entries/B2604*.TXT`
- `data/raw/official/results/K2604*.TXT`
- `data/normalized/202604*`
- `reports/daily/2026-04-*`

Outputs:
- `reports/t019_snapshot_coverage.md`
- `reports/t019_snapshot_coverage.json`
- `reports/t019_snapshot_build_result.md`
- `reports/t019_snapshot_build_result.json`
- `data/tmp/YYYYMMDD_eval/`

Snapshot note:
- This only builds shadow/eval snapshots and reruns `TASK-017` / `TASK-018`.
- It does not change the live daily pipeline or BUY rule.
- `2026-04-26` is treated as provisional and is not forced into validation.
