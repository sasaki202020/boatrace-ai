from __future__ import annotations

import json
from pathlib import Path

from src.pipeline import health_check as health_check_mod


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_health_check_ok_when_core_artifacts_present(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(health_check_mod, "ROOT", tmp_path)
    monkeypatch.setattr(health_check_mod, "REPORT_MONITORING_ROOT", tmp_path / "reports" / "monitoring")
    monkeypatch.setattr(health_check_mod, "REPORT_DAILY_ROOT", tmp_path / "reports" / "daily")
    monkeypatch.setattr(health_check_mod, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(health_check_mod, "UI_ROOT", tmp_path / "data" / "ui")
    monkeypatch.setattr(health_check_mod, "PRED_ROOT", tmp_path / "data" / "predictions")
    monkeypatch.setattr(health_check_mod, "ERRORS_ROOT", tmp_path / "reports" / "errors")
    monkeypatch.setattr(
        health_check_mod,
        "_load_backfill_readiness",
        lambda: {"canTuneWithBackfill": False},
    )
    monkeypatch.setattr(
        health_check_mod,
        "task_status_mod",
        type(
            "TaskStatusStub",
            (),
            {
                "task_status": staticmethod(
                    lambda: {
                        "summary": {
                            "status": "ok",
                            "latestTaskLogs": {"Boatrace_DailyFreeze": "logs/tasks/daily_freeze_20260425.log"},
                            "tasks": [
                                {"taskName": "Boatrace_DailyFreeze", "lastRunTime": "2026-04-25T07:00:00", "registered": True},
                                {"taskName": "Boatrace_EveningSettle", "lastRunTime": "2026-04-25T21:30:00", "registered": True},
                                {"taskName": "Boatrace_DailyReport", "lastRunTime": "2026-04-25T22:00:00", "registered": True},
                                {"taskName": "Boatrace_HealthCheck", "lastRunTime": "2026-04-25T07:30:00", "registered": True},
                            ],
                        },
                        "files": {"json": "task_status.json", "md": "task_status.md"},
                    }
                )
            },
        ),
    )

    _write_json(tmp_path / "data" / "normalized" / "20260425" / "today_venues.json", {"date": "20260425", "venues": [{"jcd": "24"}]})
    _write_json(tmp_path / "data" / "ui" / "20260425" / "raceyosou_24.json", {"date": "20260425", "races": [{"rno": 1}]})
    _write_json(tmp_path / "data" / "predictions" / "20260425" / "frozen_bets_all.json", {"date": "20260425", "predictionHash": "abc", "venues": []})
    _write_json(tmp_path / "reports" / "daily" / "20260425_summary.json", {"date": "20260425", "settledBetCount": 12, "unresolvedBetCount": 0})
    (tmp_path / "reports" / "daily" / "20260425").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports" / "daily" / "20260425" / "daily_report.json").write_text("{}", encoding="utf-8")
    _write_json(tmp_path / "data" / "normalized" / "20260425" / "24" / "race_1.json", {"data_status": {"odds3t": "ok", "beforeinfo": "ok", "result": "missing"}, "result": {"dataStatus": "missing"}})

    result = health_check_mod.health_check(target_date="20260425")
    summary = result["summary"]

    assert summary["frozenBetsExists"] is True
    assert summary["dailyReportExists"] is True
    assert summary["resultMissingCount"] >= 1
    assert summary["status"] == "ok"
    assert summary["taskSchedulerStatus"]["status"] == "ok"
    assert summary["latestTaskLogs"]["Boatrace_DailyFreeze"].endswith("daily_freeze_20260425.log")
    assert summary["dailyFreezeLastRun"] == "2026-04-25T07:00:00"
    assert Path(result["files"]["json"]).exists()
    assert Path(result["files"]["md"]).exists()


def test_health_check_warns_when_frozen_or_daily_report_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(health_check_mod, "ROOT", tmp_path)
    monkeypatch.setattr(health_check_mod, "REPORT_MONITORING_ROOT", tmp_path / "reports" / "monitoring")
    monkeypatch.setattr(health_check_mod, "REPORT_DAILY_ROOT", tmp_path / "reports" / "daily")
    monkeypatch.setattr(health_check_mod, "NORMALIZED_ROOT", tmp_path / "data" / "normalized")
    monkeypatch.setattr(health_check_mod, "UI_ROOT", tmp_path / "data" / "ui")
    monkeypatch.setattr(health_check_mod, "PRED_ROOT", tmp_path / "data" / "predictions")
    monkeypatch.setattr(health_check_mod, "ERRORS_ROOT", tmp_path / "reports" / "errors")
    monkeypatch.setattr(
        health_check_mod,
        "_load_backfill_readiness",
        lambda: {"canTuneWithBackfill": False},
    )
    monkeypatch.setattr(
        health_check_mod,
        "task_status_mod",
        type("TaskStatusStub", (), {"task_status": staticmethod(lambda: {"summary": {"status": "warning", "latestTaskLogs": {}, "tasks": []}, "files": {}})}),
    )

    _write_json(tmp_path / "data" / "normalized" / "20260425" / "today_venues.json", {"date": "20260425", "venues": [{"jcd": "24"}]})
    _write_json(tmp_path / "data" / "ui" / "20260425" / "raceyosou_24.json", {"date": "20260425", "races": [{"rno": 1}]})

    result = health_check_mod.health_check(target_date="20260425")
    summary = result["summary"]

    assert summary["frozenBetsExists"] is False
    assert summary["dailyReportExists"] is False
    assert summary["status"] == "warning"
    assert "frozen_bets_missing" in summary["warnings"]
    assert "daily_report_missing" in summary["warnings"]
    assert summary["taskSchedulerStatus"]["status"] == "warning"
