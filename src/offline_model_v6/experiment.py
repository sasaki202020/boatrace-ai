from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.offline_model_v4.core import STRICT_LAG_FEATURES, normalize_race_probabilities
from src.offline_model_v4.experiment import RESIDUAL_FEATURES, _tree_model
from src.offline_model_v5.core import build_inner_splits
from src.offline_model_v6.core import (
    DIFF_FEATURES,
    SELECTOR_CONFIGS,
    SELECTOR_FEATURES,
    SelectorConfig,
    choose_selector_config,
    selector_label,
    selector_metrics,
    validate_inner_manifest,
    validate_selector_output,
)

SEED = 42


def fit_tree(train: pd.DataFrame, valid: pd.DataFrame) -> np.ndarray:
    model = _tree_model()
    model.fit(train[STRICT_LAG_FEATURES], train["target"])
    raw = model.predict_proba(valid[STRICT_LAG_FEATURES])[:, 1]
    return normalize_race_probabilities(valid, raw)


def _entropy(values: np.ndarray) -> float:
    return float(-(values * np.log(np.clip(values, 1e-15, 1))).sum() / np.log(len(values)))


def build_selector_dataset(
    frame: pd.DataFrame, probability: np.ndarray, *, fold: int, scope: int, prediction_training_end: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if scope not in (2, 3):
        raise ValueError("selector_scope")
    work = frame.reset_index(drop=True).copy()
    work["predicted_probability"] = np.asarray(probability, dtype=float)
    work["missingness_count"] = work[list(RESIDUAL_FEATURES)].isna().sum(axis=1)
    race_rows: list[dict[str, Any]] = []
    boat_frames: list[pd.DataFrame] = []
    for race_id, race in work.groupby("race_id", sort=False):
        ranked = race.sort_values(["predicted_probability", "lane"], ascending=[False, True]).reset_index(drop=True)
        ranked["probabilityRank"] = np.arange(1, len(ranked) + 1)
        if len(ranked) != 6 or ranked["lane"].nunique() != 6 or int(ranked["target"].sum()) != 1:
            raise ValueError("selector_race_contract")
        winner_rank = int(ranked.loc[ranked["target"] == 1, "probabilityRank"].iloc[0])
        top = ranked.iloc[:3]
        probabilities = top["predicted_probability"].to_numpy(float)
        row: dict[str, Any] = {
            "predictionTrainingEnd": prediction_training_end,
            "sourceFold": fold,
            "fold": fold,
            "race_id": race_id,
            "date": str(ranked.iloc[0]["date"]),
            "jcd": str(ranked.iloc[0]["jcd"]),
            "race_no": int(ranked.iloc[0]["race_no"]),
            "winnerRank": winner_rank,
            "selectorLabel": selector_label(winner_rank=winner_rank, scope=scope),
            "p1": probabilities[0],
            "p2": probabilities[1],
            "p3": probabilities[2],
            "margin12": probabilities[0] - probabilities[1],
            "margin13": probabilities[0] - probabilities[2],
            "entropy": _entropy(ranked["predicted_probability"].to_numpy(float)),
            "jcd_numeric": float(pd.to_numeric(ranked.iloc[0]["jcd"], errors="coerce")),
            "lane1": int(top.iloc[0]["lane"]),
            "lane2": int(top.iloc[1]["lane"]),
            "lane3": int(top.iloc[2]["lane"]),
            "missingness_count": float(top["missingness_count"].mean()),
            "feature_availability_count": float(top["feature_availability_count"].mean()),
        }
        for rank in (2, 3):
            candidate = top.iloc[rank - 1]
            leader = top.iloc[0]
            for feature in DIFF_FEATURES:
                row[f"rank{rank}_minus_rank1_{feature}"] = float(candidate[feature] - leader[feature])
        race_rows.append(row)
        boats = ranked[["race_id", "date", "jcd", "race_no", "lane", "target", "predicted_probability", "probabilityRank"]].copy()
        boats["fold"] = fold
        boat_frames.append(boats)
    races = pd.DataFrame(race_rows)
    boats = pd.concat(boat_frames, ignore_index=True)
    if (pd.Timestamp(prediction_training_end) >= pd.to_datetime(races["date"])).any():
        raise ValueError("prediction_cutoff_violation")
    if (pd.to_datetime(races["date"]) > pd.Timestamp("2026-07-16")).any():
        raise ValueError("prospective_period_forbidden")
    if races[list(SELECTOR_FEATURES)].replace([np.inf, -np.inf], np.nan).isna().all(axis=0).any():
        raise ValueError("selector_feature_all_missing")
    return races, boats


def fit_selector(family: str, train: pd.DataFrame) -> Any:
    if family == "logistic":
        model = make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            LogisticRegression(C=0.1, max_iter=1000, random_state=SEED),
        )
    elif family == "small_tree":
        model = make_pipeline(
            SimpleImputer(strategy="median"),
            RandomForestClassifier(
                n_estimators=100,
                max_depth=3,
                min_samples_leaf=200,
                random_state=SEED,
                n_jobs=1,
            ),
        )
    else:
        raise ValueError("selector_family")
    model.fit(train[list(SELECTOR_FEATURES)], train["selectorLabel"])
    return model


def _aligned_probabilities(model: Any, frame: pd.DataFrame, scope: int) -> np.ndarray:
    raw = model.predict_proba(frame[list(SELECTOR_FEATURES)])
    classes = np.asarray(model.classes_, dtype=int)
    output = np.zeros((len(frame), scope + 1), dtype=float)
    for index, value in enumerate(classes):
        if 0 <= value <= scope:
            output[:, value] = raw[:, index]
    if not np.isfinite(output).all():
        raise ValueError("selector_confidence_nonfinite")
    return output


def apply_selector(
    races: pd.DataFrame, model: Any, config: SelectorConfig, *, scope: int
) -> pd.DataFrame:
    output = races.copy()
    probabilities = _aligned_probabilities(model, output, scope)
    selected_class = probabilities.argmax(axis=1)
    confidence = probabilities[np.arange(len(output)), selected_class]
    candidate_margin = np.where(selected_class == 2, output["margin13"], output["margin12"])
    applied = (
        (selected_class > 0)
        & (selected_class < scope)
        & (confidence >= config.confidence)
        & (candidate_margin <= config.margin_max)
    )
    selected_rank = np.where(applied, selected_class + 1, 1)
    output["selectorClass"] = selected_class
    output["selectorConfidence"] = confidence
    output["selectorApplied"] = applied
    output["selectorRank"] = selected_rank
    output["baselineCorrect"] = (output["winnerRank"] == 1).astype(int)
    output["selectorCorrect"] = (output["winnerRank"] == selected_rank).astype(int)
    output["selectorTopPick"] = np.select(
        [selected_rank == 2, selected_rank == 3],
        [output["lane2"], output["lane3"]],
        default=output["lane1"],
    ).astype(int)
    return output


def build_inner_selector_oof(outer_train: pd.DataFrame, *, scope: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    manifest = build_inner_splits(outer_train, folds=3, validation_days=60)
    validate_inner_manifest(manifest)
    dates = pd.to_datetime(outer_train["date"])
    frames = []
    for fold in manifest["folds"]:
        train = outer_train[(dates >= pd.Timestamp(fold["trainStart"])) & (dates <= pd.Timestamp(fold["trainEnd"]))].copy()
        valid = outer_train[(dates >= pd.Timestamp(fold["validationStart"])) & (dates <= pd.Timestamp(fold["validationEnd"]))].copy()
        probability = fit_tree(train, valid)
        races, _ = build_selector_dataset(valid, probability, fold=int(fold["fold"]), scope=scope, prediction_training_end=fold["trainEnd"])
        frames.append(races)
    output = pd.concat(frames, ignore_index=True)
    if output.duplicated("race_id").any():
        raise ValueError("inner_selector_oof_duplicate")
    return output, manifest


def select_inner_configuration(inner_oof: pd.DataFrame, *, scope: int) -> tuple[str | None, pd.DataFrame]:
    selector_train = inner_oof[inner_oof["fold"] < inner_oof["fold"].max()].copy()
    selector_valid = inner_oof[inner_oof["fold"] == inner_oof["fold"].max()].copy()
    if pd.to_datetime(selector_train["date"]).max() >= pd.to_datetime(selector_valid["date"]).min():
        raise ValueError("selector_threshold_temporal_leakage")
    rows = []
    models = {family: fit_selector(family, selector_train) for family in {item.family for item in SELECTOR_CONFIGS}}
    for config in SELECTOR_CONFIGS:
        scored = apply_selector(selector_valid, models[config.family], config, scope=scope)
        rows.append({"configId": config.config_id, "family": config.family, **selector_metrics(scored)})
    results = pd.DataFrame(rows)
    return choose_selector_config(results), results


def evaluate_v6(
    features: pd.DataFrame, outer_manifest: dict[str, Any], *, scope: int
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    dates = pd.to_datetime(features["date"])
    fold_rows = []
    race_frames = []
    boat_frames = []
    selection_frames = []
    inner_manifests = []
    config_map = {item.config_id: item for item in SELECTOR_CONFIGS}
    for outer in outer_manifest["folds"]:
        fold = int(outer["fold"])
        train = features[(dates >= pd.Timestamp(outer["trainStart"])) & (dates <= pd.Timestamp(outer["trainEnd"]))].copy()
        valid = features[(dates >= pd.Timestamp(outer["validationStart"])) & (dates <= pd.Timestamp(outer["validationEnd"]))].copy()
        inner_oof, inner_manifest = build_inner_selector_oof(train, scope=scope)
        selected_id, selection = select_inner_configuration(inner_oof, scope=scope)
        selection["outerFold"] = fold
        selection["selected"] = selection["configId"] == selected_id
        selection_frames.append(selection)
        inner_manifests.append({"outerFold": fold, **inner_manifest})
        probability = fit_tree(train, valid)
        outer_races, boats = build_selector_dataset(valid, probability, fold=fold, scope=scope, prediction_training_end=outer["trainEnd"])
        if selected_id is None:
            outer_races["selectorApplied"] = False
            outer_races["selectorRank"] = 1
            outer_races["selectorTopPick"] = outer_races["lane1"]
            outer_races["selectorConfidence"] = 0.0
            outer_races["baselineCorrect"] = (outer_races["winnerRank"] == 1).astype(int)
            outer_races["selectorCorrect"] = outer_races["baselineCorrect"]
        else:
            selected = config_map[selected_id]
            selector = fit_selector(selected.family, inner_oof)
            outer_races = apply_selector(outer_races, selector, selected, scope=scope)
        outer_races["selectedConfigId"] = selected_id
        pick_map = outer_races.set_index("race_id")["selectorTopPick"]
        applied_map = outer_races.set_index("race_id")["selectorApplied"]
        boats["selectorTopPick"] = boats["race_id"].map(pick_map)
        boats["selectorApplied"] = boats["race_id"].map(applied_map)
        validate_selector_output(boats)
        fold_rows.append({"fold": fold, "selectedConfigId": selected_id, **selector_metrics(outer_races)})
        race_frames.append(outer_races)
        boat_frames.append(boats)
    return (
        pd.DataFrame(fold_rows),
        pd.concat(race_frames, ignore_index=True),
        pd.concat(boat_frames, ignore_index=True),
        pd.concat(selection_frames, ignore_index=True),
        inner_manifests,
    )


def prediction_hash(boats: pd.DataFrame) -> str:
    columns = ["fold", "race_id", "lane", "predicted_probability", "selectorTopPick", "selectorApplied"]
    ordered = boats[columns].sort_values(columns[:3]).reset_index(drop=True)
    metadata = ordered.drop(columns="predicted_probability")
    digest = hashlib.sha256()
    digest.update(pd.util.hash_pandas_object(metadata, index=False).to_numpy(dtype="uint64").tobytes())
    digest.update(ordered["predicted_probability"].to_numpy(dtype="float64").tobytes())
    return digest.hexdigest()


def probability_hash(boats: pd.DataFrame) -> str:
    ordered = boats[["fold", "race_id", "lane", "predicted_probability"]].sort_values(
        ["fold", "race_id", "lane"]
    ).reset_index(drop=True)
    digest = hashlib.sha256()
    digest.update(
        pd.util.hash_pandas_object(
            ordered[["fold", "race_id", "lane"]], index=False
        ).to_numpy(dtype="uint64").tobytes()
    )
    digest.update(ordered["predicted_probability"].to_numpy(dtype="float64").tobytes())
    return digest.hexdigest()
