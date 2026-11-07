$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$currentScript = Join-Path $scriptRoot "register_tasks.ps1"

if (!(Test-Path $currentScript)) {
    throw "File not found: $currentScript"
}

& $currentScript
