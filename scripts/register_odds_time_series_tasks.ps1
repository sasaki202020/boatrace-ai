$ErrorActionPreference = "Stop"

$ThisScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ThisScriptRoot
$PhaseRunner = Join-Path $ThisScriptRoot "run_odds_time_series_phase.ps1"
if (!(Test-Path $PhaseRunner)) {
    throw "Phase runner not found: $PhaseRunner"
}

$TaskMorning = "BoatraceAI_OddsTimeSeries_Morning_0710"
$TaskLate = "BoatraceAI_OddsTimeSeries_Late_1530"
$TaskFinal = "BoatraceAI_OddsTimeSeries_Final_2245"

$MorningArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$PhaseRunner`" -Phase morning -Delay 1.0"
$LateArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$PhaseRunner`" -Phase late_refresh -Delay 1.0 -WaitMinutes 0.0"
$FinalArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$PhaseRunner`" -Phase final_refresh -Delay 1.0"

$ActionMorning = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $MorningArgs
$ActionLate = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $LateArgs
$ActionFinal = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $FinalArgs

$TriggerMorning = New-ScheduledTaskTrigger -Daily -At 7:10AM
$TriggerLate = New-ScheduledTaskTrigger -Daily -At 3:30PM
$TriggerFinal = New-ScheduledTaskTrigger -Daily -At 10:45PM

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskMorning -Action $ActionMorning -Trigger $TriggerMorning -Settings $Settings -Description "Odds time-series morning record" -Force | Out-Null
Register-ScheduledTask -TaskName $TaskLate -Action $ActionLate -Trigger $TriggerLate -Settings $Settings -Description "Odds time-series late refresh record" -Force | Out-Null
Register-ScheduledTask -TaskName $TaskFinal -Action $ActionFinal -Trigger $TriggerFinal -Settings $Settings -Description "Odds time-series final refresh record" -Force | Out-Null

Write-Host "Registered tasks:"
Write-Host " - $TaskMorning (DAILY 07:10)"
Write-Host " - $TaskLate (DAILY 15:30)"
Write-Host " - $TaskFinal (DAILY 22:45)"
