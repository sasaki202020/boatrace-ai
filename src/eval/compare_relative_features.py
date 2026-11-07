from __future__ import annotations

import argparse
import json
import logging
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from shutil import copyfile
from typing import Iterable

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, SGDClassifier
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.eval.backtest_buy_skip import build_race_outcomes, normalize_predictions, run_backtest
from src.features.build_features import FeatureBuilder
from src.features.build_relative_features import RELATIVE_FEATURE_COLUMNS, add_race_relative_features
from src.models.time_split import time_series_split
from src.strategy.generate_trifecta_candidates import TrifectaGenerator


logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FEATURES = ROOT / "data" / "features" / "train_features.csv"
DEFAULT_HISTORICAL = ROOT / "data" / "processed" / "historical_races.csv"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "relative_features"
DEFAULT_TEMP_DIR = ROOT / "data" / "tmp" / "relative_features"
META_COLS = {"race_id", "lane", "date", "racer_id", "jcd", "finish_position", "win_label"}
RELATIVE_SET = set(RELATIVE_FEATURE_COLUMNS)


@dataclass(frozen=True)
class ModelSummary:
    name: str
    log_loss: float
    brier_score: float
    calibration_error: float
    top1_accuracy: float
    top3_accuracy: float
    buy_candidate_count: int
    roi: float
    hit_count: int
    total_stake: float
    total_payout: float
    n_test_rows: int
    n_test_races: int
    avg_pred_prob: float
    avg_actual_rate: float
    ece_bins: int


def build_parser() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare win model performance with and without race-relative features.")
    parser.add_argument("--features-path", type=Path, default=DEFAULT_FEATURES)
    parser.add_argument("--historical-path", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--temp-dir", type=Path, default=DEFAULT_TEMP_DIR)
    parser.add_argument("--test-days", type=int, default=60, help="Number of most recent dates to hold out.")
    parser.add_argument("--bins", type=int, default=10, help="Bins for calibration summary.")
    return parser.parse_args()


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return pd.read_csv(path, low_memory=False)


def _normalize_date_col(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    out = df.copy()
    if col in out.columns:
        out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%Y-%m-%d")
    return out


def _ensure_train_features(features_path: Path, historical_path: Path) -> Path:
    if features_path.exists():
        return features_path

    logger.info("train_features.csv missing; rebuilding from historical_races.csv")
    builder = FeatureBuilder()
    builder.build(str(historical_path), str(features_path), "train")
    return features_path


def _merge_features_and_labels(features_df: pd.DataFrame, historical_df: pd.DataFrame) -> pd.DataFrame:
    feat = _normalize_date_col(features_df)
    hist = _normalize_date_col(historical_df)
    for col in ("race_id", "lane"):
        if col not in feat.columns:
            raise ValueError(f"feature file is missing required column: {col}")
        if col not in hist.columns:
            raise ValueError(f"historical file is missing required column: {col}")

    feat["race_id"] = feat["race_id"].astype(str).str.strip()
    hist["race_id"] = hist["race_id"].astype(str).str.strip()
    feat["lane"] = pd.to_numeric(feat["lane"], errors="coerce")
    hist["lane"] = pd.to_numeric(hist["lane"], errors="coerce")

    join_cols = ["race_id", "lane"]
    if "date" in feat.columns and "date" in hist.columns:
        join_cols.append("date")

    label_cols = [c for c in ["race_id", "lane", "date", "finish_position"] if c in hist.columns]
    labels = hist[label_cols].copy()
    merged = feat.merge(labels, on=join_cols, how="inner", suffixes=("", "_label"))
    merged = merged.dropna(subset=["race_id", "lane", "finish_position"]).copy()
    merged["finish_position"] = pd.to_numeric(merged["finish_position"], errors="coerce")
    merged = merged.dropna(subset=["finish_position"]).copy()
    merged["win_label"] = (merged["finish_position"].astype(int) == 1).astype(int)
    return merged


def _make_model(n_train_rows: int) -> Pipeline:
    if n_train_rows >= 250_000:
        clf = SGDClassifier(
            loss="log_loss",
            max_iter=2000,
            tol=1e-3,
            random_state=42,
            class_weight="balanced",
        )
    else:
        clf = LogisticRegression(
            max_iter=5000,
            solver="saga",
            random_state=42,
            class_weight="balanced",
        )
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="mean")),
            ("scaler", StandardScaler()),
            ("clf", clf),
        ]
    )


def _select_feature_columns(df: pd.DataFrame, *, include_relative: bool) -> list[str]:
    numeric_cols: list[str] = []
    for col in df.columns:
        if col in META_COLS:
            continue
        if not include_relative and col in RELATIVE_SET:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            numeric_cols.append(col)
    return numeric_cols


def _ece(y_true: pd.Series, y_prob: pd.Series, bins: int = 10) -> tuple[float, pd.DataFrame]:
    work = pd.DataFrame({"y_true": pd.to_numeric(y_true, errors="coerce"), "y_prob": pd.to_numeric(y_prob, errors="coerce")})
    work = work.dropna(subset=["y_true", "y_prob"]).copy()
    if work.empty:
        return 0.0, pd.DataFrame(columns=["bin", "count", "avg_pred", "avg_actual", "abs_gap", "weight"])

    work["bin"] = pd.qcut(work["y_prob"], q=min(max(1, bins), work["y_prob"].nunique()), duplicates="drop")
    grouped = work.groupby("bin", dropna=False)
    table = grouped.agg(
        count=("y_true", "size"),
        avg_pred=("y_prob", "mean"),
        avg_actual=("y_true", "mean"),
    ).reset_index()
    total = float(table["count"].sum()) if not table.empty else 0.0
    if total <= 0:
        table["abs_gap"] = 0.0
        table["weight"] = 0.0
        return 0.0, table
    table["abs_gap"] = (table["avg_pred"] - table["avg_actual"]).abs()
    table["weight"] = table["count"] / total
    ece = float((table["abs_gap"] * table["weight"]).sum())
    return ece, table


def _topk_accuracy(pred_df: pd.DataFrame, k: int = 1) -> float:
    if pred_df.empty or "race_id" not in pred_df.columns or "lane" not in pred_df.columns:
        return 0.0
    work = pred_df.copy()
    work["y_true"] = pd.to_numeric(work["y_true"], errors="coerce").fillna(0).astype(int)
    counts = []
    for _, group in work.groupby("race_id"):
        group = group.sort_values("y_prob", ascending=False)
        topk = group.head(k)
        counts.append(int(topk["y_true"].max() > 0))
    return float(sum(counts) / len(counts)) if counts else 0.0


def _build_prediction_frame(frame: pd.DataFrame, y_prob: np.ndarray) -> pd.DataFrame:
    out = frame[["race_id", "lane", "date", "finish_position", "win_label"]].copy()
    out["y_prob"] = pd.to_numeric(pd.Series(y_prob, index=out.index), errors="coerce").fillna(0.0)
    out["y_true"] = pd.to_numeric(out["win_label"], errors="coerce").fillna(0).astype(int)
    out["win_proba_raw"] = out["y_prob"]
    out["win_proba_norm"] = out.groupby("race_id")["win_proba_raw"].transform(
        lambda s: s / s.sum() if s.sum() > 0 else 0.0
    )
    return out


def _evaluate_candidate_roi(
    pred_frame: pd.DataFrame,
    temp_dir: Path,
    temp_name: str,
    *,
    historical_path: Path,
) -> tuple[int, float, int, float, float]:
    temp_dir.mkdir(parents=True, exist_ok=True)
    win_path = temp_dir / f"{temp_name}_win_proba.csv"
    pred_frame[["race_id", "lane", "date", "win_proba_raw", "win_proba_norm"]].to_csv(win_path, index=False)

    generator = TrifectaGenerator()
    generated = generator.generate(win_path)
    candidate_path = temp_dir / f"{temp_name}_trifecta_candidates.csv"
    generated.to_csv(candidate_path, index=False)

    pred_candidates = normalize_predictions(candidate_path)
    outcomes = build_race_outcomes(historical_path)
    backtest_df, summary = run_backtest(pred_candidates, outcomes, stake_mode="flat", flat_stake=100.0)
    buy_count = int(summary.get("buy_count", 0) or 0)
    roi = float(summary.get("roi", 0.0) or 0.0)
    hit_count = int(summary.get("hit_count", 0) or 0)
    total_stake = float(summary.get("total_stake", 0.0) or 0.0)
    total_payout = float(summary.get("total_payout", 0.0) or 0.0)
    return buy_count, roi, hit_count, total_stake, total_payout


def _train_and_evaluate(
    merged: pd.DataFrame,
    *,
    include_relative: bool,
    test_days: int,
    bins: int,
    temp_dir: Path,
    historical_path: Path,
) -> tuple[ModelSummary, pd.DataFrame]:
    feature_cols = _select_feature_columns(merged, include_relative=include_relative)
    work = merged.copy()
    X = work[feature_cols].copy()
    y = work["win_label"].astype(int)
    dates = work["date"]
    X_train, X_test, y_train, y_test = time_series_split(X, y, dates, test_days=test_days)

    train_idx = X_train.index
    test_idx = X_test.index
    train_frame = work.loc[train_idx].copy()
    test_frame = work.loc[test_idx].copy()

    model = _make_model(len(train_frame))
    model.fit(X_train, y_train)
    y_prob = pd.Series(model.predict_proba(X_test)[:, 1], index=test_frame.index, dtype="float64")

    row_metrics = pd.DataFrame(
        {
            "race_id": test_frame["race_id"].astype(str).values,
            "lane": pd.to_numeric(test_frame["lane"], errors="coerce").values,
            "date": test_frame["date"].values,
            "y_true": pd.to_numeric(y_test, errors="coerce").values,
            "y_prob": y_prob.values,
        }
    )
    row_metrics["rank_in_race"] = row_metrics.groupby("race_id")["y_prob"].rank(method="first", ascending=False)
    top1 = row_metrics[row_metrics["rank_in_race"] == 1].copy()
    top1_accuracy = float((top1["y_true"].astype(int) == 1).mean()) if not top1.empty else 0.0
    top3_accuracy = _topk_accuracy(row_metrics, k=3)

    ll = float(log_loss(y_test, y_prob, labels=[0, 1]))
    brier = float(brier_score_loss(y_test, y_prob))
    ece, calib_table = _ece(y_test, y_prob, bins=bins)

    pred_frame = _build_prediction_frame(test_frame, y_prob.to_numpy())
    buy_count, roi, hit_count, total_stake, total_payout = _evaluate_candidate_roi(
        pred_frame,
        temp_dir,
        "relative_train" if include_relative else "baseline_train",
        historical_path=historical_path,
    )

    summary = ModelSummary(
        name="relative" if include_relative else "baseline",
        log_loss=ll,
        brier_score=brier,
        calibration_error=ece,
        top1_accuracy=top1_accuracy,
        top3_accuracy=top3_accuracy,
        buy_candidate_count=buy_count,
        roi=roi,
        hit_count=hit_count,
        total_stake=total_stake,
        total_payout=total_payout,
        n_test_rows=int(len(test_frame)),
        n_test_races=int(test_frame["race_id"].nunique()),
        avg_pred_prob=float(pd.to_numeric(y_prob, errors="coerce").mean()),
        avg_actual_rate=float(pd.to_numeric(y_test, errors="coerce").mean()),
        ece_bins=int(len(calib_table)),
    )
    calib_table["model"] = summary.name
    return summary, calib_table


def main() -> None:
    args = build_parser()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    features_path = _ensure_train_features(args.features_path, args.historical_path)
    base_features = _normalize_date_col(_read_csv(features_path))
    if "lane" not in base_features.columns:
        raise ValueError("train features must contain lane")
    hist = _read_csv(args.historical_path)
    hist = _normalize_date_col(hist)

    merged = _merge_features_and_labels(base_features, hist)
    if merged.empty:
        raise RuntimeError("no merged rows for relative feature comparison")

    # Relative variant is built from the same base frame to keep the comparison controlled.
    relative_variant = add_race_relative_features(merged, race_key="race_id")
    baseline_summary, baseline_calib = _train_and_evaluate(
        merged,
        include_relative=False,
        test_days=args.test_days,
        bins=args.bins,
        temp_dir=args.temp_dir,
        historical_path=args.historical_path,
    )
    relative_summary, relative_calib = _train_and_evaluate(
        relative_variant,
        include_relative=True,
        test_days=args.test_days,
        bins=args.bins,
        temp_dir=args.temp_dir,
        historical_path=args.historical_path,
    )

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_rows = pd.DataFrame([asdict(baseline_summary), asdict(relative_summary)])
    summary_rows["delta_vs_baseline"] = ""
    summary_rows.loc[summary_rows["name"] == "relative", "delta_vs_baseline"] = "see diff object"
    summary_csv = output_dir / "relative_feature_comparison.csv"
    summary_rows.to_csv(summary_csv, index=False, encoding="utf-8")

    calib = pd.concat([baseline_calib, relative_calib], ignore_index=True)
    calib_csv = output_dir / "relative_feature_calibration_bins.csv"
    calib.to_csv(calib_csv, index=False, encoding="utf-8")

    diff = {
        "log_loss_diff": round(relative_summary.log_loss - baseline_summary.log_loss, 6),
        "brier_score_diff": round(relative_summary.brier_score - baseline_summary.brier_score, 6),
        "calibration_error_diff": round(relative_summary.calibration_error - baseline_summary.calibration_error, 6),
        "top1_accuracy_diff": round(relative_summary.top1_accuracy - baseline_summary.top1_accuracy, 6),
        "top3_accuracy_diff": round(relative_summary.top3_accuracy - baseline_summary.top3_accuracy, 6),
        "buy_candidate_count_diff": int(relative_summary.buy_candidate_count - baseline_summary.buy_candidate_count),
        "roi_diff": round(relative_summary.roi - baseline_summary.roi, 6),
        "hit_count_diff": int(relative_summary.hit_count - baseline_summary.hit_count),
        "total_stake_diff": round(relative_summary.total_stake - baseline_summary.total_stake, 2),
        "total_payout_diff": round(relative_summary.total_payout - baseline_summary.total_payout, 2),
    }
    report = {
        "baseline": asdict(baseline_summary),
        "relative": asdict(relative_summary),
        "diff": diff,
        "notes": [
            "BUY候補件数/ROI は、各モデルの予測確率から生成した上位三連単候補を flat stake で簡易バックテストした値です。",
            "比較は時系列 split の holdout で行っています。",
        ],
    }
    report_json = output_dir / "relative_feature_comparison.json"
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== Relative Feature Comparison ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nSaved: {summary_csv}")
    print(f"Saved: {calib_csv}")
    print(f"Saved: {report_json}")


if __name__ == "__main__":
    main()
