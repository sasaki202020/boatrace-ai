from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

GATE_FEATURES = [
    "tree_top1_probability",
    "tree_top1_top2_margin",
    "tree_entropy",
    "feature_availability",
    "missingness",
    "top1_agreement",
]


def validate_experiment_budget(families: dict[str, list[float]]) -> None:
    if len(families) > 2:
        raise ValueError("family_budget_exceeded")
    if sum(len(values) for values in families.values()) > 12:
        raise ValueError("setting_budget_exceeded")


def select_best_passing(passing_names: set[str], aggregate: pd.DataFrame) -> str | None:
    passing = aggregate[aggregate["modelName"].isin(passing_names)].sort_values(["raceLogLoss", "modelName"])
    return None if passing.empty else str(passing.iloc[0]["modelName"])


def gate_weights(probability: np.ndarray, *, g_max: float) -> np.ndarray:
    values = np.asarray(probability, dtype=float)
    if not np.isfinite(values).all() or g_max <= 0:
        raise ValueError("invalid_gate_probability")
    return g_max * np.clip((values - 0.5) * 2.0, 0.0, 1.0)


def build_inner_splits(frame: pd.DataFrame, *, folds: int = 3, validation_days: int = 60) -> dict[str, Any]:
    normalized = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    dates = sorted(normalized.unique())
    if folds < 2 or len(dates) <= folds * validation_days:
        raise ValueError("insufficient_inner_dates")
    entries = []
    for index in range(folds):
        start = len(dates) - (folds - index) * validation_days
        valid_dates = dates[start:start + validation_days]
        train_dates = dates[:start]
        train = frame[normalized.isin(train_dates)]
        valid = frame[normalized.isin(valid_dates)]
        overlap = set(train["race_id"]) & set(valid["race_id"])
        entries.append({
            "fold": index + 1,
            "trainStart": str(pd.Timestamp(train_dates[0]).date()),
            "trainEnd": str(pd.Timestamp(train_dates[-1]).date()),
            "validationStart": str(pd.Timestamp(valid_dates[0]).date()),
            "validationEnd": str(pd.Timestamp(valid_dates[-1]).date()),
            "trainRaceCount": int(train["race_id"].nunique()),
            "validationRaceCount": int(valid["race_id"].nunique()),
            "raceOverlapCount": len(overlap),
        })
    return {"evaluationLabel": "INNER_CHRONOLOGICAL_OOF", "foldCount": folds, "validationDays": validation_days, "folds": entries}


def _validate_probability(frame: pd.DataFrame, values: np.ndarray, name: str) -> None:
    if len(values) != len(frame) or not np.isfinite(values).all() or (values < 0).any() or (values > 1).any():
        raise ValueError(f"{name}_probability_contract")
    sums = pd.Series(values, index=frame.index).groupby(frame["race_id"]).sum()
    if not np.allclose(sums.to_numpy(), 1.0, atol=1e-12):
        raise ValueError(f"{name}_probability_sum")


def build_gate_features(frame: pd.DataFrame, tree_probability: np.ndarray, residual_probability: np.ndarray) -> pd.DataFrame:
    _validate_probability(frame, tree_probability, "tree")
    _validate_probability(frame, residual_probability, "residual")
    work = frame[["race_id", "feature_availability_count", "missingness_count"]].copy()
    work["tree_probability"] = tree_probability
    work["residual_probability"] = residual_probability
    rows = []
    for race_id, race in work.groupby("race_id", sort=False):
        tree = race["tree_probability"].to_numpy(float)
        residual = race["residual_probability"].to_numpy(float)
        ordered = np.sort(tree)[::-1]
        rows.append({
            "race_id": race_id,
            "tree_top1_probability": float(ordered[0]),
            "tree_top1_top2_margin": float(ordered[0] - ordered[1]),
            "tree_entropy": float(-(tree * np.log(np.clip(tree, 1e-15, 1))).sum() / np.log(len(tree))),
            "feature_availability": float(race["feature_availability_count"].mean() / 3.0),
            "missingness": float(race["missingness_count"].mean()),
            "top1_agreement": float(int(np.argmax(tree) == np.argmax(residual))),
        })
    return pd.DataFrame(rows, columns=["race_id", *GATE_FEATURES])


def gated_blend(
    frame: pd.DataFrame,
    tree_probability: np.ndarray,
    residual_probability: np.ndarray,
    gate_by_race: np.ndarray,
    *,
    g_max: float | None = None,
) -> np.ndarray:
    _validate_probability(frame, tree_probability, "tree")
    _validate_probability(frame, residual_probability, "residual")
    race_ids = list(frame["race_id"].drop_duplicates())
    gate = np.asarray(gate_by_race, dtype=float)
    if gate.shape != (len(race_ids),):
        raise ValueError("gate_shape_violation")
    upper = float(np.max(gate) if g_max is None else g_max)
    if not np.isfinite(gate).all() or (gate < 0).any() or (gate > upper + 1e-15).any():
        raise ValueError("gate_range_violation")
    if np.array_equal(gate, np.zeros_like(gate)):
        return np.asarray(tree_probability, dtype=float).copy()
    gate_map = dict(zip(race_ids, gate, strict=True))
    output = np.empty(len(frame), dtype=float)
    for race_id, indexes in frame.groupby("race_id", sort=False).groups.items():
        positions = frame.index.get_indexer(indexes)
        tree = np.clip(np.asarray(tree_probability)[positions], 1e-15, 1)
        residual = np.clip(np.asarray(residual_probability)[positions], 1e-15, 1)
        logits = np.log(tree) + gate_map[race_id] * (np.log(residual) - np.log(tree))
        logits -= logits.max()
        values = np.exp(logits)
        output[positions] = values / values.sum()
    _validate_probability(frame, output, "blend")
    return output
