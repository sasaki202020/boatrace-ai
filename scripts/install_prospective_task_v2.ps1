$ErrorActionPreference = "Stop"
$taskName = "BOATRACE-Prospective-Shadow-V2"
$repo = Split-Path -Parent $PSScriptRoot
$wrapper = Join-Path $repo "scripts\run_prospective_task_v2.ps1"
$pwsh = (Get-Command pwsh.exe -ErrorAction Stop).Source
$user = "$env:USERDOMAIN\$env:USERNAME"
$start = (Get-Date).AddMinutes(1).ToString("s")
$xmlPath = Join-Path $env:TEMP "boatrace-prospective-shadow-v2.xml"
$escapedPwsh = [Security.SecurityElement]::Escape($pwsh)
$escapedArgs = [Security.SecurityElement]::Escape("-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$wrapper`"")
$escapedRepo = [Security.SecurityElement]::Escape($repo)
$escapedUser = [Security.SecurityElement]::Escape($user)
$dirty = git -C $repo status --porcelain --untracked-files=all -- src scripts
if ($LASTEXITCODE -ne 0 -or $dirty) { throw "TRACKED_WORKTREE_NOT_CLEAN" }
$baseline = git -C $repo rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw "GIT_BASELINE_UNAVAILABLE" }
$runtime = Join-Path $repo "data\commercialization_v2\autopilot"
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
Set-Content -LiteralPath (Join-Path $runtime "baseline_commit.txt") -Value $baseline -Encoding ascii
$approval = Join-Path $repo "reports\commercialization_v2\day1\day1_real_anchor_approval_manifest.json"
if (-not (Test-Path -LiteralPath $approval -PathType Leaf)) { throw "APPROVAL_MANIFEST_MISSING" }

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Forward-only internal prospective shadow Stage A controller.</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>$escapedUser</UserId></LogonTrigger>
    <CalendarTrigger><StartBoundary>$start</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay><Repetition><Interval>PT15M</Interval><Duration>P1D</Duration><StopAtDurationEnd>false</StopAtDurationEnd></Repetition></CalendarTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>$escapedUser</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><StartWhenAvailable>true</StartWhenAvailable><AllowStartOnDemand>true</AllowStartOnDemand><ExecutionTimeLimit>PT14M</ExecutionTimeLimit><Enabled>true</Enabled></Settings>
  <Actions Context="Author"><Exec><Command>$escapedPwsh</Command><Arguments>$escapedArgs</Arguments><WorkingDirectory>$escapedRepo</WorkingDirectory></Exec></Actions>
</Task>
"@

try {
    $xml | Set-Content -LiteralPath $xmlPath -Encoding Unicode
    schtasks /Create /TN $taskName /XML $xmlPath /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "TASK_REGISTRATION_FAILED" }
}
finally {
    Remove-Item -LiteralPath $xmlPath -Force -ErrorAction SilentlyContinue
}
schtasks /Query /TN $taskName /FO LIST
