param(
    [string]$Date = ""
)

$ErrorActionPreference = "Stop"

# Detect project root (parent directory of 'scripts')
$ThisScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ThisScriptRoot

# Set up log directory
$LogDir = Join-Path $ProjectRoot "logs\post_race"
if (!(Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }

# Default to today if no date provided
if ([string]::IsNullOrWhiteSpace($Date)) {
    $Date = Get-Date -Format "yyyy-MM-dd"
}

# Define log file name
$LogFile = Join-Path $LogDir "$Date.log"

# Discovery Python
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

$StartTime = Get-Date
"[$StartTime] START post-race pipeline --date $Date" | Tee-Object -FilePath $LogFile -Append

try {
    # Execute Python script with --date
    & $PythonExe -m src.pipeline.run_daily_post_race --date $Date 2>&1 | Tee-Object -FilePath $LogFile -Append
    $ExitCode = $LASTEXITCODE
    if ($ExitCode -ne 0) {
        throw "post-race pipeline failed with exit code $ExitCode"
    }

    $EndTime = Get-Date
    "[$EndTime] SUCCESS post-race pipeline --date $Date" | Tee-Object -FilePath $LogFile -Append
    exit 0
}
catch {
    $EndTime = Get-Date
    "[$EndTime] ERROR post-race pipeline --date $Date : $_" | Tee-Object -FilePath $LogFile -Append
    exit 1
}
