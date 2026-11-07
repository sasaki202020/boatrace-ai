$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$bat = Join-Path $root "ops_pipeline.bat"

if (!(Test-Path $bat)) {
  throw "ops_pipeline.bat not found: $bat"
}

$taskDailyPredict = "BoatraceAI_Ops_Predict_0800"
$taskDailyGuard = "BoatraceAI_Ops_Guard_0830"
$taskWeeklyFull = "BoatraceAI_Ops_Weekly_0400"

schtasks /Create /F /SC DAILY /TN $taskDailyPredict /TR "`"$bat`" predict" /ST 08:00
schtasks /Create /F /SC DAILY /TN $taskDailyGuard /TR "`"$bat`" guard" /ST 08:30
schtasks /Create /F /SC WEEKLY /D SUN /TN $taskWeeklyFull /TR "`"$bat`" weekly" /ST 04:00

Write-Host "Registered tasks:"
Write-Host " - $taskDailyPredict (DAILY 08:00)"
Write-Host " - $taskDailyGuard (DAILY 08:30)"
Write-Host " - $taskWeeklyFull (WEEKLY SUN 04:00)"
