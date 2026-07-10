from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.features.build_relative_features import RELATIVE_FEATURE_COLUMNS
from src.models.evaluate_win_model_relative import evaluate_core_vs_core_relative
from src.models.train_win_model_relative import train_core_vs_core_relative


def _make_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day_idx, date in enumerate(pd.date_range("2026-01-01", periods=5, freq="D"), start=1):
        race_id = f"{date:%Y%m%d}_01_01"
        for boat_no in range(1, 7):
            rows.append(
                {
                    "race_id": race_id,
                    "date": date.strftime("%Y-%m-%d"),
                    "finish_position": boat_no,
                    "boat_no": boat_no,
                    "jcd": day_idx,
                    "race_number": 1,
                    "exhibition_time": 1000,
                    "avg_st": 0.10 + 0.01 * boat_no,
                    "national_2ren_rate": 20.0 + day_idx + boat_no,
                    "local_2ren_rate": 15.0 + day_idx + boat_no,
                    "target_win": 1 if boat_no == 1 else 0,
                }
            )
    return pd.DataFrame(rows)


def test_win_model_relative_trains_core_and_core_relative_only(tmp_path: Path) -> None:
    frame = _make_frame()
    trainable_path = tmp_path / "trainable.csv"
    frame.to_csv(trainable_path, index=False, encoding="utf-8")

    core_path = tmp_path / "core.json"
    core_path.write_text(
        json.dumps(
            {
                "feature_set_name": "win_baseline_core",
                "features": ["boat_no", "exhibition_time", "jcd", "race_number"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    runs, model_dir = train_core_vs_core_relative(
        trainable_path=trainable_path,
        core_feature_set_path=core_path,
        model_dir=tmp_path / "models",
    )

    assert len(runs) == 2
    core_run, relative_run = runs
    assert core_run.feature_set_name == "win_baseline_core"
    assert relative_run.feature_set_name == "win_baseline_core_relative"
    assert core_run.relative_features_used == []
    assert relative_run.relative_features_used == RELATIVE_FEATURE_COLUMNS
    assert "exhibition_time" in core_run.dropped_constant_features
    assert "exhibition_time" in relative_run.dropped_constant_features
    assert (model_dir / "win_baseline_core.joblib").exists()
    assert (model_dir / "win_baseline_core_relative.joblib").exists()

    report, feature_reports = evaluate_core_vs_core_relative(
        trainable_path=trainable_path,
        core_model_path=model_dir / "win_baseline_core.joblib",
        core_relative_model_path=model_dir / "win_baseline_core_relative.joblib",
    )
    assert report["report_type"] == "win_model_core_vs_core_relative"
    assert len(feature_reports) == 2
    assert report["feature_sets"][1]["relative_features_used"] == RELATIVE_FEATURE_COLUMNS
