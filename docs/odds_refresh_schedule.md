# Odds Refresh Schedule

This project uses two odds refresh passes per day.

## Fixed schedule

- Morning: `07:10`
  - Command: `ops_pipeline.bat odds-refresh`
  - Purpose: capture the day's main `real_odds_available` set as early as possible.
- Late backfill: `22:30`
  - Command: `ops_pipeline.bat odds-refresh-late`
  - Purpose: retry races that were previously classified as `pending_unpublished`.

## Registered tasks

- `BoatraceAI_OddsRefresh_Morning_0710`
  - Daily `07:10`
  - Command: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\goo10\競艇\boatrace-ai-mvp\scripts\run_odds_refresh_phase.ps1 -Phase morning -Delay 1.0`
- `BoatraceAI_OddsRefresh_Late_2230`
  - Daily `22:30`
  - Command: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\goo10\競艇\boatrace-ai-mvp\scripts\run_odds_refresh_phase.ps1 -Phase late -Delay 1.0 -Refresh -PendingOnly`

## Why this schedule

- The morning pass is the only pass that consistently increased `real_odds_available`.
- Short delayed retries did not increase available odds on the same day slice.
- A single late backfill is kept to catch races that open later without adding repeated refresh loops.

## Manual commands

```powershell
cd C:\Users\goo10\競艇\boatrace-ai-mvp
ops_pipeline.bat odds-refresh
ops_pipeline.bat odds-refresh-late
```

## Scheduler registration

```powershell
cd C:\Users\goo10\競艇\boatrace-ai-mvp
powershell -ExecutionPolicy Bypass -File .\scripts\register_odds_refresh_tasks.ps1
```

## Daily logs

- `logs\odds_refresh\YYYY-MM-DD.log`
- `logs\odds_refresh_late\YYYY-MM-DD.log`
