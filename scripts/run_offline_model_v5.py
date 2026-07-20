from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import scipy
import sklearn
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.offline_model_v4.core import STRICT_LAG_FEATURES, build_walk_forward_splits, canonical_config_hash, multiclass_metrics, paired_date_block_bootstrap
from src.offline_model_v4.experiment import RESIDUAL_FEATURES, audit_dataset
from src.offline_model_v5.core import GATE_FEATURES, build_inner_splits, select_best_passing
from src.offline_model_v5.experiment import GATED_MAXIMA, STATIC_ALPHAS, evaluate_v5, prediction_hash

EXPECTED = {
    "canonicalDatasetSha256": "bc2294f85e482ac1c1e7458236be509afd5d3adc9aa7afd4ec53fc4658e54f23",
    "asofArtifactSha256": "c1ede746393c906e7197d9a461a32fcacb34e508387b6d189d57da20089f3bcb",
    "tree15ArtifactSha256": "a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0",
    "featureSchemaSha256": "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd",
    "v4ResultHash": "c27bc3fb0b45a17a6eeac1371d4a55cb170e570338d7d913d03926249133fe93",
    "v4OofPredictionSha256": "34e995cfc798c2f1aff1796f6dcad0a4fcc7cb12469025d7d8481b3727d0fca8",
    "v4FinalReportSha256": "2b8ac2d7659c79a2081d4daf549d5964c0d3c2611e4b7432493725c37da5ee89",
    "v4WalkForwardSha256": "ec0310a1ce4b2093e49e4578b4ccc5db4810f2c17401c70224918553be256c6b",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        handle.write("\n")


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        frame.to_csv(handle, index=False, lineterminator="\n")


def source_code_hash() -> str:
    digest = hashlib.sha256()
    for path in (ROOT / "src/offline_model_v5/core.py", ROOT / "src/offline_model_v5/experiment.py", ROOT / "scripts/run_offline_model_v5.py"):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def weighted_summary(results: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, group in results.groupby("modelName"):
        weights = group["raceCount"].to_numpy(float)
        rows.append({
            "modelName": name,
            **{metric: float(np.average(group[metric], weights=weights)) for metric in ("raceLogLoss", "multiclassBrier", "top1Accuracy", "ece10", "activationRate")},
            "raceCount": int(weights.sum()),
        })
    return pd.DataFrame(rows).sort_values("raceLogLoss").reset_index(drop=True)


def calibration_metrics(frame: pd.DataFrame) -> dict[str, float]:
    top = frame.loc[frame.groupby("race_id")["predicted_probability"].idxmax()].copy()
    confidence = np.clip(top["predicted_probability"].to_numpy(float), 1e-8, 1 - 1e-8)
    x = np.log(confidence / (1 - confidence)).reshape(-1, 1)
    y = top["target"].to_numpy(int)
    model = LogisticRegression(C=1e6, max_iter=1000, random_state=42).fit(x, y)
    return {"calibrationIntercept": float(model.intercept_[0]), "calibrationSlope": float(model.coef_[0, 0])}


def segment_analysis(baseline: pd.DataFrame, candidate: pd.DataFrame) -> pd.DataFrame:
    top_lane = candidate.loc[candidate.groupby("race_id")["predicted_probability"].idxmax()].set_index("race_id")["lane"]
    coverage = candidate.groupby("race_id")["feature_availability_count"].mean()
    dimensions = {
        "venue": candidate.drop_duplicates("race_id").set_index("race_id")["jcd"].astype(str),
        "month": pd.to_datetime(candidate.drop_duplicates("race_id").set_index("race_id")["date"]).dt.to_period("M").astype(str),
        "topPickLane": top_lane.astype(str),
        "featureCoverage": coverage.astype(str),
    }
    rows = []
    for dimension, mapping in dimensions.items():
        for segment, race_ids in mapping.groupby(mapping).groups.items():
            ids = set(race_ids)
            cand = candidate[candidate["race_id"].isin(ids)]
            base = baseline[baseline["race_id"].isin(ids)]
            count = cand["race_id"].nunique()
            if count < 100:
                continue
            cm = multiclass_metrics(cand)
            bm = multiclass_metrics(base)
            rows.append({"dimension": dimension, "segment": str(segment), "raceCount": count,
                         "logLossDelta": cm["raceLogLoss"] - bm["raceLogLoss"],
                         "brierDelta": cm["multiclassBrier"] - bm["multiclassBrier"],
                         "top1Delta": cm["top1Accuracy"] - bm["top1Accuracy"],
                         "eceDelta": cm["ece10"] - bm["ece10"]})
    return pd.DataFrame(rows)


def compare_v4(results: pd.DataFrame, v4_path: Path) -> dict[str, object]:
    expected = pd.read_csv(v4_path)
    columns = ["fold", "modelName", "raceCount", "raceLogLoss", "multiclassBrier", "top1Accuracy", "ece10"]
    expected = expected[expected["modelName"].isin(["tree_15", "residual_c10_a10"])][columns].sort_values(["fold", "modelName"]).reset_index(drop=True)
    current = results[results["modelName"].isin(["tree_15", "residual_c10_a10"])][columns].sort_values(["fold", "modelName"]).reset_index(drop=True)
    numeric = ["raceLogLoss", "multiclassBrier", "top1Accuracy", "ece10"]
    passed = expected[["fold", "modelName", "raceCount"]].equals(current[["fold", "modelName", "raceCount"]]) and np.allclose(expected[numeric], current[numeric], atol=1e-12, rtol=0)
    return {"passed": bool(passed), "maximumMetricDifference": float(np.max(np.abs(expected[numeric].to_numpy() - current[numeric].to_numpy())))}


def promotion_audit(results: pd.DataFrame, predictions: pd.DataFrame, deterministic: bool, bootstrap_iterations: int) -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    baseline_results = results[results["modelName"] == "tree_15"].set_index("fold")
    baseline_predictions = predictions[predictions["modelName"] == "tree_15"]
    audits = []
    segments = []
    candidates = sorted(set(results["modelName"]) - {"tree_15", "residual_c10_a10"})
    for name in candidates:
        candidate_results = results[results["modelName"] == name].set_index("fold")
        joined = candidate_results.join(baseline_results[["raceLogLoss", "multiclassBrier", "ece10", "top1Accuracy"]], rsuffix="Baseline")
        log_delta = joined["raceLogLoss"] - joined["raceLogLossBaseline"]
        brier_delta = joined["multiclassBrier"] - joined["multiclassBrierBaseline"]
        candidate_predictions = predictions[predictions["modelName"] == name]
        ci = paired_date_block_bootstrap(baseline_predictions, candidate_predictions, iterations=bootstrap_iterations, seed=42)
        table = segment_analysis(baseline_predictions, candidate_predictions)
        if not table.empty:
            table.insert(0, "modelName", name)
            segments.append(table)
        aggregate = multiclass_metrics(candidate_predictions)
        base_aggregate = multiclass_metrics(baseline_predictions)
        ece_delta = aggregate["ece10"] - base_aggregate["ece10"]
        top1_delta = aggregate["top1Accuracy"] - base_aggregate["top1Accuracy"]
        expected_segments = segment_analysis(baseline_predictions, baseline_predictions)
        expected_keys = set(zip(expected_segments["dimension"], expected_segments["segment"]))
        candidate_keys = set(zip(table["dimension"], table["segment"])) if not table.empty else set()
        segment_pass = bool(
            candidate_keys == expected_keys
            and table["dimension"].nunique() == 4
            and float(table["logLossDelta"].max()) <= 0.01
            and float(table["brierDelta"].max()) <= 0.005
            and float(table["top1Delta"].min()) >= -0.01
            and float(table["eceDelta"].max()) <= 0.01
        )
        activation_pass = True
        if name.startswith("gated_"):
            activation_pass = bool(((candidate_results["activationRate"] >= 0.05) & (candidate_results["activationRate"] <= 0.40)).all())
        passed = bool(
            deterministic
            and int((log_delta < 0).sum()) >= 4
            and ci["logLossCi95Upper"] < 0
            and (int((brier_delta < 0).sum()) >= 4 or ci["brierCi95Upper"] < 0)
            and ece_delta <= 0.005
            and top1_delta >= 0
            and ci["top1Ci95Lower"] >= 0
            and float(log_delta.max()) <= 0.002
            and segment_pass
            and activation_pass
        )
        audits.append({"modelName": name, "logLossImprovedFolds": int((log_delta < 0).sum()), "brierImprovedFolds": int((brier_delta < 0).sum()),
                       "worstFoldLogLossDelta": float(log_delta.max()), "aggregateEceDelta": ece_delta, "aggregateTop1Delta": top1_delta,
                       "segmentCoverage": len(candidate_keys), "expectedSegmentCoverage": len(expected_keys),
                       "segmentPassed": segment_pass, "activationPassed": activation_pass, **ci, "passed": passed})
    audit_frame = pd.DataFrame(audits)
    passing = set(audit_frame.loc[audit_frame["passed"], "modelName"])
    promoted = select_best_passing(passing, weighted_summary(results))
    return audit_frame, pd.concat(segments, ignore_index=True) if segments else pd.DataFrame(), promoted


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Run bounded offline v5 gated residual research")
    parser.add_argument("--source-root", type=Path, default=Path(r"C:\Users\goo10\競艇-recovery\boatrace-ai-clean"))
    parser.add_argument("--bootstrap-iterations", type=int, default=1000)
    args = parser.parse_args()
    source = args.source_root.resolve()
    canonical_path = source / "data/offline_model_v3/canonical_race_results.csv"
    asof_path = source / "data/offline_model_v3/asof_features.csv"
    tree_path = source / "data/commercialization_v1/frozen_candidate/tree_15.joblib"
    hashes = {"canonicalDatasetSha256": sha256_file(canonical_path), "asofArtifactSha256": sha256_file(asof_path), "tree15ArtifactSha256": sha256_file(tree_path), "featureSchemaSha256": canonical_config_hash(STRICT_LAG_FEATURES)}
    if any(hashes[key] != EXPECTED[key] for key in hashes):
        raise SystemExit("FIXED_HASH_MISMATCH")
    v4_report = json.loads((ROOT / "reports/offline_model_v4/final_report.json").read_text(encoding="utf-8"))
    if sha256_file(ROOT / "reports/offline_model_v4/final_report.json") != EXPECTED["v4FinalReportSha256"] or sha256_file(ROOT / "reports/offline_model_v4/walk_forward_results.csv") != EXPECTED["v4WalkForwardSha256"]:
        raise SystemExit("V4_REPORT_FILE_HASH_MISMATCH")
    if v4_report["resultHash"] != EXPECTED["v4ResultHash"] or not v4_report["deterministicRerun"]:
        raise SystemExit("V4_REPRODUCTION_HASH_MISMATCH")
    canonical = pd.read_csv(canonical_path, low_memory=False)
    features = pd.read_csv(asof_path, low_memory=False)
    audit = audit_dataset(features)
    outer = build_walk_forward_splits(features, folds=5, validation_days=60)
    first_results, first_predictions, first_errors = evaluate_v5(features, outer)
    reproduction = compare_v4(first_results, ROOT / "reports/offline_model_v4/walk_forward_results.csv")
    if not reproduction["passed"]:
        raise SystemExit("V4_METRIC_REPRODUCTION_MISMATCH")
    first_hash = prediction_hash(first_predictions)
    second_results, second_predictions, second_errors = evaluate_v5(features, outer)
    second_hash = prediction_hash(second_predictions)
    deterministic = first_hash == second_hash and first_results.equals(second_results) and first_errors.equals(second_errors)
    if not deterministic:
        raise SystemExit("V5_DETERMINISTIC_RERUN_FAILED")
    v4_models = ["tree_15", "residual_c10_a10"]
    actual_v4_oof_hash = prediction_hash(first_predictions[first_predictions["modelName"].isin(v4_models)])
    v4_prediction_hash_matched = actual_v4_oof_hash == EXPECTED["v4OofPredictionSha256"]
    if not v4_prediction_hash_matched:
        raise SystemExit("V4_OOF_PREDICTION_HASH_MISMATCH")
    audits, segments, promoted = promotion_audit(first_results, first_predictions, deterministic, args.bootstrap_iterations)
    status = "OFFLINE_RESEARCH_CHALLENGER_V5" if promoted else "NO_CHALLENGER_FOUND"
    report_root = ROOT / "reports/offline_model_v5"
    report_root.mkdir(parents=True, exist_ok=True)
    inner_manifests = []
    dates = pd.to_datetime(features["date"])
    for fold in outer["folds"]:
        train = features[(dates >= pd.Timestamp(fold["trainStart"])) & (dates <= pd.Timestamp(fold["trainEnd"]))]
        inner_manifests.append({"outerFold": fold["fold"], **build_inner_splits(train, folds=3, validation_days=60)})
    calibration = []
    for name, frame in first_predictions.groupby("modelName"):
        calibration.append({"modelName": name, **calibration_metrics(frame)})
    error_summary = first_errors.assign(
        marginBand=pd.qcut(first_errors["tree_top1_top2_margin"], 4, duplicates="drop"),
        entropyBand=pd.qcut(first_errors["tree_entropy"], 4, duplicates="drop"),
    ).groupby(["fold", "marginBand", "entropyBand", "feature_availability", "top1_agreement"], observed=True).agg(
        races=("race_id", "count"), residualOnlyCorrect=("residualOnlyCorrect", "sum"), treeOnlyCorrect=("treeOnlyCorrect", "sum"),
        residualLogLossImprovementRate=("residualLogLossDelta", lambda values: float((values < 0).mean())),
        meanResidualLogLossDelta=("residualLogLossDelta", "mean"),
    ).reset_index()
    manifest = {**hashes, "v4ResultHash": EXPECTED["v4ResultHash"], "v4OofPredictionSha256": actual_v4_oof_hash,
                "v4PredictionHashMatched": v4_prediction_hash_matched, "v4MetricReproduction": reproduction,
                "v4OofReferenceEvaluatorCommit": "cee2cf161209050e90ec27efa193bc763a29d2d9",
                "v4OofReferenceDerivation": "INDEPENDENT_V4_EVALUATOR_RUN",
                "v4OofComparisonMode": "FIXED_REFERENCE_HASH_COMPARISON",
                "benchmarkMode": "FOLD_RETRAINED_TREE15_CONFIGURATION", "frozenArtifactRole": "LINEAGE_ONLY_NOT_USED_FOR_PAST_FOLD_INFERENCE",
                "outerFoldCount": 5, "innerFoldCount": 3, "candidateFamilies": 2, "candidateSettings": 6,
                "gateFeatures": GATE_FEATURES, "residualFeatures": RESIDUAL_FEATURES, "seed": 42,
                "python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__, "scikitLearn": sklearn.__version__, "scipy": scipy.__version__, "joblib": joblib.__version__,
                "baseGitCommit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip(),
                "workingTreeDirtyAtEvaluation": bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()),
                "commitShaRole": "RESEARCH_PARENT_NOT_PREDICTION_CODE_IDENTITY",
                "sourceCodeSha256": source_code_hash(),
                "productionAdoptionAllowed": False, "prospectiveConnected": False, "networkRequests": 0, "roiCalculated": False}
    write_json(report_root / "data_manifest.json", manifest)
    write_json(report_root / "split_manifest.json", {"outer": outer, "inner": inner_manifests})
    write_csv(first_results, report_root / "fold_results.csv")
    write_csv(weighted_summary(first_results), report_root / "aggregate_results.csv")
    write_csv(audits, report_root / "promotion_audit.csv")
    write_csv(segments, report_root / "segment_analysis.csv")
    write_csv(pd.DataFrame(calibration), report_root / "calibration.csv")
    write_csv(error_summary, report_root / "error_analysis.csv")
    write_json(report_root / "champion_challenger_contract.json", {"champion": "tree_15", "challenger": promoted, "futureComparisonMode": "PARALLEL_SHADOW_DESIGN_ONLY", "currentProspectiveChanged": False, "productionAdoptionAllowed": False})
    write_json(report_root / "candidate_manifest.json", {"candidate": promoted, "candidateType": "STATIC_LOG_PROBABILITY_BLEND" if promoted and promoted.startswith("static_") else "GATED_LOG_PROBABILITY_BLEND" if promoted else None, "configurationOnlyArtifact": True, "fixedHashes": hashes, "predictionCodeCommitSha": None, "researchParentCommitSha": manifest["baseGitCommit"], "sourceCodeSha256": manifest["sourceCodeSha256"], "productionAdoptionAllowed": False, "prospectiveConnected": False})
    final = {"status": status, "champion": "tree_15", "bestCandidate": promoted, "offlineResearchChallengerSelected": promoted is not None,
             "candidateAcceptedForProspective": False, "selectionPeriodConsumed": True,
             "v4PredictionHashMatched": v4_prediction_hash_matched, "v4OofPredictionSha256": actual_v4_oof_hash,
             "v4ResultHash": EXPECTED["v4ResultHash"], "v4MetricReproduction": reproduction, "firstPredictionHash": first_hash, "secondPredictionHash": second_hash,
             "v4OofReferenceEvaluatorCommit": "cee2cf161209050e90ec27efa193bc763a29d2d9",
             "v4OofReferenceDerivation": "INDEPENDENT_V4_EVALUATOR_RUN",
             "v4OofComparisonMode": "FIXED_REFERENCE_HASH_COMPARISON",
             "deterministicRerun": deterministic, "fixedHashes": hashes, "promotionAudits": audits.to_dict("records"),
             "historicalCaptureTimestampVerified": False, "leakageFreeEvidenceComplete": False,
             "productionAdoptionAllowed": False, "prospectiveConnected": False, "roiCalculated": False}
    write_json(report_root / "final_report.json", final)
    summary = weighted_summary(first_results)
    markdown = ["# Offline Model v5 Gated Ensemble", "", f"- Status: `{status}`", "- Champion: `tree_15`", f"- Best candidate: `{promoted or 'NONE'}`",
                "- Existing period is a consumed diagnostic window, not an unused holdout.", "- Frozen tree_15 artifact is lineage-only; fold OOF retrains the exact tree_15 configuration to avoid future leakage.",
                "- Prospective/production integration: none.", "- ROI: not calculated.", "", "## Aggregate metrics", "", summary.to_csv(index=False, lineterminator="\n"),
                "## Remaining risks", "", "- Model-selection bias remains across the consumed diagnostic period.", "- Historical pre-race capture timestamps are unavailable.", "- The 2020-03 through 2023-12 coverage gap remains.", "- Only future prospective data can confirm any accepted candidate."]
    with (report_root / "final_report.md").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(markdown) + "\n")
    print(json.dumps(final, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
