from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

import scripts.run_architecture_v2_walk_forward_validation as module


def test_walk_forward_script_can_run_as_direct_cli() -> None:
    result = subprocess.run(
        [sys.executable, str(Path(module.__file__).resolve()), "--help"],
        cwd=Path(module.__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def _make_training_data(path: Path) -> None:
    rows: list[dict[str, object]] = []
    for day_index, date in enumerate(pd.date_range("2026-01-01", periods=12, freq="D")):
        for boat_no in range(1, 7):
            rows.append(
                {
                    "date": date.strftime("%Y-%m-%d"),
                    "jcd": 1 + day_index % 2,
                    "venue": "test",
                    "race_number": 1,
                    "finish_position": ((boat_no + day_index - 1) % 6) + 1,
                    "boat_no": boat_no,
                    "racer_id": 1000 + boat_no,
                    "avg_st": 0.10 + boat_no * 0.01,
                    "exhibition_time": 6.7 + boat_no * 0.01,
                    "union_key": f"{date:%Y%m%d}_01_01",
                    "race_id": f"{date:%Y%m%d}_01_01",
                    "national_2ren_rate": 20.0 + boat_no + day_index,
                    "local_2ren_rate": 15.0 + boat_no + day_index,
                    "target_win": 1 if ((boat_no + day_index - 1) % 6) + 1 == 1 else 0,
                }
            )
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")


def _write_feature_sets(root: Path) -> tuple[Path, Path]:
    core = root / "core.json"
    challenger = root / "challenger.json"
    core.write_text(
        json.dumps({"feature_set_name": "core", "features": ["boat_no", "jcd", "race_number"]}),
        encoding="utf-8",
    )
    challenger.write_text(
        json.dumps(
            {
                "feature_set_name": "core_relative",
                "features": ["boat_no", "jcd", "race_number", "national_2ren_rate_rank_in_race"],
                "relative_features": ["national_2ren_rate_rank_in_race"],
            }
        ),
        encoding="utf-8",
    )
    return core, challenger


def test_walk_forward_retrains_both_models_on_identical_time_windows(tmp_path: Path) -> None:
    training_path = tmp_path / "trainable.csv"
    _make_training_data(training_path)
    core_path, challenger_path = _write_feature_sets(tmp_path)
    trace_path = tmp_path / "candidate_trace.json"
    trace_path.write_text(
        json.dumps({"startDate": "2026-01-11", "endDate": "2026-01-12"}),
        encoding="utf-8",
    )

    report = module.build_walk_forward_validation(
        trainable_path=training_path,
        core_feature_set_path=core_path,
        challenger_feature_set_path=challenger_path,
        candidate_trace_path=trace_path,
        fold_count=2,
        valid_days=2,
        test_days=2,
        model_work_dir=tmp_path / "models",
    )

    assert report["counts"]["foldCount"] == 2
    assert report["quality"]["samePeriodModelComparison"] is True
    assert report["quality"]["futureLeakageDetected"] is False
    assert report["quality"]["classification"] == "validation_ready"
    assert report["crossLayer"]["policyPeriodOverlapDays"] == 2
    assert report["crossLayer"]["samePeriodCrossLayerValidation"] is True
    assert all(fold["splitParity"] for fold in report["folds"])
    assert all(fold["core"]["metrics"]["test"]["n_races"] == 2 for fold in report["folds"])


def test_walk_forward_marks_missing_policy_period_overlap_as_warning(tmp_path: Path) -> None:
    training_path = tmp_path / "trainable.csv"
    _make_training_data(training_path)
    core_path, challenger_path = _write_feature_sets(tmp_path)
    trace_path = tmp_path / "candidate_trace.json"
    trace_path.write_text(
        json.dumps({"startDate": "2026-02-01", "endDate": "2026-02-02"}),
        encoding="utf-8",
    )

    report = module.build_walk_forward_validation(
        trainable_path=training_path,
        core_feature_set_path=core_path,
        challenger_feature_set_path=challenger_path,
        candidate_trace_path=trace_path,
        fold_count=1,
        valid_days=2,
        test_days=2,
        model_work_dir=tmp_path / "models",
    )

    assert report["quality"]["classification"] == "validation_warning"
    assert report["crossLayer"]["policyPeriodOverlapDays"] == 0
    assert report["crossLayer"]["samePeriodCrossLayerValidation"] is False
