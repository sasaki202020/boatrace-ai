from __future__ import annotations

import pandas as pd

from src.models.win_lgbm import REQUESTED_RELATIVE_SET, select_feature_columns


def test_select_feature_columns_excludes_identifiers_and_relative_features_from_baseline() -> None:
    frame = pd.DataFrame(
        {
            "race_id": ["r1"],
            "lane": [1],
            "date": ["2026-04-01"],
            "target_win": [1],
            "national_win_rate": [0.55],
            "motor_2ren_rate": [0.42],
            "racer_id": [12345],
            "jcd": [1],
            "win_rate_diff_to_avg": [0.05],
            "exhibition_time_rank": [1.0],
            "national_2ren_rate_rank_in_race": [1.0],
        }
    )

    cols = select_feature_columns(frame, "baseline")

    assert "national_win_rate" in cols
    assert "motor_2ren_rate" in cols
    assert "racer_id" not in cols
    assert "jcd" not in cols
    assert "win_rate_diff_to_avg" not in cols
    assert "exhibition_time_rank" not in cols
    assert "national_2ren_rate_rank_in_race" not in cols


def test_select_feature_columns_includes_relative_features_for_candidate() -> None:
    frame = pd.DataFrame(
        {
            "race_id": ["r1"],
            "lane": [1],
            "date": ["2026-04-01"],
            "target_win": [1],
            "national_win_rate": [0.55],
            "win_rate_diff_to_avg": [0.05],
            "national_2ren_rate_rank_in_race": [1.0],
        }
    )

    cols = select_feature_columns(frame, "relative")

    assert "national_win_rate" in cols
    assert REQUESTED_RELATIVE_SET.issuperset({"national_2ren_rate_rank_in_race"})
    assert "win_rate_diff_to_avg" not in cols
    assert "national_2ren_rate_rank_in_race" in cols
