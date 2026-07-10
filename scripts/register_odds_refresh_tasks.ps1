$ErrorActionPreference = "Stop"

$ThisScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PhaseRunner = Join-Path $ThisScriptRoot "run_odds_refresh_phase.ps1"

if (!(Test-Path $PhaseRunner)) {
  throw "run_odds_refresh_phase.ps1 not found: $PhaseRunner"
}

$taskMorningOdds = "BoatraceAI_OddsRefresh_Morning_0710"
$taskLateOdds = "BoatraceAI_OddsRefresh_Late_2230"

$MorningArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$PhaseRunner`" -Phase morning -Delay 1.0"
$LateArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$PhaseRunner`" -Phase late -Delay 1.0 -Refresh -PendingOnly"

schtasks /Create /F /SC DAILY /TN $taskMorningOdds /TR "`"powershell.exe`" $MorningArgs" /ST 07:10
schtasks /Create /F /SC DAILY /TN $taskLateOdds /TR "`"powershell.exe`" $LateArgs" /ST 22:30

Write-Host "Registered tasks:"
Write-Host " - $taskMorningOdds (DAILY 07:10)"
Write-Host " - $taskLateOdds (DAILY 22:30)"
