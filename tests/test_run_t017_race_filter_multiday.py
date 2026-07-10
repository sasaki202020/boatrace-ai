from __future__ import annotations

import json
import sys
from pathlib import Path

import src.eval.run_t017_race_filter_multiday as runner


def _make_report() -> dict[str, object]:
    methods = {
        "no_filter": {
            "result_available_rows": 1,
            "bought_races": 1,
            "skipped_races": 0,
            "missing_odds_rows": 0,
            "total_stake": 1.0,
            "total_return": 1.0,
            "profit": 0.0,
            "hit_count": 0,
            "hit_rate": 0.0,
            "roi": 0.0,
            "recovery_rate": 1.0,
            "max_drawdown": 0.0,
            "longest_losing_streak": 0,
            "score": 0.0,
        },
        "first_gap_filter": {
            "result_available_rows": 1,
            "bought_races": 1,
            "skipped_races": 0,
            "missing_odds_rows": 0,
            "total_stake": 1.0,
            "total_return": 1.0,
            "profit": 0.0,
            "hit_count": 0,
            "hit_rate": 0.0,
            "roi": 0.0,
            "recovery_rate": 1.0,
            "max_drawdown": 0.0,
            "longest_losing_streak": 0,
            "score": 0.0,
        },
        "top_score_gap_filter": {
            "result_available_rows": 1,
            "bought_races": 1,
            "skipped_races": 0,
            "missing_odds_rows": 0,
            "total_stake": 1.0,
            "total_return": 1.0,
            "profit": 0.0,
            "hit_count": 0,
            "hit_rate": 0.0,
            "roi": 0.0,
            "recovery_rate": 1.0,
            "max_drawdown": 0.0,
            "longest_losing_streak": 0,
            "score": 0.0,
        },
        "concentration_filter": {
            "result_available_rows": 1,
            "bought_races": 1,
            "skipped_races": 0,
            "missing_odds_rows": 0,
            "total_stake": 1.0,
            "total_return": 1.0,
            "profit": 0.0,
            "hit_count": 0,
            "hit_rate": 0.0,
            "roi": 0.0,
            "recovery_rate": 1.0,
            "max_drawdown": 0.0,
            "longest_losing_streak": 0,
            "score": 0.0,
        },
        "and_filter": {
            "result_available_rows": 1,
            "bought_races": 1,
            "skipped_races": 0,
            "missing_odds_rows": 0,
            "total_stake": 1.0,
            "total_return": 1.0,
            "profit": 0.0,
            "hit_count": 0,
            "hit_rate": 0.0,
            "roi": 0.0,
            "recovery_rate": 1.0,
            "max_drawdown": 0.0,
            "longest_losing_streak": 0,
            "score": 0.0,
        },
    }
    return {"target": "race_filter_comparison", "methods": methods}


def test_main_skips_missing_official_results(tmp_path, monkeypatch) -> None:
    daily_root = tmp_path / "reports" / "daily"
    tmp_root = tmp_path / "data" / "tmp"
    report_tmp_root = tmp_path / "reports" / "tmp"

    available = runner.SnapshotSource(
        date_label="2026-04-25",
        daily_dir=daily_root / "2026-04-25",
        snapshot_dir=tmp_root / "20260425_eval",
        source_status="available",
        missing_items=[],
    )
    missing_results = runner.SnapshotSource(
        date_label="2026-04-26",
        daily_dir=daily_root / "2026-04-26",
        snapshot_dir=tmp_root / "20260426_eval",
        source_status="available",
        missing_items=[],
    )

    monkeypatch.setattr(runner, "list_daily_sources", lambda: [available, missing_results])

    def fake_materialize(source: runner.SnapshotSource) -> dict[str, object]:
        if source.date_label == "2026-04-26":
            raise FileNotFoundError(
                "missing official results file for 2026-04-26: "
                "C:\\Users\\goo10\\競艇\\boatrace-ai-mvp\\data\\raw\\official\\results\\K260426.TXT"
            )
        return {"date": "20260425"}

    monkeypatch.setattr(runner, "materialize_snapshot", fake_materialize)
    monkeypatch.setattr(runner, "_run_ablation", lambda snapshot_dir, date_label: (_make_report(), tmp_path / f"{date_label}.json", 0))

    out_md = tmp_path / "out.md"
    out_json = tmp_path / "out.json"
    diag_md = tmp_path / "diag.md"
    diag_json = tmp_path / "diag.json"
    argv = [
        "prog",
        "--daily-root",
        str(daily_root),
        "--tmp-root",
        str(tmp_root),
        "--report-tmp-root",
        str(report_tmp_root),
        "--output-md",
        str(out_md),
        "--output-json",
        str(out_json),
        "--diag-md",
        str(diag_md),
        "--diag-json",
        str(diag_json),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    runner.main()

    summary = json.loads(out_json.read_text(encoding="utf-8"))
    diagnostics = json.loads(diag_json.read_text(encoding="utf-8"))

    assert summary["validated_dates"] == ["2026-04-25"]
    assert any(item["date"] == "2026-04-26" for item in summary["skipped_dates"])
    assert "missing official results file" in summary["skipped_dates"][-1]["reason"]
    assert diagnostics["materialized_snapshot_count"] == 1
