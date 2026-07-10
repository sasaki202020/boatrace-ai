from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from pandas.errors import EmptyDataError


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_v2_smoke_outputs_for_two_success_days() -> None:
    summary_0403 = _load_json(REPO_ROOT / "reports" / "v2" / "shadow_summary_20260403.json")
    summary_0404 = _load_json(REPO_ROOT / "reports" / "v2" / "shadow_summary_20260404.json")
    batch_summary = _load_json(REPO_ROOT / "reports" / "v2" / "batch_summary.json")
    batch_results = pd.read_csv(REPO_ROOT / "reports" / "v2" / "batch_results.csv")
    failures_path = REPO_ROOT / "reports" / "v2" / "batch_failures.csv"
    try:
        batch_failures = pd.read_csv(failures_path)
    except EmptyDataError:
        batch_failures = pd.DataFrame()

    assert summary_0403["compare_possible"] is True
    assert summary_0403["result_available_races"] == summary_0403["target_races"]
    assert summary_0403["failure_reasons"] == []

    assert summary_0404["compare_possible"] is True
    assert summary_0404["result_available_races"] == summary_0404["target_races"]
    assert summary_0404["failure_reasons"] == []

    assert batch_summary["success_count"] == 1
    assert batch_summary["fail_count"] == 0
    assert batch_summary["aggregate_count"] == 1
    assert batch_results["status"].tolist() == ["SUCCESS"]
    assert batch_results["reference_only"].tolist() == [False]
    assert batch_failures.empty
