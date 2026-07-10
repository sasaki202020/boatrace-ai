from __future__ import annotations

import numpy as np
import pandas as pd

from src.features.build_relative_features import RELATIVE_FEATURE_COLUMNS, add_race_relative_features


def test_add_race_relative_features_adds_expected_columns_and_values() -> None:
    df = pd.DataFrame(
        {
            "race_id": ["r1"] * 6,
            "boat_no": [1, 2, 3, 4, 5, 6],
            "national_2ren_rate": [0.10, 0.20, 0.20, 0.05, 0.30, np.nan],
            "local_2ren_rate": [0.30, 0.30, 0.30, 0.30, 0.30, 0.30],
            "avg_st": [0.15, 0.14, 0.16, 0.12, 0.13, np.nan],
        }
    )

    out = add_race_relative_features(df)

    for col in RELATIVE_FEATURE_COLUMNS:
        assert col in out.columns
    assert out.attrs["relative_feature_columns"] == RELATIVE_FEATURE_COLUMNS

    # national/local: higher is better, so boat_no=5 should rank first.
    assert out.loc[4, "national_2ren_rate_rank_in_race"] == 1
    # avg_st: lower is better, so boat_no=4 should rank first.
    assert out.loc[3, "avg_st_rank_in_race"] == 1
    # Constant local_2ren_rate => z should be zero, not NaN or inf.
    assert out["local_2ren_rate_z_in_race"].dropna().eq(0.0).all()
    # Missing source value should remain missing in derived features.
    assert pd.isna(out.loc[5, "avg_st_advantage_vs_mean"])


def test_add_race_relative_features_handles_small_race_safely() -> None:
    df = pd.DataFrame(
        {
            "race_id": ["r2"] * 5,
            "boat_no": [1, 2, 3, 4, 5],
            "national_2ren_rate": [0.10] * 5,
            "local_2ren_rate": [0.20] * 5,
            "avg_st": [0.15] * 5,
        }
    )

    out = add_race_relative_features(df)

    assert len(out) == 5
    assert out["national_2ren_rate_z_in_race"].eq(0.0).all()
    assert out["local_2ren_rate_z_in_race"].eq(0.0).all()
    assert out["avg_st_advantage_z_in_race"].eq(0.0).all()
    assert out.attrs["relative_feature_warnings"]
