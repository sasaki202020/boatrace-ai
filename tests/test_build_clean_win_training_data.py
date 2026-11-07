from __future__ import annotations

import pandas as pd

from src.data.build_clean_win_training_data import build_clean_win_training_data


def test_build_clean_win_training_data_cleans_duplicates_and_drops_invalid_races() -> None:
    rows: list[dict[str, object]] = []

    for boat_no in range(1, 7):
        rows.append(
            {
                "race_id": "legacy-a",
                "date": "2026-04-01",
                "jcd": 1,
                "venue": "TEST",
                "race_no": 1,
                "lane": boat_no,
                "finish_position": boat_no,
                "national_2ren_rate": None,
                "local_2ren_rate": None,
                "st": 0.10 + 0.01 * boat_no,
            }
        )
    rows.append(
        {
            "race_id": "legacy-a-dup",
            "date": "2026-04-01",
            "jcd": 1,
            "venue": "TEST",
            "race_no": 1,
            "lane": 1,
            "finish_position": None,
            "national_2ren_rate": None,
            "local_2ren_rate": None,
            "st": None,
        }
    )

    for boat_no in range(1, 6):
        rows.append(
            {
                "race_id": "legacy-b",
                "date": "2026-04-02",
                "jcd": 2,
                "venue": "TEST",
                "race_no": 2,
                "lane": boat_no,
                "finish_position": boat_no,
                "national_2ren_rate": 0.1,
                "local_2ren_rate": 0.2,
                "st": 0.15,
            }
        )

    for boat_no in range(1, 7):
        rows.append(
            {
                "race_id": "legacy-c",
                "date": "2026-04-03",
                "jcd": 3,
                "venue": "TEST",
                "race_no": 3,
                "lane": boat_no,
                "finish_position": 2 if boat_no in (1, 2) else boat_no,
                "national_2ren_rate": 0.1,
                "local_2ren_rate": 0.2,
                "st": 0.15,
            }
        )

    clean_df, summary, dropped_races_df = build_clean_win_training_data(pd.DataFrame(rows))

    assert len(clean_df) == 6
    assert clean_df["race_id"].nunique() == 1
    assert clean_df["race_id"].iloc[0] == "20260401_01_01"
    assert clean_df["target_win"].sum() == 1
    assert clean_df["boat_no"].tolist() == [1, 2, 3, 4, 5, 6]

    assert summary.input_row_count == 18
    assert summary.output_row_count == 6
    assert summary.input_unique_race_count == 3
    assert summary.output_unique_race_count == 1
    assert summary.dropped_race_count == 2
    assert summary.duplicate_resolution_count == 1
    assert summary.dropped_reason_counts["non_six_boat_race"] == 1
    assert summary.dropped_reason_counts["target_win_invalid_race"] == 1
    assert "national_2ren_rate" in summary.baseline_feature_exclusion_candidates

    assert set(dropped_races_df["race_id"]) == {"20260402_02_02", "20260403_03_03"}
    assert set(dropped_races_df["drop_reasons"]) == {"non_six_boat_race", "target_win_invalid_race"}
