from __future__ import annotations

from pathlib import Path
from shutil import copyfile

import joblib
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.strategy.generate_trifecta_candidates import TrifectaGenerator


ROOT = Path(__file__).resolve().parents[2]
TRAIN_FEATURES = ROOT / "data" / "features" / "train_features.csv"
TRAIN_LABELS = ROOT / "data" / "processed" / "historical_races.csv"
EVAL_FEATURES = ROOT / "data" / "tmp" / "20260311_eval" / "today_features.csv"
EVAL_WIN = ROOT / "data" / "tmp" / "20260311_eval" / "today_win_proba_heuristic.csv"
EVAL_RESULT = ROOT / "data" / "csv" / "20260311_test" / "result" / "result.csv"
FEATURES_BACKUP = ROOT / "data" / "tmp" / "20260311_eval" / "today_features.backup.csv"
ROOT_TODAY_FEATURES = ROOT / "data" / "features" / "today_features.csv"


FEATURE_SETS = {
    "baseline": ["start_timing"],
    "current_best": ["start_timing", "course_win_rate"],
}


def _lane_prior_map(labels: pd.DataFrame) -> dict[int, float]:
    if "lane" not in labels.columns or "finish_position" not in labels.columns:
        return {}
    tmp = labels[["lane", "finish_position"]].copy()
    tmp["lane"] = pd.to_numeric(tmp["lane"], errors="coerce")
    tmp["finish_position"] = pd.to_numeric(tmp["finish_position"], errors="coerce")
    tmp = tmp.dropna(subset=["lane", "finish_position"])
    if tmp.empty:
        return {}
    tmp["lane"] = tmp["lane"].astype(int)
    tmp["finish_position"] = tmp["finish_position"].astype(int)
    return tmp.groupby("lane")["finish_position"].apply(lambda s: float((s == 1).mean())).to_dict()


def _load_training_frame(feature_columns: list[str]) -> pd.DataFrame:
    feat_usecols = ["race_id", "lane", "date", "jcd", *feature_columns]
    feat = pd.read_csv(TRAIN_FEATURES, usecols=lambda c: c in feat_usecols, low_memory=False)
    feat = feat.dropna(subset=["race_id", "lane"]).copy()
    feat["race_id"] = feat["race_id"].astype(str).str.strip()
    feat["lane"] = pd.to_numeric(feat["lane"], errors="coerce")
    feat["lane"] = feat["lane"].astype("Int64")
    feat["date"] = (
        feat["date"]
        .astype(str)
        .str.replace("-", "", regex=False)
        .str.replace("/", "", regex=False)
    )
    feat["date"] = pd.to_numeric(feat["date"], errors="coerce")
    feat["jcd"] = pd.to_numeric(feat["jcd"], errors="coerce")
    feat["jcd"] = feat["jcd"].fillna(0).astype(int)
    feat["race_no"] = pd.to_numeric(
        feat["race_id"].astype(str).str.extract(r"(\d+)$", expand=False),
        errors="coerce",
    )
    feat = feat.dropna(subset=["lane", "race_no", "date"]).copy()
    feat["lane"] = feat["lane"].astype(int)
    feat["race_no"] = feat["race_no"].astype(int)
    feat["date"] = feat["date"].astype(int)

    labels = pd.read_csv(TRAIN_LABELS, usecols=["date", "jcd", "race_no", "lane", "finish_position"], low_memory=False)
    labels["date"] = (
        labels["date"]
        .astype(str)
        .str.replace("-", "", regex=False)
        .str.replace("/", "", regex=False)
    )
    labels["date"] = pd.to_numeric(labels["date"], errors="coerce")
    labels["jcd"] = pd.to_numeric(labels["jcd"], errors="coerce").fillna(0).astype(int)
    labels["race_no"] = pd.to_numeric(labels["race_no"], errors="coerce")
    labels["lane"] = pd.to_numeric(labels["lane"], errors="coerce")
    labels = labels.dropna(subset=["date", "race_no", "lane", "finish_position"]).copy()
    labels["date"] = labels["date"].astype(int)
    labels["race_no"] = labels["race_no"].astype(int)
    labels["lane"] = labels["lane"].astype(int)
    labels["finish_position"] = labels["finish_position"].astype(int)
    lane_prior = _lane_prior_map(labels)

    top2 = (
        labels[labels["finish_position"].isin([1, 2])]
        .sort_values(["date", "jcd", "race_no", "finish_position"])
        .pivot_table(
            index=["date", "jcd", "race_no"],
            columns="finish_position",
            values="lane",
            aggfunc="first",
        )
        .rename(columns={1: "first_lane", 2: "second_lane"})
        .reset_index()
    )
    top2 = top2.dropna(subset=["first_lane", "second_lane"]).copy()
    top2["first_lane"] = top2["first_lane"].astype(int)
    top2["second_lane"] = top2["second_lane"].astype(int)

    merged = feat.merge(top2, on=["date", "jcd", "race_no"], how="inner")
    merged = merged[merged["lane"] != merged["first_lane"]].copy()
    merged["label"] = (merged["lane"] == merged["second_lane"]).astype(int)
    if "lane_win_rate_prior" in feature_columns and "lane_win_rate_prior" not in merged.columns:
        merged["lane_win_rate_prior"] = merged["lane"].map(lane_prior).fillna(0.0)
    return merged


def _make_pipeline() -> Pipeline:
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "clf",
                LogisticRegression(
                    max_iter=2000,
                    class_weight="balanced",
                    solver="lbfgs",
                    random_state=42,
                ),
            ),
        ]
    )


def _train_bundle(train_df: pd.DataFrame, feature_columns: list[str]) -> dict:
    work = train_df.copy()
    for col in feature_columns:
        if col not in work.columns:
            work[col] = np.nan

    global_model = None
    if work["label"].nunique() >= 2:
        global_model = _make_pipeline()
        global_model.fit(work[feature_columns], work["label"])

    models = {}
    stats = {}
    for first_lane, group in work.groupby("first_lane"):
        if group["label"].nunique() < 2:
            continue
        model = _make_pipeline()
        model.fit(group[feature_columns], group["label"])
        models[int(first_lane)] = model
        stats[int(first_lane)] = {
            "rows": int(len(group)),
            "positives": int(group["label"].sum()),
            "negatives": int((group["label"] == 0).sum()),
        }

    return {
        "feature_columns": feature_columns,
        "models": models,
        "global_model": global_model,
        "metadata": {"feature_columns": feature_columns, "stats": stats},
    }


def _normalized_key(race_id: str) -> str:
    race_no = int(str(race_id).split("-")[-1])
    date_part = str(race_id).split("-")[0]
    return f"d{date_part}-c01-r{race_no:02d}"


def _evaluate_bundle(bundle: dict, win_path: Path, actual_map: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    gen = TrifectaGenerator()
    gen.use_conditional_place_model_p2 = True
    gen.place2_bundle = bundle

    out = gen.generate(str(win_path))
    actual = pd.read_csv(EVAL_RESULT, low_memory=False)
    actual = actual.drop_duplicates(subset=["normalized_race_key"], keep="first").copy()
    actual["normalized_race_key"] = actual["normalized_race_key"].astype(str)
    actual_map = actual.set_index("normalized_race_key")["combo"].to_dict()

    rows = []
    for race_id, grp in out.groupby("race_id"):
        grp = grp.sort_values("approx_prob", ascending=False).reset_index(drop=True)
        key = _normalized_key(str(race_id))
        if key not in actual_map:
            continue
        combo = actual_map.get(key)
        hit = grp[grp["trifecta"] == combo]
        rows.append(
            {
                "normalized_race_key": key,
                "actual_combo": combo,
                "rank": int(hit.index[0] + 1) if not hit.empty else None,
                "in_candidate": bool(not hit.empty),
            }
        )
    summary = pd.DataFrame(rows).drop_duplicates(subset=["normalized_race_key"], keep="first").sort_values("normalized_race_key")
    return out, summary


def _inject_eval_lane_prior(eval_path: Path, out_path: Path) -> None:
    feat = pd.read_csv(eval_path, low_memory=False)
    if "start_timing" not in feat.columns:
        if "exhibition_time" in feat.columns:
            feat["start_timing"] = pd.to_numeric(feat["exhibition_time"], errors="coerce")
        elif "start_display_st" in feat.columns:
            feat["start_timing"] = pd.to_numeric(feat["start_display_st"], errors="coerce")
        elif "avg_st" in feat.columns:
            feat["start_timing"] = pd.to_numeric(feat["avg_st"], errors="coerce")
    if "lane_win_rate_prior" in feat.columns:
        feat.to_csv(out_path, index=False)
        return
    labels = pd.read_csv(TRAIN_LABELS, usecols=["lane", "finish_position"], low_memory=False)
    lane_prior = _lane_prior_map(labels)
    if "lane" in feat.columns:
        feat["lane_win_rate_prior"] = pd.to_numeric(feat["lane"], errors="coerce").map(lane_prior).fillna(0.0)
    feat.to_csv(out_path, index=False)


def main() -> None:
    if not EVAL_FEATURES.exists():
        raise FileNotFoundError(f"missing eval features: {EVAL_FEATURES}")
    if not EVAL_WIN.exists():
        raise FileNotFoundError(f"missing eval win file: {EVAL_WIN}")

    train_cache: dict[str, pd.DataFrame] = {}
    summaries = []
    if ROOT_TODAY_FEATURES.exists():
        copyfile(ROOT_TODAY_FEATURES, FEATURES_BACKUP)
    _inject_eval_lane_prior(EVAL_FEATURES, ROOT_TODAY_FEATURES)
    actual = pd.read_csv(EVAL_RESULT, low_memory=False)
    actual = actual.drop_duplicates(subset=["normalized_race_key"], keep="first").copy()
    actual["normalized_race_key"] = actual["normalized_race_key"].astype(str)
    actual_map = actual.set_index("normalized_race_key")["combo"].to_dict()

    try:
        for name, feature_columns in FEATURE_SETS.items():
            train_df = train_cache.get(name)
            if train_df is None:
                train_df = _load_training_frame(feature_columns)
                train_cache[name] = train_df
            bundle = _train_bundle(train_df, feature_columns)
            _, summary = _evaluate_bundle(bundle, EVAL_WIN, actual_map)
            summary["model"] = name
            summaries.append(summary)
    finally:
        if FEATURES_BACKUP.exists():
            copyfile(FEATURES_BACKUP, ROOT_TODAY_FEATURES)

    compare = pd.concat(summaries, ignore_index=True)
    compare = compare.drop_duplicates(subset=["normalized_race_key", "model"], keep="first")
    print("RANK_TABLE")
    print(compare.pivot(index="normalized_race_key", columns="model", values="rank").to_string())
    print("\nCOVERAGE")
    for model_name, grp in compare.groupby("model"):
        print(f"\nMODEL={model_name}")
        for thr in [10, 20, 40, 60]:
            hit = int((grp["rank"].fillna(10**9) <= thr).sum())
            print(f"top{thr}: {hit}/{len(grp)}")
        for key in ["d20260311-c01-r03", "d20260311-c01-r05", "d20260311-c01-r06"]:
            row = grp[grp["normalized_race_key"] == key]
            if row.empty:
                continue
            print(
                f"{key}: actual={row.iloc[0]['actual_combo']} rank={row.iloc[0]['rank']} candidate={row.iloc[0]['in_candidate']}"
            )
    print("\nDELTA")
    if {"baseline", "current_best"}.issubset(set(compare["model"].unique())):
        pivot = compare.pivot(index="normalized_race_key", columns="model", values="rank")
        pivot["delta"] = pivot["baseline"] - pivot["current_best"]
        improved = int((pivot["delta"] > 0).sum())
        worsened = int((pivot["delta"] < 0).sum())
        unchanged = int((pivot["delta"] == 0).sum())
        rescued = compare.pivot(index="normalized_race_key", columns="model", values="in_candidate")
        rescued["rescued"] = (~rescued["baseline"].fillna(False)) & (rescued["current_best"].fillna(False))
        broken = (~rescued["baseline"].fillna(False)) & (~rescued["current_best"].fillna(False))
        print("BASELINE_VS_CURRENT_BEST")
        print(f"improved: {improved}")
        print(f"worsened: {worsened}")
        print(f"unchanged: {unchanged}")
        print(f"rescued: {int(rescued['rescued'].sum())}")
        print(f"still_missing: {int(broken.sum())}")



if __name__ == "__main__":
    main()
