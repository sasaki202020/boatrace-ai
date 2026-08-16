from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SelectorConfig:
    config_id: str
    family: str
    confidence: float
    margin_max: float


SELECTOR_CONFIGS = (
    SelectorConfig("logistic_c60_m05", "logistic", 0.60, 0.05),
    SelectorConfig("logistic_c65_m05", "logistic", 0.65, 0.05),
    SelectorConfig("logistic_c70_m05", "logistic", 0.70, 0.05),
    SelectorConfig("logistic_c65_m10", "logistic", 0.65, 0.10),
    SelectorConfig("small_tree_c60_m05", "small_tree", 0.60, 0.05),
    SelectorConfig("small_tree_c65_m05", "small_tree", 0.65, 0.05),
    SelectorConfig("small_tree_c70_m05", "small_tree", 0.70, 0.05),
    SelectorConfig("small_tree_c65_m10", "small_tree", 0.65, 0.10),
)

DIFF_FEATURES = (
    "lane_prior_win_rate",
    "venue_lane_prior_win_rate",
    "racer_prior_win_rate",
    "racer_prior_top2_rate",
    "racer_prior_mean_finish",
    "racer_prior10_win_rate",
    "days_since_previous_race",
    "feature_availability_count",
)

BASE_SELECTOR_FEATURES = (
    "p1",
    "p2",
    "p3",
    "margin12",
    "margin13",
    "entropy",
    "jcd_numeric",
    "race_no",
    "lane1",
    "lane2",
    "lane3",
    "missingness_count",
    "feature_availability_count",
)

SELECTOR_FEATURES = BASE_SELECTOR_FEATURES + tuple(
    f"rank{rank}_minus_rank1_{feature}" for rank in (2, 3) for feature in DIFF_FEATURES
)


def choose_oracle_scope(*, top1: float, top2: float, top3: float) -> int | None:
    """Choose scope with thresholds fixed before observing the v6 oracle result."""
    top2_gain = top2 - top1
    top3_gain = top3 - top1
    if top2_gain >= 0.05 and top3_gain > 0 and top2_gain >= 0.70 * top3_gain:
        return 2
    if top3_gain >= 0.05:
        return 3
    return None


def selector_label(*, winner_rank: int, scope: int) -> int:
    """Map winner rank to a candidate class, preserving an explicit none class."""
    if scope not in (2, 3) or winner_rank < 1:
        raise ValueError("selector_label_contract")
    return winner_rank - 1 if winner_rank <= scope else scope


def choose_selector_config(rows: pd.DataFrame) -> str | None:
    """Select only from inner selector validation rows with bounded coverage."""
    valid = rows[(rows["coverage"] >= 0.02) & (rows["coverage"] <= 0.20)].copy()
    if "netAdditionalCorrect" not in valid:
        valid["netAdditionalCorrect"] = 0
    if valid.empty:
        return None
    valid = valid.sort_values(
        ["accuracyDelta", "netAdditionalCorrect", "coverage", "configId"],
        ascending=[False, False, True, True],
    )
    return str(valid.iloc[0]["configId"])


def validate_selector_output(frame: pd.DataFrame) -> None:
    required = {
        "race_id",
        "lane",
        "predicted_probability",
        "selectorTopPick",
        "selectorApplied",
    }
    if not required.issubset(frame.columns):
        raise ValueError("selector_output_columns")
    values = frame["predicted_probability"].to_numpy(float)
    if not np.isfinite(values).all() or (values < 0).any() or (values > 1).any():
        raise ValueError("selector_probability_contract")
    sums = frame.groupby("race_id")["predicted_probability"].sum().to_numpy(float)
    if not np.allclose(sums, 1.0, atol=1e-12):
        raise ValueError("selector_probability_sum")
    picks = frame.groupby("race_id")["selectorTopPick"].nunique()
    applied = frame.groupby("race_id")["selectorApplied"].nunique()
    if (picks != 1).any() or (applied != 1).any():
        raise ValueError("selector_race_output_inconsistent")


def selector_metrics(races: pd.DataFrame) -> dict[str, float | int]:
    count = len(races)
    baseline_correct = races["baselineCorrect"].to_numpy(int)
    selector_correct = races["selectorCorrect"].to_numpy(int)
    applied = races["selectorApplied"].to_numpy(bool)
    return {
        "raceCount": count,
        "baselineTop1Accuracy": float(baseline_correct.mean()),
        "selectorTop1Accuracy": float(selector_correct.mean()),
        "accuracyDelta": float((selector_correct - baseline_correct).mean()),
        "netAdditionalCorrect": int(selector_correct.sum() - baseline_correct.sum()),
        "coverage": float(applied.mean()),
        "appliedRaceCount": int(applied.sum()),
        "appliedAccuracy": float(selector_correct[applied].mean()) if applied.any() else 0.0,
    }


def paired_date_bootstrap(
    races: pd.DataFrame, *, iterations: int = 2000, seed: int = 42
) -> dict[str, float | int]:
    daily = races.assign(
        delta=races["selectorCorrect"].astype(int) - races["baselineCorrect"].astype(int)
    ).groupby("date").agg(delta=("delta", "sum"), races=("race_id", "count"))
    rng = np.random.default_rng(seed)
    values = np.empty(iterations, dtype=float)
    for index in range(iterations):
        sampled = daily.iloc[rng.integers(0, len(daily), len(daily))]
        values[index] = sampled["delta"].sum() / sampled["races"].sum()
    return {
        "iterations": iterations,
        "dateBlockCount": len(daily),
        "seed": seed,
        "ci95Lower": float(np.quantile(values, 0.025)),
        "ci95Upper": float(np.quantile(values, 0.975)),
    }


def validate_inner_manifest(manifest: dict[str, Any]) -> None:
    previous_validation_end: pd.Timestamp | None = None
    for fold in manifest["folds"]:
        train_end = pd.Timestamp(fold["trainEnd"])
        validation_start = pd.Timestamp(fold["validationStart"])
        validation_end = pd.Timestamp(fold["validationEnd"])
        if train_end >= validation_start or fold["raceOverlapCount"] != 0:
            raise ValueError("inner_temporal_leakage")
        if previous_validation_end is not None and validation_start <= previous_validation_end:
            raise ValueError("inner_validation_overlap")
        previous_validation_end = validation_end
