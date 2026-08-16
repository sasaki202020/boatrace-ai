from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
REPORT_MONITORING_ROOT = ROOT / "reports" / "monitoring"
LOGS_ROOT = ROOT / "logs" / "tasks"
TASK_DEFINITIONS = [
    {
        "taskName": "Boatrace_PaperOps_Preflight",
        "aliases": ["Boatrace_HealthCheck"],
        "logPrefix": "paper_ops_preflight",
    },
    {
        "taskName": "Boatrace_PaperOps_Morning",
        "aliases": ["Boatrace_DailyFreeze", "Boatrace_PreRace"],
        "logPrefix": "paper_ops_morning",
    },
    {
        "taskName": "Boatrace_PaperOps_Evening",
        "aliases": ["Boatrace_EveningSettle", "Boatrace_SettleToday"],
        "logPrefix": "paper_ops_evening",
    },
    {
        "taskName": "Boatrace_PaperOps_Monitor",
        "aliases": ["Boatrace_DailyReport"],
        "logPrefix": "paper_ops_monitor",
    },
]
SUCCESSFUL_TASK_RESULTS = {0, 267011}
ACTIVE_TASK_STATES = {"queued", "running"}


def _execution_status(task: dict[str, Any]) -> str:
    if not task.get("registered"):
        return "warning" if task.get("status") == "warning" else "missing"
    state = str(task.get("state") or "").strip().lower()
    if state in ACTIVE_TASK_STATES:
        return "running"
    if state == "disabled":
        return "failed"
    result = task.get("lastTaskResult")
    if result is None:
        return "warning"
    try:
        return "ok" if int(result) in SUCCESSFUL_TASK_RESULTS else "failed"
    except (TypeError, ValueError):
        return "warning"


def _latest_path(pattern: str) -> Path | None:
    candidates = list(LOGS_ROOT.glob(pattern))
    candidates.extend(REPORT_MONITORING_ROOT.parent.joinpath("daily").glob(f"*/logs/{pattern}"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p.name, p.stat().st_mtime))


def _latest_report_path(pattern: str) -> Path | None:
    candidates = list((ROOT / "reports").rglob(pattern))
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p.name, p.stat().st_mtime))


def _query_windows_task(task_name: str) -> dict[str, Any]:
    if platform.system().lower() != "windows":
        return {"taskName": task_name, "registered": False, "status": "unsupported_platform"}
    script = f"""
try {{
  $task = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {{ $_.TaskName -eq "{task_name}" }}
}} catch {{
  $task = $null
}}
if ($null -eq $task) {{
  [pscustomobject]@{{
    taskName = "{task_name}"
    registered = $false
    status = "missing"
    lastRunTime = $null
    lastTaskResult = $null
    nextRunTime = $null
    state = $null
  }} | ConvertTo-Json -Compress
}} else {{
  $info = Get-ScheduledTaskInfo -TaskName "{task_name}" -ErrorAction SilentlyContinue
  [pscustomobject]@{{
    taskName = "{task_name}"
    registered = $true
    status = "ok"
    lastRunTime = if ($info) {{ $info.LastRunTime.ToString("o") }} else {{ $null }}
    lastTaskResult = if ($info) {{ $info.LastTaskResult }} else {{ $null }}
    nextRunTime = if ($info) {{ $info.NextRunTime.ToString("o") }} else {{ $null }}
    state = if ($task) {{ $task.State.ToString() }} else {{ $null }}
  }} | ConvertTo-Json -Compress
}}
"""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return {
            "taskName": task_name,
            "registered": False,
            "status": "warning",
            "error": (proc.stderr or proc.stdout or "").strip(),
            "lastRunTime": None,
            "lastTaskResult": None,
            "nextRunTime": None,
            "state": None,
        }
    try:
        payload = json.loads(proc.stdout)
    except Exception:
        return {
            "taskName": task_name,
            "registered": False,
            "status": "warning",
            "error": "task_parse_error",
            "lastRunTime": None,
            "lastTaskResult": None,
            "nextRunTime": None,
            "state": None,
        }
    if not isinstance(payload, dict):
        return {
            "taskName": task_name,
            "registered": False,
            "status": "warning",
            "error": "task_parse_error",
            "lastRunTime": None,
            "lastTaskResult": None,
            "nextRunTime": None,
            "state": None,
        }
    payload.setdefault("status", "ok" if payload.get("registered") else "missing")
    return payload


def _resolve_windows_task(task_name: str, aliases: list[str]) -> dict[str, Any]:
    candidates = [task_name, *aliases]
    last_payload: dict[str, Any] | None = None
    for candidate in candidates:
        payload = _query_windows_task(candidate)
        last_payload = payload
        if payload.get("registered"):
            payload["taskName"] = task_name
            payload["resolvedTaskName"] = candidate
            payload["legacyTaskName"] = candidate != task_name
            payload["aliases"] = aliases
            return payload
    fallback = last_payload or {
        "taskName": task_name,
        "registered": False,
        "status": "missing",
        "lastRunTime": None,
        "lastTaskResult": None,
        "nextRunTime": None,
        "state": None,
    }
    fallback["taskName"] = task_name
    fallback["resolvedTaskName"] = ""
    fallback["legacyTaskName"] = False
    fallback["aliases"] = aliases
    fallback["registered"] = False
    fallback["status"] = fallback.get("status") or "missing"
    fallback.setdefault("lastRunTime", None)
    fallback.setdefault("lastTaskResult", None)
    fallback.setdefault("nextRunTime", None)
    fallback.setdefault("state", None)
    return fallback


def task_status() -> dict[str, Any]:
    platform_name = platform.system().lower()
    if platform_name != "windows":
        summary = {
            "platform": platform_name,
            "status": "unsupported_platform",
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "tasks": [],
            "latestHealthCheckExists": bool(_latest_report_path("*_health_check.json")),
            "latestDailyReportExists": bool(_latest_report_path("daily_report.json")),
            "latestTaskLogs": {},
        }
    else:
        tasks = []
        missing = 0
        failed = 0
        warning = 0
        running = 0
        for task_def in TASK_DEFINITIONS:
            task_name = task_def["taskName"]
            info = _resolve_windows_task(task_name, list(task_def.get("aliases", [])))
            execution_status = _execution_status(info)
            info["status"] = execution_status
            if execution_status == "missing":
                missing += 1
            elif execution_status == "failed":
                failed += 1
            elif execution_status == "warning":
                warning += 1
            elif execution_status == "running":
                running += 1
            prefix = task_def.get("logPrefix", task_name.lower())
            latest_log = _latest_path(f"{prefix}_*.log")
            info["logFileExists"] = bool(latest_log and latest_log.exists())
            info["latestLogPath"] = str(latest_log) if latest_log else ""
            tasks.append(info)
        latest_logs = {
            task_def["taskName"]: str(_latest_path(f"{task_def.get('logPrefix', task_def['taskName'].lower())}_*.log") or "")
            for task_def in TASK_DEFINITIONS
        }
        summary = {
            "platform": platform_name,
            "status": "warning" if missing or failed or warning else "ok",
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "tasks": tasks,
            "taskCount": len(TASK_DEFINITIONS),
            "registeredTaskCount": sum(1 for task in tasks if task.get("registered")),
            "missingTaskCount": missing,
            "failedTaskCount": failed,
            "warningTaskCount": warning,
            "runningTaskCount": running,
            "latestHealthCheckExists": bool(_latest_report_path("*_health_check.json")),
            "latestDailyReportExists": bool(_latest_report_path("*_summary.json") or _latest_report_path("daily_report.json")),
            "latestTaskLogs": latest_logs,
        }

    REPORT_MONITORING_ROOT.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_MONITORING_ROOT / "task_status.json"
    md_path = REPORT_MONITORING_ROOT / "task_status.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Task Status",
        "",
        f"- platform: {summary['platform']}",
        f"- status: {summary['status']}",
        f"- latestHealthCheckExists: {summary['latestHealthCheckExists']}",
        f"- latestDailyReportExists: {summary['latestDailyReportExists']}",
        "",
        "| taskName | registered | lastRunTime | lastTaskResult | nextRunTime | state | logFileExists | latestLogPath |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for task in summary.get("tasks", []):
        lines.append(
            f"| {task.get('taskName')} | {task.get('registered')} | {task.get('lastRunTime') or ''} | {task.get('lastTaskResult') or ''} | {task.get('nextRunTime') or ''} | {task.get('state') or ''} | {task.get('logFileExists')} | {task.get('latestLogPath') or ''} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"summary": summary, "files": {"json": str(json_path), "md": str(md_path)}}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    result = task_status()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
