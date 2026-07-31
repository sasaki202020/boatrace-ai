from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from copy import deepcopy
from typing import Any, Iterable

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

FEATURE_GROUP = "course_and_start_exhibition"
FEATURE_COLUMNS = ("courseEntry", "startExhibition", "tilt", "bodyWeight")
CHAMPION_ID = "tree_15"
CHAMPION_MODEL_SHA256 = "a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0"
MIN_FORWARD_DAYS = 30
MIN_SETTLED_RACES = 1500
MIN_COVERAGE = 0.8
BOOTSTRAP_SEED = 0
BOOTSTRAP_REPETITIONS = 1000
TARGET_TOKENS = (
    "target", "winner", "finish", "payout", "return", "refund",
    "result", "actual", "rank", "着", "払戻", "結果", "確定",
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _target_key(key: Any) -> bool:
    lowered = str(key).lower()
    return any(token in lowered for token in TARGET_TOKENS)


def _validate_feature_values(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("feature_row_invalid")
    for key, nested in value.items():
        if _target_key(key):
            raise ValueError(f"target_column_prohibited:{key}")
        if isinstance(nested, dict):
            _validate_feature_values(nested)


def _validate_probability_vector(values: Iterable[Any], field: str) -> list[float]:
    probabilities = [float(value) for value in values]
    if len(probabilities) != 6 or any(not math.isfinite(value) or value < 0 or value > 1 for value in probabilities):
        raise ValueError(f"{field}_probability_contract_invalid")
    if not math.isclose(sum(probabilities), 1.0, abs_tol=1e-8):
        raise ValueError(f"{field}_probability_sum_invalid")
    return probabilities


def build_course_start_race_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and copy race rows used by the offline challenger only."""
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in rows:
        race = deepcopy(source)
        race_key = str(race.get("raceKey") or "")
        if not race_key or race_key in seen:
            raise ValueError("race_key_duplicate_or_missing")
        seen.add(race_key)
        if not isinstance(race.get("raceDate"), str) or not race["raceDate"]:
            raise ValueError("race_date_missing")
        baseline = race.get("baselineProbabilities")
        if not isinstance(baseline, list):
            raise ValueError("baseline_probabilities_missing")
        baseline = _validate_probability_vector(baseline, "baseline")
        features = race.get("features")
        if not isinstance(features, list) or len(features) != 6:
            raise ValueError("feature_boat_count_invalid")
        by_boat: dict[int, dict[str, Any]] = {}
        for feature in features:
            if not isinstance(feature, dict) or type(feature.get("boatNo")) is not int:
                raise ValueError("feature_boat_identity_invalid")
            boat_no = int(feature["boatNo"])
            if boat_no in by_boat or not 1 <= boat_no <= 6:
                raise ValueError("feature_boat_identity_invalid")
            _validate_feature_values(feature)
            by_boat[boat_no] = feature
        if set(by_boat) != set(range(1, 7)):
            raise ValueError("feature_boat_identity_invalid")
        if type(race.get("winnerBoat")) is not int or not 1 <= race["winnerBoat"] <= 6:
            raise ValueError("winner_identity_invalid")
        if race.get("featureGroup") not in {None, FEATURE_GROUP}:
            raise ValueError("feature_group_invalid")
        if race.get("researchEligible") is False:
            raise ValueError("ineligible_feature_row")
        if race.get("captureTimestampVerified") is False or race.get("provenanceVerified") is False or race.get("schemaVerified") is False:
            raise ValueError("feature_provenance_invalid")
        if float(race.get("secondsBeforeDeadline") or 0) <= 0:
            raise ValueError("post_deadline_feature")
        normalized_features = []
        for boat_no in range(1, 7):
            feature = by_boat[boat_no]
            values = feature.get("values", feature)
            _validate_feature_values(values)
            normalized_features.append({
                "boatNo": boat_no,
                **{column: values.get(column) for column in FEATURE_COLUMNS},
            })
        output.append({
            "raceKey": race_key,
            "raceDate": race["raceDate"],
            "venue": str(race.get("venue", race.get("jcd", ""))),
            "raceNo": int(race.get("raceNo", 0)),
            "winnerBoat": int(race["winnerBoat"]),
            "baselineProbabilities": baseline,
            "features": normalized_features,
            "featureGroup": FEATURE_GROUP,
        })
    return sorted(output, key=lambda row: (row["raceDate"], row["raceKey"]))


def build_readiness_report(
    *,
    settled_races: int,
    feature_races: int,
    quality: dict[str, Any],
    model_sha256: str,
    observed_forward_days: int | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    if model_sha256 != CHAMPION_MODEL_SHA256:
        reasons.append("champion_model_hash_mismatch")
    days = int(observed_forward_days if observed_forward_days is not None else quality.get("consecutiveCollectionDays") or 0)
    coverage = quality.get("coverage")
    if settled_races < MIN_SETTLED_RACES:
        reasons.append("minimum_settled_races_not_met")
    if days < MIN_FORWARD_DAYS:
        reasons.append("minimum_forward_days_not_met")
    if coverage is None:
        reasons.append("coverage_denominator_unavailable")
    elif float(coverage) < MIN_COVERAGE:
        reasons.append("minimum_coverage_not_met")
    if int(quality.get("postDeadlineCount") or 0):
        reasons.append("post_deadline_records_present")
    if int(quality.get("resultLeakageCount") or 0):
        reasons.append("result_leakage_present")
    if int(quality.get("duplicateCount") or 0):
        reasons.append("duplicate_records_present")
    for field, reason in (
        ("captureTimestampVerifiedRate", "timestamp_not_fully_verified"),
        ("provenanceVerifiedRate", "provenance_not_fully_verified"),
        ("schemaConsistencyRate", "schema_not_fully_verified"),
    ):
        if quality.get(field) != 1.0:
            reasons.append(reason)
    if not quality.get("deterministicParsing", False):
        reasons.append("deterministic_parsing_not_verified")
    return {
        "status": "COURSE_START_CHALLENGER_READY" if not reasons else "CHALLENGER_EVALUATION_BLOCKED",
        "evaluationExecuted": False,
        "featureGroup": FEATURE_GROUP,
        "settledRaceBasis": "settled_predictions_with_complete_verified_course_start_snapshot",
        "championId": CHAMPION_ID,
        "championModelSha256": CHAMPION_MODEL_SHA256,
        "modelHashMatches": model_sha256 == CHAMPION_MODEL_SHA256,
        "settledRaces": int(settled_races),
        "minimumSettledRaces": MIN_SETTLED_RACES,
        "remainingSettledRaces": max(0, MIN_SETTLED_RACES - int(settled_races)),
        "verifiedFeatureRaces": int(feature_races),
        "observedForwardDays": days,
        "minimumForwardDays": MIN_FORWARD_DAYS,
        "remainingForwardDays": max(0, MIN_FORWARD_DAYS - days),
        "coverage": coverage,
        "minimumCoverage": MIN_COVERAGE,
        "blockedReasons": sorted(set(reasons)),
        "productionAdoptionAllowed": False,
    }


def _feature_matrix(races: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    values: list[list[float]] = []
    labels: list[int] = []
    for race in races:
        for boat_no, probability, feature in zip(
            range(1, 7), race["baselineProbabilities"], race["features"]
        ):
            row = [
                math.log(max(probability, 1e-15) / max(1.0 - probability, 1e-15)),
                feature.get("courseEntry"),
                feature.get("startExhibition"),
                feature.get("tilt"),
                feature.get("bodyWeight"),
            ]
            values.append([float(value) if value is not None else np.nan for value in row])
            labels.append(int(boat_no == race["winnerBoat"]))
    return np.asarray(values, dtype=float), np.asarray(labels, dtype=int)


def _fit_candidate(train_races: list[dict[str, Any]]):
    matrix, labels = _feature_matrix(train_races)
    if len(set(labels.tolist())) < 2:
        raise ValueError("challenger_training_target_insufficient")
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    transformed = scaler.fit_transform(imputer.fit_transform(matrix))
    model = LogisticRegression(C=0.1, max_iter=2000, solver="lbfgs", random_state=0)
    model.fit(transformed, labels)
    return imputer, scaler, model


def _predict_candidate(model_bundle: Any, races: list[dict[str, Any]]) -> list[dict[str, Any]]:
    imputer, scaler, model = model_bundle
    matrix, _ = _feature_matrix(races)
    scores = model.decision_function(scaler.transform(imputer.transform(matrix)))
    output: list[dict[str, Any]] = []
    offset = 0
    for race in races:
        race_scores = np.asarray(scores[offset : offset + 6], dtype=float)
        offset += 6
        race_scores -= np.max(race_scores)
        probabilities = np.exp(race_scores)
        probabilities /= probabilities.sum()
        output.append({
            "raceKey": race["raceKey"],
            "raceDate": race["raceDate"],
            "venue": race["venue"],
            "raceNo": race["raceNo"],
            "winnerBoat": race["winnerBoat"],
            "probabilities": [float(value) for value in probabilities],
        })
    return output


def _baseline_predictions(races: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "raceKey": race["raceKey"],
            "raceDate": race["raceDate"],
            "venue": race["venue"],
            "raceNo": race["raceNo"],
            "winnerBoat": race["winnerBoat"],
            "probabilities": list(race["baselineProbabilities"]),
        }
        for race in races
    ]


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"raceCount": 0, "logLoss": None, "brier": None, "top1": None, "ece": None}
    log_losses: list[float] = []
    briers: list[float] = []
    correct: list[int] = []
    confidences: list[float] = []
    for row in rows:
        probabilities = _validate_probability_vector(row["probabilities"], "prediction")
        winner_index = int(row["winnerBoat"]) - 1
        log_losses.append(-math.log(max(probabilities[winner_index], 1e-15)))
        briers.append(sum((probability - (1.0 if index == winner_index else 0.0)) ** 2 for index, probability in enumerate(probabilities)))
        top_index = int(np.argmax(probabilities))
        correct.append(int(top_index == winner_index))
        confidences.append(max(probabilities))
    bins: list[tuple[list[float], list[int]]] = [([], []) for _ in range(10)]
    for confidence, hit in zip(confidences, correct):
        index = min(9, int(confidence * 10))
        bins[index][0].append(confidence)
        bins[index][1].append(hit)
    ece = 0.0
    for confidence_values, hit_values in bins:
        if confidence_values:
            ece += len(confidence_values) / len(rows) * abs(
                sum(confidence_values) / len(confidence_values) - sum(hit_values) / len(hit_values)
            )
    return {
        "raceCount": len(rows),
        "logLoss": float(sum(log_losses) / len(log_losses)),
        "brier": float(sum(briers) / len(briers)),
        "top1": float(sum(correct) / len(correct)),
        "ece": float(ece),
    }


def _bootstrap_ci(
    baseline: list[dict[str, Any]], candidate: list[dict[str, Any]], repetitions: int,
) -> dict[str, list[float] | None]:
    by_date: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(baseline):
        by_date[str(row["raceDate"])].append(index)
    dates = sorted(by_date)
    if not dates:
        return {"logLossDifference": None, "brierDifference": None, "top1Difference": None}
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    differences = {"logLossDifference": [], "brierDifference": [], "top1Difference": []}
    for _ in range(repetitions):
        sampled = rng.choice(dates, size=len(dates), replace=True)
        indices = [index for day in sampled for index in by_date[str(day)]]
        base_sample = [baseline[index] for index in indices]
        candidate_sample = [candidate[index] for index in indices]
        base_metrics = _metrics(base_sample)
        candidate_metrics = _metrics(candidate_sample)
        differences["logLossDifference"].append(candidate_metrics["logLoss"] - base_metrics["logLoss"])
        differences["brierDifference"].append(candidate_metrics["brier"] - base_metrics["brier"])
        differences["top1Difference"].append(candidate_metrics["top1"] - base_metrics["top1"])
    return {
        key: [float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))]
        for key, values in differences.items()
    }


def _segment_metrics(rows: list[dict[str, Any]], candidate_rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        if field == "month":
            value = str(row["raceDate"])[:7]
        elif field == "topPredictedBoat":
            value = str(int(np.argmax(row["probabilities"])) + 1)
        else:
            value = str(row.get(field, "UNKNOWN"))
        groups[value].append(index)
    output = {}
    for key, indices in sorted(groups.items()):
        output[key] = {
            "raceCount": len(indices),
            "baseline": _metrics([rows[index] for index in indices]),
            "candidate": _metrics([candidate_rows[index] for index in indices]),
        }
    return output


def evaluate_course_start_challenger(
    rows: Iterable[dict[str, Any]], *, bootstrap_repetitions: int = BOOTSTRAP_REPETITIONS,
) -> dict[str, Any]:
    races = build_course_start_race_rows(rows)
    if len(races) < 6:
        raise ValueError("insufficient_races_for_five_fold_oof")
    count = len(races)
    boundaries = [round(index * count / 6) for index in range(7)]
    folds: list[dict[str, Any]] = []
    all_baseline: list[dict[str, Any]] = []
    all_candidate: list[dict[str, Any]] = []
    for fold_number in range(1, 6):
        train_start, train_end = 0, boundaries[fold_number]
        validation_start, validation_end = boundaries[fold_number], boundaries[fold_number + 1]
        train = races[train_start:train_end]
        validation = races[validation_start:validation_end]
        if not train or not validation:
            raise ValueError("oof_fold_empty")
        model_bundle = _fit_candidate(train)
        baseline = _baseline_predictions(validation)
        candidate = _predict_candidate(model_bundle, validation)
        base_metrics = _metrics(baseline)
        candidate_metrics = _metrics(candidate)
        folds.append({
            "fold": fold_number,
            "trainRaceCount": len(train),
            "validationRaceCount": len(validation),
            "trainStart": train[0]["raceDate"],
            "trainEnd": train[-1]["raceDate"],
            "validationStart": validation[0]["raceDate"],
            "validationEnd": validation[-1]["raceDate"],
            "baseline": base_metrics,
            "candidate": candidate_metrics,
            "deltaLogLoss": candidate_metrics["logLoss"] - base_metrics["logLoss"],
            "deltaBrier": candidate_metrics["brier"] - base_metrics["brier"],
            "deltaTop1": candidate_metrics["top1"] - base_metrics["top1"],
            "deltaEce": candidate_metrics["ece"] - base_metrics["ece"],
        })
        all_baseline.extend(baseline)
        all_candidate.extend(candidate)
    aggregate_baseline = _metrics(all_baseline)
    aggregate_candidate = _metrics(all_candidate)
    ci = _bootstrap_ci(all_baseline, all_candidate, bootstrap_repetitions)
    log_loss_improved_folds = sum(fold["deltaLogLoss"] < 0 for fold in folds)
    brier_improved_folds = sum(fold["deltaBrier"] < 0 for fold in folds)
    reasons: list[str] = []
    if log_loss_improved_folds < 4:
        reasons.append("log_loss_not_improved_in_4_of_5_folds")
    if ci["logLossDifference"] is None or ci["logLossDifference"][1] >= 0:
        reasons.append("log_loss_ci_includes_zero")
    if brier_improved_folds < 4 and (ci["brierDifference"] is None or ci["brierDifference"][1] >= 0):
        reasons.append("brier_not_stably_improved")
    if aggregate_candidate["ece"] > aggregate_baseline["ece"] + 0.005:
        reasons.append("ece_materially_worse")
    if aggregate_candidate["top1"] < aggregate_baseline["top1"]:
        reasons.append("top1_degraded")
    if max(fold["deltaLogLoss"] for fold in folds) > 0.002:
        reasons.append("worst_fold_log_loss_degradation_exceeded")
    segments = {
        "venue": _segment_metrics(all_baseline, all_candidate, "venue"),
        "raceNo": _segment_metrics(all_baseline, all_candidate, "raceNo"),
        "month": _segment_metrics(all_baseline, all_candidate, "month"),
        "topPredictedBoat": _segment_metrics(all_baseline, all_candidate, "topPredictedBoat"),
    }
    if len(segments["venue"]) < 2 or len(segments["month"]) < 2 or len(segments["topPredictedBoat"]) < 3:
        reasons.append("segment_diversity_insufficient")
    return {
        "status": "PERSONAL_OFFLINE_CHALLENGER" if not reasons else "NO_CHALLENGER_FOUND",
        "candidateId": "tree_15_plus_course_start_logistic",
        "championId": CHAMPION_ID,
        "featureGroup": FEATURE_GROUP,
        "modelSha256": CHAMPION_MODEL_SHA256,
        "candidateConfig": {
            "family": "logistic_regression_on_champion_logit_and_course_start",
            "featureColumns": ["championLogit", *FEATURE_COLUMNS],
            "imputer": "median_fit_on_outer_train_only",
            "scaler": "standard_fit_on_outer_train_only",
            "C": 0.1,
            "solver": "lbfgs",
            "maxIter": 2000,
            "randomState": 0,
        },
        "folds": folds,
        "aggregate": {"baseline": aggregate_baseline, "candidate": aggregate_candidate},
        "aggregateDifferences": {
            "logLoss": aggregate_candidate["logLoss"] - aggregate_baseline["logLoss"],
            "brier": aggregate_candidate["brier"] - aggregate_baseline["brier"],
            "top1": aggregate_candidate["top1"] - aggregate_baseline["top1"],
            "ece": aggregate_candidate["ece"] - aggregate_baseline["ece"],
        },
        "bootstrap95Ci": ci,
        "logLossImprovedFoldCount": log_loss_improved_folds,
        "brierImprovedFoldCount": brier_improved_folds,
        "adoptionReasons": sorted(reasons),
        "leakageAuditPassed": True,
        "probabilityContractPassed": all(
            math.isclose(sum(row["probabilities"]), 1.0, abs_tol=1e-8)
            for row in all_candidate
        ),
        "deterministicProtocol": True,
        "productionAdoptionAllowed": False,
        "candidate": {
            "predictionCount": len(all_candidate),
            "probabilityContractPassed": all(
                math.isclose(sum(row["probabilities"]), 1.0, abs_tol=1e-8)
                for row in all_candidate
            ),
            "predictions": all_candidate,
        },
        "segments": segments,
    }


def result_digest(result: dict[str, Any]) -> str:
    return _hash({key: value for key, value in result.items() if key != "candidate"})
