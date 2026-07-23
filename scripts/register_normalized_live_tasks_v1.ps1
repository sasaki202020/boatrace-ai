$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = "C:\Users\goo10\AppData\Local\Programs\Python\Python313\python.exe"
$runtime = "C:\Users\goo10\競艇\boatrace-ai-mvp"

$featureArgs = @(
  "scripts\run_live_feature_capture_v1.py"
  "--b-root `"$runtime\data\raw\official\entries`""
  "--store `"$runtime\data\research\feature_forward_v1\store`""
  "--status `"$runtime\reports\feature_forward_v1\latest_status.json`""
) -join " "
$featureAction = New-ScheduledTaskAction -Execute $python -Argument $featureArgs -WorkingDirectory $repo
$featureTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes 1) `
  -RepetitionDuration (New-TimeSpan -Days 3650)
$featureSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
  -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "BOATRACE-Feature-Forward-Collector-V1" `
  -Action $featureAction -Trigger $featureTrigger -Settings $featureSettings `
  -Description "Personal research beforeinfo capture; one request per due race; no retries." `
  -Force | Out-Null

$localAction = New-ScheduledTaskAction `
  -Execute "C:\Program Files\PowerShell\7\pwsh.exe" `
  -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$repo\scripts\run_local_prediction_settlement_v1.ps1`"" `
  -WorkingDirectory $repo
$localTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
  -RepetitionInterval (New-TimeSpan -Minutes 5) `
  -RepetitionDuration (New-TimeSpan -Days 3650)
$localSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
  -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
Register-ScheduledTask -TaskName "BOATRACE-Local-Prediction-Settlement-V1" `
  -Action $localAction -Trigger $localTrigger -Settings $localSettings `
  -Description "Append-only local tree_15 prediction and K settlement." `
  -Force | Out-Null
