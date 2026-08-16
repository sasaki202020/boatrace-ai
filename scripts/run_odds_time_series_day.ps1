param(
    [string]$Date,
    [double]$LateWaitMinutes = 240,
    [double]$Delay = 1.0,
    [switch]$SkipLate,
    [switch]$SkipFinal
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if ([string]::IsNullOrWhiteSpace($py)) {
    throw 'PYTHON_NOT_FOUND'
}
Set-Location $repoRoot

if (-not $Date) {
    $Date = (Get-Date).ToString('yyyy-MM-dd')
}

& $py -m src.pipeline.run_daily_odds_time_series --date $Date --phase morning --delay $Delay

if (-not $SkipLate) {
    & $py -m src.pipeline.run_daily_odds_time_series --date $Date --phase late_refresh --wait-minutes $LateWaitMinutes --delay $Delay
}

if (-not $SkipFinal) {
    & $py -m src.pipeline.run_daily_odds_time_series --date $Date --phase final_refresh --delay $Delay
}

& $py -m src.eval.summarize_odds_time_series
