param(
    [string]$PythonCommand = $(if ($env:BOATRACE_PYTHON) { $env:BOATRACE_PYTHON } else { "py" }),
    [string]$RuntimeRoot = $env:BOATRACE_RUNTIME_ROOT,
    [string]$ModelArtifact = $env:BOATRACE_MODEL_ARTIFACT
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $repo "scripts\run_parallel_shadow_v1.py"

$python = Get-Command $PythonCommand -ErrorAction SilentlyContinue
if ($null -eq $python) { throw "PYTHON_NOT_FOUND: set -PythonCommand or BOATRACE_PYTHON" }
if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) { throw "RUNTIME_ROOT_REQUIRED: set -RuntimeRoot or BOATRACE_RUNTIME_ROOT" }
if ([string]::IsNullOrWhiteSpace($ModelArtifact)) { throw "MODEL_ARTIFACT_REQUIRED: set -ModelArtifact or BOATRACE_MODEL_ARTIFACT" }
if (-not (Test-Path -LiteralPath $RuntimeRoot -PathType Container)) { throw "RUNTIME_ROOT_NOT_FOUND" }
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) { throw "RUNNER_NOT_FOUND" }
if (-not (Test-Path -LiteralPath $ModelArtifact -PathType Leaf)) { throw "MODEL_ARTIFACT_NOT_FOUND" }

Push-Location -LiteralPath $repo
try {
    & $python.Source $runner `
        --prediction-root (Join-Path $RuntimeRoot "data\prospective\predictions") `
        --feature-store (Join-Path $RuntimeRoot "data\research\feature_forward_v1\store") `
        --shadow-root (Join-Path $RuntimeRoot "data\research\feature_forward_v1\parallel_shadow") `
        --model-artifact $ModelArtifact `
        --config (Join-Path $repo "config\feature_forward_v1\parallel_shadow_config.json") `
        --code-repo $repo
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
