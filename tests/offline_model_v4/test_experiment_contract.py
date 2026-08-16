from __future__ import annotations

import pandas as pd
import pytest

from src.offline_model_v4.experiment import RESIDUAL_FEATURES, audit_dataset, build_gap_reset_features, default_specs


def valid_frame() -> pd.DataFrame:
    rows = []
    for race_no in (1, 2):
        for lane in range(1, 7):
            rows.append({
                "date": "2026-01-01", "race_id": f"20260101-01-{race_no:02d}",
                "jcd": 1, "race_no": race_no, "lane": lane,
                "target": int(lane == 1), "feature_availability_count": 3,
            })
    return pd.DataFrame(rows)


def test_default_grid_has_three_families_and_six_bounded_settings() -> None:
    specs = default_specs()
    assert len(specs) == 6
    assert {spec.family for spec in specs} == {"ranking", "residual", "calibration"}
    assert len({spec.name for spec in specs}) == len(specs)


def test_residual_features_do_not_double_count_lane_or_venue() -> None:
    assert "lane" not in RESIDUAL_FEATURES
    assert "jcd" not in RESIDUAL_FEATURES
    assert "race_no" not in RESIDUAL_FEATURES
    assert "lane_prior_win_rate" not in RESIDUAL_FEATURES
    assert "venue_lane_prior_win_rate" not in RESIDUAL_FEATURES
    assert set(RESIDUAL_FEATURES) == {
        "racer_prior_count", "racer_prior_win_rate", "racer_prior_top2_rate",
        "racer_prior_mean_finish", "racer_prior5_win_rate", "racer_prior10_win_rate",
        "days_since_previous_race", "feature_availability_count",
    }


def test_dataset_audit_requires_unique_six_boat_one_winner_races() -> None:
    audit = audit_dataset(valid_frame())
    assert audit["raceCount"] == 2
    assert audit["duplicateRaceLaneCount"] == 0
    assert audit["invalidRaceCount"] == 0
    duplicate = pd.concat([valid_frame(), valid_frame().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="dataset_contract_violation"):
        audit_dataset(duplicate)


def test_dataset_audit_rejects_result_columns_as_model_features() -> None:
    frame = valid_frame().assign(finish_position=[1, 2, 3, 4, 5, 6] * 2)
    audit = audit_dataset(frame)
    assert "finish_position" in audit["excludedPostRaceColumns"]


def test_gap_reset_does_not_carry_2020_history_into_2024() -> None:
    rows = []
    for date in ("2020-02-01", "2024-01-01", "2024-01-02"):
        for lane in range(1, 7):
            rows.append({"date": date, "race_id": f"{date.replace('-', '')}-01-01", "jcd": 1,
                         "race_no": 1, "lane": lane, "racer_id": 1000 + lane,
                         "finish_position": lane, "target": int(lane == 1)})
    reset = build_gap_reset_features(pd.DataFrame(rows), reset_date="2024-01-01")
    first = reset[reset["date"] == "2024-01-01"]
    second = reset[reset["date"] == "2024-01-02"]
    assert first["racer_prior_count"].eq(0).all()
    assert second["racer_prior_count"].eq(1).all()
