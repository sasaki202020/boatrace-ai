from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier, early_stopping
from sklearn.metrics import brier_score_loss, log_loss

from src.features.build_features import FeatureBuilder
from src.features.build_relative_features import RELATIVE_FEATURE_COLUMNS


logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FEATURE_PATH = ROOT / "data" / "features" / "train_features.csv"
DEFAULT_HISTORICAL_PATH = ROOT / "data" / "processed" / "historical_races.csv"
DEFAULT_MODEL_DIR = ROOT / "models" / "win_lgbm"
DEFAULT_REPORT_DIR = ROOT / "reports" / "model_eval"
DEFAULT_TODAY_FEATURE_PATH = ROOT / "data" / "features" / "today_features.csv"
DEFAULT_PREDICTION_PATH = ROOT / "data" / "model_outputs" / "win_lgbm_predictions.csv"
FEATURE_REGISTRY_PATH = ROOT / "config" / "feature_registry.json"

with FEATURE_REGISTRY_PATH.open("r", encoding="utf-8") as fp:
    FEATURE_REGISTRY = json.load(fp)

META_COLS = {
    "race_id",
    "lane",
    "date",
    "finish_position",
    "win_label",
    "target_win",
}
IDENTIFIER_COLS = {
    "racer_id",
    "jcd",
    "race_no",
    "union_key",
    "boat_no",
}
REQUESTED_RELATIVE_SET = set(RELATIVE_FEATURE_COLUMNS)
EXCLUDED_RACE_RELATIVE_COLS = {
    "win_rate_diff_to_avg",
    "st_diff_to_min",
    "exhibition_time_rank",
}


@dataclass(frozen=True)
class SplitPeriod:
    train_start: str
    train_end: str
    valid_start: str
    valid_end: str
    test_start: str
    test_end: str
    valid_days: int
    test_days: int


@dataclass(frozen=True)
class ModelMetrics:
    log_loss: float
    brier_score: float
    calibration_error: float
    top1_accuracy: float
    top1_win_rate: float
    n_rows: int
    n_races: int
    avg_pred_prob: float
    avg_actual_rate: float
    ece_bins: int


def _ensure_train_features(feature_path: Path, historical_path: Path, *, force_rebuild: bool = False) -> Path:
    feature_path = Path(feature_path)
    historical_path = Path(historical_path)
    if force_rebuild or not feature_path.exists():
        feature_path.parent.mkdir(parents=True, exist_ok=True)
        FeatureBuilder().build(str(historical_path), str(feature_path), "train")
        return feature_path

    try:
        preview = pd.read_csv(feature_path, nrows=5, low_memory=False)
    except Exception:
        feature_path.parent.mkdir(parents=True, exist_ok=True)
        FeatureBuilder().build(str(historical_path), str(feature_path), "train")
        return feature_path

    missing_relative = [c for c in RELATIVE_FEATURE_COLUMNS if c not in preview.columns]
    if missing_relative:
        logger.info("Rebuilding train features to include relative columns: %s", missing_relative)
        feature_path.parent.mkdir(parents=True, exist_ok=True)
        FeatureBuilder().build(str(historical_path), str(feature_path), "train")
    return feature_path


def _normalize_date_series(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series.astype(str).str.replace("-", "", regex=False), format="%Y%m%d", errors="coerce")


def _prepare_supervised_frame(
    feature_path: Path,
    historical_path: Path,
    *,
    force_rebuild_features: bool = False,
) -> pd.DataFrame:
    feature_path = _ensure_train_features(feature_path, historical_path, force_rebuild=force_rebuild_features)
    feat = pd.read_csv(feature_path, low_memory=False)
    hist = pd.read_csv(historical_path, low_memory=False)

    if "race_id" not in feat.columns or "lane" not in feat.columns:
        raise ValueError("feature frame must contain race_id and lane")
    required_hist = {"race_id", "lane", "finish_position", "date"}
    missing_hist = required_hist - set(hist.columns)
    if missing_hist:
        raise ValueError(f"historical file missing required columns: {sorted(missing_hist)}")

    feat = feat.copy()
    feat["race_id"] = feat["race_id"].astype(str).str.strip()
    feat["lane"] = pd.to_numeric(feat["lane"], errors="coerce")
    feat["date"] = _normalize_date_series(feat["date"])
    feat["jcd"] = pd.to_numeric(feat["jcd"], errors="coerce") if "jcd" in feat.columns else np.nan
    feat = feat.dropna(subset=["race_id", "lane", "date"]).copy()
    feat["lane"] = feat["lane"].astype(int)

    hist = hist.copy()
    hist["race_id"] = hist["race_id"].astype(str).str.strip()
    hist["lane"] = pd.to_numeric(hist["lane"], errors="coerce")
    hist["finish_position"] = pd.to_numeric(hist["finish_position"], errors="coerce")
    hist["date"] = _normalize_date_series(hist["date"])
    hist = hist.dropna(subset=["race_id", "lane", "finish_position", "date"]).copy()
    hist["lane"] = hist["lane"].astype(int)
    hist["finish_position"] = hist["finish_position"].astype(int)

    race_counts = hist.groupby("race_id")["lane"].transform("count")
    hist = hist[race_counts == 6].copy()
    win_counts = hist.groupby("race_id")["finish_position"].transform(lambda s: int((s == 1).sum()))
    hist = hist[win_counts == 1].copy()

    merged = feat.merge(hist[["race_id", "lane", "finish_position"]], on=["race_id", "lane"], how="inner")
    merged = merged.dropna(subset=["finish_position"]).copy()
    merged["target_win"] = (pd.to_numeric(merged["finish_position"], errors="coerce") == 1).astype(int)

    race_counts = merged.groupby("race_id")["lane"].transform("count")
    merged = merged[race_counts == 6].copy()
    winners = merged.groupby("race_id")["target_win"].transform("sum")
    merged = merged[winners == 1].copy()

    merged = merged.sort_values(["date", "race_id", "lane"], kind="mergesort").reset_index(drop=True)
    return merged


def select_feature_columns(frame: pd.DataFrame, feature_set: str) -> list[str]:
    feature_set = feature_set.lower().strip()
    if feature_set not in {"baseline", "relative"}:
        raise ValueError("feature_set must be 'baseline' or 'relative'")

    candidate_cols = []
    for col in frame.columns:
        if col in META_COLS:
            continue
        if col in IDENTIFIER_COLS:
            continue
        if col == "target_win":
            continue
        if col in EXCLUDED_RACE_RELATIVE_COLS:
            continue
        if not pd.api.types.is_numeric_dtype(frame[col]):
            continue
        if feature_set == "baseline" and col in REQUESTED_RELATIVE_SET:
            continue
        candidate_cols.append(col)
    return candidate_cols


def _date_strings(dates: pd.Series) -> list[str]:
    values = pd.to_datetime(dates, errors="coerce").dropna().dt.strftime("%Y-%m-%d").tolist()
    return sorted(dict.fromkeys(values))


def split_train_valid_test(
    frame: pd.DataFrame,
    *,
    valid_days: int = 30,
    test_days: int = 30,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, SplitPeriod]:
    if "date" not in frame.columns:
        raise ValueError("frame must contain date")
    if valid_days < 1 or test_days < 1:
        raise ValueError("valid_days and test_days must be at least 1")

    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    unique_dates = [pd.Timestamp(d) for d in sorted(work["date"].dropna().dt.normalize().unique())]
    if len(unique_dates) < 3:
        raise ValueError("at least three unique dates are required for train/valid/test split")

    test_days = min(test_days, len(unique_dates) - 2)
    valid_days = min(valid_days, len(unique_dates) - test_days - 1)
    if valid_days < 1:
        valid_days = 1
    if test_days < 1:
        test_days = 1

    test_start = pd.Timestamp(unique_dates[-test_days])
    valid_start = pd.Timestamp(unique_dates[-(test_days + valid_days)])
    train_mask = work["date"] < valid_start
    valid_mask = (work["date"] >= valid_start) & (work["date"] < test_start)
    test_mask = work["date"] >= test_start

    train_df = work.loc[train_mask].copy()
    valid_df = work.loc[valid_mask].copy()
    test_df = work.loc[test_mask].copy()
    if train_df.empty or valid_df.empty or test_df.empty:
        raise ValueError("time split produced an empty train, valid, or test set")

    split_period = SplitPeriod(
        train_start=train_df["date"].min().strftime("%Y-%m-%d"),
        train_end=train_df["date"].max().strftime("%Y-%m-%d"),
        valid_start=valid_df["date"].min().strftime("%Y-%m-%d"),
        valid_end=valid_df["date"].max().strftime("%Y-%m-%d"),
        test_start=test_df["date"].min().strftime("%Y-%m-%d"),
        test_end=test_df["date"].max().strftime("%Y-%m-%d"),
        valid_days=int(valid_days),
        test_days=int(test_days),
    )
    return train_df, valid_df, test_df, split_period


def _make_model(random_state: int = 42) -> LGBMClassifier:
    return LGBMClassifier(
        objective="binary",
        n_estimators=600,
        learning_rate=0.05,
        num_leaves=63,
        min_child_samples=40,
        subsample=0.85,
        colsample_bytree=0.85,
        reg_lambda=1.0,
        class_weight="balanced",
        random_state=random_state,
        n_jobs=-1,
        verbosity=-1,
    )


def _expected_calibration_error(y_true: np.ndarray, y_prob: np.ndarray, *, n_bins: int = 10) -> tuple[float, pd.DataFrame]:
    work = pd.DataFrame({"y_true": pd.to_numeric(pd.Series(y_true), errors="coerce"), "y_prob": pd.to_numeric(pd.Series(y_prob), errors="coerce")})
    work = work.dropna(subset=["y_true", "y_prob"]).copy()
    if work.empty:
        bins_df = pd.DataFrame(columns=["bin", "count", "avg_pred", "avg_actual", "gap"])
        return 0.0, bins_df

    bins = np.linspace(0.0, 1.0, n_bins + 1)
    work["bin"] = pd.cut(work["y_prob"], bins=bins, include_lowest=True, duplicates="drop")
    grouped = work.groupby("bin", dropna=False, observed=False)
    rows = []
    total = len(work)
    ece = 0.0
    for bin_label, grp in grouped:
        if grp.empty:
            continue
        avg_pred = float(grp["y_prob"].mean())
        avg_actual = float(grp["y_true"].mean())
        count = int(len(grp))
        gap = abs(avg_pred - avg_actual)
        ece += (count / total) * gap
        rows.append(
            {
                "bin": str(bin_label),
                "count": count,
                "avg_pred": avg_pred,
                "avg_actual": avg_actual,
                "gap": gap,
            }
        )
    return float(ece), pd.DataFrame(rows)


def _row_metrics(y_true: pd.Series, y_prob: pd.Series) -> dict[str, float]:
    y_true_arr = pd.to_numeric(y_true, errors="coerce").astype(float)
    y_prob_arr = pd.to_numeric(y_prob, errors="coerce").astype(float)
    valid_mask = y_true_arr.notna() & y_prob_arr.notna()
    y_true_arr = y_true_arr[valid_mask]
    y_prob_arr = y_prob_arr[valid_mask]
    if y_true_arr.empty:
        return {
            "log_loss": float("nan"),
            "brier_score": float("nan"),
            "calibration_error": float("nan"),
            "avg_pred_prob": float("nan"),
            "avg_actual_rate": float("nan"),
            "ece_bins": 10,
        }
    ll = float(log_loss(y_true_arr, y_prob_arr, labels=[0, 1]))
    bs = float(brier_score_loss(y_true_arr, y_prob_arr))
    ece, _ = _expected_calibration_error(y_true_arr.to_numpy(), y_prob_arr.to_numpy(), n_bins=10)
    return {
        "log_loss": ll,
        "brier_score": bs,
        "calibration_error": ece,
        "avg_pred_prob": float(np.mean(y_prob_arr)),
        "avg_actual_rate": float(np.mean(y_true_arr)),
        "ece_bins": 10,
    }


def _race_level_metrics(frame: pd.DataFrame, prob_col: str = "p_win_raw") -> dict[str, float]:
    work = frame.copy()
    work[prob_col] = pd.to_numeric(work[prob_col], errors="coerce").fillna(0.0)
    if "target_win" not in work.columns:
        raise ValueError("frame must contain target_win for race-level metrics")
    top_idx = work.groupby("race_id")[prob_col].idxmax()
    top = work.loc[top_idx].copy()
    top1_accuracy = float((top["target_win"] == 1).mean()) if not top.empty else float("nan")
    return {
        "top1_accuracy": top1_accuracy,
        "top1_win_rate": top1_accuracy,
        "n_races": int(top["race_id"].nunique()) if not top.empty else 0,
    }


def _prediction_frame(
    frame: pd.DataFrame,
    model: LGBMClassifier,
    feature_columns: list[str],
) -> pd.DataFrame:
    missing = [c for c in feature_columns if c not in frame.columns]
    if missing:
        raise ValueError(f"missing required feature columns: {missing}")
    out = frame[["race_id", "lane", "date", "finish_position", "target_win"]].copy()
    prob = model.predict_proba(frame[feature_columns])[:, 1]
    out["win_proba_raw"] = prob
    out["win_proba_norm"] = out.groupby("race_id")["win_proba_raw"].transform(
        lambda s: float(s.sum()) and s / s.sum()
    )
    out["win_proba_norm"] = out["win_proba_norm"].fillna(0.0)
    out["p_win_raw"] = out["win_proba_raw"]
    out["p_win_norm"] = out["win_proba_norm"]
    out["pred_rank_in_race"] = out.groupby("race_id")["win_proba_norm"].rank(method="first", ascending=False)
    return out


def _save_artifact(
    *,
    model: LGBMClassifier,
    feature_columns: list[str],
    feature_set: str,
    split_period: SplitPeriod,
    metrics: dict,
    model_path: Path,
) -> Path:
    model_path = Path(model_path)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "model": model,
        "feature_columns": feature_columns,
        "feature_set": feature_set,
        "split_period": asdict(split_period),
        "metrics": metrics,
    }
    joblib.dump(bundle, model_path)
    return model_path


def train_feature_set(
    *,
    feature_set: str,
    feature_path: Path = DEFAULT_FEATURE_PATH,
    historical_path: Path = DEFAULT_HISTORICAL_PATH,
    model_path: Path | None = None,
    valid_days: int = 30,
    test_days: int = 30,
    force_rebuild_features: bool = False,
    random_state: int = 42,
) -> dict:
    feature_path = Path(feature_path)
    historical_path = Path(historical_path)
    if model_path is None:
        model_path = DEFAULT_MODEL_DIR / f"{feature_set}.joblib"

    frame = _prepare_supervised_frame(
        feature_path,
        historical_path,
        force_rebuild_features=force_rebuild_features,
    )
    feature_columns = select_feature_columns(frame, feature_set)
    if not feature_columns:
        raise ValueError(f"no usable feature columns found for feature_set={feature_set}")

    train_df, valid_df, test_df, split_period = split_train_valid_test(
        frame,
        valid_days=valid_days,
        test_days=test_days,
    )

    model = _make_model(random_state=random_state)
    X_train = train_df[feature_columns]
    y_train = train_df["target_win"]
    X_valid = valid_df[feature_columns]
    y_valid = valid_df["target_win"]
    try:
        model.fit(
            X_train,
            y_train,
            eval_set=[(X_valid, y_valid)],
            callbacks=[early_stopping(50, verbose=False)],
        )
    except Exception as exc:
        logger.warning("LightGBM early stopping failed, retrying without callbacks: %s", exc)
        model.fit(X_train, y_train)

    valid_pred = _prediction_frame(valid_df, model, feature_columns)
    test_pred = _prediction_frame(test_df, model, feature_columns)
    valid_row_metrics = _row_metrics(valid_df["target_win"], valid_pred["p_win_raw"])
    test_row_metrics = _row_metrics(test_df["target_win"], test_pred["p_win_raw"])
    valid_race_metrics = _race_level_metrics(valid_pred)
    test_race_metrics = _race_level_metrics(test_pred)
    _, valid_bins = _expected_calibration_error(
        valid_df["target_win"].to_numpy(),
        valid_pred["p_win_raw"].to_numpy(),
        n_bins=10,
    )
    _, test_bins = _expected_calibration_error(
        test_df["target_win"].to_numpy(),
        test_pred["p_win_raw"].to_numpy(),
        n_bins=10,
    )

    model_metrics = {
        "feature_set": feature_set,
        "split_period": asdict(split_period),
        "valid": {
            **valid_row_metrics,
            **valid_race_metrics,
            "n_rows": int(len(valid_df)),
            "calibration_bins": valid_bins.to_dict(orient="records"),
        },
        "test": {
            **test_row_metrics,
            **test_race_metrics,
            "n_rows": int(len(test_df)),
            "calibration_bins": test_bins.to_dict(orient="records"),
        },
    }

    artifact_path = _save_artifact(
        model=model,
        feature_columns=feature_columns,
        feature_set=feature_set,
        split_period=split_period,
        metrics=model_metrics,
        model_path=Path(model_path),
    )
    return {
        "artifact_path": str(artifact_path),
        "feature_set": feature_set,
        "feature_columns": feature_columns,
        "split_period": asdict(split_period),
        "metrics": model_metrics,
        "model": model,
    }


def predict_feature_set(
    *,
    artifact_path: Path,
    feature_path: Path = DEFAULT_TODAY_FEATURE_PATH,
) -> pd.DataFrame:
    artifact = joblib.load(artifact_path)
    model = artifact["model"]
    feature_columns = list(artifact["feature_columns"])
    frame = pd.read_csv(feature_path, low_memory=False)
    frame["race_id"] = frame["race_id"].astype(str).str.strip()
    if "lane" in frame.columns:
        frame["lane"] = pd.to_numeric(frame["lane"], errors="coerce")
    missing = [c for c in feature_columns if c not in frame.columns]
    if missing:
        raise ValueError(f"missing required feature columns for prediction: {missing}")
    pred = frame[["race_id", "lane", "date"]].copy()
    pred["win_proba_raw"] = model.predict_proba(frame[feature_columns])[:, 1]
    pred["win_proba_norm"] = pred.groupby("race_id")["win_proba_raw"].transform(
        lambda s: s / s.sum() if s.sum() > 0 else 0.0
    )
    pred["p_win_raw"] = pred["win_proba_raw"]
    pred["p_win_norm"] = pred["win_proba_norm"]
    pred["pred_rank_in_race"] = pred.groupby("race_id")["win_proba_norm"].rank(method="first", ascending=False)
    return pred


def compare_feature_sets(
    *,
    feature_path: Path = DEFAULT_FEATURE_PATH,
    historical_path: Path = DEFAULT_HISTORICAL_PATH,
    report_dir: Path = DEFAULT_REPORT_DIR,
    model_dir: Path = DEFAULT_MODEL_DIR,
    valid_days: int = 30,
    test_days: int = 30,
    force_rebuild_features: bool = False,
    random_state: int = 42,
) -> dict:
    report_dir = Path(report_dir)
    model_dir = Path(model_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    test_calib_bins: list[pd.DataFrame] = []
    comparison_rows: list[dict] = []

    for feature_set in ("baseline", "relative"):
        trained = train_feature_set(
            feature_set=feature_set,
            feature_path=feature_path,
            historical_path=historical_path,
            model_path=model_dir / f"{feature_set}.joblib",
            valid_days=valid_days,
            test_days=test_days,
            force_rebuild_features=force_rebuild_features,
            random_state=random_state,
        )
        results.append(
            {
                "feature_set": feature_set,
                "artifact_path": trained["artifact_path"],
                "split_period": trained["split_period"],
                "metrics": trained["metrics"],
            }
        )
        test_metrics = trained["metrics"]["test"]
        comparison_rows.append(
            {
                "feature_set": feature_set,
                "log_loss": test_metrics["log_loss"],
                "brier_score": test_metrics["brier_score"],
                "calibration_error": test_metrics["calibration_error"],
                "top1_accuracy": test_metrics["top1_accuracy"],
                "top1_win_rate": test_metrics["top1_win_rate"],
                "n_rows": test_metrics["n_rows"],
                "n_races": test_metrics["n_races"],
            }
        )

    baseline = comparison_rows[0]
    relative = comparison_rows[1]
    comparison = {
        key: float(relative[key]) - float(baseline[key])
        for key in [
            "log_loss",
            "brier_score",
            "calibration_error",
            "top1_accuracy",
            "top1_win_rate",
        ]
    }

    report = {
        "report_type": "win_model_baseline_vs_relative",
        "feature_sets": results,
        "comparison": comparison,
        "notes": [
            "train/valid/test are chronological splits",
            "baseline excludes race-relative features and identifier columns",
            "candidate adds race-relative features on top of the baseline feature set",
        ],
        "warnings": [],
    }
    if baseline["top1_accuracy"] >= 0.999 or relative["top1_accuracy"] >= 0.999:
        report["warnings"].append(
            "Near-perfect holdout accuracy detected. Verify feature leakage or data construction before trusting the ablation."
        )

    json_path = report_dir / "win_model_baseline_vs_relative.json"
    csv_path = report_dir / "win_model_baseline_vs_relative.csv"
    bins_path = report_dir / "win_model_calibration_bins.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame(comparison_rows).to_csv(csv_path, index=False, encoding="utf-8")

    bins_rows = []
    for feature_set in ("baseline", "relative"):
        artifact = joblib.load(model_dir / f"{feature_set}.joblib")
        model = artifact["model"]
        feature_columns = list(artifact["feature_columns"])
        frame = _prepare_supervised_frame(feature_path, historical_path, force_rebuild_features=False)
        _, _, test_df, _ = split_train_valid_test(frame, valid_days=valid_days, test_days=test_days)
        test_pred = _prediction_frame(test_df, model, feature_columns)
        _, bins_df = _expected_calibration_error(test_df["target_win"].to_numpy(), test_pred["p_win_raw"].to_numpy(), n_bins=10)
        if not bins_df.empty:
            bins_df = bins_df.copy()
            bins_df.insert(0, "feature_set", feature_set)
            bins_rows.append(bins_df)
    if bins_rows:
        pd.concat(bins_rows, ignore_index=True).to_csv(bins_path, index=False, encoding="utf-8")
    else:
        pd.DataFrame(columns=["feature_set", "bin", "count", "avg_pred", "avg_actual", "gap"]).to_csv(
            bins_path,
            index=False,
            encoding="utf-8",
        )

    report["report_paths"] = {
        "json": str(json_path),
        "csv": str(csv_path),
        "calibration_bins": str(bins_path),
    }
    return report
