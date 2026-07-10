from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.pipeline import generate_trifecta_candidates as mod


def _write_frame(path: Path, frame: pd.DataFrame) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8")
    return path


def _build_feature_availability(path: Path, extra_rows: list[dict] | None = None) -> Path:
    rows = [
        {"feature_name": "win_proba", "source_table": "win_proba_predictions", "available_phase": "pre_race", "allowed_for_training": True, "allowed_for_live": True, "description": "win"},
        {"feature_name": "win_proba_norm", "source_table": "win_proba_predictions", "available_phase": "pre_race", "allowed_for_training": True, "allowed_for_live": True, "description": "norm"},
        {"feature_name": "final_win_proba", "source_table": "win_proba_predictions", "available_phase": "pre_race", "allowed_for_training": True, "allowed_for_live": True, "description": "final"},
        {"feature_name": "pred_rank_within_race", "source_table": "win_proba_predictions", "available_phase": "pre_race", "allowed_for_training": True, "allowed_for_live": True, "description": "rank"},
        {"feature_name": "lane", "source_table": "normalized_entries", "available_phase": "entry", "allowed_for_training": True, "allowed_for_live": True, "description": "lane"},
        {"feature_name": "class", "source_table": "normalized_pre_race", "available_phase": "pre_race", "allowed_for_training": True, "allowed_for_live": True, "description": "class"},
        {"feature_name": "avg_st", "source_table": "normalized_pre_race", "available_phase": "pre_race", "allowed_for_training": True, "allowed_for_live": True, "description": "avg_st"},
        {"feature_name": "nat_win_rate", "source_table": "normalized_pre_race", "available_phase": "pre_race", "allowed_for_training": True, "allowed_for_live": True, "description": "nat"},
        {"feature_name": "local_win_rate", "source_table": "normalized_pre_race", "available_phase": "pre_race", "allowed_for_training": True, "allowed_for_live": True, "description": "local"},
        {"feature_name": "motor_rate", "source_table": "normalized_pre_race", "available_phase": "pre_race", "allowed_for_training": True, "allowed_for_live": True, "description": "motor"},
        {"feature_name": "boat_rate", "source_table": "normalized_pre_race", "available_phase": "pre_race", "allowed_for_training": True, "allowed_for_live": True, "description": "boat"},
        {"feature_name": "exhibition_time", "source_table": "normalized_pre_race", "available_phase": "pre_race", "allowed_for_training": True, "allowed_for_live": True, "description": "exh"},
        {"feature_name": "winning_trifecta", "source_table": "normalized_results", "available_phase": "result", "allowed_for_training": True, "allowed_for_live": False, "description": "label"},
    ]
    if extra_rows:
        rows.extend(extra_rows)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")
    return path


def _build_six_lane_input() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    race_id = "20260407-12-03"
    base = []
    feat = []
    result = []
    for lane in range(1, 7):
        strength = 7 - lane
        base.append(
            {
                "race_id": race_id,
                "date": "2026-04-07",
                "race_date": "2026-04-07",
                "jcd": 12,
                "race_no": 3,
                "lane": lane,
                "win_proba_norm": {1: 0.55, 2: 0.20, 3: 0.12, 4: 0.07, 5: 0.04, 6: 0.02}[lane],
                "final_win_proba": {1: 0.55, 2: 0.20, 3: 0.12, 4: 0.07, 5: 0.04, 6: 0.02}[lane],
                "pred_rank_within_race": lane,
            }
        )
        feat.append(
            {
                "race_id": race_id,
                "race_date": "2026-04-07",
                "jcd": 12,
                "race_no": 3,
                "lane": lane,
                "class": "A1" if lane == 1 else "B1",
                "avg_st": 0.10 + lane * 0.02,
                "nat_win_rate": strength * 10.0,
                "local_win_rate": strength * 9.0,
                "motor_rate": strength * 8.0,
                "boat_rate": strength * 7.0,
                "exhibition_time": 6.9 + lane * 0.05,
            }
        )
        result.append(
            {
                "race_id": race_id,
                "race_date": "2026-04-07",
                "jcd": 12,
                "race_no": 3,
                "lane": lane,
                "finish_position": lane,
                "winning_trifecta": "1-2-3",
            }
        )
    return pd.DataFrame(base), pd.DataFrame(feat), pd.DataFrame(result)


def test_generate_120_unique_candidates_and_topn(tmp_path: Path) -> None:
    win_df, feat_df, result_df = _build_six_lane_input()
    win_path = _write_frame(tmp_path / "win.csv", win_df)
    feat_path = _write_frame(tmp_path / "feat.csv", feat_df)
    result_path = _write_frame(tmp_path / "result.csv", result_df)
    avail_path = _build_feature_availability(tmp_path / "feature_availability.csv")

    summary = mod.generate_trifecta_candidates(
        win_path,
        pre_race_features_path=feat_path,
        results_path=result_path,
        feature_availability_path=avail_path,
        top_n=5,
        out_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        compat_candidates_path=tmp_path / "data" / "strategy_outputs" / "trifecta_candidates.csv",
    )

    full_path = tmp_path / "data" / "predictions" / "trifecta_candidates_full_2026-04-07.csv"
    topn_path = tmp_path / "data" / "predictions" / "trifecta_candidates_topn_2026-04-07.csv"
    compat_path = tmp_path / "data" / "strategy_outputs" / "trifecta_candidates.csv"

    assert full_path.exists()
    assert topn_path.exists()
    assert compat_path.exists()

    full_df = pd.read_csv(full_path)
    topn_df = pd.read_csv(topn_path)
    assert len(full_df) == 120
    assert full_df["trifecta_key"].nunique() == 120
    assert full_df["candidate_rank"].min() == 1
    assert full_df.sort_values("candidate_rank").iloc[0]["candidate_rank"] == 1
    assert len(topn_df) == 5
    assert summary["generation_summary"]["candidate_rows"] == 120
    assert summary["evaluation_available"] is True
    assert summary["overall_metrics"]["hit@1"] == 1.0
    assert summary["overall_metrics"]["mean_winning_rank"] == 1.0


def test_generation_works_without_results_path(tmp_path: Path) -> None:
    win_df, feat_df, _ = _build_six_lane_input()
    win_path = _write_frame(tmp_path / "win.csv", win_df)
    feat_path = _write_frame(tmp_path / "feat.csv", feat_df)
    avail_path = _build_feature_availability(tmp_path / "feature_availability.csv")

    summary = mod.generate_trifecta_candidates(
        win_path,
        pre_race_features_path=feat_path,
        results_path=None,
        feature_availability_path=avail_path,
        top_n=3,
        out_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        compat_candidates_path=tmp_path / "data" / "strategy_outputs" / "trifecta_candidates.csv",
    )

    assert summary["evaluation_available"] is False
    assert summary["generation_summary"]["candidate_rows"] == 120


def test_result_columns_in_pre_race_features_are_rejected(tmp_path: Path) -> None:
    win_df, feat_df, _ = _build_six_lane_input()
    feat_df["winning_trifecta"] = "1-2-3"
    win_path = _write_frame(tmp_path / "win.csv", win_df)
    feat_path = _write_frame(tmp_path / "feat.csv", feat_df)
    avail_path = _build_feature_availability(tmp_path / "feature_availability.csv")

    with pytest.raises(ValueError, match="result_phase_used"):
        mod.generate_trifecta_candidates(
            win_path,
            pre_race_features_path=feat_path,
            feature_availability_path=avail_path,
            out_dir=tmp_path / "data",
            report_dir=tmp_path / "reports",
            compat_candidates_path=tmp_path / "data" / "strategy_outputs" / "trifecta_candidates.csv",
        )


def test_live_forbidden_columns_are_rejected(tmp_path: Path) -> None:
    win_df, feat_df, _ = _build_six_lane_input()
    feat_df["bad_live_col"] = 1.0
    win_path = _write_frame(tmp_path / "win.csv", win_df)
    feat_path = _write_frame(tmp_path / "feat.csv", feat_df)
    avail_path = _build_feature_availability(
        tmp_path / "feature_availability.csv",
        extra_rows=[
            {
                "feature_name": "bad_live_col",
                "source_table": "normalized_pre_race",
                "available_phase": "pre_race",
                "allowed_for_training": True,
                "allowed_for_live": False,
                "description": "forbidden",
            }
        ],
    )

    with pytest.raises(ValueError, match="live_forbidden_columns"):
        mod.generate_trifecta_candidates(
            win_path,
            pre_race_features_path=feat_path,
            feature_availability_path=avail_path,
            out_dir=tmp_path / "data",
            report_dir=tmp_path / "reports",
            compat_candidates_path=tmp_path / "data" / "strategy_outputs" / "trifecta_candidates.csv",
        )


def test_less_than_six_lanes_are_skipped(tmp_path: Path) -> None:
    win_df, feat_df, _ = _build_six_lane_input()
    win_df = win_df[win_df["lane"] <= 5].copy()
    feat_df = feat_df[feat_df["lane"] <= 5].copy()
    win_path = _write_frame(tmp_path / "win.csv", win_df)
    feat_path = _write_frame(tmp_path / "feat.csv", feat_df)
    avail_path = _build_feature_availability(tmp_path / "feature_availability.csv")

    summary = mod.generate_trifecta_candidates(
        win_path,
        pre_race_features_path=feat_path,
        feature_availability_path=avail_path,
        out_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        compat_candidates_path=tmp_path / "data" / "strategy_outputs" / "trifecta_candidates.csv",
    )

    assert summary["generation_summary"]["candidate_rows"] == 0
    assert summary["generation_summary"]["skip_reason_counts"]["less_than_six_boats"] == 1
