$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script = Join-Path $repo 'download_boatrace_data.py'
$logDir = Join-Path $repo 'logs'
New-Item -ItemType Directory -Force $logDir | Out-Null

# 2020-06 以降を月ごとに継続取得する
$months = @(
  @('20200601', '20200630'),
  @('20200701', '20200731'),
  @('20200801', '20200831'),
  @('20200901', '20200930'),
  @('20201001', '20201031'),
  @('20201101', '20201130'),
  @('20201201', '20201231')
)

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$logFile = Join-Path $logDir "continue_downloads_$stamp.log"

foreach ($m in $months) {
  $start = $m[0]
  $end = $m[1]
  $marker = "[RUN] $start -> $end $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
  Add-Content -Path $logFile -Value $marker
  Write-Host $marker
  & py $script --start $start --end $end --interval 0 --skip-fan 2>&1 | Tee-Object -FilePath $logFile -Append
  if ($LASTEXITCODE -ne 0) {
    $failMarker = "[FAIL] $start -> $end exit=$LASTEXITCODE $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Add-Content -Path $logFile -Value $failMarker
    Write-Host $failMarker
    exit $LASTEXITCODE
  }
}

$doneMarker = "[DONE] all monthly chunks completed $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Add-Content -Path $logFile -Value $doneMarker
Write-Host $doneMarker
