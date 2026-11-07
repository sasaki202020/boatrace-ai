from __future__ import annotations

"""Shared helpers for the 1st-place baseline comparison workflow."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier, early_stopping
from sklearn.metrics import brier_score_loss, log_loss


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRAINABLE_PATH = ROOT / "data" / "processed" / "trainable_win_training_data.csv"
DEFAULT_CORE_FEATURE_SET_PATH = ROOT / "config" / "feature_sets" / "win_baseline_core.json"
DEFAULT_EXTENDED_FEATURE_SET_PATH = ROOT / "config" / "feature_sets" / "win_baseline_extended.json"
DEFAULT_MODEL_DIR = ROOT / "models" / "win_baseline"
DEFAULT_REPORT_DIR = ROOT / "reports" / "model_eval"
DEFAULT_REPORT_JSON = DEFAULT_REPORT_DIR / "win_model_baseline_core_vs_extended.json"
DEFAULT_REPORT_CSV = DEFAULT_REPORT_DIR / "win_model_baseline_core_vs_extended.csv"

META_COLUMNS = {"race_id", "date", "finish_position", "target_win"}
DEFAULT_VALID_DAYS = 30
DEFAULT_TEST_DAYS = 30
DEFAULT_RANDOM_STATE = 42
RELATIVE_FEATURE_COLUMNS = [
    "national_2ren_rate_rank_in_race",
    "national_2ren_rate_diff_from_race_mean",
    "national_2ren_rate_z_in_race",
    "local_2ren_rate_rank_in_race",
    "local_2ren_rate_diff_from_race_mean",
    "local_2ren_rate_z_in_race",
    "avg_st_rank_in_race",
    "avg_st_advantage_vs_mean",
    "avg_st_advantage_z_in_race",
]


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
    calibration_bins: list[dict[str, Any]]


@dataclass(frozen=True)
class FeatureSetRun:
    feature_set_name: str
    raw_feature_list: list[str]
    dropped_constant_features: list[str]
    dropped_all_null_features: list[str]
    relative_features_used: list[str]
    final_feature_list: list[str]
    split_period: SplitPeriod
    metrics: dict[str, Any]
    artifact_path: str


def load_feature_set_config(path: Path) -> dict[str, Any]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if "feature_set_name" not in data or "features" not in data:
        raise ValueError(f"feature set config must contain feature_set_name and features: {path}")
    if not isinstance(data["features"], list) or not all(isinstance(item, str) for item in data["features"]):
        raise ValueError(f"feature set features must be a list of strings: {path}")
    return data


def load_trainable_frame(path: Path = DEFAULT_TRAINABLE_PATH) -> pd.DataFrame:
    path = Path(path)
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            frame = pd.read_csv(path, low_memory=False, encoding=encoding)
            break
        except UnicodeDecodeError as exc:
            last_error = exc
    else:
        if last_error is not None:
            raise last_error
        frame = pd.read_csv(path, low_memory=False)

    if "date" not in frame.columns:
        raise ValueError("trainable dataset must contain date")
    if "finish_position" not in frame.columns:
        raise ValueError("trainable dataset must contain finish_position")
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["finish_position"] = pd.to_numeric(frame["finish_position"], errors="coerce")
    frame["target_win"] = (frame["finish_position"] == 1).astype(int)
    if "race_id" in frame.columns:
        frame["race_id"] = frame["race_id"].astype("string").str.strip()
    return frame


def split_train_valid_test(
    frame: pd.DataFrame,
    *,
    valid_days: int = DEFAULT_VALID_DAYS,
    test_days: int = DEFAULT_TEST_DAYS,
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


def load_feature_columns(feature_set_config: dict[str, Any]) -> list[str]:
    raw_features = feature_set_config.get("features", [])
    return [str(item) for item in raw_features]


def add_relative_features(frame: pd.DataFrame) -> pd.DataFrame:
    from src.features.build_relative_features import add_race_relative_features

    out = add_race_relative_features(frame)
    if "boat_no" in out.columns:
        out["boat_no"] = pd.to_numeric(out["boat_no"], errors="coerce")
    return out


def _numeric_coerce(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for col in columns:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def select_training_features(
    train_df: pd.DataFrame,
    raw_feature_list: list[str],
) -> tuple[list[str], list[str], list[str], pd.DataFrame]:
    missing = [col for col in raw_feature_list if col not in train_df.columns]
    if missing:
        raise ValueError(f"training data missing required feature columns: {missing}")

    train_features = _numeric_coerce(train_df, raw_feature_list)
    dropped_constant: list[str] = []
    dropped_all_null: list[str] = []
    final_features: list[str] = []

    for col in raw_feature_list:
        series = train_features[col]
        if series.isna().all():
            dropped_all_null.append(col)
            continue
        if series.nunique(dropna=True) <= 1:
            dropped_constant.append(col)
            continue
        final_features.append(col)

    return final_features, dropped_constant, dropped_all_null, train_features


def _make_model(random_state: int = DEFAULT_RANDOM_STATE) -> LGBMClassifier:
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


def _expected_calibration_error(y_true: np.ndarray, y_prob: np.ndarray, *, n_bins: int = 10) -> tuple[float, list[dict[str, Any]]]:
    work = pd.DataFrame(
        {
            "y_true": pd.to_numeric(pd.Series(y_true), errors="coerce"),
            "y_prob": pd.to_numeric(pd.Series(y_prob), errors="coerce"),
        }
    ).dropna(subset=["y_true", "y_prob"])
    if work.empty:
        return 0.0, []

    bins = np.linspace(0.0, 1.0, n_bins + 1)
    work["bin"] = pd.cut(work["y_prob"], bins=bins, include_lowest=True, duplicates="drop")
    grouped = work.groupby("bin", dropna=False, observed=False)
    rows: list[dict[str, Any]] = []
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
    return float(ece), rows


def _row_metrics(y_true: pd.Series, y_prob: pd.Series) -> dict[str, Any]:
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
            "top1_accuracy": float("nan"),
            "top1_win_rate": float("nan"),
            "n_rows": 0,
            "n_races": 0,
            "avg_pred_prob": float("nan"),
            "avg_actual_rate": float("nan"),
            "ece_bins": 10,
            "calibration_bins": [],
        }

    ll = float(log_loss(y_true_arr, y_prob_arr, labels=[0, 1]))
    bs = float(brier_score_loss(y_true_arr, y_prob_arr))
    ece, bins = _expected_calibration_error(y_true_arr.to_numpy(), y_prob_arr.to_numpy(), n_bins=10)
    return {
        "log_loss": ll,
        "brier_score": bs,
        "calibration_error": ece,
        "top1_accuracy": float("nan"),
        "top1_win_rate": float("nan"),
        "n_rows": int(len(y_true_arr)),
        "n_races": 0,
        "avg_pred_prob": float(np.mean(y_prob_arr)),
        "avg_actual_rate": float(np.mean(y_true_arr)),
        "ece_bins": 10,
        "calibration_bins": bins,
    }


def _race_metrics(frame: pd.DataFrame, *, prob_col: str) -> dict[str, Any]:
    work = frame.copy()
    work[prob_col] = pd.to_numeric(work[prob_col], errors="coerce").fillna(0.0)
    if "target_win" not in work.columns:
        raise ValueError("frame must contain target_win for race metrics")
    top_idx = work.groupby("race_id")[prob_col].idxmax()
    top = work.loc[top_idx].copy()
    if top.empty:
        return {"top1_accuracy": float("nan"), "top1_win_rate": float("nan"), "n_races": 0}
    top1_accuracy = float((top["target_win"] == 1).mean())
    return {
        "top1_accuracy": top1_accuracy,
        "top1_win_rate": top1_accuracy,
        "n_races": int(top["race_id"].nunique()),
    }


def _predict_frame(frame: pd.DataFrame, model: LGBMClassifier, feature_columns: list[str]) -> pd.DataFrame:
    missing = [c for c in feature_columns if c not in frame.columns]
    if missing:
        raise ValueError(f"missing required feature columns: {missing}")

    work = frame.copy()
    work = _numeric_coerce(work, feature_columns)
    out = work[["race_id", "date", "finish_position", "target_win"]].copy()
    out["p_win_raw"] = model.predict_proba(work[feature_columns])[:, 1]
    out["p_win_norm"] = out.groupby("race_id")["p_win_raw"].transform(
        lambda s: s / s.sum() if float(s.sum()) > 0 else 0.0
    )
    out["pred_rank_in_race"] = out.groupby("race_id")["p_win_norm"].rank(method="first", ascending=False)
    return out


def augment_features_for_relative_comparison(frame: pd.DataFrame) -> pd.DataFrame:
    """Add race-relative features while preserving the original frame columns."""

    return add_relative_features(frame)


def train_single_feature_set(
    *,
    trainable_frame: pd.DataFrame,
    feature_set_config: dict[str, Any],
    feature_set_path: Path,
    model_path: Path,
    relative_features_used: list[str] | None = None,
    valid_days: int = DEFAULT_VALID_DAYS,
    test_days: int = DEFAULT_TEST_DAYS,
    random_state: int = DEFAULT_RANDOM_STATE,
) -> tuple[FeatureSetRun, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    relative_features_used = list(relative_features_used or [])
    raw_feature_list = load_feature_columns(feature_set_config)
    train_df, valid_df, test_df, split_period = split_train_valid_test(
        trainable_frame, valid_days=valid_days, test_days=test_days
    )
    final_features, dropped_constant, dropped_all_null, train_features = select_training_features(
        train_df, raw_feature_list
    )
    if not final_features:
        raise ValueError(f"no usable features remain after filtering: {feature_set_config['feature_set_name']}")

    model = _make_model(random_state=random_state)
    X_train = _numeric_coerce(train_df, final_features)[final_features]
    y_train = train_df["target_win"]
    X_valid = _numeric_coerce(valid_df, final_features)[final_features]
    y_valid = valid_df["target_win"]
    try:
        model.fit(X_train, y_train, eval_set=[(X_valid, y_valid)], callbacks=[early_stopping(50, verbose=False)])
    except Exception:
        model.fit(X_train, y_train)

    model_path = Path(model_path)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "model": model,
        "feature_columns": final_features,
        "feature_set_name": feature_set_config["feature_set_name"],
        "raw_feature_list": raw_feature_list,
        "dropped_constant_features": dropped_constant,
        "dropped_all_null_features": dropped_all_null,
        "relative_features_used": relative_features_used,
        "split_period": asdict(split_period),
        "random_state": random_state,
        "feature_set_path": str(feature_set_path),
    }
    joblib.dump(bundle, model_path)

    valid_pred = _predict_frame(valid_df, model, final_features)
    test_pred = _predict_frame(test_df, model, final_features)
    valid_row_metrics = _row_metrics(valid_df["target_win"], valid_pred["p_win_norm"])
    test_row_metrics = _row_metrics(test_df["target_win"], test_pred["p_win_norm"])
    valid_race_metrics = _race_metrics(valid_pred.assign(target_win=valid_df["target_win"].values), prob_col="p_win_norm")
    test_race_metrics = _race_metrics(test_pred.assign(target_win=test_df["target_win"].values), prob_col="p_win_norm")

    valid_row_metrics.update(valid_race_metrics)
    test_row_metrics.update(test_race_metrics)

    result = FeatureSetRun(
        feature_set_name=feature_set_config["feature_set_name"],
        raw_feature_list=raw_feature_list,
        dropped_constant_features=dropped_constant,
        dropped_all_null_features=dropped_all_null,
        relative_features_used=relative_features_used,
        final_feature_list=final_features,
        split_period=split_period,
        metrics={
            "valid": valid_row_metrics,
            "test": test_row_metrics,
        },
        artifact_path=str(model_path),
    )
    return result, valid_pred, test_pred, train_features


def evaluate_model_bundle(
    *,
    trainable_frame: pd.DataFrame,
    bundle_path: Path,
    valid_days: int = DEFAULT_VALID_DAYS,
    test_days: int = DEFAULT_TEST_DAYS,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    bundle = joblib.load(bundle_path)
    if not isinstance(bundle, dict) or "model" not in bundle or "feature_columns" not in bundle:
        raise ValueError(f"invalid model bundle: {bundle_path}")

    model = bundle["model"]
    feature_columns = list(bundle["feature_columns"])
    train_df, valid_df, test_df, split_period = split_train_valid_test(
        trainable_frame, valid_days=valid_days, test_days=test_days
    )
    valid_pred = _predict_frame(valid_df, model, feature_columns)
    test_pred = _predict_frame(test_df, model, feature_columns)

    valid_row_metrics = _row_metrics(valid_df["target_win"], valid_pred["p_win_norm"])
    test_row_metrics = _row_metrics(test_df["target_win"], test_pred["p_win_norm"])
    valid_race_metrics = _race_metrics(valid_pred.assign(target_win=valid_df["target_win"].values), prob_col="p_win_norm")
    test_race_metrics = _race_metrics(test_pred.assign(target_win=test_df["target_win"].values), prob_col="p_win_norm")
    valid_row_metrics.update(valid_race_metrics)
    test_row_metrics.update(test_race_metrics)

    report = {
        "feature_set_name": bundle.get("feature_set_name"),
        "raw_feature_list": bundle.get("raw_feature_list", feature_columns),
        "dropped_constant_features": bundle.get("dropped_constant_features", []),
        "dropped_all_null_features": bundle.get("dropped_all_null_features", []),
        "relative_features_used": bundle.get("relative_features_used", []),
        "split_period": asdict(split_period),
        "metrics": {
            "valid": valid_row_metrics,
            "test": test_row_metrics,
        },
        "artifact_path": str(bundle_path),
        "final_feature_list": feature_columns,
    }
    return report, valid_pred, test_pred


def build_comparison_report(
    runs: list[FeatureSetRun],
    *,
    input_dataset: str,
) -> dict[str, Any]:
    comparisons: dict[str, Any] = {}
    if len(runs) == 2:
        core_run, extended_run = runs
        core_test = core_run.metrics["test"]
        ext_test = extended_run.metrics["test"]
        comparisons["test_metric_delta_extended_minus_core"] = {
            key: float(ext_test[key]) - float(core_test[key])
            for key in ["log_loss", "brier_score", "calibration_error", "top1_accuracy", "top1_win_rate"]
            if key in core_test and key in ext_test
        }

    return {
        "report_type": "win_model_baseline_core_vs_extended",
        "input_dataset": input_dataset,
        "feature_sets": [asdict(run) for run in runs],
        "comparison": comparisons,
    }


def rows_for_summary_csv(runs: list[FeatureSetRun]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for run in runs:
        for split_name, metrics in run.metrics.items():
            rows.append(
                {
                    "feature_set_name": run.feature_set_name,
                    "split_name": split_name,
                    "log_loss": metrics["log_loss"],
                    "brier_score": metrics["brier_score"],
                    "calibration_error": metrics["calibration_error"],
                    "top1_accuracy": metrics["top1_accuracy"],
                    "top1_win_rate": metrics["top1_win_rate"],
                    "n_rows": metrics["n_rows"],
                    "n_races": metrics["n_races"],
                    "avg_pred_prob": metrics["avg_pred_prob"],
                    "avg_actual_rate": metrics["avg_actual_rate"],
                    "dropped_constant_features": "|".join(run.dropped_constant_features),
                    "dropped_all_null_features": "|".join(run.dropped_all_null_features),
                    "relative_features_used": "|".join(run.relative_features_used),
                }
            )
    return pd.DataFrame(rows)


def rows_for_report_dicts(reports: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for report in reports:
        metrics_by_split = report.get("metrics", {})
        for split_name, metrics in metrics_by_split.items():
            rows.append(
                {
                    "feature_set_name": report.get("feature_set_name"),
                    "split_name": split_name,
                    "log_loss": metrics.get("log_loss"),
                    "brier_score": metrics.get("brier_score"),
                    "calibration_error": metrics.get("calibration_error"),
                    "top1_accuracy": metrics.get("top1_accuracy"),
                    "top1_win_rate": metrics.get("top1_win_rate"),
                    "n_rows": metrics.get("n_rows"),
                    "n_races": metrics.get("n_races"),
                    "avg_pred_prob": metrics.get("avg_pred_prob"),
                    "avg_actual_rate": metrics.get("avg_actual_rate"),
                    "dropped_constant_features": "|".join(report.get("dropped_constant_features", [])),
                    "dropped_all_null_features": "|".join(report.get("dropped_all_null_features", [])),
                    "relative_features_used": "|".join(report.get("relative_features_used", [])),
                }
            )
    return pd.DataFrame(rows)


def build_relative_comparison_report(
    runs: list[Any],
    *,
    input_dataset: str,
) -> dict[str, Any]:
    if len(runs) != 2:
        raise ValueError("relative comparison requires exactly two runs")

    def _as_report(run: Any) -> dict[str, Any]:
        if isinstance(run, FeatureSetRun):
            return asdict(run)
        if isinstance(run, dict):
            return run
        raise TypeError(f"unsupported run type: {type(run)!r}")

    core_run, core_relative_run = (_as_report(runs[0]), _as_report(runs[1]))
    core_test = core_run["metrics"]["test"]
    relative_test = core_relative_run["metrics"]["test"]
    return {
        "report_type": "win_model_core_vs_core_relative",
        "input_dataset": input_dataset,
        "feature_sets": [core_run, core_relative_run],
        "comparison": {
            "test_metric_delta_core_relative_minus_core": {
                key: float(relative_test[key]) - float(core_test[key])
                for key in ["log_loss", "brier_score", "calibration_error", "top1_accuracy", "top1_win_rate"]
                if key in core_test and key in relative_test
            }
        },
    }


def build_phase1_comparison_report(
    runs: list[Any],
    *,
    input_dataset: str,
) -> dict[str, Any]:
    """Build the official Phase 1 report for 1st-place model fixed selection."""

    report = build_relative_comparison_report(runs, input_dataset=input_dataset)
    report["report_type"] = "win_model_phase1_core_vs_core_relative"
    report["phase"] = 1
    report["regression_baseline"] = "core"
    report["official_predictor"] = "core_relative"
    report["phase_goal"] = "1着モデルを official predictor として固定する"
    return report
