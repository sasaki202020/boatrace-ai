$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
$runtime = $repo
if ([string]::IsNullOrWhiteSpace($python) -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
  throw "PYTHON_NOT_FOUND"
}

$featureArgs = @(
  "scripts\run_live_feature_capture_v1.py"
  "--b-root `"$runtime\data\raw\official\entries`""
  "--store `"$runtime\data\research\feature_forward_v1\store`""
  "--status `"$runtime\reports\feature_forward_v1\latest_status.json`""
) -join " "
$featureAction = New-ScheduledTaskAction -Execute $python -Argument $featureArgs -WorkingDirectory $repo
$featureTaskName = "BOATRACE-Feature-Forward-Collector-V1"
$existingFeatureTask = Get-ScheduledTask -TaskName $featureTaskName -ErrorAction SilentlyContinue
$featureUser = if ($existingFeatureTask -and $existingFeatureTask.Principal.UserId) {
  $existingFeatureTask.Principal.UserId
} else {
  $env:USERNAME
}
if ([string]::IsNullOrWhiteSpace($featureUser)) {
  throw "FEATURE_COLLECTOR_TASK_USER_UNRESOLVED"
}
$featurePrincipal = New-ScheduledTaskPrincipal -UserId $featureUser -LogonType S4U -RunLevel Limited
$featureTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes 1) `
  -RepetitionDuration (New-TimeSpan -Days 3650)
$featureSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $featureTaskName `
  -Action $featureAction -Trigger $featureTrigger -Settings $featureSettings -Principal $featurePrincipal `
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
