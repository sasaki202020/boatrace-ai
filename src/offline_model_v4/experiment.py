from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd
from catboost import CatBoostRanker
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .core import ExperimentSpec, STRICT_LAG_FEATURES, assert_feature_contract, multiclass_metrics, normalize_race_logits, normalize_race_probabilities

SEED = 42
RESIDUAL_FEATURES = [
    "racer_prior_count", "racer_prior_win_rate", "racer_prior_top2_rate",
    "racer_prior_mean_finish", "racer_prior5_win_rate", "racer_prior10_win_rate",
    "days_since_previous_race", "feature_availability_count",
]


def build_gap_reset_features(canonical: pd.DataFrame, *, reset_date: str = "2024-01-01") -> pd.DataFrame:
    work = canonical[pd.to_datetime(canonical["date"]) >= pd.Timestamp(reset_date)].copy()
    work = work.sort_values(["date", "jcd", "race_no", "lane"]).reset_index(drop=True)
    lane_hist: dict[int, list[int]] = defaultdict(list)
    venue_lane_hist: dict[tuple[int, int], list[int]] = defaultdict(list)
    racer_hist: dict[int, deque[tuple[str, int]]] = defaultdict(lambda: deque(maxlen=30))
    output: list[dict[str, Any]] = []
    for date, day in work.groupby("date", sort=True):
        pending = []
        for row in day.itertuples(index=False):
            lane = int(row.lane); venue = int(row.jcd); racer = int(row.racer_id)
            lh = lane_hist[lane]; vlh = venue_lane_hist[(venue, lane)]; rh = list(racer_hist[racer])
            count = len(rh); wins = sum(value[1] == 1 for value in rh); top2 = sum(value[1] <= 2 for value in rh)
            record = row._asdict()
            record.update({
                "lane_prior_count": len(lh), "lane_prior_win_rate": (sum(lh) + 1) / (len(lh) + 6),
                "venue_lane_prior_count": len(vlh), "venue_lane_prior_win_rate": (sum(vlh) + 1) / (len(vlh) + 6),
                "racer_prior_count": count, "racer_prior_win_rate": wins / count if count else np.nan,
                "racer_prior_top2_rate": top2 / count if count else np.nan,
                "racer_prior_mean_finish": np.mean([value[1] for value in rh]) if rh else np.nan,
                "racer_prior5_win_rate": np.mean([value[1] == 1 for value in rh[-5:]]) if rh else np.nan,
                "racer_prior10_win_rate": np.mean([value[1] == 1 for value in rh[-10:]]) if rh else np.nan,
                "days_since_previous_race": (pd.Timestamp(date) - pd.Timestamp(rh[-1][0])).days if rh else np.nan,
                "feature_availability_count": int(bool(lh)) + int(bool(vlh)) + int(bool(rh)),
            })
            output.append(record); pending.append(row)
        for row in pending:
            lane = int(row.lane); venue = int(row.jcd); racer = int(row.racer_id)
            lane_hist[lane].append(int(row.target)); venue_lane_hist[(venue, lane)].append(int(row.target))
            racer_hist[racer].append((str(date), int(row.finish_position)))
    return pd.DataFrame(output)


def default_specs() -> list[ExperimentSpec]:
    specs = [
        ExperimentSpec("ranking", "ranking_leaf15", {"depth": 4, "iterations": 50}),
        ExperimentSpec("ranking", "ranking_leaf31", {"depth": 6, "iterations": 50}),
        ExperimentSpec("residual", "residual_c01_a05", {"c": 0.1, "alpha": 0.5}),
        ExperimentSpec("residual", "residual_c10_a10", {"c": 1.0, "alpha": 1.0}),
        ExperimentSpec("calibration", "tree15_temp085", {"temperature": 0.85}),
        ExperimentSpec("calibration", "tree15_temp115", {"temperature": 1.15}),
    ]
    ExperimentSpec.validate_budget(specs)
    return specs


def audit_dataset(frame: pd.DataFrame) -> dict[str, Any]:
    required = {"date", "race_id", "jcd", "race_no", "lane", "target"}
    if not required.issubset(frame.columns):
        raise ValueError("dataset_contract_violation:missing_columns")
    duplicate_count = int(frame.duplicated(["race_id", "lane"]).sum())
    grouped = frame.groupby("race_id")
    invalid = int(sum(len(group) != 6 or set(group["lane"].astype(int)) != set(range(1, 7)) or int(group["target"].sum()) != 1 for _, group in grouped))
    if duplicate_count or invalid or frame["target"].isna().any():
        raise ValueError("dataset_contract_violation")
    excluded = sorted(set(frame.columns) & {"finish_position", "target", "classification", "st", "exhibition_time", "final_odds", "payout", "result"})
    venue_counts = frame.groupby("jcd")["race_id"].nunique()
    return {
        "rowCount": len(frame), "raceCount": int(frame["race_id"].nunique()),
        "duplicateRaceLaneCount": duplicate_count, "invalidRaceCount": invalid,
        "targetMissingCount": int(frame["target"].isna().sum()),
        "minimumDate": str(pd.to_datetime(frame["date"]).min().date()),
        "maximumDate": str(pd.to_datetime(frame["date"]).max().date()),
        "venueCount": int(frame["jcd"].nunique()),
        "minimumVenueRaceCount": int(venue_counts.min()), "maximumVenueRaceCount": int(venue_counts.max()),
        "excludedPostRaceColumns": excluded,
        "coverageGap": "2020-03-01..2023-12-31",
    }


def _lane_frequency(train: pd.DataFrame, valid: pd.DataFrame) -> np.ndarray:
    rates = train.groupby("lane")["target"].agg(["sum", "count"])
    score = valid["lane"].map((rates["sum"] + 1) / (rates["count"] + 6)).fillna(1 / 6)
    return normalize_race_probabilities(valid, score)


def _lane1(valid: pd.DataFrame) -> np.ndarray:
    epsilon = 1e-12
    raw = np.where(valid["lane"].to_numpy() == 1, 1 - 5 * epsilon, epsilon)
    return normalize_race_probabilities(valid, raw)


def _tree_model() -> Any:
    return make_pipeline(SimpleImputer(strategy="median"), HistGradientBoostingClassifier(max_leaf_nodes=15, max_iter=100, random_state=SEED))


def _ranking_model(depth: int, iterations: int) -> Any:
    return CatBoostRanker(loss_function="QuerySoftMax", depth=depth, iterations=iterations,
                          learning_rate=0.03, l2_leaf_reg=5, random_seed=SEED,
                          thread_count=1, verbose=False, allow_writing_files=False)


def evaluate(features: pd.DataFrame, split: dict[str, Any], specs: list[ExperimentSpec] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    specs = default_specs() if specs is None else specs
    ExperimentSpec.validate_budget(specs); assert_feature_contract(STRICT_LAG_FEATURES)
    dates = pd.to_datetime(features["date"])
    result_rows: list[dict[str, Any]] = []; prediction_frames: list[pd.DataFrame] = []
    for fold in split["folds"]:
        train = features[(dates >= pd.Timestamp(fold["trainStart"])) & (dates <= pd.Timestamp(fold["trainEnd"]))].copy()
        valid = features[(dates >= pd.Timestamp(fold["validationStart"])) & (dates <= pd.Timestamp(fold["validationEnd"]))].copy()
        tree = _tree_model(); tree.fit(train[STRICT_LAG_FEATURES], train["target"])
        tree_raw = tree.predict_proba(valid[STRICT_LAG_FEATURES])[:, 1]
        probabilities: dict[str, np.ndarray] = {
            "lane1_always": _lane1(valid), "lane_frequency": _lane_frequency(train, valid),
            "tree_15": normalize_race_probabilities(valid, tree_raw),
        }
        residual_models: dict[float, Any] = {}
        ranking_models: dict[tuple[int, int], tuple[SimpleImputer, Any]] = {}
        for spec in specs:
            if spec.family == "calibration":
                probabilities[spec.name] = normalize_race_probabilities(valid, tree_raw, temperature=float(spec.parameters["temperature"]))
            elif spec.family == "residual":
                c = float(spec.parameters["c"])
                if c not in residual_models:
                    residual_models[c] = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=c, max_iter=1000, random_state=SEED))
                    residual_models[c].fit(train[RESIDUAL_FEATURES], train["target"])
                raw = np.clip(residual_models[c].predict_proba(valid[RESIDUAL_FEATURES])[:, 1], 1e-8, 1 - 1e-8)
                lane = np.clip(_lane_frequency(train, valid), 1e-8, 1)
                score = lane * (raw / (1 - raw)) ** float(spec.parameters["alpha"])
                probabilities[spec.name] = normalize_race_probabilities(valid, score)
            elif spec.family == "ranking":
                depth = int(spec.parameters["depth"]); iterations = int(spec.parameters["iterations"]); key = (depth, iterations)
                if key not in ranking_models:
                    ordered = train.sort_values(["race_id", "lane"]).copy(); groups = ordered.groupby("race_id", sort=False).size().to_numpy()
                    imputer = SimpleImputer(strategy="median"); matrix = imputer.fit_transform(ordered[STRICT_LAG_FEATURES])
                    model = _ranking_model(depth, iterations)
                    group_id = np.repeat(np.arange(len(groups)), groups)
                    model.fit(matrix, ordered["target"].to_numpy(), group_id=group_id)
                    ranking_models[key] = (imputer, model)
                imputer, model = ranking_models[key]
                score = model.predict(imputer.transform(valid[STRICT_LAG_FEATURES]))
                probabilities[spec.name] = normalize_race_logits(valid, score)
        for name, probability in probabilities.items():
            scored = valid[["race_id", "date", "jcd", "race_no", "lane", "target", "feature_availability_count"]].copy()
            scored["predicted_probability"] = probability; scored["fold"] = int(fold["fold"]); scored["modelName"] = name
            metrics = multiclass_metrics(scored)
            result_rows.append({"fold": int(fold["fold"]), "modelName": name, **metrics})
            prediction_frames.append(scored)
    return pd.DataFrame(result_rows), pd.concat(prediction_frames, ignore_index=True)


def experiment_manifest(specs: list[ExperimentSpec] | None = None) -> list[dict[str, Any]]:
    return [asdict(spec) for spec in (specs or default_specs())]
