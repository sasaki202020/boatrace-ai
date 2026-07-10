$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$batDir = Join-Path $root "scripts"
$pythonExe = $env:PYTHON_EXE
if ([string]::IsNullOrWhiteSpace($pythonExe)) {
    $pythonExe = (Get-Command py -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1)
}
if ([string]::IsNullOrWhiteSpace($pythonExe)) {
    $pythonExe = (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1)
}
if ([string]::IsNullOrWhiteSpace($pythonExe)) {
    $pythonExe = "py"
}

function Register-BoatraceTask {
    param(
        [string]$Name,
        [string]$BatFile,
        [string]$Time
    )

    $argument = '/c "set PYTHON_EXE={0} && cd /d ""{1}"" && call ""{2}"""' -f $pythonExe, $root, $BatFile
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $argument
    $trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($Time, "HH:mm", $null))
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Force | Out-Null
}

Register-BoatraceTask -Name "Boatrace_PaperOps_Preflight" -BatFile (Join-Path $batDir "run_paper_ops_preflight.bat") -Time "06:50"
Register-BoatraceTask -Name "Boatrace_PaperOps_Morning" -BatFile (Join-Path $batDir "run_paper_ops_morning.bat") -Time "07:00"
Register-BoatraceTask -Name "Boatrace_PaperOps_Evening" -BatFile (Join-Path $batDir "run_paper_ops_evening.bat") -Time "21:30"
Register-BoatraceTask -Name "Boatrace_PaperOps_Monitor" -BatFile (Join-Path $batDir "run_paper_ops_monitor.bat") -Time "22:10"
