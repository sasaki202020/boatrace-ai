from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[2]
TRAIN_FEAT = ROOT / "data" / "features" / "train_features.csv"
HIST = ROOT / "data" / "processed" / "historical_races.csv"
OUT = ROOT / "data" / "processed" / "venue_model_split_analysis.json"


def build_model():
    return make_pipeline(
        SimpleImputer(strategy="mean"),
        StandardScaler(),
        SGDClassifier(
            loss="log_loss",
            max_iter=1500,
            tol=1e-3,
            class_weight="balanced",
            random_state=42,
        ),
    )


def safe_auc(y_true, y_prob):
    if len(np.unique(y_true)) < 2:
        return None
    return float(roc_auc_score(y_true, y_prob))


def main() -> None:
    if not TRAIN_FEAT.exists() or not HIST.exists():
        raise FileNotFoundError("train_features.csv or historical_races.csv is missing")

    feat = pd.read_csv(TRAIN_FEAT, low_memory=False)
    hist = pd.read_csv(HIST, low_memory=False)
    if len(feat) != len(hist):
        raise ValueError("row mismatch: train_features and historical_races")

    df = feat.copy()
    df["finish_position"] = pd.to_numeric(hist["finish_position"], errors="coerce")
    df["win_label"] = (df["finish_position"] == 1).astype(int)
    df["date"] = pd.to_datetime(df.get("date", hist.get("date")), errors="coerce")
    df["jcd"] = pd.to_numeric(df.get("jcd", hist.get("jcd")), errors="coerce")
    df = df.dropna(subset=["date", "jcd"]).copy()

    race_counts = (
        df.groupby("jcd")["race_id"].nunique().sort_values(ascending=False).astype(int)
        if "race_id" in df.columns
        else pd.Series(dtype=int)
    )
    venue_counts = {str(int(k)): int(v) for k, v in race_counts.items()}

    # 大村を優先、無ければ最多場を対象
    target_jcd = 24 if 24 in race_counts.index else (int(race_counts.index[0]) if len(race_counts) else None)
    if target_jcd is None:
        raise ValueError("no jcd data found")

    split_date = df["date"].max() - pd.Timedelta(days=30)
    train_mask = df["date"] < split_date
    test_mask = df["date"] >= split_date

    meta_cols = {"race_id", "lane", "date", "jcd", "racer_id", "finish_position", "win_label"}
    feature_cols = [c for c in df.columns if c not in meta_cols and pd.api.types.is_numeric_dtype(df[c])]
    if not feature_cols:
        raise ValueError("no numeric feature columns")

    # 共通モデル（高速化のため大規模時はサンプリング）
    train_df = df.loc[train_mask].copy()
    if len(train_df) > 220_000:
        train_df = train_df.sample(n=220_000, random_state=42)
    common = build_model()
    common.fit(train_df[feature_cols], train_df["win_label"])
    venue_test = df.loc[test_mask & (df["jcd"] == target_jcd)].copy()
    venue_train = df.loc[train_mask & (df["jcd"] == target_jcd)].copy()

    result = {
        "total_rows": int(len(df)),
        "race_counts_by_jcd": venue_counts,
        "target_jcd": int(target_jcd),
        "target_train_rows": int(len(venue_train)),
        "target_test_rows": int(len(venue_test)),
        "target_ready_over_300": bool(len(venue_train) >= 300),
    }

    if len(venue_test) < 30:
        result["status"] = "insufficient_target_test_rows"
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    y_test = venue_test["win_label"].astype(int).values
    p_common = common.predict_proba(venue_test[feature_cols])[:, 1]
    result["common_model"] = {
        "auc": safe_auc(y_test, p_common),
        "logloss": float(log_loss(y_test, p_common, labels=[0, 1])),
    }

    # 場別モデル（条件を満たす時のみ試行）
    if len(venue_train) >= 300:
        venue_train_fit = venue_train.copy()
        if len(venue_train_fit) > 80_000:
            venue_train_fit = venue_train_fit.sample(n=80_000, random_state=42)
        venue_model = build_model()
        venue_model.fit(venue_train_fit[feature_cols], venue_train_fit["win_label"])
        p_venue = venue_model.predict_proba(venue_test[feature_cols])[:, 1]
        result["venue_specific_model"] = {
            "auc": safe_auc(y_test, p_venue),
            "logloss": float(log_loss(y_test, p_venue, labels=[0, 1])),
        }
    else:
        result["venue_specific_model"] = {"status": "not_trained_insufficient_rows"}

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
