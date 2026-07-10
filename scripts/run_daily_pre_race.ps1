param(
    [string]$Date = ""
)

$ErrorActionPreference = "Stop"

# Detect project root (parent directory of 'scripts')
$ThisScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ThisScriptRoot

# Set up log directory
$LogDir = Join-Path $ProjectRoot "logs\pre_race"
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
"[$StartTime] START pre-race pipeline --date $Date" | Tee-Object -FilePath $LogFile -Append

try {
    $StdOutFile = [System.IO.Path]::GetTempFileName()
    $StdErrFile = [System.IO.Path]::GetTempFileName()
    try {
        $Process = Start-Process `
            -FilePath $PythonExe `
            -ArgumentList @("-m", "src.pipeline.run_daily_pre_race", "--date", $Date) `
            -WorkingDirectory $ProjectRoot `
            -NoNewWindow `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $StdOutFile `
            -RedirectStandardError $StdErrFile

        if ((Test-Path $StdOutFile) -and ((Get-Item $StdOutFile).Length -gt 0)) {
            Get-Content -Raw -Path $StdOutFile -Encoding UTF8 | Tee-Object -FilePath $LogFile -Append
        }
        if ((Test-Path $StdErrFile) -and ((Get-Item $StdErrFile).Length -gt 0)) {
            Get-Content -Raw -Path $StdErrFile -Encoding UTF8 | Tee-Object -FilePath $LogFile -Append
        }

        $ExitCode = $Process.ExitCode
    }
    finally {
        Remove-Item $StdOutFile, $StdErrFile -ErrorAction SilentlyContinue
    }

    if ($ExitCode -ne 0) {
        throw "pre-race pipeline failed with exit code $ExitCode"
    }

    $EndTime = Get-Date
    "[$EndTime] SUCCESS pre-race pipeline --date $Date" | Tee-Object -FilePath $LogFile -Append
    exit 0
}
catch {
    $EndTime = Get-Date
    "[$EndTime] ERROR pre-race pipeline --date $Date : $_" | Tee-Object -FilePath $LogFile -Append
    exit 1
}
