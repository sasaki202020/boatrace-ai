from __future__ import annotations

import json
from pathlib import Path

from src.pipeline import task_status as task_status_mod


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
            "registered": task_name not in {"Boatrace_HealthCheck"},
            "status": "ok" if task_name not in {"Boatrace_HealthCheck"} else "missing",
            "lastRunTime": None,
            "lastTaskResult": 0 if task_name not in {"Boatrace_HealthCheck"} else None,
            "nextRunTime": None,
            "state": "Ready",
        },
    )

    result = task_status_mod.task_status()
    summary = result["summary"]

    assert summary["status"] == "warning"
    assert summary["registeredTaskCount"] == len(task_status_mod.TASK_NAMES) - 1
    assert summary["missingTaskCount"] == 1
    assert "Boatrace_HealthCheck" in [task["taskName"] for task in summary["tasks"] if not task["registered"]]
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
            "Boatrace_DailyFreeze",
            "Boatrace_EveningSettle",
            "Boatrace_OddsRefresh",
            "Boatrace_BeforeInfo",
            "Boatrace_HealthCheck",
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

    pre_race = next(task for task in summary["tasks"] if task["taskName"] == "Boatrace_PreRace")
    settle_today = next(task for task in summary["tasks"] if task["taskName"] == "Boatrace_SettleToday")

    assert summary["status"] == "ok"
    assert pre_race["registered"] is True
    assert pre_race["resolvedTaskName"] == "Boatrace_DailyFreeze"
    assert pre_race["legacyTaskName"] is True
    assert settle_today["registered"] is True
    assert settle_today["resolvedTaskName"] == "Boatrace_EveningSettle"
    assert settle_today["legacyTaskName"] is True
