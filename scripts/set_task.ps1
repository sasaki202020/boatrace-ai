param(
    [Parameter(Mandatory = $true)]
    [string]$TaskFile
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $projectRoot "tasks\$TaskFile"
$target = Join-Path $projectRoot "tasks\CURRENT_TASK.md"

if (!(Test-Path $source)) {
    Write-Error "Task file not found: $source"
    exit 1
}

Copy-Item $source $target -Force
Write-Host "CURRENT_TASK.md updated from $TaskFile"
