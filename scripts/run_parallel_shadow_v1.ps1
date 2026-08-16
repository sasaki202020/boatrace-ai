$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$python = "C:\Users\goo10\AppData\Local\Programs\Python\Python313\python.exe"
$runtime = "C:\Users\goo10\競艇\boatrace-ai-mvp"
$runner = Join-Path $repo "scripts\run_parallel_shadow_v1.py"
$model = "C:\Users\goo10\競艇-recovery\boatrace-day1\data\commercialization_v1\frozen_candidate\tree_15.joblib"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "PYTHON_NOT_FOUND" }
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) { throw "RUNNER_NOT_FOUND" }
if (-not (Test-Path -LiteralPath $model -PathType Leaf)) { throw "MODEL_ARTIFACT_NOT_FOUND" }

Push-Location -LiteralPath $repo
try {
    & $python $runner `
        --prediction-root (Join-Path $runtime "data\prospective\predictions") `
        --feature-store (Join-Path $runtime "data\research\feature_forward_v1\store") `
        --shadow-root (Join-Path $runtime "data\research\feature_forward_v1\parallel_shadow") `
        --model-artifact $model `
        --config (Join-Path $repo "config\feature_forward_v1\parallel_shadow_config.json") `
        --code-repo $repo
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
