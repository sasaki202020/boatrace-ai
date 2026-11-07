from __future__ import annotations

import pandas as pd

from src.data.summarize_feature_readiness import summarize_feature_readiness


def test_summarize_feature_readiness_splits_core_and_extended() -> None:
    clean_df = pd.DataFrame(
        [
            {
                "race_id": "r1",
                "boat_no": 1,
                "date": "2026-04-01",
                "jcd": 1,
                "venue": "A",
                "race_number": 1,
                "finish_position": 1,
                "target_win": 1,
                "racer_id": 10,
                "avg_st": None,
                "exhibition_time": 680,
                "national_2ren_rate": None,
                "local_2ren_rate": None,
                "national_win_rate": None,
            },
            {
                "race_id": "r1",
                "boat_no": 2,
                "date": "2026-04-01",
                "jcd": 1,
                "venue": "A",
                "race_number": 1,
                "finish_position": 2,
                "target_win": 0,
                "racer_id": 11,
                "avg_st": 0.16,
                "exhibition_time": 690,
                "national_2ren_rate": None,
                "local_2ren_rate": None,
                "national_win_rate": None,
            },
        ]
    )
    trainable_df = clean_df.copy()
    trainable_df["avg_st"] = [0.18, 0.16]
    trainable_df["national_2ren_rate"] = [30.0, 31.0]
    trainable_df["local_2ren_rate"] = [28.0, 29.0]

    summary, detail_df, core_config, extended_config = summarize_feature_readiness(trainable_df, clean_df)

    assert core_config["features"] == ["boat_no", "exhibition_time", "jcd", "race_number"]
    assert extended_config["features"] == ["avg_st", "boat_no", "exhibition_time", "jcd", "local_2ren_rate", "national_2ren_rate", "race_number"]
    avg_st = detail_df.loc[detail_df["feature_name"] == "avg_st"].iloc[0]
    national_2ren_rate = detail_df.loc[detail_df["feature_name"] == "national_2ren_rate"].iloc[0]
    assert bool(avg_st["include_in_core"]) is False
    assert bool(avg_st["include_in_extended"]) is True
    assert bool(national_2ren_rate["include_in_core"]) is False
    assert bool(national_2ren_rate["include_in_extended"]) is True
    assert summary["avg_st_decision"]["feature_name"] == "avg_st"
