$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo

& py -3.13 scripts\run_prospective_autopilot_v2.py
exit $LASTEXITCODE
