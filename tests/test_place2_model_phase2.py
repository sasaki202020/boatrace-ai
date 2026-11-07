from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.features.build_place2_context_features import build_place2_context_frame
from src.models.place2_phase2_common import (
    DEFAULT_PHASE1_MODEL_PATH,
    evaluate_phase2_model,
    train_phase2_model,
)


def _make_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day_idx, date in enumerate(pd.date_range("2026-01-01", periods=5, freq="D"), start=1):
        race_id = f"{date:%Y%m%d}_01_01"
        for boat_no in range(1, 7):
            finish_position = ((boat_no + day_idx - 1) % 6) + 1
            rows.append(
                {
                    "date": date.strftime("%Y-%m-%d"),
                    "jcd": day_idx,
                    "venue": 1,
                    "race_number": 1,
                    "finish_position": finish_position,
                    "boat_no": boat_no,
                    "racer_id": 1000 + boat_no,
                    "avg_st": 0.10 + 0.01 * boat_no,
                    "exhibition_time": 1000 + boat_no,
                    "union_key": f"{race_id}_{boat_no}",
                    "race_id": race_id,
                    "racer_class": 1,
                    "national_win_rate": 10.0 + day_idx + boat_no,
                    "national_2ren_rate": 20.0 + day_idx + boat_no,
                    "local_win_rate": 8.0 + day_idx + boat_no,
                    "local_2ren_rate": 15.0 + day_idx + boat_no,
                    "motor_2ren_rate": 12.0 + boat_no,
                    "boat_2ren_rate": 11.0 + boat_no,
                    "target_win": 1 if finish_position == 1 else 0,
                }
            )
    return pd.DataFrame(rows)


def test_phase2_context_build_and_training_roundtrip(tmp_path: Path) -> None:
    frame = _make_frame()
    context_frame, summary = build_place2_context_frame(
        frame,
        phase1_bundle_path=DEFAULT_PHASE1_MODEL_PATH,
        split_name="train",
    )

    assert summary.input_race_count == 5
    assert summary.output_context_count == 5
    assert context_frame["place2_context_id"].nunique() == 5
    assert len(context_frame) == 25
    assert "candidate_boat_no" in context_frame.columns
    assert "fixed_first_place_boat_no" in context_frame.columns
    assert "candidate_rank_within_remaining_field" in context_frame.columns
    assert context_frame.groupby("place2_context_id").size().eq(5).all()
    assert context_frame["target_place2"].sum() == 5

    trainable_path = tmp_path / "trainable.csv"
    frame.to_csv(trainable_path, index=False, encoding="utf-8")

    model_path = tmp_path / "models" / "place2_model_phase2_core_relative.joblib"
    report_json = tmp_path / "reports" / "place2_model_phase2_core_relative.json"
    report_csv = tmp_path / "reports" / "place2_model_phase2_core_relative.csv"
    split_manifest = tmp_path / "reports" / "place2_model_phase2_split_manifest.json"

    result = train_phase2_model(
        trainable_path=trainable_path,
        phase1_model_path=DEFAULT_PHASE1_MODEL_PATH,
        model_path=model_path,
        report_json=report_json,
        report_csv=report_csv,
        split_manifest_path=split_manifest,
        valid_days=1,
        test_days=1,
    )

    assert model_path.exists()
    assert report_json.exists()
    assert report_csv.exists()
    assert split_manifest.exists()
    csv_df = pd.read_csv(report_csv, low_memory=False)
    required_cols = {
        "race_id",
        "place2_context_id",
        "fixed_first_place_boat_no",
        "fixed_first_place_source",
        "fixed_first_place_rank_within_race",
        "fixed_first_place_win_proba_raw",
        "fixed_first_place_win_proba_norm",
        "candidate_boat_no",
        "candidate_rank_within_race_before_fix",
        "candidate_rank_within_remaining_field",
        "p_place2_raw",
        "p_place2_norm",
        "target_place2",
        "date",
        "jcd",
        "race_number",
        "model_name",
        "feature_set_name",
        "split_name",
        "calibrated_flag",
    }
    assert required_cols.issubset(set(csv_df.columns))
    assert result["report"]["spec_name"] == "phase2_place2_model"
    assert result["report"]["phase"] == 2
    assert result["report"]["feature_set_name"] == "phase2_place2_conditional"
    assert result["report"]["metrics"]["test"]["top1_accuracy"] >= 0.0
    assert result["report"]["naive_baseline_metrics"]["test"]["top1_accuracy"] == 0.2

    eval_result = evaluate_phase2_model(
        model_path=model_path,
        trainable_path=trainable_path,
        phase1_model_path=DEFAULT_PHASE1_MODEL_PATH,
        report_json=report_json,
        report_csv=report_csv,
        split_manifest_path=split_manifest,
        valid_days=1,
        test_days=1,
    )
    assert eval_result["report"]["spec_name"] == "phase2_place2_model"
    assert eval_result["report"]["phase"] == 2
    saved = json.loads(report_json.read_text(encoding="utf-8"))
    assert saved["spec_name"] == "phase2_place2_model"
    assert saved["phase"] == 2
