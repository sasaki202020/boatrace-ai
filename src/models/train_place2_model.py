import argparse
import json
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


FEATURE_COLUMNS = [
    "start_timing",
    "course_win_rate",
]


def _load_training_frame(features_path: Path, labels_path: Path, cutoff_date: str | None) -> pd.DataFrame:
    feature_usecols = ["race_id", "lane", "date", "jcd", *FEATURE_COLUMNS]
    feat_df = pd.read_csv(features_path, usecols=lambda c: c in feature_usecols, low_memory=False)
    feat_df = feat_df.dropna(subset=["race_id", "lane"]).copy()
    feat_df["race_id"] = feat_df["race_id"].astype(str).str.strip()
    feat_df["lane"] = pd.to_numeric(feat_df["lane"], errors="coerce")
    feat_df = feat_df.dropna(subset=["lane"]).copy()
    feat_df["lane"] = feat_df["lane"].astype(int)
    feat_df["date"] = (
        feat_df.get("date")
        .astype(str)
        .str.replace("-", "", regex=False)
        .str.replace("/", "", regex=False)
    )
    feat_df["date"] = pd.to_numeric(feat_df["date"], errors="coerce")
    feat_df["jcd"] = pd.to_numeric(feat_df.get("jcd"), errors="coerce")
    feat_df["jcd"] = feat_df["jcd"].fillna(0).astype(int)
    feat_df["race_no"] = (
        feat_df["race_id"].astype(str).str.extract(r"(\d+)$", expand=False)
    )
    feat_df["race_no"] = pd.to_numeric(feat_df["race_no"], errors="coerce")
    feat_df = feat_df.dropna(subset=["date", "jcd", "race_no"]).copy()
    feat_df["race_no"] = feat_df["race_no"].astype(int)
    if cutoff_date:
        cutoff_val = int(cutoff_date)
        feat_df = feat_df[feat_df["date"].astype(int) <= cutoff_val].copy()

    label_usecols = [
        "race_id",
        "date",
        "jcd",
        "race_no",
        "lane",
        "finish_position",
        "motor_2ren_rate",
        "boat_2ren_rate",
    ]
    lab_df = pd.read_csv(labels_path, usecols=lambda c: c in label_usecols, low_memory=False)
    lab_df = lab_df.dropna(subset=["race_id", "lane", "finish_position"]).copy()
    lab_df["race_id"] = lab_df["race_id"].astype(str).str.strip()
    lab_df["lane"] = pd.to_numeric(lab_df["lane"], errors="coerce")
    lab_df["finish_position"] = pd.to_numeric(lab_df["finish_position"], errors="coerce")
    lab_df["date"] = (
        lab_df.get("date")
        .astype(str)
        .str.replace("-", "", regex=False)
        .str.replace("/", "", regex=False)
    )
    lab_df["date"] = pd.to_numeric(lab_df["date"], errors="coerce")
    lab_df["jcd"] = pd.to_numeric(lab_df.get("jcd"), errors="coerce")
    lab_df["jcd"] = lab_df["jcd"].fillna(0).astype(int)
    lab_df["race_no"] = pd.to_numeric(lab_df.get("race_no"), errors="coerce")
    lab_df["motor_2ren_rate"] = pd.to_numeric(lab_df.get("motor_2ren_rate"), errors="coerce")
    lab_df["boat_2ren_rate"] = pd.to_numeric(lab_df.get("boat_2ren_rate"), errors="coerce")
    lab_df = lab_df.dropna(subset=["lane", "finish_position"]).copy()
    lab_df["lane"] = lab_df["lane"].astype(int)
    lab_df["finish_position"] = lab_df["finish_position"].astype(int)
    lab_df = lab_df.dropna(subset=["race_no"]).copy()
    lab_df["race_no"] = lab_df["race_no"].astype(int)
    lab_df = lab_df[lab_df["finish_position"].isin([1, 2])].copy()
    if cutoff_date:
        cutoff_val = int(cutoff_date)
        lab_df = lab_df[lab_df["date"].astype(int) <= cutoff_val].copy()

    top2 = (
        lab_df.sort_values(["date", "jcd", "race_no", "finish_position"])
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
    top2["date"] = top2["date"].astype(int)
    top2["jcd"] = top2["jcd"].astype(int)
    top2["first_lane"] = top2["first_lane"].astype(int)
    top2["second_lane"] = top2["second_lane"].astype(int)

    lane_meta = lab_df[
        ["date", "jcd", "race_no", "lane", "motor_2ren_rate", "boat_2ren_rate"]
    ].drop_duplicates(subset=["date", "jcd", "race_no", "lane"])
    merged = feat_df.merge(
        lane_meta,
        on=["date", "jcd", "race_no", "lane"],
        how="left",
        suffixes=("", "_hist"),
    )
    for col in ["motor_2ren_rate", "boat_2ren_rate"]:
        hist_col = f"{col}_hist"
        if hist_col in merged.columns:
            merged[col] = pd.to_numeric(merged[col], errors="coerce").combine_first(
                pd.to_numeric(merged[hist_col], errors="coerce")
            )
            merged = merged.drop(columns=[hist_col])

    merged = merged.merge(top2[["date", "jcd", "race_no", "first_lane", "second_lane"]], on=["date", "jcd", "race_no"], how="inner")
    merged = merged[merged["lane"] != merged["first_lane"]].copy()
    merged["label"] = (merged["lane"] == merged["second_lane"]).astype(int)
    merged["first_lane"] = merged["first_lane"].astype(int)
    merged["second_lane"] = merged["second_lane"].astype(int)
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


def train_place2_models(train_df: pd.DataFrame) -> dict:
    models = {}
    stats = {}

    global_model = None
    if not train_df.empty and train_df["label"].nunique() >= 2:
        global_model = _make_pipeline()
        global_model.fit(train_df[FEATURE_COLUMNS], train_df["label"])

    for first_lane, group in train_df.groupby("first_lane"):
        if group["label"].nunique() < 2:
            continue
        model = _make_pipeline()
        model.fit(group[FEATURE_COLUMNS], group["label"])
        models[int(first_lane)] = model
        stats[int(first_lane)] = {
            "rows": int(len(group)),
            "positives": int(group["label"].sum()),
            "negatives": int((group["label"] == 0).sum()),
        }

    bundle = {
        "feature_columns": FEATURE_COLUMNS,
        "models": models,
        "global_model": global_model,
        "metadata": {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "feature_columns": FEATURE_COLUMNS,
            "first_lane_models": sorted(models.keys()),
            "stats": stats,
        },
    }
    return bundle


def main():
    parser = argparse.ArgumentParser(description="Train first_lane-conditioned place2 model")
    parser.add_argument("--features", default="data/features/train_features.csv")
    parser.add_argument("--labels", default="data/processed/historical_races.csv")
    parser.add_argument("--output", default="models/place2_model.joblib")
    parser.add_argument("--summary", default="data/model_outputs/place2_model_summary.json")
    parser.add_argument("--cutoff-date", default="20260310", help="Use data on or before YYYYMMDD")
    args = parser.parse_args()

    features_path = Path(args.features)
    labels_path = Path(args.labels)
    output_path = Path(args.output)
    summary_path = Path(args.summary)

    train_df = _load_training_frame(features_path, labels_path, args.cutoff_date)
    bundle = train_place2_models(train_df)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, output_path)

    summary = {
        "output": str(output_path),
        "summary_created_at": bundle["metadata"]["created_at"],
        "cutoff_date": args.cutoff_date,
        "rows": int(len(train_df)),
        "races": int(train_df["race_id"].nunique()),
        "first_lane_models": bundle["metadata"]["first_lane_models"],
        "stats": bundle["metadata"]["stats"],
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
