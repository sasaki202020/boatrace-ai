$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$rawRoot = Join-Path $repo 'data\raw'
$logsDir = Join-Path $repo 'logs'

function Get-CountAndLatest {
  param([string]$DirPath)
  $files = Get-ChildItem $DirPath -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -match '^\.(txt|TXT)$' } |
    Sort-Object FullName
  if (-not $files) {
    return [pscustomobject]@{
      Count = 0
      Latest = '-'
      Earliest = '-'
      LatestDate = '-'
      EarliestDate = '-'
    }
  }

  $dates = foreach ($f in $files) {
    $stem = $f.BaseName
    if ($stem -match '(\d{8})') {
      [datetime]::ParseExact($Matches[1], 'yyyyMMdd', $null)
    } elseif ($stem -match '([BK])(\d{6})') {
      [datetime]::ParseExact('20' + $Matches[2], 'yyyyMMdd', $null)
    }
  }
  $dates = @($dates | Where-Object { $_ })
  $earliestDate = if ($dates.Count) { ($dates | Sort-Object | Select-Object -First 1).ToString('yyyy-MM-dd') } else { '-' }
  $latestDate = if ($dates.Count) { ($dates | Sort-Object | Select-Object -Last 1).ToString('yyyy-MM-dd') } else { '-' }

  return [pscustomobject]@{
    Count = $files.Count
    Earliest = $files[0].Name
    Latest = $files[-1].Name
    EarliestDate = $earliestDate
    LatestDate = $latestDate
  }
}

function Get-CompletedRangeStats {
  param([string]$LogPath)
  if (-not (Test-Path $LogPath)) {
    return [pscustomobject]@{
      Completed = 0
      AvgMinutes = $null
      LastDuration = $null
      LastDone = $null
    }
  }

  $doneLines = Get-Content $LogPath -ErrorAction SilentlyContinue |
    Where-Object { $_ -match '^\[DONE_RANGE\]\s+(\d{8})\s+->\s+(\d{8})\s+duration=([0-9.]+)m\s+finished=(.+)$' }

  if (-not $doneLines) {
    return [pscustomobject]@{
      Completed = 0
      AvgMinutes = $null
      LastDuration = $null
      LastDone = $null
    }
  }

  $durations = @()
  $lastDuration = $null
  $lastDone = $null
  foreach ($line in $doneLines) {
    if ($line -match '^\[DONE_RANGE\]\s+(\d{8})\s+->\s+(\d{8})\s+duration=([0-9.]+)m\s+finished=(.+)$') {
      $dur = [double]$Matches[3]
      $durations += $dur
      $lastDuration = $dur
      $lastDone = $Matches[4]
    }
  }

  $avg = if ($durations.Count) { [math]::Round((($durations | Measure-Object -Average).Average), 1) } else { $null }
  return [pscustomobject]@{
    Completed = $durations.Count
    AvgMinutes = $avg
    LastDuration = $lastDuration
    LastDone = $lastDone
  }
}

$b = Get-CountAndLatest (Join-Path $rawRoot 'B')
$k = Get-CountAndLatest (Join-Path $rawRoot 'K')

$proc = Get-CimInstance Win32_Process |
  Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*download_boatrace_data.py*' } |
  Select-Object -First 1 ProcessId, CommandLine

$latestLog = Get-ChildItem $logsDir -Filter 'resume_downloads_*.log' -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

Write-Host "repo=$repo"
Write-Host "B: count=$($b.Count) earliest=$($b.Earliest) latest=$($b.Latest) range=$($b.EarliestDate)..$($b.LatestDate)"
Write-Host "K: count=$($k.Count) earliest=$($k.Earliest) latest=$($k.Latest) range=$($k.EarliestDate)..$($k.LatestDate)"

if ($proc) {
  Write-Host "downloader=RUNNING pid=$($proc.ProcessId)"
  Write-Host "cmd=$($proc.CommandLine)"
} else {
  Write-Host "downloader=STOPPED"
}

if ($latestLog) {
  Write-Host "latest_log=$($latestLog.FullName)"
  Write-Host "latest_log_updated=$($latestLog.LastWriteTime)"
  $rangeStats = Get-CompletedRangeStats $latestLog.FullName
  if ($rangeStats.Completed -gt 0) {
    $remainingStart = $null
    if ($k.LatestDate -ne '-' -and $b.LatestDate -ne '-') {
      $kDate = [datetime]::Parse($k.LatestDate)
      $bDate = [datetime]::Parse($b.LatestDate)
      if ($kDate -gt $bDate) {
        $remainingStart = $kDate
      } else {
        $remainingStart = $bDate
      }
    }
    if ($remainingStart -and $rangeStats.AvgMinutes) {
      $monthsLeft = 0
      $cursor = $remainingStart.AddMonths(1)
      $endDate = [datetime]::Parse('2024-12-31')
      while ($cursor -le $endDate) {
        $monthsLeft++
        $cursor = $cursor.AddMonths(1)
      }
      $etaMinutes = [math]::Round($monthsLeft * $rangeStats.AvgMinutes, 1)
      Write-Host "download_ranges_done=$($rangeStats.Completed) avg_minutes=$($rangeStats.AvgMinutes) last_minutes=$($rangeStats.LastDuration)"
      Write-Host "eta_remaining_months=$monthsLeft eta_minutes=$etaMinutes"
    } else {
      Write-Host "download_ranges_done=$($rangeStats.Completed)"
    }
  }
  Get-Content $latestLog.FullName -Tail 20
} else {
  Write-Host 'latest_log=none'
}
