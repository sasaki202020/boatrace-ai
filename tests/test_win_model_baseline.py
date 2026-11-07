from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.models.win_baseline_common import (
    build_comparison_report,
    evaluate_model_bundle,
    load_feature_set_config,
    rows_for_report_dicts,
    train_single_feature_set,
)


def _make_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day_idx, date in enumerate(pd.date_range("2026-01-01", periods=5, freq="D"), start=1):
        race_id = f"{date:%Y%m%d}_01_01"
        for boat_no in range(1, 7):
            finish_position = ((boat_no + day_idx - 1) % 6) + 1
            rows.append(
                {
                    "race_id": race_id,
                    "date": date.strftime("%Y-%m-%d"),
                    "finish_position": finish_position,
                    "boat_no": boat_no,
                    "jcd": day_idx,
                    "race_number": 1,
                    "exhibition_time": 1000,
                    "avg_st": 0.10 + 0.01 * boat_no,
                    "national_2ren_rate": 20.0 + day_idx + boat_no,
                    "local_2ren_rate": 15.0 + day_idx + boat_no,
                    "target_win": 1 if finish_position == 1 else 0,
                }
            )
    return pd.DataFrame(rows)


def test_win_baseline_training_filters_constant_features_and_builds_report(tmp_path: Path) -> None:
    frame = _make_frame()

    core_path = tmp_path / "core.json"
    extended_path = tmp_path / "extended.json"
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
    extended_path.write_text(
        json.dumps(
            {
                "feature_set_name": "win_baseline_extended",
                "features": ["avg_st", "boat_no", "exhibition_time", "jcd", "local_2ren_rate", "national_2ren_rate", "race_number"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    core_config = load_feature_set_config(core_path)
    extended_config = load_feature_set_config(extended_path)

    core_result, _, _, _ = train_single_feature_set(
        trainable_frame=frame,
        feature_set_config=core_config,
        feature_set_path=core_path,
        model_path=tmp_path / "core.joblib",
        valid_days=1,
        test_days=1,
        random_state=7,
    )
    extended_result, _, _, _ = train_single_feature_set(
        trainable_frame=frame,
        feature_set_config=extended_config,
        feature_set_path=extended_path,
        model_path=tmp_path / "extended.joblib",
        valid_days=1,
        test_days=1,
        random_state=7,
    )

    assert "exhibition_time" in core_result.dropped_constant_features
    assert "race_number" in core_result.dropped_constant_features
    assert "avg_st" in extended_result.final_feature_list
    assert "national_2ren_rate" in extended_result.final_feature_list
    assert core_result.metrics["test"]["top1_accuracy"] >= 0.0
    assert extended_result.metrics["test"]["top1_accuracy"] >= 0.0

    core_report, _, _ = evaluate_model_bundle(trainable_frame=frame, bundle_path=tmp_path / "core.joblib", valid_days=1, test_days=1)
    extended_report, _, _ = evaluate_model_bundle(trainable_frame=frame, bundle_path=tmp_path / "extended.joblib", valid_days=1, test_days=1)
    comparison = build_comparison_report([core_result, extended_result], input_dataset="synthetic")
    csv_df = rows_for_report_dicts([core_report, extended_report])

    assert core_report["feature_set_name"] == "win_baseline_core"
    assert extended_report["feature_set_name"] == "win_baseline_extended"
    assert comparison["report_type"] == "win_model_baseline_core_vs_extended"
    assert not csv_df.empty
