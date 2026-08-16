param(
  [string]$PythonCommand = $(if ($env:BOATRACE_PYTHON) { $env:BOATRACE_PYTHON } else { "py" }),
  [string]$RuntimeRoot = $env:BOATRACE_RUNTIME_ROOT,
  [string]$ArtifactRoot = $env:BOATRACE_ARTIFACT_ROOT,
  [string]$EntrySource = $env:BOATRACE_ENTRY_SOURCE,
  [string]$ResultSource = $env:BOATRACE_RESULT_SOURCE,
  [string]$MinimumToken = "260721"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo

$python = Get-Command $PythonCommand -ErrorAction SilentlyContinue
if ($null -eq $python) { throw "PYTHON_NOT_FOUND: set -PythonCommand or BOATRACE_PYTHON" }
if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) { throw "RUNTIME_ROOT_REQUIRED: set -RuntimeRoot or BOATRACE_RUNTIME_ROOT" }
if ([string]::IsNullOrWhiteSpace($ArtifactRoot)) { throw "ARTIFACT_ROOT_REQUIRED: set -ArtifactRoot or BOATRACE_ARTIFACT_ROOT" }
if ([string]::IsNullOrWhiteSpace($EntrySource)) { $EntrySource = Join-Path $RuntimeRoot "data\raw\official\entries" }
if ([string]::IsNullOrWhiteSpace($ResultSource)) { $ResultSource = Join-Path $RuntimeRoot "data\raw\official\results" }

& $python.Source scripts\run_local_prediction_settlement_v1.py `
  --runtime $RuntimeRoot `
  --artifact-root $ArtifactRoot `
  --entry-source $EntrySource `
  --result-source $ResultSource `
  --minimum-token $MinimumToken
exit $LASTEXITCODE
