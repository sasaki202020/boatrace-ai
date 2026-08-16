$ErrorActionPreference = "Stop"

$taskName = "BOATRACE-CourseStart-Challenger-Gate-V1"
$repo = Split-Path -Parent $PSScriptRoot
$python = "C:\Users\goo10\AppData\Local\Programs\Python\Python313\python.exe"
$runner = Join-Path $repo "scripts\run_course_start_challenger_gate_v1.ps1"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "PYTHON_NOT_FOUND"
}
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "RUNNER_NOT_FOUND"
}

$arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runner`""
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    $action = $existing.Actions | Select-Object -First 1
    if ($action.Execute -ne "C:\Program Files\PowerShell\7\pwsh.exe" -or $action.Arguments -ne $arguments) {
        throw "EXISTING_TASK_CONFIGURATION_CONFLICT"
    }
    if ($existing.State -eq "Disabled") {
        Enable-ScheduledTask -TaskName $taskName | Out-Null
    }
    Write-Output "EXISTING_TASK_VERIFIED"
    exit 0
}

$action = New-ScheduledTaskAction `
    -Execute "C:\Program Files\PowerShell\7\pwsh.exe" `
    -Argument $arguments `
    -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Read-only course/start challenger readiness gate; no production or prospective writes." | Out-Null

Write-Output "TASK_REGISTERED"
