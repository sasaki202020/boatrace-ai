param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("morning", "late_refresh", "final_refresh")]
    [string]$Phase,
    [string]$Date = "",
    [double]$Delay = 1.0,
    [double]$WaitMinutes = 0.0
)

$ErrorActionPreference = "Stop"

$ThisScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ThisScriptRoot

if ([string]::IsNullOrWhiteSpace($Date)) {
    $Date = Get-Date -Format "yyyy-MM-dd"
}

$PyExe = "python"
if (Test-Path "$ProjectRoot\.venv\Scripts\python.exe") {
    $PyExe = "$ProjectRoot\.venv\Scripts\python.exe"
} elseif (Test-Path "$ProjectRoot\venv\Scripts\python.exe") {
    $PyExe = "$ProjectRoot\venv\Scripts\python.exe"
}

Set-Location $ProjectRoot

$Args = @(
    "-m",
    "src.pipeline.run_daily_odds_time_series",
    "--date",
    $Date,
    "--phase",
    $Phase,
    "--delay",
    $Delay.ToString()
)

if ($Phase -eq "late_refresh" -and $WaitMinutes -gt 0) {
    $Args += @("--wait-minutes", $WaitMinutes.ToString())
}

& $PyExe @Args
exit $LASTEXITCODE
