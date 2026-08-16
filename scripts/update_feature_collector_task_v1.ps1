[CmdletBinding()]
param(
    [string]$TaskName = "BOATRACE-Feature-Forward-Collector-V1"
)

$ErrorActionPreference = "Stop"

function Get-TriggerSignature {
    param([object[]]$Triggers)

    return @(
        $Triggers | ForEach-Object {
            "{0}|{1}|{2}|{3}" -f `
                $_.CimClass.CimClassName, `
                $_.StartBoundary, `
                $_.Repetition.Interval, `
                $_.Repetition.Duration
        }
    )
}

$repo = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    throw "FEATURE_COLLECTOR_TASK_NOT_FOUND"
}

# Preserve the existing action and trigger; reject a task that is not the collector.
$action = @($task.Actions | Select-Object -First 1)[0]
if (
    $null -eq $action -or
    $action.Arguments -notlike "*scripts\run_live_feature_capture_v1.py*" -or
    -not $action.WorkingDirectory
) {
    throw "FEATURE_COLLECTOR_TASK_CONFIGURATION_CONFLICT"
}
$workingDirectory = (Resolve-Path -LiteralPath $action.WorkingDirectory -ErrorAction Stop).Path
if ($workingDirectory -ne $repo) {
    throw "FEATURE_COLLECTOR_TASK_CONFIGURATION_CONFLICT"
}
$triggerSignature = Get-TriggerSignature -Triggers @($task.Triggers)

$taskUser = [string]$task.Principal.UserId
if ([string]::IsNullOrWhiteSpace($taskUser)) {
    throw "FEATURE_COLLECTOR_TASK_USER_UNRESOLVED"
}

$principal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 1)

try {
    Set-ScheduledTask -TaskName $TaskName -Principal $principal -Settings $settings -ErrorAction Stop | Out-Null
} catch {
    if (
        $_.Exception.HResult -eq -2147024891 -or
        $_.Exception.Message -match "Access is denied|アクセスが拒否"
    ) {
        throw "FEATURE_COLLECTOR_TASK_ELEVATION_REQUIRED"
    }
    throw
}

$updated = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$updatedAction = @($updated.Actions | Select-Object -First 1)[0]
$updatedTriggerSignature = Get-TriggerSignature -Triggers @($updated.Triggers)
$triggerDifference = @(
    Compare-Object -ReferenceObject $triggerSignature -DifferenceObject $updatedTriggerSignature
)
if (
    $updated.Principal.LogonType -ne "S4U" -or
    $updated.Settings.DisallowStartIfOnBatteries -or
    $updated.Settings.StopIfGoingOnBatteries -or
    $null -eq $updatedAction -or
    $updatedAction.Arguments -ne $action.Arguments -or
    $updatedAction.WorkingDirectory -ne $action.WorkingDirectory -or
    $triggerDifference.Count -ne 0
) {
    throw "FEATURE_COLLECTOR_TASK_UPDATE_VERIFICATION_FAILED"
}

[PSCustomObject]@{
    status = "FEATURE_COLLECTOR_TASK_UNATTENDED_READY"
    taskName = $TaskName
    logonType = $updated.Principal.LogonType
    startOnBattery = -not $updated.Settings.DisallowStartIfOnBatteries
    continueOnBattery = -not $updated.Settings.StopIfGoingOnBatteries
    actionPreserved = $true
    triggerPreserved = $true
} | ConvertTo-Json -Compress
