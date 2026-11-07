from __future__ import annotations

import pandas as pd

from src.data.build_trainable_win_training_data import build_trainable_win_training_data


def test_build_trainable_win_training_data_imputes_required_columns() -> None:
    df = pd.DataFrame(
        [
            {
                "race_id": "20260401_01_01",
                "date": "2026-04-01",
                "venue": "TEST",
                "race_number": 1,
                "boat_no": 1,
                "finish_position": 1,
                "target_win": 1,
                "racer_id": 100,
                "national_2ren_rate": 35.0,
                "local_2ren_rate": 30.0,
                "avg_st": 0.14,
            },
            {
                "race_id": "20260401_01_01",
                "date": "2026-04-01",
                "venue": "TEST",
                "race_number": 1,
                "boat_no": 2,
                "finish_position": 2,
                "target_win": 0,
                "racer_id": 100,
                "national_2ren_rate": None,
                "local_2ren_rate": None,
                "avg_st": None,
            },
            {
                "race_id": "20260402_01_01",
                "date": "2026-04-02",
                "venue": "TEST",
                "race_number": 1,
                "boat_no": 1,
                "finish_position": 2,
                "target_win": 0,
                "racer_id": 101,
                "national_2ren_rate": None,
                "local_2ren_rate": None,
                "avg_st": 0.18,
            },
        ]
    )

    trainable_df, summary = build_trainable_win_training_data(df)

    assert len(trainable_df) == 3
    assert trainable_df["national_2ren_rate"].isna().sum() == 0
    assert trainable_df["local_2ren_rate"].isna().sum() == 0
    assert trainable_df["avg_st"].isna().sum() == 0
    assert summary.imputed_value_counts["national_2ren_rate"] == 2
    assert summary.imputed_value_counts["local_2ren_rate"] == 2
    assert summary.imputed_value_counts["avg_st"] == 1
    assert all(rate == 0.0 for rate in summary.remaining_missing_rate_by_feature.values())
