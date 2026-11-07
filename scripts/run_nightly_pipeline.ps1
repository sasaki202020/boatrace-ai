param(
    [string]$Date = "",
    [switch]$SkipLateRefresh
)

$ErrorActionPreference = "Stop"

$ThisScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ThisScriptRoot

$LogDir = Join-Path $ProjectRoot "logs\nightly"
if (!(Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }

if ([string]::IsNullOrWhiteSpace($Date)) {
    $Date = Get-Date -Format "yyyy-MM-dd"
}

$LogFile = Join-Path $LogDir "$Date.log"

$PythonCandidates = @(
    "$ProjectRoot\.venv\Scripts\python.exe",
    "$ProjectRoot\venv\Scripts\python.exe",
    "C:\Users\goo10\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\goo10\AppData\Local\Programs\Python\Python313\python.exe"
)
$PythonExe = $PythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $PythonExe = "python"
}

Set-Location $ProjectRoot

$LateRefreshExitCode = 0
$PostRaceExitCode = 0
$StartTime = Get-Date
"[$StartTime] START nightly pipeline --date $Date" | Tee-Object -FilePath $LogFile -Append

try {
    if (-not $SkipLateRefresh) {
        "[$(Get-Date)] RUN late odds refresh --date $Date" | Tee-Object -FilePath $LogFile -Append
        & $PythonExe -m src.pipeline.run_daily_odds_refresh_late --date $Date 2>&1 | Tee-Object -FilePath $LogFile -Append
        $LateRefreshExitCode = $LASTEXITCODE
        "[$(Get-Date)] END late odds refresh exit_code=$LateRefreshExitCode" | Tee-Object -FilePath $LogFile -Append
    } else {
        "[$(Get-Date)] SKIP late odds refresh --date $Date" | Tee-Object -FilePath $LogFile -Append
    }

    "[$(Get-Date)] RUN post-race pipeline --date $Date" | Tee-Object -FilePath $LogFile -Append
    & $PythonExe -m src.pipeline.run_daily_post_race --date $Date 2>&1 | Tee-Object -FilePath $LogFile -Append
    $PostRaceExitCode = $LASTEXITCODE
    "[$(Get-Date)] END post-race pipeline exit_code=$PostRaceExitCode" | Tee-Object -FilePath $LogFile -Append

    if ($LateRefreshExitCode -ne 0 -or $PostRaceExitCode -ne 0) {
        throw "nightly pipeline failed (late_refresh=$LateRefreshExitCode, post_race=$PostRaceExitCode)"
    }

    "[$(Get-Date)] SUCCESS nightly pipeline --date $Date" | Tee-Object -FilePath $LogFile -Append
    exit 0
}
catch {
    "[$(Get-Date)] ERROR nightly pipeline --date $Date : $_" | Tee-Object -FilePath $LogFile -Append
    exit 1
}
