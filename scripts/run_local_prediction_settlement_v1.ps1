$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo

& py -3.13 scripts\run_local_prediction_settlement_v1.py `
  --runtime "C:\Users\goo10\競艇\boatrace-ai-mvp" `
  --artifact-root "C:\Users\goo10\競艇-recovery\boatrace-day1" `
  --entry-source "C:\Users\goo10\競艇-recovery\boatrace-ai-clean\data\raw\official\entries" `
  --result-source "C:\Users\goo10\OneDrive\ドキュメント\New project\boat_race_ai\data\official\results\txt" `
  --minimum-token "260721"
exit $LASTEXITCODE
