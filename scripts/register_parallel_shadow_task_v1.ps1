$ErrorActionPreference = "Stop"

$taskName = "BOATRACE-CourseStart-Parallel-Shadow-V1"
$repo = Split-Path -Parent $PSScriptRoot
$wrapper = Join-Path $repo "scripts\run_parallel_shadow_v1.ps1"

if (-not (Test-Path -LiteralPath $wrapper -PathType Leaf)) { throw "RUNNER_NOT_FOUND" }

$arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$wrapper`""
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    $action = $existing.Actions | Select-Object -First 1
    if ($action.Execute -ne "C:\Program Files\PowerShell\7\pwsh.exe" -or $action.Arguments -ne $arguments) {
        throw "EXISTING_TASK_CONFIGURATION_CONFLICT"
    }
    Write-Output "EXISTING_TASK_VERIFIED"
    exit 0
}

$action = New-ScheduledTaskAction `
    -Execute "C:\Program Files\PowerShell\7\pwsh.exe" `
    -Argument $arguments `
    -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
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
    -Description "Research-only course/start parallel shadow; no production or settlement writes." | Out-Null

Write-Output "TASK_REGISTERED"
