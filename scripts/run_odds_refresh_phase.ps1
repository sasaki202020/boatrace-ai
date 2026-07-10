param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("morning", "late", "final")]
    [string]$Phase,
    [string]$Date = "",
    [double]$Delay = 1.0,
    [switch]$Refresh,
    [switch]$PendingOnly
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
    "src.pipeline.run_daily_odds_refresh",
    "--date",
    $Date,
    "--phase",
    $Phase,
    "--delay",
    $Delay.ToString()
)

if ($Refresh) {
    $Args += "--refresh"
}
if ($PendingOnly) {
    $Args += "--pending-only"
}

& $PyExe @Args
exit $LASTEXITCODE
