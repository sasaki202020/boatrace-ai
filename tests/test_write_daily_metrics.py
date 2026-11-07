from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pandas as pd


def test_write_daily_metrics_emits_monitoring_summary(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    skip_path = tmp_path / "skip_decisions.csv"
    pd.DataFrame(
        [
            {"date": "2026-04-30", "decision": "BUY", "hit": 1, "odds": 5.2},
            {"date": "2026-04-30", "decision": "SKIP", "hit": 0, "odds": 0.0},
            {"date": "2026-04-30", "decision": "PENDING", "hit": 0, "odds": 0.0},
        ]
    ).to_csv(skip_path, index=False, encoding="utf-8")

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "write_daily_metrics.py"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script_path),
            "--date",
            "2026-04-30",
            "--skip-decisions",
            str(skip_path),
            "--real-odds-available",
            "2",
            "--pending-unpublished",
            "1",
            "--improvement-report-top1",
            "approx_prob",
        ],
    )

    runpy.run_path(str(script_path), run_name="__main__")

    daily_csv = tmp_path / "reports" / "daily" / "2026-04-30" / "daily_metrics.csv"
    monitor_csv = tmp_path / "reports" / "monitoring" / "daily_monitoring_summary.csv"
    monitor_json = tmp_path / "reports" / "monitoring" / "daily_monitoring_summary.json"

    assert daily_csv.exists()
    assert monitor_csv.exists()
    assert monitor_json.exists()

    df = pd.read_csv(monitor_csv)
    assert list(df["date"]) == ["2026-04-30", "TOTAL"]
    assert int(df.loc[0, "buy_count"]) == 1
    assert int(df.loc[0, "real_odds_available"]) == 2
    assert df.loc[0, "improvement_report_top1"] == "approx_prob"
