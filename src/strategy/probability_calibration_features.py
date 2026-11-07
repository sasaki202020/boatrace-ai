from __future__ import annotations

import numpy as np
import pandas as pd


CALIBRATION_FEATURE_COLUMNS = [
    "approx_prob",
    "sort_score",
    "first_win_proba",
    "candidate_rank_by_sort",
    "sort_gap_top1",
    "approx_gap_top1",
    "race_first_win_gap12",
    "race_first_win_gap23",
]


def add_probability_calibration_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["approx_prob", "sort_score", "first_win_proba"]:
        if col not in out.columns:
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)

    sort_order = ["race_id", "sort_score", "approx_prob", "first_win_proba"]
    out = out.sort_values(sort_order, ascending=[True, False, False, False], kind="mergesort").reset_index(drop=True)
    out["candidate_rank_by_sort"] = out.groupby("race_id").cumcount() + 1

    out["sort_gap_top1"] = out.groupby("race_id")["sort_score"].transform("max") - out["sort_score"]
    out["approx_gap_top1"] = out.groupby("race_id")["approx_prob"].transform("max") - out["approx_prob"]

    race_gap12: dict[object, float] = {}
    race_gap23: dict[object, float] = {}
    for race_id, grp in out.groupby("race_id", sort=False):
        vals = (
            pd.to_numeric(grp["first_win_proba"], errors="coerce")
            .dropna()
            .sort_values(ascending=False)
            .drop_duplicates()
            .tolist()
        )
        top1 = float(vals[0]) if len(vals) >= 1 else 0.0
        top2 = float(vals[1]) if len(vals) >= 2 else 0.0
        top3 = float(vals[2]) if len(vals) >= 3 else 0.0
        race_gap12[race_id] = max(top1 - top2, 0.0)
        race_gap23[race_id] = max(top2 - top3, 0.0)

    out["race_first_win_gap12"] = out["race_id"].map(race_gap12).fillna(0.0)
    out["race_first_win_gap23"] = out["race_id"].map(race_gap23).fillna(0.0)

    for col in CALIBRATION_FEATURE_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return out


def available_calibration_feature_columns(df: pd.DataFrame) -> list[str]:
    usable: list[str] = []
    for col in CALIBRATION_FEATURE_COLUMNS:
        if col not in df.columns:
            continue
        series = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
        if float(series.std(ddof=0)) <= 1e-12:
            continue
        usable.append(col)
    return usable
