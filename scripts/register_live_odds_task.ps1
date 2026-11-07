$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$cmd = "cd /d `"$root`" && py src/data/fetch_live_odds.py --max-targets 300 --timeout 8"
$taskName = "BoatraceAI_LiveOdds_Hourly"

# 30分前固定トリガーはレース時刻テーブル連動が必要なため、
# 実運用の暫定として毎時実行を登録する。
schtasks /Create /F /SC HOURLY /MO 1 /TN $taskName /TR "cmd /c $cmd" /ST 08:00
Write-Host "Registered task: $taskName (HOURLY from 08:00)"

