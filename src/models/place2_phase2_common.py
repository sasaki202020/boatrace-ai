from __future__ import annotations

"""Shared helpers for the Phase 2 place2 conditional model."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier, early_stopping
from sklearn.metrics import brier_score_loss, log_loss

from src.features.build_place2_context_features import (
    Place2ContextBuildSummary,
    build_place2_context_frame,
)
from src.models.win_baseline_common import (
    DEFAULT_RANDOM_STATE,
    SplitPeriod,
    load_trainable_frame,
    select_training_features,
    split_train_valid_test,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC_PATH = ROOT / "config" / "model_pipeline" / "phase2_place2_model.json"
DEFAULT_TRAINABLE_PATH = ROOT / "data" / "processed" / "trainable_win_training_data.csv"
DEFAULT_PHASE1_MODEL_PATH = ROOT / "models" / "win_model_phase1" / "win_model_phase1_core_relative.joblib"
DEFAULT_MODEL_DIR = ROOT / "models" / "place2_model_phase2"
DEFAULT_REPORT_DIR = ROOT / "reports" / "model_eval"
DEFAULT_REPORT_JSON = DEFAULT_REPORT_DIR / "place2_model_phase2_core_relative.json"
DEFAULT_REPORT_CSV = DEFAULT_REPORT_DIR / "place2_model_phase2_core_relative.csv"
DEFAULT_SPLIT_MANIFEST = DEFAULT_REPORT_DIR / "place2_model_phase2_split_manifest.json"

META_COLUMNS = {
    "race_id",
    "place2_context_id",
    "split_name",
    "date",
    "finish_position",
    "target_place2",
    "fixed_first_place_source",
    "model_name",
    "feature_set_name",
    "calibrated_flag",
}


@dataclass(frozen=True)
class Phase2Metrics:
    log_loss: float
    brier_score: float
    calibration_error: float
    top1_accuracy: float
    top3_hit_rate: float
    context_coverage: float
    n_rows: int
    n_contexts: int
    avg_pred_prob: float
    avg_actual_rate: float
    ece_bins: int
    calibration_bins: list[dict[str, Any]]


@dataclass(frozen=True)
class Phase2Run:
    spec_name: str
    phase: int
    feature_set_name: str
    raw_feature_list: list[str]
    dropped_constant_features: list[str]
    dropped_all_null_features: list[str]
    final_feature_list: list[str]
    split_period: SplitPeriod
    metrics: dict[str, Any]
    naive_baseline_metrics: dict[str, Any]
    artifact_path: str
    phase1_model_path: str
    phase1_feature_set_name: str
    context_summary: dict[str, Any]
    split_manifest_path: str


def load_phase2_spec(path: Path = DEFAULT_SPEC_PATH) -> dict[str, Any]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if "spec_name" not in data or "phase" not in data:
        raise ValueError(f"invalid phase2 spec: {path}")
    return data


def load_trainable_phase2_frame(
    *,
    trainable_path: Path = DEFAULT_TRAINABLE_PATH,
    phase1_model_path: Path = DEFAULT_PHASE1_MODEL_PATH,
) -> pd.DataFrame:
    phase1_model_path = Path(phase1_model_path)
    if not phase1_model_path.exists():
        raise FileNotFoundError(f"missing Phase 1 model bundle: {phase1_model_path}")
    frame = load_trainable_frame(trainable_path)
    return frame


def _make_model(random_state: int = DEFAULT_RANDOM_STATE) -> LGBMClassifier:
    return LGBMClassifier(
        objective="binary",
        n_estimators=700,
        learning_rate=0.04,
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


def _expected_calibration_error(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    *,
    n_bins: int = 10,
) -> tuple[float, list[dict[str, Any]]]:
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
            "avg_pred_prob": float("nan"),
            "avg_actual_rate": float("nan"),
            "ece_bins": 10,
            "n_rows": 0,
        }
    ll = float(log_loss(y_true_arr, y_prob_arr, labels=[0, 1]))
    bs = float(brier_score_loss(y_true_arr, y_prob_arr))
    ece, bins = _expected_calibration_error(y_true_arr.to_numpy(), y_prob_arr.to_numpy(), n_bins=10)
    return {
        "log_loss": ll,
        "brier_score": bs,
        "calibration_error": ece,
        "avg_pred_prob": float(np.mean(y_prob_arr)),
        "avg_actual_rate": float(np.mean(y_true_arr)),
        "ece_bins": 10,
        "n_rows": int(len(y_true_arr)),
        "calibration_bins": bins,
    }


def _context_metrics(
    frame: pd.DataFrame,
    prob_col: str = "p_place2_norm",
    *,
    expected_contexts: int | None = None,
) -> dict[str, Any]:
    work = frame.copy()
    work[prob_col] = pd.to_numeric(work[prob_col], errors="coerce").fillna(0.0)
    if "target_place2" not in work.columns:
        raise ValueError("frame must contain target_place2 for context metrics")
    if work.empty:
        return {"top1_accuracy": float("nan"), "top3_hit_rate": float("nan"), "context_coverage": 0.0, "n_contexts": 0}

    top_idx = work.groupby("place2_context_id")[prob_col].idxmax()
    top = work.loc[top_idx].copy()
    top1_accuracy = float((top["target_place2"] == 1).mean()) if not top.empty else float("nan")

    top3_hits = 0
    total_contexts = 0
    for _, grp in work.groupby("place2_context_id", dropna=False):
        total_contexts += 1
        top3 = grp.sort_values(prob_col, ascending=False, kind="mergesort").head(3)
        if int(top3["target_place2"].sum()) > 0:
            top3_hits += 1
    top3_hit_rate = float(top3_hits / total_contexts) if total_contexts else float("nan")
    coverage = float(total_contexts / expected_contexts) if expected_contexts else (1.0 if total_contexts else 0.0)
    return {
        "top1_accuracy": top1_accuracy,
        "top3_hit_rate": top3_hit_rate,
        "context_coverage": coverage,
        "n_contexts": int(total_contexts),
    }


def _naive_baseline_metrics(frame: pd.DataFrame, *, expected_contexts: int | None = None) -> dict[str, Any]:
    work = frame.copy()
    if work.empty:
        return {
            "log_loss": float("nan"),
            "brier_score": float("nan"),
            "calibration_error": float("nan"),
            "top1_accuracy": float("nan"),
            "top3_hit_rate": float("nan"),
            "context_coverage": 0.0,
            "n_rows": 0,
            "n_contexts": 0,
            "avg_pred_prob": float("nan"),
            "avg_actual_rate": float("nan"),
            "ece_bins": 10,
            "calibration_bins": [],
        }
    work["naive_prob"] = 1.0 / 5.0
    row_metrics = _row_metrics(work["target_place2"], work["naive_prob"])
    context_count = int(work["place2_context_id"].nunique())
    coverage = float(context_count / expected_contexts) if expected_contexts else (1.0 if context_count else 0.0)
    return {
        **row_metrics,
        "top1_accuracy": 1.0 / 5.0,
        "top3_hit_rate": 3.0 / 5.0,
        "context_coverage": coverage,
        "n_contexts": context_count,
    }


def _prediction_frame(frame: pd.DataFrame, model: LGBMClassifier, feature_columns: list[str]) -> pd.DataFrame:
    missing = [c for c in feature_columns if c not in frame.columns]
    if missing:
        raise ValueError(f"missing required feature columns: {missing}")

    work = frame.copy()
    X = work[feature_columns]
    work["p_place2_raw"] = model.predict_proba(X)[:, 1]
    work["p_place2_norm"] = work.groupby("place2_context_id")["p_place2_raw"].transform(
        lambda s: s / s.sum() if float(s.sum()) > 0 else 0.0
    )
    work["p_place2_norm"] = work["p_place2_norm"].fillna(0.0)
    work["pred_rank_in_context"] = work.groupby("place2_context_id")["p_place2_norm"].rank(
        method="first", ascending=False
    )
    return work


def _save_bundle(
    *,
    model: LGBMClassifier,
    feature_columns: list[str],
    raw_feature_list: list[str],
    dropped_constant_features: list[str],
    dropped_all_null_features: list[str],
    split_period: SplitPeriod,
    spec: dict[str, Any],
    model_path: Path,
    phase1_model_path: Path,
    context_summary: dict[str, Any],
) -> Path:
    model_path = Path(model_path)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "model": model,
        "feature_columns": feature_columns,
        "raw_feature_list": raw_feature_list,
        "dropped_constant_features": dropped_constant_features,
        "dropped_all_null_features": dropped_all_null_features,
        "split_period": asdict(split_period),
        "spec_name": spec["spec_name"],
        "phase": spec["phase"],
        "feature_set_name": spec["feature_set"]["conditional_feature_set_name"],
        "phase1_model_path": str(phase1_model_path),
        "phase1_feature_set_name": spec["conditioned_on_phase1"]["official_predictor"],
        "context_summary": context_summary,
    }
    joblib.dump(bundle, model_path)
    return model_path


def _build_report(
    *,
    spec: dict[str, Any],
    raw_feature_list: list[str],
    dropped_constant_features: list[str],
    dropped_all_null_features: list[str],
    split_period: SplitPeriod,
    feature_set_name: str,
    phase1_model_path: Path,
    phase1_feature_set_name: str,
    train_pred: pd.DataFrame,
    valid_pred: pd.DataFrame,
    test_pred: pd.DataFrame,
    train_naive: pd.DataFrame,
    valid_naive: pd.DataFrame,
    test_naive: pd.DataFrame,
    context_summary: dict[str, Any],
    artifact_path: Path,
    split_manifest_path: Path,
) -> dict[str, Any]:
    def _metrics_for_split(
        pred_df: pd.DataFrame,
        naive_df: pd.DataFrame,
        *,
        expected_contexts: int,
    ) -> dict[str, Any]:
        row_metrics = _row_metrics(pred_df["target_place2"], pred_df["p_place2_norm"])
        context_metrics = _context_metrics(pred_df, prob_col="p_place2_norm", expected_contexts=expected_contexts)
        naive_row_metrics = _row_metrics(naive_df["target_place2"], naive_df["naive_prob"])
        naive_context_metrics = _naive_baseline_metrics(naive_df, expected_contexts=expected_contexts)
        return {
            "model": {**row_metrics, **context_metrics},
            "naive": {**naive_row_metrics, **naive_context_metrics},
            "rows": int(len(pred_df)),
            "contexts": int(pred_df["place2_context_id"].nunique()) if not pred_df.empty else 0,
            "calibration_bins": row_metrics.get("calibration_bins", []),
        }

    return {
        "spec_name": spec["spec_name"],
        "phase": spec["phase"],
        "model_name": "place2_model_phase2_core_relative",
        "feature_set_name": feature_set_name,
        "phase1_model_path": str(phase1_model_path),
        "phase1_feature_set_name": phase1_feature_set_name,
        "split_period": asdict(split_period),
        "metrics": {
            "train": _metrics_for_split(
                train_pred,
                train_naive,
                expected_contexts=int(context_summary["train"]["input_race_count"]),
            )["model"],
            "valid": _metrics_for_split(
                valid_pred,
                valid_naive,
                expected_contexts=int(context_summary["valid"]["input_race_count"]),
            )["model"],
            "test": _metrics_for_split(
                test_pred,
                test_naive,
                expected_contexts=int(context_summary["test"]["input_race_count"]),
            )["model"],
        },
        "naive_baseline_metrics": {
            "train": _metrics_for_split(
                train_pred,
                train_naive,
                expected_contexts=int(context_summary["train"]["input_race_count"]),
            )["naive"],
            "valid": _metrics_for_split(
                valid_pred,
                valid_naive,
                expected_contexts=int(context_summary["valid"]["input_race_count"]),
            )["naive"],
            "test": _metrics_for_split(
                test_pred,
                test_naive,
                expected_contexts=int(context_summary["test"]["input_race_count"]),
            )["naive"],
        },
        "dropped_constant_features": dropped_constant_features,
        "dropped_all_null_features": dropped_all_null_features,
        "context_summary": context_summary,
        "artifact_path": str(artifact_path),
        "split_manifest_path": str(split_manifest_path),
        "notes": [
            "train/valid/test are chronological splits on race_id",
            "Phase 1 predictions are attached from the fixed core_relative model",
            "target_place2 is defined within each fixed-first context",
        ],
    }


def _context_bundle_from_split(
    split_frame: pd.DataFrame,
    *,
    split_name: str,
    phase1_model_path: Path,
) -> tuple[pd.DataFrame, Place2ContextBuildSummary]:
    return build_place2_context_frame(split_frame, phase1_bundle_path=phase1_model_path, split_name=split_name)


def prepare_phase2_splits(
    frame: pd.DataFrame,
    *,
    phase1_model_path: Path,
    valid_days: int,
    test_days: int,
) -> tuple[dict[str, pd.DataFrame], SplitPeriod, dict[str, Place2ContextBuildSummary]]:
    train_df, valid_df, test_df, split_period = split_train_valid_test(
        frame, valid_days=valid_days, test_days=test_days
    )
    train_ctx, train_summary = _context_bundle_from_split(train_df, split_name="train", phase1_model_path=phase1_model_path)
    valid_ctx, valid_summary = _context_bundle_from_split(valid_df, split_name="valid", phase1_model_path=phase1_model_path)
    test_ctx, test_summary = _context_bundle_from_split(test_df, split_name="test", phase1_model_path=phase1_model_path)
    return (
        {"train": train_ctx, "valid": valid_ctx, "test": test_ctx},
        split_period,
        {"train": train_summary, "valid": valid_summary, "test": test_summary},
    )


def train_phase2_model(
    *,
    trainable_path: Path = DEFAULT_TRAINABLE_PATH,
    spec_path: Path = DEFAULT_SPEC_PATH,
    phase1_model_path: Path = DEFAULT_PHASE1_MODEL_PATH,
    model_path: Path = DEFAULT_MODEL_DIR / "place2_model_phase2_core_relative.joblib",
    report_json: Path = DEFAULT_REPORT_JSON,
    report_csv: Path = DEFAULT_REPORT_CSV,
    split_manifest_path: Path = DEFAULT_SPLIT_MANIFEST,
    valid_days: int = 30,
    test_days: int = 30,
    random_state: int = DEFAULT_RANDOM_STATE,
) -> dict[str, Any]:
    spec = load_phase2_spec(spec_path)
    frame = load_trainable_phase2_frame(trainable_path=trainable_path, phase1_model_path=phase1_model_path)
    splits, split_period, context_summaries = prepare_phase2_splits(
        frame,
        phase1_model_path=phase1_model_path,
        valid_days=valid_days,
        test_days=test_days,
    )

    raw_feature_list = [
        *spec["feature_set"]["candidate_features"],
        *spec["feature_set"]["context_features"],
    ]
    train_features, dropped_constant, dropped_all_null, _ = select_training_features(
        splits["train"], raw_feature_list
    )
    if not train_features:
        raise ValueError("no usable phase2 features remain after filtering")

    model = _make_model(random_state=random_state)
    X_train = splits["train"][train_features]
    y_train = splits["train"]["target_place2"]
    X_valid = splits["valid"][train_features]
    y_valid = splits["valid"]["target_place2"]
    try:
        model.fit(
            X_train,
            y_train,
            eval_set=[(X_valid, y_valid)],
            callbacks=[early_stopping(50, verbose=False)],
        )
    except Exception:
        model.fit(X_train, y_train)

    train_pred = _prediction_frame(splits["train"], model, train_features)
    valid_pred = _prediction_frame(splits["valid"], model, train_features)
    test_pred = _prediction_frame(splits["test"], model, train_features)

    train_naive = splits["train"].copy()
    valid_naive = splits["valid"].copy()
    test_naive = splits["test"].copy()
    for df in (train_naive, valid_naive, test_naive):
        df["naive_prob"] = 1.0 / 5.0

    report = _build_report(
        spec=spec,
        raw_feature_list=raw_feature_list,
        dropped_constant_features=dropped_constant,
        dropped_all_null_features=dropped_all_null,
        split_period=split_period,
        feature_set_name=spec["feature_set"]["conditional_feature_set_name"],
        phase1_model_path=phase1_model_path,
        phase1_feature_set_name=spec["conditioned_on_phase1"]["official_predictor"],
        train_pred=train_pred,
        valid_pred=valid_pred,
        test_pred=test_pred,
        train_naive=train_naive,
        valid_naive=valid_naive,
        test_naive=test_naive,
        context_summary={
            key: asdict(summary)
            for key, summary in context_summaries.items()
        },
        artifact_path=model_path,
        split_manifest_path=split_manifest_path,
    )

    bundle_path = _save_bundle(
        model=model,
        feature_columns=train_features,
        raw_feature_list=raw_feature_list,
        dropped_constant_features=dropped_constant,
        dropped_all_null_features=dropped_all_null,
        split_period=split_period,
        spec=spec,
        model_path=model_path,
        phase1_model_path=phase1_model_path,
        context_summary={
            key: asdict(summary)
            for key, summary in context_summaries.items()
        },
    )

    model_path = Path(model_path)
    report_json = Path(report_json)
    report_csv = Path(report_csv)
    split_manifest_path = Path(split_manifest_path)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_csv.parent.mkdir(parents=True, exist_ok=True)
    split_manifest_path.parent.mkdir(parents=True, exist_ok=True)

    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_df = pd.concat(
        [
            train_pred.assign(split_name="train", model_prob=train_pred["p_place2_norm"]),
            valid_pred.assign(split_name="valid", model_prob=valid_pred["p_place2_norm"]),
            test_pred.assign(split_name="test", model_prob=test_pred["p_place2_norm"]),
        ],
        ignore_index=True,
    )
    csv_df.to_csv(report_csv, index=False, encoding="utf-8")

    split_manifest = {
        "spec_name": spec["spec_name"],
        "phase": spec["phase"],
        "split_period": asdict(split_period),
        "context_summary": {
            key: asdict(summary)
            for key, summary in context_summaries.items()
        },
        "phase1_model_path": str(phase1_model_path),
        "phase1_feature_set_name": spec["conditioned_on_phase1"]["official_predictor"],
        "artifact_path": str(bundle_path),
    }
    split_manifest_path.write_text(json.dumps(split_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "bundle_path": str(bundle_path),
        "report": report,
        "csv_path": str(report_csv),
        "split_manifest_path": str(split_manifest_path),
        "feature_columns": train_features,
        "split_period": asdict(split_period),
        "context_summary": {
            key: asdict(summary)
            for key, summary in context_summaries.items()
        },
        "train_pred": train_pred,
        "valid_pred": valid_pred,
        "test_pred": test_pred,
    }


def evaluate_phase2_model(
    *,
    model_path: Path = DEFAULT_MODEL_DIR / "place2_model_phase2_core_relative.joblib",
    trainable_path: Path = DEFAULT_TRAINABLE_PATH,
    phase1_model_path: Path = DEFAULT_PHASE1_MODEL_PATH,
    report_json: Path = DEFAULT_REPORT_JSON,
    report_csv: Path = DEFAULT_REPORT_CSV,
    split_manifest_path: Path = DEFAULT_SPLIT_MANIFEST,
    valid_days: int = 30,
    test_days: int = 30,
) -> dict[str, Any]:
    spec = load_phase2_spec()
    bundle = joblib.load(model_path)
    if not isinstance(bundle, dict) or "model" not in bundle or "feature_columns" not in bundle:
        raise ValueError(f"invalid phase2 model bundle: {model_path}")
    frame = load_trainable_phase2_frame(trainable_path=trainable_path, phase1_model_path=phase1_model_path)
    splits, split_period, context_summaries = prepare_phase2_splits(
        frame,
        phase1_model_path=phase1_model_path,
        valid_days=valid_days,
        test_days=test_days,
    )
    feature_columns = list(bundle["feature_columns"])
    model = bundle["model"]

    train_pred = _prediction_frame(splits["train"], model, feature_columns)
    valid_pred = _prediction_frame(splits["valid"], model, feature_columns)
    test_pred = _prediction_frame(splits["test"], model, feature_columns)
    train_naive = splits["train"].copy()
    valid_naive = splits["valid"].copy()
    test_naive = splits["test"].copy()
    for df in (train_naive, valid_naive, test_naive):
        df["naive_prob"] = 1.0 / 5.0

    report = _build_report(
        spec=spec,
        raw_feature_list=bundle.get("raw_feature_list", feature_columns),
        dropped_constant_features=bundle.get("dropped_constant_features", []),
        dropped_all_null_features=bundle.get("dropped_all_null_features", []),
        split_period=split_period,
        feature_set_name=bundle.get("feature_set_name", spec["feature_set"]["conditional_feature_set_name"]),
        phase1_model_path=Path(bundle.get("phase1_model_path", phase1_model_path)),
        phase1_feature_set_name=bundle.get(
            "phase1_feature_set_name",
            spec["conditioned_on_phase1"]["official_predictor"],
        ),
        train_pred=train_pred,
        valid_pred=valid_pred,
        test_pred=test_pred,
        train_naive=train_naive,
        valid_naive=valid_naive,
        test_naive=test_naive,
        context_summary={
            key: asdict(summary)
            for key, summary in context_summaries.items()
        },
        artifact_path=model_path,
        split_manifest_path=split_manifest_path,
    )

    report_json = Path(report_json)
    report_csv = Path(report_csv)
    split_manifest_path = Path(split_manifest_path)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_csv.parent.mkdir(parents=True, exist_ok=True)
    split_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_df = pd.concat(
        [
            train_pred.assign(split_name="train", model_prob=train_pred["p_place2_norm"]),
            valid_pred.assign(split_name="valid", model_prob=valid_pred["p_place2_norm"]),
            test_pred.assign(split_name="test", model_prob=test_pred["p_place2_norm"]),
        ],
        ignore_index=True,
    )
    csv_df.to_csv(report_csv, index=False, encoding="utf-8")
    split_manifest = {
        "spec_name": spec["spec_name"],
        "phase": spec["phase"],
        "split_period": asdict(split_period),
        "context_summary": {
            key: asdict(summary)
            for key, summary in context_summaries.items()
        },
        "phase1_model_path": str(bundle.get("phase1_model_path", phase1_model_path)),
        "phase1_feature_set_name": bundle.get(
            "phase1_feature_set_name",
            spec["conditioned_on_phase1"]["official_predictor"],
        ),
        "artifact_path": str(model_path),
    }
    split_manifest_path.write_text(json.dumps(split_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "report": report,
        "csv_path": str(report_csv),
        "split_manifest_path": str(split_manifest_path),
        "bundle_path": str(model_path),
    }
