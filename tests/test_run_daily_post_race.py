from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from src.pipeline import run_daily_post_race


def test_missing_predictions_result_marks_missing_data() -> None:
    status, failure_step, warnings = run_daily_post_race._missing_predictions_result(date(2026, 4, 28))

    assert status == "missing_data"
    assert failure_step == "predictions_unavailable_for_date"
    assert warnings == ["predictions_unavailable_for_date:2026-04-28"]


def test_main_missing_predictions_writes_missing_data_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(run_daily_post_race, "ROOT", tmp_path)
    monkeypatch.setattr(run_daily_post_race, "report_dir_for", lambda target_date: tmp_path / "reports" / "daily" / target_date.isoformat())
    monkeypatch.setattr(run_daily_post_race, "existing_report_dir_for", lambda target_date: tmp_path / "reports" / "daily" / target_date.isoformat())
    monkeypatch.setattr(run_daily_post_race, "log_file_for", lambda name, target_date: tmp_path / "logs" / f"{name}_{target_date.isoformat()}.log")
    monkeypatch.setattr(run_daily_post_race, "append_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_daily_post_race, "run_step", lambda *args, **kwargs: {"label": args[0], "cmd": list(args[1]), "cwd": str(tmp_path), "started_at": "", "ended_at": "", "duration_sec": 0.0, "status": "ok", "returncode": 0, "stdout_tail": "", "stderr_tail": "", "allow_failure": bool(kwargs.get("allow_failure", False))})
    monkeypatch.setattr(run_daily_post_race, "_load_csv", lambda path: pd.DataFrame())
    monkeypatch.setattr(run_daily_post_race, "build_truth", lambda path: pd.DataFrame({"race_id": []}))
    monkeypatch.setattr(run_daily_post_race, "_run_write_daily_metrics", lambda **kwargs: 0)
    monkeypatch.setattr(run_daily_post_race, "_run_monitoring_summary", lambda: 0)

    monkeypatch.setattr(run_daily_post_race.sys, "argv", ["run_daily_post_race.py", "--date", "2026-04-28"])

    run_daily_post_race.main()

    report_dir = tmp_path / "reports" / "daily" / "2026-04-28"
    payload = json.loads((report_dir / "post_race_run.json").read_text(encoding="utf-8"))

    assert payload["status"] == "missing_data"
    assert payload["failure_step"] == "predictions_unavailable_for_date"
    assert payload["warnings"] == ["predictions_unavailable_for_date:2026-04-28"]
    assert not (report_dir / "daily_summary.json").exists()
    assert not (report_dir / "improvement_report.json").exists()
