from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import catboost
import scipy
import sklearn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.offline_model_v4.core import STRICT_LAG_FEATURES, build_walk_forward_splits, canonical_config_hash, eligible_challengers, multiclass_metrics, paired_date_block_bootstrap, promotion_passes
from src.offline_model_v4.experiment import audit_dataset, build_gap_reset_features, default_specs, evaluate, experiment_manifest

EXPECTED_CANONICAL = "bc2294f85e482ac1c1e7458236be509afd5d3adc9aa7afd4ec53fc4658e54f23"
EXPECTED_ASOF = "c1ede746393c906e7197d9a461a32fcacb34e508387b6d189d57da20089f3bcb"
EXPECTED_TREE = "a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0"
EXPECTED_FEATURE_SCHEMA = "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def result_hash(results: pd.DataFrame, predictions: pd.DataFrame) -> str:
    result_records = results.sort_values(["fold", "modelName"]).round(14).to_dict("records")
    prediction_digest = pd.util.hash_pandas_object(predictions.sort_values(["fold", "modelName", "race_id", "lane"]).reset_index(drop=True), index=False).to_numpy().tobytes()
    return hashlib.sha256(canonical_config_hash(result_records).encode() + prediction_digest).hexdigest()


def source_code_hash() -> str:
    digest = hashlib.sha256()
    for path in (ROOT / "src/offline_model_v4/core.py", ROOT / "src/offline_model_v4/experiment.py", ROOT / "scripts/run_offline_model_v4.py"):
        digest.update(path.relative_to(ROOT).as_posix().encode()); digest.update(path.read_bytes())
    return digest.hexdigest()


def weighted_summary(results: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, group in results.groupby("modelName"):
        weights = group["raceCount"].to_numpy()
        rows.append({"modelName": name, **{metric: float(np.average(group[metric], weights=weights)) for metric in ("raceLogLoss", "multiclassBrier", "top1Accuracy", "ece10")}, "raceCount": int(weights.sum())})
    return pd.DataFrame(rows).set_index("modelName").sort_values("raceLogLoss")


def segment_analysis(baseline: pd.DataFrame, candidate: pd.DataFrame) -> pd.DataFrame:
    candidate = candidate.copy(); baseline = baseline.copy()
    top_lane = candidate.loc[candidate.groupby("race_id")["predicted_probability"].idxmax()].set_index("race_id")["lane"]
    coverage = candidate.groupby("race_id")["feature_availability_count"].mean()
    segment_maps = {
        "venue": candidate.set_index("race_id")["jcd"].astype(str),
        "month": pd.to_datetime(candidate.set_index("race_id")["date"]).dt.to_period("M").astype(str),
        "topPickLane": top_lane.astype(str),
        "featureCoverage": pd.cut(coverage, [-1, 0, 1, 2, 3], labels=["0", "1", "2", "3"]).astype(str),
    }
    rows = []
    for dimension, mapping in segment_maps.items():
        race_segment = mapping[~mapping.index.duplicated()]
        for segment, race_ids in race_segment.groupby(race_segment).groups.items():
            ids = set(race_ids); cand = candidate[candidate["race_id"].isin(ids)]; base = baseline[baseline["race_id"].isin(ids)]
            races = cand["race_id"].nunique()
            if races < 20:
                continue
            cm = multiclass_metrics(cand); bm = multiclass_metrics(base)
            rows.append({"dimension": dimension, "segment": str(segment), "raceCount": races,
                         "logLossDelta": cm["raceLogLoss"] - bm["raceLogLoss"],
                         "brierDelta": cm["multiclassBrier"] - bm["multiclassBrier"],
                         "top1Delta": cm["top1Accuracy"] - bm["top1Accuracy"],
                         "eceDelta": cm["ece10"] - bm["ece10"]})
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded offline prediction-edge v4 research without network or production writes")
    parser.add_argument("--source-root", type=Path, default=Path(r"C:\Users\goo10\競艇-recovery\boatrace-ai-clean"))
    parser.add_argument("--bootstrap-iterations", type=int, default=1000)
    parser.add_argument("--verify-determinism", action="store_true", help="Retained for compatibility; verification is always performed")
    args = parser.parse_args()
    source = args.source_root.resolve(); source_data = source / "data/offline_model_v3"
    canonical_path = source_data / "canonical_race_results.csv"; asof_path = source_data / "asof_features.csv"
    tree_path = source / "data/commercialization_v1/frozen_candidate/tree_15.joblib"
    hashes = {"canonicalDatasetSha256": sha256_file(canonical_path), "asofArtifactSha256": sha256_file(asof_path), "tree15ArtifactSha256": sha256_file(tree_path)}
    expected = {"canonicalDatasetSha256": EXPECTED_CANONICAL, "asofArtifactSha256": EXPECTED_ASOF, "tree15ArtifactSha256": EXPECTED_TREE}
    if hashes != expected:
        raise SystemExit("INPUT_HASH_MISMATCH")
    feature_schema_hash = canonical_config_hash(STRICT_LAG_FEATURES)
    if feature_schema_hash != EXPECTED_FEATURE_SCHEMA:
        raise SystemExit("FEATURE_SCHEMA_HASH_MISMATCH")
    canonical = pd.read_csv(canonical_path, low_memory=False); features = pd.read_csv(asof_path, low_memory=False)
    audit = audit_dataset(features); split = build_walk_forward_splits(features, folds=5, validation_days=60)
    specs = default_specs(); results, predictions = evaluate(features, split, specs)
    first_hash = result_hash(results, predictions)
    repeat_results, repeat_predictions = evaluate(features, split, specs)
    second_hash = result_hash(repeat_results, repeat_predictions)
    deterministic = first_hash == second_hash
    if not deterministic:
        raise SystemExit("DETERMINISTIC_RERUN_FAILED")
    candidate_names = {spec.name for spec in specs}
    fold_eligible = eligible_challengers(results, baseline="tree_15", candidate_names=candidate_names)
    baseline_predictions = predictions[predictions["modelName"] == "tree_15"].copy()
    ci_rows = []
    for name in sorted(set(predictions["modelName"]) - {"tree_15", "lane1_always", "lane_frequency"}):
        candidate_frame = predictions[predictions["modelName"] == name]
        ci_rows.append({"modelName": name, **paired_date_block_bootstrap(baseline_predictions, candidate_frame, iterations=args.bootstrap_iterations, seed=42)})
    ci_table = pd.DataFrame(ci_rows)
    weighted = weighted_summary(results)
    best_research = weighted.drop(index=[name for name in ("tree_15", "lane1_always", "lane_frequency") if name in weighted.index]).index[0]
    segment_tables = []
    for name in sorted(set(predictions["modelName"]) - {"tree_15", "lane1_always", "lane_frequency"}):
        table = segment_analysis(baseline_predictions, predictions[predictions["modelName"] == name]); table.insert(0, "modelName", name); segment_tables.append(table)
    segments = pd.concat(segment_tables, ignore_index=True) if segment_tables else pd.DataFrame()
    reset_summary: dict[str, object]
    promoted = None
    reset = build_gap_reset_features(canonical, reset_date="2024-01-01")
    reset_split = build_walk_forward_splits(reset, folds=5, validation_days=60)
    reset_specs = [next(spec for spec in specs if spec.name == name) for name in fold_eligible]
    reset_results, _ = evaluate(reset, reset_split, reset_specs)
    reset_lane = reset_results[reset_results["modelName"] == "lane_frequency"].set_index("fold")
    reset_tree = reset_results[reset_results["modelName"] == "tree_15"].set_index("fold")
    reset_joined = reset_tree.join(reset_lane[["raceLogLoss", "multiclassBrier"]], rsuffix="Baseline")
    reset_summary = {"status": "PASS" if int((reset_joined["raceLogLoss"] < reset_joined["raceLogLossBaseline"]).sum()) >= 4 and int((reset_joined["multiclassBrier"] < reset_joined["multiclassBrierBaseline"]).sum()) >= 4 else "FAIL", "tree15VsLaneFrequencyLogLossImprovedFolds": int((reset_joined["raceLogLoss"] < reset_joined["raceLogLossBaseline"]).sum()), "tree15VsLaneFrequencyBrierImprovedFolds": int((reset_joined["multiclassBrier"] < reset_joined["multiclassBrierBaseline"]).sum())}
    candidate_gate_audits = []
    reset_base = reset_results[reset_results["modelName"] == "tree_15"].set_index("fold")
    for name in fold_eligible:
        ci = next(row for row in ci_rows if row["modelName"] == name)
        reset_candidate = reset_results[reset_results["modelName"] == name].set_index("fold")
        joined = reset_candidate.join(reset_base[["raceLogLoss", "multiclassBrier"]], rsuffix="Baseline")
        reset_log_wins = int((joined["raceLogLoss"] < joined["raceLogLossBaseline"]).sum()); reset_brier_wins = int((joined["multiclassBrier"] < joined["multiclassBrierBaseline"]).sum())
        candidate_segments = segments[segments["modelName"] == name]
        checked = candidate_segments[candidate_segments["raceCount"] >= 100]
        top_lane_segments = checked[checked["dimension"] == "topPickLane"]
        segment_pass = not checked.empty and top_lane_segments["segment"].nunique() >= 2 and float(checked["logLossDelta"].max()) <= 0.01
        ci_pass = ci["logLossCi95Upper"] < 0 and ci["brierCi95Upper"] < 0
        gap_reset_pass = reset_log_wins >= 4 and reset_brier_wins >= 4
        passed = promotion_passes(deterministic=deterministic, ci_pass=ci_pass, segment_pass=segment_pass, gap_reset_pass=gap_reset_pass)
        candidate_gate_audits.append({"modelName": name, "deterministicRerunPassed": deterministic, "ciPassed": ci_pass, "segmentPassed": segment_pass, "gapResetPassed": gap_reset_pass, "passed": passed})
        if passed and promoted is None:
            promoted = name
    reset_summary["challengerAudits"] = candidate_gate_audits
    report_out = ROOT / "reports/offline_model_v4"; data_out = ROOT / "data/offline_model_v4"
    report_out.mkdir(parents=True, exist_ok=True); data_out.mkdir(parents=True, exist_ok=True)
    manifest = {**hashes, "sourcePaths": {"canonical": str(canonical_path), "asof": str(asof_path), "tree15": str(tree_path)},
                "rowCount": len(features), "raceCount": int(features["race_id"].nunique()), "featureSchemaSha256": feature_schema_hash,
                "python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__, "joblib": joblib.__version__,
                "scikitLearn": sklearn.__version__, "catBoost": catboost.__version__, "scipy": scipy.__version__,
                "baseGitCommit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip(),
                "sourceCodeSha256": source_code_hash(),
                "productionAdoptionAllowed": False, "prospectiveConnected": False, "networkRequests": 0}
    write_json(report_out / "data_manifest.json", manifest); write_json(report_out / "split_manifest.json", split)
    pd.DataFrame([{"feature": feature, "timing": "STRICT_LAG_PRIOR_DATE", "modelInputAllowed": True} for feature in STRICT_LAG_FEATURES] +
                 [{"feature": feature, "timing": "POST_RACE_OR_UNKNOWN", "modelInputAllowed": False} for feature in audit["excludedPostRaceColumns"]]).to_csv(report_out / "feature_timing_audit.csv", index=False)
    results.assign(experimentId=results.apply(lambda row: canonical_config_hash({"fold": row["fold"], "model": row["modelName"]})[:16], axis=1), datasetHash=hashes["canonicalDatasetSha256"], configHash=canonical_config_hash(experiment_manifest(specs)), seed=42).to_csv(report_out / "walk_forward_results.csv", index=False)
    results.to_csv(report_out / "experiment_registry.csv", index=False); segments.to_csv(report_out / "segment_analysis.csv", index=False)
    ci_table.to_csv(report_out / "candidate_bootstrap_ci.csv", index=False)
    write_json(report_out / "bootstrap_ci.json", ci_rows); write_json(report_out / "gap_reset_sensitivity.json", reset_summary)
    audit_lines = ["# Data Quality Audit", "", f"- rows/races: {audit['rowCount']} / {audit['raceCount']}", f"- duplicate race+lane: {audit['duplicateRaceLaneCount']}", f"- invalid races: {audit['invalidRaceCount']}", f"- venues: {audit['venueCount']}", f"- period: {audit['minimumDate']}..{audit['maximumDate']}", f"- coverage gap: {audit['coverageGap']}", "- historical pre-race capture timestamp: unavailable", "- complete scheduled-race denominator: unavailable", "- status: CANONICAL_DATA_PARTIAL"]
    (report_out / "data_quality_report.md").write_text("\n".join(audit_lines) + "\n", encoding="utf-8")
    status = "OFFLINE_RESEARCH_CHALLENGER" if promoted else "NO_CHALLENGER_FOUND"
    contract = {"champion": "tree_15", "challenger": promoted, "comparisonMode": "FUTURE_PROSPECTIVE_PARALLEL_ONLY", "publicAnchorChanged": False, "predictionPackageChanged": False, "stageAChanged": False, "productionAdoptionAllowed": False}
    write_json(report_out / "champion_challenger_contract.json", contract)
    final = {"status": status, "challenger": promoted, "foldEligibleCandidates": fold_eligible, "bestResearchAlternative": best_research, "deterministicRerun": deterministic, "firstResultHash": first_hash, "secondResultHash": second_hash, "resultHash": first_hash, "inputHashes": hashes, "candidateBootstrap": ci_rows, "gapResetSensitivity": reset_summary, "productionAdoptionAllowed": False, "prospectiveConnected": False, "roiCalculated": False}
    write_json(report_out / "final_report.json", final)
    summary = weighted
    report = ["# Offline Model v4 Final Report", "", f"- Status: `{status}`", f"- Challenger: `{promoted or 'NONE'}`", "- Evaluation: `RESEARCH_WALK_FORWARD`", "- Existing period is not an unused holdout.", "- ROI was not calculated.", "- Production/prospective integration: none.", "", "## Mean Metrics", "", summary.to_csv(), "## Gap Reset", "", json.dumps(reset_summary, ensure_ascii=False), "", "## Remaining Risks", "", "- Historical pre-race capture timestamps unavailable.", "- Complete scheduled-race denominator unavailable.", "- 2020-03 through 2023-12 coverage gap.", "- Model-selection bias remains; only future prospective data can confirm the challenger."]
    (report_out / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
