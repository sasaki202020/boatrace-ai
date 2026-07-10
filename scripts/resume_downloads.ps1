$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script = 'C:\Users\goo10\dl\download_boatrace_data.py'
$python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
$logDir = Join-Path $repo 'logs'
New-Item -ItemType Directory -Force $logDir | Out-Null

function Get-LatestDownloadedDate {
  param([string]$DirPath)
  $files = Get-ChildItem $DirPath -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -match '^\.(txt|TXT)$' }
  if (-not $files) { return $null }
  $dates = @()
  foreach ($f in $files) {
    $stem = $f.BaseName
    if ($stem -match '(\d{8})') {
      $dates += [datetime]::ParseExact($Matches[1], 'yyyyMMdd', $null)
    } elseif ($stem -match '([BK])(\d{6})') {
      $dates += [datetime]::ParseExact('20' + $Matches[2], 'yyyyMMdd', $null)
    }
  }
  if (-not $dates) { return $null }
  return ($dates | Sort-Object | Select-Object -Last 1)
}

function Get-MonthRanges {
  param(
    [datetime]$StartDate,
    [datetime]$EndDate
  )
  $cur = Get-Date -Year $StartDate.Year -Month $StartDate.Month -Day 1
  $endMonth = Get-Date -Year $EndDate.Year -Month $EndDate.Month -Day 1
  $ranges = @()
  while ($cur -le $endMonth) {
    $monthStart = $cur
    $monthEnd = $cur.AddMonths(1).AddDays(-1)
    if ($monthStart -lt $StartDate) { $monthStart = $StartDate }
    if ($monthEnd -gt $EndDate) { $monthEnd = $EndDate }
    $ranges += ,@($monthStart.ToString('yyyyMMdd'), $monthEnd.ToString('yyyyMMdd'))
    $cur = $cur.AddMonths(1)
  }
  return $ranges
}

$rawB = Join-Path $repo 'data\raw\B'
$rawK = Join-Path $repo 'data\raw\K'
$lastB = Get-LatestDownloadedDate $rawB
$lastK = Get-LatestDownloadedDate $rawK
$last = @($lastB, $lastK) | Where-Object { $_ } | Sort-Object | Select-Object -Last 1

if (-not $last) {
  $startDate = Get-Date '2020-01-01'
} else {
  $startDate = $last.AddDays(1)
}

$endDate = Get-Date '2024-12-31'
$ranges = Get-MonthRanges -StartDate $startDate -EndDate $endDate

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$logFile = Join-Path $logDir "resume_downloads_$stamp.log"

Add-Content -Path $logFile -Value "[START] $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') from=$($startDate.ToString('yyyy-MM-dd')) to=$($endDate.ToString('yyyy-MM-dd'))"
Write-Host "[START] from=$($startDate.ToString('yyyy-MM-dd')) to=$($endDate.ToString('yyyy-MM-dd'))"

foreach ($range in $ranges) {
  $start = $range[0]
  $end = $range[1]
  $rangeStarted = Get-Date
  $marker = "[RUN] $start -> $end $($rangeStarted.ToString('yyyy-MM-dd HH:mm:ss'))"
  $marker | Out-File -FilePath $logFile -Append -Encoding utf8
  Write-Host $marker
  Push-Location $repo
  try {
    # output-dir is intentionally omitted to avoid mojibake path issues on Windows;
    # with Push-Location, the downloader writes into .\data under the repo.
    & $python $script --start $start --end $end --interval 0 --skip-fan 2>&1 |
      Out-File -FilePath $logFile -Append -Encoding utf8
  } finally {
    Pop-Location
  }
  if ($LASTEXITCODE -ne 0) {
    $failMarker = "[FAIL] $start -> $end exit=$LASTEXITCODE $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    $failMarker | Out-File -FilePath $logFile -Append -Encoding utf8
    Write-Host $failMarker
    exit $LASTEXITCODE
  }
  $rangeEnded = Get-Date
  $elapsed = New-TimeSpan -Start $rangeStarted -End $rangeEnded
  $doneMarker = "[DONE_RANGE] $start -> $end duration=$([math]::Round($elapsed.TotalMinutes, 1))m finished=$($rangeEnded.ToString('yyyy-MM-dd HH:mm:ss'))"
  $doneMarker | Out-File -FilePath $logFile -Append -Encoding utf8
  Write-Host $doneMarker
}

$doneMarker = "[DONE] all ranges completed $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
$doneMarker | Out-File -FilePath $logFile -Append -Encoding utf8
Write-Host $doneMarker
