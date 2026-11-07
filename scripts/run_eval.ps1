$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$env:PYTHONPATH = "."
py src/eval/ablation_and_bottleneck.py
