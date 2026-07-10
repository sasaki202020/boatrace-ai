from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from src.strategy.probability_calibration_features import (
    add_probability_calibration_features,
    available_calibration_feature_columns,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRED_PATH = ROOT / "data" / "strategy_outputs" / "ev_analysis.csv"
DEFAULT_HIST_PATH = ROOT / "data" / "processed" / "historical_races.csv"
DEFAULT_OUT_PATH = ROOT / "models" / "probability_calibrator.json"
DEFAULT_REPORT_PATH = ROOT / "reports" / "probability_calibration_report.csv"
DEFAULT_SUMMARY_PATH = ROOT / "reports" / "probability_calibration_summary.json"
DEFAULT_DIAG_PATH = ROOT / "reports" / "probability_calibration_diagnostics.json"


def _safe_log_loss(y_true: np.ndarray, y_prob: np.ndarray) -> float | None:
    try:
        clipped = np.clip(np.asarray(y_prob, dtype=float), 1e-6, 1 - 1e-6)
        return float(log_loss(np.asarray(y_true, dtype=int), clipped, labels=[0, 1]))
    except Exception:
        return None


def _safe_brier(y_true: np.ndarray, y_prob: np.ndarray) -> float | None:
    try:
        return float(brier_score_loss(np.asarray(y_true, dtype=int), np.asarray(y_prob, dtype=float)))
    except Exception:
        return None


def _safe_auc(y_true: np.ndarray, y_prob: np.ndarray) -> float | None:
    try:
        if len(np.unique(np.asarray(y_true, dtype=int))) < 2:
            return None
        return float(roc_auc_score(np.asarray(y_true, dtype=int), np.asarray(y_prob, dtype=float)))
    except Exception:
        return None


def _safe_rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except Exception:
        return str(path)


def _build_truth(historical_path: Path) -> pd.DataFrame:
    hist = pd.read_csv(historical_path, low_memory=False)
    required = {"race_id", "lane", "finish_position"}
    missing = required - set(hist.columns)
    if missing:
        raise ValueError(f"historical file missing columns: {sorted(missing)}")

    work = hist.copy()
    work["race_id"] = work["race_id"].astype(str).str.strip()
    work["lane"] = pd.to_numeric(work["lane"], errors="coerce")
    work["finish_position"] = pd.to_numeric(work["finish_position"], errors="coerce")
    if "odds_trifecta" in work.columns:
        work["odds_trifecta"] = pd.to_numeric(work["odds_trifecta"], errors="coerce")
    else:
        work["odds_trifecta"] = pd.NA
    work = work.dropna(subset=["race_id", "lane", "finish_position"]).copy()

    rows: list[dict[str, object]] = []
    for race_id, grp in work.groupby("race_id", sort=False):
        top3 = grp[grp["finish_position"].isin([1, 2, 3])].sort_values("finish_position").copy()
        if len(top3) != 3:
            continue
        rows.append(
            {
                "race_id": race_id,
                "actual_trifecta": "-".join(top3["lane"].astype(int).astype(str).tolist()),
                "official_odds": float(top3["odds_trifecta"].dropna().iloc[0]) if top3["odds_trifecta"].notna().any() else np.nan,
                "date": grp["date"].iloc[0] if "date" in grp.columns else None,
            }
        )
    return pd.DataFrame(rows)


def _prepare_candidate_dataset(pred_path: Path, historical_path: Path) -> pd.DataFrame:
    preds = pd.read_csv(pred_path, low_memory=False)
    if not {"race_id", "trifecta", "approx_prob"}.issubset(preds.columns):
        raise ValueError("predictions must contain race_id, trifecta, approx_prob")

    truth = _build_truth(historical_path)
    work = preds.merge(truth, on="race_id", how="inner")
    if work.empty:
        raise RuntimeError("no overlapping labeled races found for calibration")

    work["hit"] = work["trifecta"].astype(str).eq(work["actual_trifecta"].astype(str)).astype(int)
    if "date_x" in work.columns:
        work["date"] = pd.to_datetime(work["date_x"], errors="coerce")
    elif "date" in work.columns:
        work["date"] = pd.to_datetime(work["date"], errors="coerce")
    else:
        work["date"] = pd.NaT
    work = add_probability_calibration_features(work)
    work = work.sort_values(["date", "race_id", "candidate_rank_by_sort"], kind="mergesort").reset_index(drop=True)
    return work


def _fit_logistic(train_df: pd.DataFrame, feature_columns: list[str]) -> tuple[LogisticRegression, np.ndarray, np.ndarray]:
    x = train_df[feature_columns].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    means = x.mean(axis=0)
    scales = x.std(axis=0)
    scales = np.where(np.abs(scales) < 1e-12, 1.0, scales)
    x_scaled = (x - means) / scales
    model = LogisticRegression(max_iter=2000, solver="lbfgs")
    model.fit(x_scaled, train_df["hit"].to_numpy(dtype=int))
    return model, means, scales


def _predict_logistic(df: pd.DataFrame, feature_columns: list[str], model: LogisticRegression, means: np.ndarray, scales: np.ndarray) -> np.ndarray:
    x = df[feature_columns].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    x_scaled = (x - means) / scales
    return model.predict_proba(x_scaled)[:, 1]


def _build_bin_report(df: pd.DataFrame, score_col: str, label_col: str, bins: list[float]) -> pd.DataFrame:
    labels = [f"[{bins[i]:.2f},{bins[i+1]:.2f})" for i in range(len(bins) - 1)]
    work = df.copy()
    work["prob_bin"] = pd.cut(
        pd.to_numeric(work[score_col], errors="coerce"),
        bins=bins,
        labels=labels,
        include_lowest=True,
        right=False,
    )
    grouped = (
        work.groupby("prob_bin", dropna=False)
        .agg(
            sample_count=(label_col, "size"),
            hit_count=(label_col, "sum"),
            avg_pred=(score_col, "mean"),
        )
        .reset_index()
    )
    grouped["hit_rate"] = grouped["hit_count"] / grouped["sample_count"].where(grouped["sample_count"] > 0, 1)
    grouped["calibration_gap"] = grouped["avg_pred"] - grouped["hit_rate"]
    return grouped


def _distribution_summary(series: pd.Series) -> dict[str, float | int]:
    s = pd.to_numeric(series, errors="coerce").fillna(0.0)
    return {
        "count": int(len(s)),
        "min": float(s.min()),
        "p10": float(s.quantile(0.10)),
        "p50": float(s.quantile(0.50)),
        "p90": float(s.quantile(0.90)),
        "p99": float(s.quantile(0.99)),
        "max": float(s.max()),
        "mean": float(s.mean()),
        "nunique_6dp": int(s.round(6).nunique()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train candidate-level logistic probability calibrator for trifecta hits")
    parser.add_argument("--predictions", default=str(DEFAULT_PRED_PATH))
    parser.add_argument("--historical", default=str(DEFAULT_HIST_PATH))
    parser.add_argument("--out-model", default=str(DEFAULT_OUT_PATH))
    parser.add_argument("--out-report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--out-summary", default=str(DEFAULT_SUMMARY_PATH))
    parser.add_argument("--out-diagnostics", default=str(DEFAULT_DIAG_PATH))
    parser.add_argument("--validation-ratio", type=float, default=0.2)
    parser.add_argument("--bins", default="0,0.05,0.10,0.15,0.20,0.30,0.50,1.01")
    args = parser.parse_args()

    pred_path = Path(args.predictions)
    historical_path = Path(args.historical)
    out_model = Path(args.out_model)
    out_report = Path(args.out_report)
    out_summary = Path(args.out_summary)
    out_diag = Path(args.out_diagnostics)
    if not pred_path.exists():
        raise FileNotFoundError(f"predictions not found: {pred_path}")
    if not historical_path.exists():
        raise FileNotFoundError(f"historical not found: {historical_path}")

    out_model.parent.mkdir(parents=True, exist_ok=True)
    out_report.parent.mkdir(parents=True, exist_ok=True)
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_diag.parent.mkdir(parents=True, exist_ok=True)

    try:
        work = _prepare_candidate_dataset(pred_path, historical_path)
    except RuntimeError as exc:
        if "no overlapping labeled races found for calibration" not in str(exc):
            raise
        summary = {
            "method": "logistic",
            "status": "skipped_no_labeled_overlap",
            "predictions_path": _safe_rel(pred_path),
            "historical_path": _safe_rel(historical_path),
            "model_path": _safe_rel(out_model) if out_model.exists() else None,
            "report_path": _safe_rel(out_report),
            "diagnostics_path": _safe_rel(out_diag),
            "used_existing_model": bool(out_model.exists()),
            "message": str(exc),
        }
        pd.DataFrame(
            [
                {
                    "model": "skipped",
                    "prob_bin": "all",
                    "sample_count": 0,
                    "hit_count": 0,
                    "avg_pred": None,
                    "hit_rate": None,
                    "calibration_gap": None,
                }
            ]
        ).to_csv(out_report, index=False)
        out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        out_diag.write_text(
            json.dumps(
                {
                    "status": "skipped_no_labeled_overlap",
                    "used_existing_model": bool(out_model.exists()),
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"saved: {out_report}")
        print(f"saved: {out_summary}")
        print(f"saved: {out_diag}")
        return

    feature_columns = available_calibration_feature_columns(work)
    dropped_columns = [c for c in [
        "approx_prob",
        "sort_score",
        "first_win_proba",
        "candidate_rank_by_sort",
        "sort_gap_top1",
        "approx_gap_top1",
        "race_first_win_gap12",
        "race_first_win_gap23",
    ] if c not in feature_columns]
    if not feature_columns:
        raise RuntimeError("no usable calibration feature columns found")

    n_total = len(work)
    split = int(round(n_total * (1.0 - float(args.validation_ratio))))
    split = min(max(split, 1), n_total - 1 if n_total > 1 else 1)
    train = work.iloc[:split].copy()
    valid = work.iloc[split:].copy()
    if valid.empty:
        valid = train.copy()

    model, means, scales = _fit_logistic(train, feature_columns)
    train["calibrated_prob"] = _predict_logistic(train, feature_columns, model, means, scales)
    valid["calibrated_prob"] = _predict_logistic(valid, feature_columns, model, means, scales)
    valid["base_prob"] = pd.to_numeric(valid["approx_prob"], errors="coerce").fillna(0.0)

    bins = [float(x.strip()) for x in str(args.bins).split(",") if x.strip()]
    base_report = _build_bin_report(valid, "base_prob", "hit", bins)
    base_report.insert(0, "model", "base")
    logistic_report = _build_bin_report(valid, "calibrated_prob", "hit", bins)
    logistic_report.insert(0, "model", "logistic")
    report_df = pd.concat([base_report, logistic_report], ignore_index=True)

    valid_sorted = valid.sort_values(["race_id", "sort_score"], ascending=[True, False], kind="mergesort").copy()
    top1 = valid_sorted.groupby("race_id", as_index=False).first()
    top1_top5 = valid_sorted[valid_sorted["candidate_rank_by_sort"] <= 5].copy()
    top1_top5_hit_rate = float(top1_top5["hit"].mean()) if len(top1_top5) else 0.0

    metrics = {
        "base": {
            "brier": _safe_brier(valid["hit"].to_numpy(dtype=int), valid["base_prob"].to_numpy(dtype=float)),
            "logloss": _safe_log_loss(valid["hit"].to_numpy(dtype=int), valid["base_prob"].to_numpy(dtype=float)),
            "auc": _safe_auc(valid["hit"].to_numpy(dtype=int), valid["base_prob"].to_numpy(dtype=float)),
        },
        "logistic": {
            "brier": _safe_brier(valid["hit"].to_numpy(dtype=int), valid["calibrated_prob"].to_numpy(dtype=float)),
            "logloss": _safe_log_loss(valid["hit"].to_numpy(dtype=int), valid["calibrated_prob"].to_numpy(dtype=float)),
            "auc": _safe_auc(valid["hit"].to_numpy(dtype=int), valid["calibrated_prob"].to_numpy(dtype=float)),
        },
    }

    artifact = {
        "method": "logistic",
        "base_prob_col": "approx_prob",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "train_rows": int(len(train)),
        "validation_rows": int(len(valid)),
        "validation_ratio": float(args.validation_ratio),
        "feature_columns": feature_columns,
        "validation_metrics": metrics["logistic"],
        "fallback_method": "approx_prob_scale",
        "logistic": {
            "coef": [float(v) for v in np.asarray(model.coef_).ravel().tolist()],
            "intercept": [float(v) for v in np.asarray(model.intercept_).ravel().tolist()],
            "means": [float(v) for v in means.tolist()],
            "scales": [float(v) for v in scales.tolist()],
        },
    }
    out_model.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    report_df.to_csv(out_report, index=False)

    summary = {
        "method": "logistic",
        "train_rows": int(len(train)),
        "validation_rows": int(len(valid)),
        "feature_columns": feature_columns,
        "dropped_constant_features": dropped_columns,
        "base_metrics": metrics["base"],
        "logistic_metrics": metrics["logistic"],
        "selected_better_than_base_brier": (
            metrics["logistic"]["brier"] is not None
            and metrics["base"]["brier"] is not None
            and metrics["logistic"]["brier"] <= metrics["base"]["brier"]
        ),
        "top_candidate_band": {
            "top1_hit_rate": float(top1["hit"].mean()) if len(top1) else 0.0,
            "top5_candidate_hit_rate": top1_top5_hit_rate,
        },
        "prediction_distribution": {
            "base_prob": _distribution_summary(valid["base_prob"]),
            "calibrated_prob": _distribution_summary(valid["calibrated_prob"]),
        },
        "model_path": _safe_rel(out_model),
        "report_path": _safe_rel(out_report),
    }
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    diagnostics = {
        "cause_hypotheses": {
            "previous_artifact_train_rows": 599,
            "previous_issue": "race-level top1 only + single approx_prob input caused severe compression under low hit-rate validation",
            "current_candidate_rows": int(len(work)),
            "current_positive_rate": float(work["hit"].mean()),
            "feature_columns": feature_columns,
        },
        "train_distribution": {
            "approx_prob": _distribution_summary(train["approx_prob"]),
            "first_win_proba": _distribution_summary(train["first_win_proba"]),
        },
        "validation_distribution": {
            "approx_prob": _distribution_summary(valid["approx_prob"]),
            "first_win_proba": _distribution_summary(valid["first_win_proba"]),
        },
        "metrics": summary,
    }
    out_diag.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"saved: {out_model}")
    print(f"saved: {out_report}")
    print(f"saved: {out_summary}")
    print(f"saved: {out_diag}")


if __name__ == "__main__":
    main()
