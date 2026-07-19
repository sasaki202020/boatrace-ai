from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.offline_model_v4.core import STRICT_LAG_FEATURES, multiclass_metrics, normalize_race_probabilities
from src.offline_model_v4.experiment import RESIDUAL_FEATURES, _lane_frequency, _tree_model
from src.offline_model_v5.core import GATE_FEATURES, build_gate_features, build_inner_splits, gate_weights, gated_blend, validate_experiment_budget

SEED = 42
STATIC_ALPHAS = (0.02, 0.05, 0.10)
GATED_MAXIMA = (0.05, 0.10, 0.20)


def gate_training_columns() -> list[str]:
    return list(GATE_FEATURES)


def _with_missingness(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["missingness_count"] = work[RESIDUAL_FEATURES].isna().sum(axis=1).astype(int)
    return work


def fit_pair(train: pd.DataFrame, valid: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    tree = _tree_model()
    tree.fit(train[STRICT_LAG_FEATURES], train["target"])
    tree_raw = tree.predict_proba(valid[STRICT_LAG_FEATURES])[:, 1]
    tree_probability = normalize_race_probabilities(valid, tree_raw)
    residual = make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(C=1.0, max_iter=1000, random_state=SEED),
    )
    residual.fit(train[RESIDUAL_FEATURES], train["target"])
    raw = np.clip(residual.predict_proba(valid[RESIDUAL_FEATURES])[:, 1], 1e-8, 1 - 1e-8)
    lane = np.clip(_lane_frequency(train, valid), 1e-8, 1)
    residual_probability = normalize_race_probabilities(valid, lane * (raw / (1 - raw)))
    return tree_probability, residual_probability


def _scored(frame: pd.DataFrame, probability: np.ndarray, fold: int, model_name: str, gate: np.ndarray | None = None) -> pd.DataFrame:
    columns = ["race_id", "date", "jcd", "race_no", "lane", "target", "feature_availability_count", "missingness_count"]
    scored = frame[columns].copy()
    scored["predicted_probability"] = probability
    scored["fold"] = fold
    scored["modelName"] = model_name
    if gate is not None:
        gate_map = dict(zip(frame["race_id"].drop_duplicates(), gate, strict=True))
        scored["gate"] = scored["race_id"].map(gate_map)
    return scored


def build_inner_oof(outer_train: pd.DataFrame) -> pd.DataFrame:
    manifest = build_inner_splits(outer_train, folds=3, validation_days=60)
    dates = pd.to_datetime(outer_train["date"])
    frames = []
    for fold in manifest["folds"]:
        train = outer_train[(dates >= pd.Timestamp(fold["trainStart"])) & (dates <= pd.Timestamp(fold["trainEnd"]))].copy()
        valid = outer_train[(dates >= pd.Timestamp(fold["validationStart"])) & (dates <= pd.Timestamp(fold["validationEnd"]))].copy()
        tree, residual = fit_pair(train, valid)
        scored = _scored(_with_missingness(valid), tree, int(fold["fold"]), "inner")
        scored["tree_probability"] = tree
        scored["residual_probability"] = residual
        frames.append(scored)
    output = pd.concat(frames, ignore_index=True)
    if output.duplicated(["race_id", "lane"]).any():
        raise ValueError("inner_oof_duplicate")
    return output


def _gate_training_set(inner_oof: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    gate = build_gate_features(inner_oof, inner_oof["tree_probability"].to_numpy(), inner_oof["residual_probability"].to_numpy())
    winner = inner_oof[inner_oof["target"] == 1].set_index("race_id")
    label = (winner["residual_probability"] > winner["tree_probability"]).astype(int)
    gate = gate.set_index("race_id").loc[label.index].reset_index()
    if label.nunique() != 2:
        raise ValueError("gate_label_single_class")
    return gate, label.to_numpy(int)


def _fit_gate(inner_oof: pd.DataFrame) -> Any:
    gate, label = _gate_training_set(inner_oof)
    model = make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=1000, random_state=SEED))
    model.fit(gate[GATE_FEATURES], label)
    return model


def evaluate_v5(features: pd.DataFrame, outer_manifest: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    validate_experiment_budget({"static": list(STATIC_ALPHAS), "gated": list(GATED_MAXIMA)})
    dates = pd.to_datetime(features["date"])
    result_rows = []
    prediction_frames = []
    error_rows = []
    for outer in outer_manifest["folds"]:
        fold = int(outer["fold"])
        train = features[(dates >= pd.Timestamp(outer["trainStart"])) & (dates <= pd.Timestamp(outer["trainEnd"]))].copy()
        valid = features[(dates >= pd.Timestamp(outer["validationStart"])) & (dates <= pd.Timestamp(outer["validationEnd"]))].copy()
        inner_oof = build_inner_oof(train)
        gate_model = _fit_gate(inner_oof)
        tree, residual = fit_pair(train, valid)
        valid = _with_missingness(valid).reset_index(drop=True)
        gate_features = build_gate_features(valid, tree, residual)
        candidate_probabilities: dict[str, tuple[np.ndarray, np.ndarray | None]] = {
            "tree_15": (tree, None),
            "residual_c10_a10": (residual, None),
        }
        race_count = valid["race_id"].nunique()
        for alpha in STATIC_ALPHAS:
            gate = np.full(race_count, alpha)
            candidate_probabilities[f"static_a{int(alpha * 100):02d}"] = (gated_blend(valid, tree, residual, gate, g_max=alpha), gate)
        gate_probability = gate_model.predict_proba(gate_features[GATE_FEATURES])[:, 1]
        for maximum in GATED_MAXIMA:
            gate = gate_weights(gate_probability, g_max=maximum)
            candidate_probabilities[f"gated_g{int(maximum * 100):02d}"] = (gated_blend(valid, tree, residual, gate, g_max=maximum), gate)
        for name, (probability, gate) in candidate_probabilities.items():
            scored = _scored(valid, probability, fold, name, gate)
            metrics = multiclass_metrics(scored)
            if name.startswith("gated_"):
                maximum = float(name.removeprefix("gated_g")) / 100.0
                activation = float((gate > 0).mean())
            elif name.startswith("static_"):
                activation = 1.0
            else:
                activation = 0.0
            result_rows.append({"fold": fold, "modelName": name, **metrics, "activationRate": activation})
            prediction_frames.append(scored)
        tree_top = valid.iloc[pd.Series(tree).groupby(valid["race_id"]).idxmax()][["race_id", "lane"]].set_index("race_id")["lane"]
        residual_top = valid.iloc[pd.Series(residual).groupby(valid["race_id"]).idxmax()][["race_id", "lane"]].set_index("race_id")["lane"]
        winner = valid[valid["target"] == 1].copy()
        winner["tree_winner_probability"] = tree[winner.index]
        winner["residual_winner_probability"] = residual[winner.index]
        gate_by_race = gate_features.set_index("race_id")
        for row in winner.itertuples(index=False):
            rid = row.race_id
            error_rows.append({
                "fold": fold, "race_id": rid, "date": row.date, "jcd": row.jcd, "race_no": row.race_no,
                **gate_by_race.loc[rid].to_dict(),
                "tree_top1": int(tree_top.loc[rid]), "residual_top1": int(residual_top.loc[rid]),
                "treeCorrect": int(tree_top.loc[rid] == row.lane), "residualCorrect": int(residual_top.loc[rid] == row.lane),
                "treeOnlyCorrect": int(tree_top.loc[rid] == row.lane and residual_top.loc[rid] != row.lane),
                "residualOnlyCorrect": int(residual_top.loc[rid] == row.lane and tree_top.loc[rid] != row.lane),
                "treeWinnerProbability": row.tree_winner_probability, "residualWinnerProbability": row.residual_winner_probability,
                "residualLogLossDelta": float(-np.log(row.residual_winner_probability) + np.log(row.tree_winner_probability)),
            })
    results = pd.DataFrame(result_rows)
    predictions = pd.concat(prediction_frames, ignore_index=True)
    errors = pd.DataFrame(error_rows)
    if predictions[predictions["modelName"] == "tree_15"].duplicated(["race_id", "lane"]).any():
        raise ValueError("outer_oof_duplicate")
    return results, predictions, errors


def prediction_hash(predictions: pd.DataFrame) -> str:
    columns = ["fold", "modelName", "race_id", "lane", "predicted_probability"]
    ordered = predictions[columns].sort_values(columns[:4]).reset_index(drop=True).round(14)
    return hashlib.sha256(pd.util.hash_pandas_object(ordered, index=False).to_numpy().tobytes()).hexdigest()
