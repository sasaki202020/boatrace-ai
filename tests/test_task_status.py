from __future__ import annotations

import json
import re
from pathlib import Path

from src.pipeline import task_status as task_status_mod


def test_task_definitions_match_registered_task_names() -> None:
    register_script = (task_status_mod.ROOT / "scripts" / "register_tasks.ps1").read_text(encoding="utf-8")
    registered_names = re.findall(r'Register-BoatraceTask -Name "([^"]+)"', register_script)

    assert [task["taskName"] for task in task_status_mod.TASK_DEFINITIONS] == registered_names


def test_task_status_unsupported_platform_writes_reports(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(task_status_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(task_status_mod, "ROOT", tmp_path)
    monkeypatch.setattr(task_status_mod, "REPORT_MONITORING_ROOT", tmp_path / "reports" / "monitoring")
    monkeypatch.setattr(task_status_mod, "LOGS_ROOT", tmp_path / "logs" / "tasks")

    result = task_status_mod.task_status()
    summary = result["summary"]

    assert summary["status"] == "unsupported_platform"
    assert Path(result["files"]["json"]).exists()
    assert Path(result["files"]["md"]).exists()
    loaded = json.loads(Path(result["files"]["json"]).read_text(encoding="utf-8"))
    assert loaded["platform"] == "linux"


def test_task_status_warning_when_tasks_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(task_status_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(task_status_mod, "ROOT", tmp_path)
    monkeypatch.setattr(task_status_mod, "REPORT_MONITORING_ROOT", tmp_path / "reports" / "monitoring")
    monkeypatch.setattr(task_status_mod, "LOGS_ROOT", tmp_path / "logs" / "tasks")
    monkeypatch.setattr(task_status_mod, "_latest_report_path", lambda pattern: None)
    monkeypatch.setattr(task_status_mod, "_latest_path", lambda pattern: None)
    monkeypatch.setattr(
        task_status_mod,
        "_query_windows_task",
        lambda task_name: {
            "taskName": task_name,
            "registered": task_name not in {
                "Boatrace_PaperOps_Morning",
                "Boatrace_DailyFreeze",
                "Boatrace_PreRace",
            },
            "status": "ok" if task_name not in {
                "Boatrace_PaperOps_Morning",
                "Boatrace_DailyFreeze",
                "Boatrace_PreRace",
            } else "missing",
            "lastRunTime": None,
            "lastTaskResult": 0 if task_name not in {
                "Boatrace_PaperOps_Morning",
                "Boatrace_DailyFreeze",
                "Boatrace_PreRace",
            } else None,
            "nextRunTime": None,
            "state": "Ready",
        },
    )

    result = task_status_mod.task_status()
    summary = result["summary"]

    assert summary["status"] == "warning"
    assert summary["registeredTaskCount"] == len(task_status_mod.TASK_DEFINITIONS) - 1
    assert summary["missingTaskCount"] == 1
    assert "Boatrace_PaperOps_Morning" in [task["taskName"] for task in summary["tasks"] if not task["registered"]]
    assert Path(result["files"]["json"]).exists()
    assert Path(result["files"]["md"]).exists()


def test_task_status_prefers_current_names_but_accepts_legacy_aliases(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(task_status_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(task_status_mod, "ROOT", tmp_path)
    monkeypatch.setattr(task_status_mod, "REPORT_MONITORING_ROOT", tmp_path / "reports" / "monitoring")
    monkeypatch.setattr(task_status_mod, "LOGS_ROOT", tmp_path / "logs" / "tasks")
    monkeypatch.setattr(task_status_mod, "_latest_report_path", lambda pattern: None)
    monkeypatch.setattr(task_status_mod, "_latest_path", lambda pattern: None)

    def fake_query(task_name: str) -> dict[str, object]:
        registered = task_name in {
            "Boatrace_HealthCheck",
            "Boatrace_DailyFreeze",
            "Boatrace_EveningSettle",
            "Boatrace_DailyReport",
        }
        return {
            "taskName": task_name,
            "registered": registered,
            "status": "ok" if registered else "missing",
            "lastRunTime": None,
            "lastTaskResult": 0 if registered else None,
            "nextRunTime": None,
            "state": "Ready",
        }

    monkeypatch.setattr(task_status_mod, "_query_windows_task", fake_query)

    result = task_status_mod.task_status()
    summary = result["summary"]

    preflight = next(task for task in summary["tasks"] if task["taskName"] == "Boatrace_PaperOps_Preflight")
    morning = next(task for task in summary["tasks"] if task["taskName"] == "Boatrace_PaperOps_Morning")
    evening = next(task for task in summary["tasks"] if task["taskName"] == "Boatrace_PaperOps_Evening")
    monitor = next(task for task in summary["tasks"] if task["taskName"] == "Boatrace_PaperOps_Monitor")

    assert summary["status"] == "ok"
    assert preflight["resolvedTaskName"] == "Boatrace_HealthCheck"
    assert morning["resolvedTaskName"] == "Boatrace_DailyFreeze"
    assert evening["resolvedTaskName"] == "Boatrace_EveningSettle"
    assert monitor["resolvedTaskName"] == "Boatrace_DailyReport"
    assert all(task["legacyTaskName"] is True for task in [preflight, morning, evening, monitor])


def test_task_status_marks_terminal_failure_without_counting_running_task(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(task_status_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(task_status_mod, "ROOT", tmp_path)
    monkeypatch.setattr(task_status_mod, "REPORT_MONITORING_ROOT", tmp_path / "reports" / "monitoring")
    monkeypatch.setattr(task_status_mod, "LOGS_ROOT", tmp_path / "logs" / "tasks")
    monkeypatch.setattr(task_status_mod, "_latest_report_path", lambda pattern: None)
    monkeypatch.setattr(task_status_mod, "_latest_path", lambda pattern: None)

    def fake_query(task_name: str) -> dict[str, object]:
        result = 0
        state = "Ready"
        if task_name == "Boatrace_PaperOps_Preflight":
            result = 2147946720
        elif task_name == "Boatrace_PaperOps_Monitor":
            result = 267009
            state = "Running"
        return {
            "taskName": task_name,
            "registered": True,
            "status": "ok",
            "lastRunTime": None,
            "lastTaskResult": result,
            "nextRunTime": None,
            "state": state,
        }

    monkeypatch.setattr(task_status_mod, "_query_windows_task", fake_query)

    summary = task_status_mod.task_status()["summary"]
    preflight = next(task for task in summary["tasks"] if task["taskName"] == "Boatrace_PaperOps_Preflight")
    monitor = next(task for task in summary["tasks"] if task["taskName"] == "Boatrace_PaperOps_Monitor")

    assert summary["status"] == "warning"
    assert summary["failedTaskCount"] == 1
    assert summary["runningTaskCount"] == 1
    assert preflight["status"] == "failed"
    assert monitor["status"] == "running"
