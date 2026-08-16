$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
$runtime = $repo
$model = Join-Path $repo "data\commercialization_v1\frozen_candidate\tree_15.joblib"
$runner = Join-Path $repo "scripts\run_course_start_challenger_v1.py"
$reportRoot = Join-Path $repo "reports\feature_forward"

if ([string]::IsNullOrWhiteSpace($python) -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "PYTHON_NOT_FOUND"
}

Push-Location -LiteralPath $repo
try {
    & $python $runner `
        --prediction-root (Join-Path $runtime "data\prospective\predictions") `
        --settlement-root (Join-Path $runtime "data\prospective\settlements") `
        --feature-store (Join-Path $runtime "data\research\feature_forward_v1\store") `
        --report-root $reportRoot `
        --model-artifact $model `
        --b-root (Join-Path $runtime "data\raw\official\entries") `
        --request-ledger (Join-Path $runtime "data\research\feature_forward_v1\store\request_ledger.sqlite3")

    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
