$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$bat = Join-Path $root "weekly_auto_filter.bat"

if (!(Test-Path $bat)) {
  throw "weekly_auto_filter.bat not found: $bat"
}

$taskName = "BoatraceAI_AutoFilterWeekly"

schtasks /Create /F /SC WEEKLY /D SUN /TN $taskName /TR "cmd /c `"$bat`"" /ST 04:00

Write-Host "Registered task: $taskName (WEEKLY / SUN 04:00)"
