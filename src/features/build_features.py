# -*- coding: utf-8 -*-
import argparse
import pandas as pd
import numpy as np
import os
import json
from pathlib import Path

from src.features.build_relative_features import add_race_relative_features

MOTOR_LOW_THRESH = 0.30
BOAT_LOW_THRESH = 0.30
JCD_LOW_THRESH = 0.30
STYLE_NAMES = ("nige", "sashi", "makuri")

def _to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")

def _lane_to_style(lane: float | int | None) -> str | None:
    try:
        lane_int = int(lane)
    except Exception:
        return None
    if lane_int == 1:
        return "nige"
    if lane_int in (2, 3):
        return "sashi"
    if lane_int in (4, 5, 6):
        return "makuri"
    return None

def _recent_form_feature_frame(history_df: pd.DataFrame, windows: tuple[int, ...] = (3, 6)) -> pd.DataFrame:
    required = {"racer_id", "date"}
    if not required.issubset(history_df.columns):
        return pd.DataFrame(index=history_df.index)

    work = history_df.copy()
    work["_orig_idx"] = work.index
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["racer_id"] = pd.to_numeric(work["racer_id"], errors="coerce")
    work["finish_position_num"] = pd.to_numeric(work.get("finish_position"), errors="coerce")
    st_src = work.get("avg_st")
    if st_src is None:
        st_src = work.get("start_display_st")
    if st_src is None:
        st_src = work.get("st")
    work["st_num"] = pd.to_numeric(st_src, errors="coerce")
    work["lane_num"] = pd.to_numeric(work.get("lane"), errors="coerce")
    work = work.dropna(subset=["racer_id", "date"])
    if work.empty:
        return pd.DataFrame(index=history_df.index)

    sort_cols = ["racer_id", "date"]
    if "race_no" in work.columns:
        work["race_no"] = pd.to_numeric(work["race_no"], errors="coerce")
        sort_cols.append("race_no")
    if "lane" in work.columns:
        sort_cols.append("lane")
    work = work.sort_values(sort_cols + ["_orig_idx"]).reset_index(drop=True)
    group = work.groupby("racer_id", sort=False)

    out = pd.DataFrame(index=work.index)
    for w in windows:
        out[f"recent{w}_avg_finish"] = group["finish_position_num"].transform(
            lambda s, window=w: s.shift(1).rolling(window=window, min_periods=1).mean()
        )
        out[f"recent{w}_win_rate"] = group["finish_position_num"].transform(
            lambda s, window=w: (s.shift(1) == 1).rolling(window=window, min_periods=1).mean()
        )
        out[f"recent{w}_top3_rate"] = group["finish_position_num"].transform(
            lambda s, window=w: (s.shift(1) <= 3).rolling(window=window, min_periods=1).mean()
        )
        out[f"recent{w}_avg_st"] = group["st_num"].transform(
            lambda s, window=w: s.shift(1).rolling(window=window, min_periods=1).mean()
        )

    out["rank_mean_recent3"] = out.get("recent3_avg_finish")
    out["rank_mean_recent6"] = out.get("recent6_avg_finish")
    out["win_rate_recent6"] = out.get("recent6_win_rate")
    out["top3_rate_recent6"] = out.get("recent6_top3_rate")
    out["st_mean_recent6"] = out.get("recent6_avg_st")
    out["st_std_recent6"] = group["st_num"].transform(
        lambda s: s.shift(1).rolling(window=6, min_periods=1).std(ddof=0)
    )
    out["st_under010_rate"] = group["st_num"].transform(
        lambda s: (s.shift(1) <= 0.10).rolling(window=6, min_periods=1).mean()
    )
    out["rank_trend"] = group["finish_position_num"].transform(
        lambda s: s.shift(1).rolling(window=3, min_periods=2).apply(
            lambda arr: float(arr[-1] - arr[0]), raw=True
        )
    )

    course_grp = work.groupby(["racer_id", "lane_num"], sort=False)
    prior_race_count = course_grp.cumcount()
    prior_win_count = course_grp["finish_position_num"].transform(
        lambda s: (s.shift(1) == 1).cumsum()
    )
    out["course_win_rate"] = np.where(
        prior_race_count > 0,
        prior_win_count / prior_race_count,
        np.nan,
    )
    lane1 = work[work["lane_num"] == 1].copy()
    if not lane1.empty:
        lane1["lane1_prior_count"] = lane1.groupby("racer_id").cumcount()
        lane1["lane1_prior_win"] = lane1.groupby("racer_id")["finish_position_num"].transform(
            lambda s: (s.shift(1) == 1).cumsum()
        )
        lane1["course1_win_rate"] = np.where(
            lane1["lane1_prior_count"] > 0,
            lane1["lane1_prior_win"] / lane1["lane1_prior_count"],
            np.nan,
        )
        out["course1_win_rate"] = np.nan
        out.loc[lane1.index, "course1_win_rate"] = lane1["course1_win_rate"].values
        out["course1_win_rate"] = out.groupby(work["racer_id"])["course1_win_rate"].ffill()
    else:
        out["course1_win_rate"] = np.nan

    out["_orig_idx"] = work["_orig_idx"].values
    out = out.set_index("_orig_idx").reindex(history_df.index)
    return out


def _prior_success_rate_series(
    history_df: pd.DataFrame,
    group_cols: tuple[str, ...],
    success_col: str = "finish_position",
    success_value: int = 1,
) -> pd.Series:
    if not set(group_cols).issubset(history_df.columns) or success_col not in history_df.columns:
        return pd.Series(index=history_df.index, dtype=float)

    work = history_df.copy()
    work["_orig_idx"] = work.index
    work["date"] = pd.to_datetime(work.get("date"), errors="coerce")
    work[success_col] = pd.to_numeric(work[success_col], errors="coerce")

    sort_cols = [c for c in group_cols if c in work.columns]
    if "date" in work.columns:
        sort_cols.append("date")
    if "race_no" in work.columns:
        work["race_no"] = pd.to_numeric(work["race_no"], errors="coerce")
        sort_cols.append("race_no")
    if "lane" in work.columns:
        work["lane"] = pd.to_numeric(work["lane"], errors="coerce")
        sort_cols.append("lane")

    work = work.dropna(subset=list(group_cols))
    if work.empty:
        return pd.Series(index=history_df.index, dtype=float)

    work = work.sort_values(sort_cols + ["_orig_idx"]).reset_index(drop=True)
    grp = work.groupby(list(group_cols), sort=False)
    prior_count = grp.cumcount()
    prior_success = grp[success_col].transform(lambda s: (s.shift(1) == success_value).cumsum())
    out = pd.Series(np.nan, index=work.index, dtype=float)
    mask = prior_count > 0
    out.loc[mask] = prior_success.loc[mask] / prior_count.loc[mask]
    out.index = work["_orig_idx"].values
    return out.reindex(history_df.index)

def _prior_success_rate_by_group(
    history_df: pd.DataFrame,
    group_cols: tuple[str, ...],
    success_col: str = "finish_position",
    success_value: int = 1,
) -> pd.Series:
    if not set(group_cols).issubset(history_df.columns) or success_col not in history_df.columns:
        return pd.Series(np.nan, index=history_df.index, dtype=float)

    work = history_df.copy()
    work["_orig_idx"] = work.index
    work["date"] = pd.to_datetime(work.get("date"), errors="coerce")
    work[success_col] = pd.to_numeric(work[success_col], errors="coerce")
    for col in group_cols:
        work[col] = pd.to_numeric(work[col], errors="coerce")

    sort_cols = [c for c in group_cols if c in work.columns]
    if "date" in work.columns:
        sort_cols.append("date")
    if "race_no" in work.columns:
        work["race_no"] = pd.to_numeric(work["race_no"], errors="coerce")
        sort_cols.append("race_no")
    if "lane" in work.columns:
        work["lane"] = pd.to_numeric(work["lane"], errors="coerce")
        sort_cols.append("lane")

    work = work.dropna(subset=list(group_cols))
    if work.empty:
        return pd.Series(np.nan, index=history_df.index, dtype=float)

    work = work.sort_values(sort_cols + ["_orig_idx"]).reset_index(drop=True)
    grp = work.groupby(list(group_cols), sort=False)
    prior_count = grp.cumcount()
    prior_success = grp[success_col].transform(lambda s: (s.shift(1) == success_value).cumsum())
    rate = pd.Series(np.nan, index=work.index, dtype=float)
    mask = prior_count > 0
    rate.loc[mask] = prior_success.loc[mask] / prior_count.loc[mask]
    rate.index = work["_orig_idx"].values
    return rate.reindex(history_df.index)

def add_condition_flags(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "motor_2ren_rate" in out.columns:
        out["low_motor_flag"] = (pd.to_numeric(out["motor_2ren_rate"], errors="coerce") < MOTOR_LOW_THRESH).astype(int)
    else: out["low_motor_flag"] = 0
    if "boat_2ren_rate" in out.columns:
        out["low_boat_flag"] = (pd.to_numeric(out["boat_2ren_rate"], errors="coerce") < BOAT_LOW_THRESH).astype(int)
    else: out["low_boat_flag"] = 0
    if "jcd" in out.columns and "motor_2ren_rate" in out.columns:
        out["jcd_low_motor_flag"] = (
            (pd.to_numeric(out["jcd"], errors="coerce") < JCD_LOW_THRESH)
            & (pd.to_numeric(out["motor_2ren_rate"], errors="coerce") < MOTOR_LOW_THRESH)
        ).astype(int)
    else: out["jcd_low_motor_flag"] = 0
    if "jcd" in out.columns and "boat_2ren_rate" in out.columns:
        out["jcd_low_boat_flag"] = (
            (pd.to_numeric(out["jcd"], errors="coerce") < JCD_LOW_THRESH)
            & (pd.to_numeric(out["boat_2ren_rate"], errors="coerce") < BOAT_LOW_THRESH)
        ).astype(int)
    else: out["jcd_low_boat_flag"] = 0
    return out

class FeatureBuilder:
    def __init__(self, registry_path=None):
        # 実行スクリプトからの相対位置で解決
        if registry_path is None:
            script_dir = Path(__file__).parent.absolute()
            registry_path = script_dir.parent.parent / "config" / "feature_registry.json"
        
        with open(registry_path, "r", encoding="utf-8") as f:
            self.registry = json.load(f)
        self.meta_cols = ["race_id", "lane", "racer_id", "date", "jcd"]
        self.forbidden_cols = set(self.registry.get("blocked", [])) | set(self.registry.get("target_only", []))
        self.summary = {"datasets": {}}

    def build(self, input_path, output_path, dataset_name):
        df = pd.read_csv(input_path, low_memory=False)
        numeric_candidates = [
            "avg_st", "national_win_rate", "national_2ren_rate", "local_2ren_rate",
            "motor_2ren_rate", "boat_2ren_rate", "win_rate_venue", "exhibition_time", "wind_speed",
            "start_display_st", "prev_race_st"
        ]
        for col in numeric_candidates:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        available_base = [c for c in self.registry["base_features"] if c in df.columns and c not in self.forbidden_cols]
        features = df[available_base].copy()
        
        # Base Path を解決
        script_dir = Path(__file__).parent.absolute()
        base_dir = script_dir.parent.parent

        history_ref = df
        if dataset_name == "today":
            hist_path = base_dir / "data/processed/historical_races.csv"
            if hist_path.exists():
                history_ref = pd.read_csv(hist_path, low_memory=False)
        
        recent_form = _recent_form_feature_frame(history_ref, windows=(3, 6))
        recent_cols = list(recent_form.columns)
        if dataset_name == "today" and not recent_form.empty and "racer_id" in df.columns:
            ref = history_ref.copy()
            ref["racer_id"] = pd.to_numeric(ref["racer_id"], errors="coerce")
            latest_recent = (
                recent_form.join(ref[["racer_id"]], how="left")
                .reset_index()
                .groupby("racer_id", as_index=False)
                .last()
            )
            latest_recent["racer_id"] = pd.to_numeric(latest_recent["racer_id"], errors="coerce")
            features = features.copy()
            features["racer_id"] = df["racer_id"].values
            features = features.merge(latest_recent[["racer_id"] + recent_cols], on="racer_id", how="left")
        elif dataset_name != "today" and not recent_form.empty:
            features = pd.concat([features, recent_form[recent_cols].reset_index(drop=True)], axis=1)

        # Derived Features
        if "racer_class" in df.columns:
            class_map = {"A1": 4, "A2": 3, "B1": 2, "B2": 1}
            features["racer_rank"] = df["racer_class"].map(class_map).fillna(0)
            if "racer_class" in features.columns: features = features.drop(columns=["racer_class"])

        if "race_id" in df.columns:
            group = df.groupby("race_id")
            if "national_win_rate" in df.columns:
                avg_win = group["national_win_rate"].transform("mean")
                features["win_rate_diff_to_avg"] = df["national_win_rate"] - avg_win
            if "avg_st" in df.columns:
                min_st = group["avg_st"].transform("min")
                features["st_diff_to_min"] = df["avg_st"] - min_st
            
            # EXHIBITION
            if "exhibition_time" in df.columns:
                features["exhibition_time"] = _to_numeric(df["exhibition_time"])
                features["exhibition_time_rank"] = group["exhibition_time"].rank(method="first", ascending=True)
            if "start_timing" not in features.columns:
                if "exhibition_time" in df.columns:
                    features["start_timing"] = _to_numeric(df["exhibition_time"])
                elif "start_display_st" in df.columns:
                    features["start_timing"] = _to_numeric(df["start_display_st"])
                elif "avg_st" in df.columns:
                    features["start_timing"] = _to_numeric(df["avg_st"])

        if dataset_name == "today":
            work = features.copy()
            if "union_key" in df.columns:
                parts = df["union_key"].astype(str).str.split("_", expand=True)
                if parts.shape[1] >= 3:
                    if "race_id" not in work.columns:
                        work["race_id"] = parts[0].astype(str) + "-" + parts[1].astype(str) + "-" + parts[2].astype(str)
                    if "date" not in work.columns:
                        work["date"] = pd.to_datetime(parts[0], format="%Y%m%d", errors="coerce")
                    if "jcd" not in work.columns:
                        work["jcd"] = parts[1].astype(str).str.zfill(2)
                    if "race_no" not in work.columns:
                        work["race_no"] = parts[2].astype(str)
            if "avg_st" not in work.columns:
                work["avg_st"] = np.nan
            if "win_rate_venue" not in work.columns:
                if "local_win_rate" in df.columns:
                    work["win_rate_venue"] = pd.to_numeric(df["local_win_rate"], errors="coerce")
                else:
                    work["win_rate_venue"] = np.nan
            if "win_rate_diff_to_avg" not in work.columns:
                if "national_win_rate" in work.columns and "race_id" in work.columns:
                    work["win_rate_diff_to_avg"] = work.groupby("race_id")["national_win_rate"].transform("mean")
                    work["win_rate_diff_to_avg"] = pd.to_numeric(work["national_win_rate"], errors="coerce") - work["win_rate_diff_to_avg"]
                else:
                    work["win_rate_diff_to_avg"] = np.nan
            if "st_diff_to_min" not in work.columns:
                work["st_diff_to_min"] = np.nan
            if "exhibition_time" not in work.columns:
                work["exhibition_time"] = np.nan
            if "exhibition_time_rank" not in work.columns:
                work["exhibition_time_rank"] = np.nan
            if "start_timing" not in work.columns:
                work["start_timing"] = np.nan
            features = work

        if "lane" in df.columns:
            lane_num = pd.to_numeric(df["lane"], errors="coerce")
            features["lane_num"] = lane_num.fillna(0)
            features["inside_course_flag"] = (lane_num <= 2).astype(int)
            if "lane_win_rate_prior" not in features.columns:
                prior_source = df
                if dataset_name == "today":
                    hist_path = base_dir / "data/processed/historical_races.csv"
                    if hist_path.exists():
                        prior_source = pd.read_csv(hist_path, low_memory=False)
                if "finish_position" in prior_source.columns and "lane" in prior_source.columns:
                    lane_prior_map = (
                        prior_source.assign(
                            lane=pd.to_numeric(prior_source["lane"], errors="coerce"),
                            finish_position=pd.to_numeric(prior_source["finish_position"], errors="coerce"),
                        )
                        .dropna(subset=["lane", "finish_position"])
                        .groupby("lane")["finish_position"]
                        .apply(lambda s: float((s == 1).mean()))
                        .to_dict()
                    )
                    features["lane_win_rate_prior"] = lane_num.map(lane_prior_map).fillna(0.0)
                else:
                    features["lane_win_rate_prior"] = 0.0

        venue_missing = (
            "win_rate_venue" not in features.columns
            or pd.to_numeric(features["win_rate_venue"], errors="coerce").notna().sum() == 0
        )
        if venue_missing:
            if dataset_name != "today" and {"racer_id", "jcd", "finish_position"}.issubset(df.columns):
                venue_prior = _prior_success_rate_by_group(df, ("racer_id", "jcd"))
                features["win_rate_venue"] = venue_prior.fillna(0.0)
            elif dataset_name == "today" and {"racer_id", "jcd"}.issubset(df.columns):
                history_source = history_ref if history_ref is not None else df
                if {"racer_id", "jcd", "finish_position"}.issubset(history_source.columns):
                    hist = history_source.copy()
                    hist["racer_id"] = pd.to_numeric(hist["racer_id"], errors="coerce")
                    hist["jcd"] = pd.to_numeric(hist["jcd"], errors="coerce")
                    hist["finish_position"] = pd.to_numeric(hist["finish_position"], errors="coerce")
                    venue_map = (
                        hist.dropna(subset=["racer_id", "jcd", "finish_position"])
                        .groupby(["racer_id", "jcd"])["finish_position"]
                        .apply(lambda s: float((s == 1).mean()))
                        .to_dict()
                    )
                    today_keys = list(
                        zip(
                            pd.to_numeric(df["racer_id"], errors="coerce"),
                            pd.to_numeric(df["jcd"], errors="coerce"),
                        )
                    )
                    features["win_rate_venue"] = [venue_map.get((r, j), 0.0) for r, j in today_keys]
                else:
                    features["win_rate_venue"] = 0.0
            else:
                features["win_rate_venue"] = 0.0
        
        # Meta cols
        for c in self.meta_cols:
            if c in df.columns: features[c] = df[c]

        features = add_condition_flags(features)
        features = add_race_relative_features(features, race_key="race_id")
        
        output_p = Path(output_path)
        output_p.parent.mkdir(parents=True, exist_ok=True)
        features.to_csv(output_p, index=False)
        print(f"Features built: {output_path}")

if __name__ == "__main__":
    script_dir = Path(__file__).parent.absolute()
    base_dir = script_dir.parent.parent
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--historical-input", default=str(base_dir / "data/processed/historical_races.csv"))
    parser.add_argument("--today-input", default=str(base_dir / "data/processed/today_races.csv"))
    parser.add_argument("--train-output", default=str(base_dir / "data/features/train_features.csv"))
    parser.add_argument("--today-output", default=str(base_dir / "data/features/today_features.csv"))
    args = parser.parse_args()

    builder = FeatureBuilder()
    if os.path.exists(args.historical_input):
        builder.build(args.historical_input, args.train_output, "train")
    if os.path.exists(args.today_input):
        builder.build(args.today_input, args.today_output, "today")
# --- MVP helpers -----------------------------------------------------------

from typing import Any


def build_mvp_feature_rows(snapshot: dict[str, Any], stage: str = "pre_race") -> pd.DataFrame:
    races = snapshot.get("boats") or []
    rows = []
    for boat in races:
        lane = int(boat.get("boat_no") or boat.get("no") or 0)
        row = {
            "boat_no": lane,
            "course_adjustment": {1: 1.2, 2: 0.8, 3: 0.4, 4: 0.15, 5: -0.15, 6: -0.35}.get(lane, 0.0),
            "national_win_rate": pd.to_numeric(boat.get("national_win_rate"), errors="coerce"),
            "local_win_rate": pd.to_numeric(boat.get("local_win_rate"), errors="coerce"),
            "motor_2rate": pd.to_numeric(boat.get("motor_2rate"), errors="coerce"),
            "boat_2rate": pd.to_numeric(boat.get("boat_2rate"), errors="coerce"),
            "avg_st": pd.to_numeric(boat.get("avg_st"), errors="coerce"),
            "exhibition_time": pd.to_numeric(boat.get("exhibition_time"), errors="coerce"),
            "exhibition_st": pd.to_numeric(boat.get("exhibition_st"), errors="coerce"),
            "f_penalty": -float(boat.get("f_count") or 0),
            "l_penalty": -float(boat.get("l_count") or 0),
            "missing_penalty": 0.0 if str(boat.get("data_status") or "").lower() in {"available", "complete"} else -1.0,
            "stage": stage,
        }
        rows.append(row)
    return pd.DataFrame(rows)
