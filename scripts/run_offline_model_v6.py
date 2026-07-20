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
import sklearn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.offline_model_v4.core import (
    STRICT_LAG_FEATURES,
    build_walk_forward_splits,
    canonical_config_hash,
    multiclass_metrics,
)
from src.offline_model_v4.experiment import audit_dataset
from src.offline_model_v6.core import (
    SELECTOR_CONFIGS,
    SELECTOR_FEATURES,
    choose_oracle_scope,
    paired_date_bootstrap,
)
from src.offline_model_v6.experiment import evaluate_v6, prediction_hash, probability_hash

EXPECTED = {
    "canonicalDatasetSha256": "bc2294f85e482ac1c1e7458236be509afd5d3adc9aa7afd4ec53fc4658e54f23",
    "asofArtifactSha256": "c1ede746393c906e7197d9a461a32fcacb34e508387b6d189d57da20089f3bcb",
    "tree15ArtifactSha256": "a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0",
    "featureSchemaSha256": "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd",
    "v5Commit": "224d38c37bdbbb54f9b429d419ab9335e1f139d7",
    "raceLogLoss": 1.2433071678376793,
    "multiclassBrier": 0.6050476943773007,
    "top1Accuracy": 0.5580636674559326,
    "tree15OofProbabilitySha256": "2b5fe69564d474a4ebc4554959f6741b963649e3c3dda8fbb7f9f1ce5255750e",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_frame_hash(frame: pd.DataFrame, columns: list[str]) -> str:
    ordered = frame[columns].sort_values(columns[:3]).reset_index(drop=True)
    payload = ordered.to_csv(index=False, lineterminator="\n").encode()
    return hashlib.sha256(payload).hexdigest()


def source_code_hash() -> str:
    digest = hashlib.sha256()
    for path in (
        ROOT / "src/offline_model_v4/core.py",
        ROOT / "src/offline_model_v4/experiment.py",
        ROOT / "src/offline_model_v5/core.py",
        ROOT / "src/offline_model_v6/core.py",
        ROOT / "src/offline_model_v6/experiment.py",
        ROOT / "scripts/run_offline_model_v6.py",
    ):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        handle.write("\n")


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")


def oracle_summary(races: pd.DataFrame) -> dict[str, float | int]:
    rank = races["winnerRank"].to_numpy(int)
    top1 = float((rank <= 1).mean())
    top2 = float((rank <= 2).mean())
    top3 = float((rank <= 3).mean())
    return {
        "raceCount": len(races),
        "top1Accuracy": top1,
        "winnerInTop2": top2,
        "winnerInTop3": top3,
        "top2OracleGain": top2 - top1,
        "top3OracleGain": top3 - top1,
        "treeWrongWinnerRank2": int((rank == 2).sum()),
        "treeWrongWinnerRank3": int((rank == 3).sum()),
        "treeWrongWinnerRank4Plus": int((rank >= 4).sum()),
    }


def oracle_segments(races: pd.DataFrame) -> pd.DataFrame:
    work = races.copy()
    work["month"] = pd.to_datetime(work["date"]).dt.to_period("M").astype(str)
    work["marginBand"] = pd.qcut(work["margin12"], 4, duplicates="drop").astype(str)
    work["entropyBand"] = pd.qcut(work["entropy"], 4, duplicates="drop").astype(str)
    work["coverageBand"] = pd.cut(
        work["feature_availability_count"], [-np.inf, 1, 2, np.inf], labels=["low", "mid", "high"]
    ).astype(str)
    work["missingnessBand"] = pd.cut(
        work["missingness_count"], [-np.inf, 0, 2, np.inf], labels=["none", "some", "high"]
    ).astype(str)
    rows = []
    for dimension in (
        "marginBand",
        "entropyBand",
        "lane1",
        "jcd",
        "race_no",
        "month",
        "coverageBand",
        "missingnessBand",
    ):
        for value, group in work.groupby(dimension, dropna=False):
            rank = group["winnerRank"]
            rows.append(
                {
                    "dimension": dimension,
                    "segment": str(value),
                    "races": len(group),
                    "top1Accuracy": float((rank <= 1).mean()),
                    "winnerInTop2": float((rank <= 2).mean()),
                    "winnerInTop3": float((rank <= 3).mean()),
                    "top2OracleGain": float((rank == 2).mean()),
                    "top3AdditionalGain": float((rank == 3).mean()),
                }
            )
    return pd.DataFrame(rows)


def selector_segments(races: pd.DataFrame) -> pd.DataFrame:
    work = races.copy()
    work["month"] = pd.to_datetime(work["date"]).dt.to_period("M").astype(str)
    work["predictedLane"] = work["lane1"].astype(str)
    work["missingnessBand"] = pd.cut(
        work["missingness_count"], [-np.inf, 0, 2, np.inf], labels=["none", "some", "high"]
    ).astype(str)
    rows = []
    for dimension in ("jcd", "month", "predictedLane", "race_no", "missingnessBand"):
        for value, group in work.groupby(dimension, dropna=False):
            delta = group["selectorCorrect"].astype(int) - group["baselineCorrect"].astype(int)
            rows.append(
                {
                    "dimension": dimension,
                    "segment": str(value),
                    "races": len(group),
                    "appliedRaces": int(group["selectorApplied"].sum()),
                    "coverage": float(group["selectorApplied"].mean()),
                    "baselineAccuracy": float(group["baselineCorrect"].mean()),
                    "selectorAccuracy": float(group["selectorCorrect"].mean()),
                    "accuracyDelta": float(delta.mean()),
                    "netAdditionalCorrect": int(delta.sum()),
                }
            )
    return pd.DataFrame(rows)


def promotion_audit(
    fold_results: pd.DataFrame,
    races: pd.DataFrame,
    segments: pd.DataFrame,
    bootstrap: dict[str, float | int],
    deterministic: bool,
) -> dict[str, object]:
    delta = float((races["selectorCorrect"].astype(int) - races["baselineCorrect"].astype(int)).mean())
    improved_folds = int((fold_results["accuracyDelta"] > 0).sum())
    coverage_by_fold = fold_results["coverage"].to_numpy(float)
    coverage_passed = bool(((coverage_by_fold >= 0.02) & (coverage_by_fold <= 0.20)).all())
    applied_count = int(races["selectorApplied"].sum())
    eligible_segments = segments[segments["races"] >= 300]
    segment_passed = bool(not eligible_segments.empty and (eligible_segments["accuracyDelta"] >= -0.01).all())
    applied = races[races["selectorApplied"]]
    venue_concentration = float(applied["jcd"].value_counts(normalize=True).max()) if len(applied) else 1.0
    month_concentration = float(pd.to_datetime(applied["date"]).dt.to_period("M").value_counts(normalize=True).max()) if len(applied) else 1.0
    concentration_passed = venue_concentration <= 0.25 and month_concentration <= 0.35
    passed = all(
        (
            delta >= 0.002,
            float(bootstrap["ci95Lower"]) > 0,
            improved_folds >= 4,
            coverage_passed,
            applied_count >= 300,
            segment_passed,
            concentration_passed,
            deterministic,
        )
    )
    return {
        "accuracyDelta": delta,
        "ci95Lower": bootstrap["ci95Lower"],
        "ci95Upper": bootstrap["ci95Upper"],
        "improvedFolds": improved_folds,
        "coveragePassed": coverage_passed,
        "appliedRaceCount": applied_count,
        "segmentPassed": segment_passed,
        "venueConcentration": venue_concentration,
        "monthConcentration": month_concentration,
        "concentrationPassed": concentration_passed,
        "deterministic": deterministic,
        "passed": passed,
    }


def feature_contract() -> pd.DataFrame:
    rows = []
    for feature in SELECTOR_FEATURES:
        source = "tree_15_oof_probability" if feature.startswith(("p", "margin", "entropy")) else "strict_lag_asof_feature"
        rows.append(
            {
                "feature": feature,
                "source": source,
                "cutoff": "PRE_RACE_ASOF_CONTRACT",
                "dtype": "float64",
                "missingRule": "train_only_median_imputation",
                "resultDerived": False,
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate bounded offline Top-pick selector research")
    parser.add_argument("--source-root", type=Path, default=Path(r"C:\Users\goo10\競艇-recovery\boatrace-ai-clean"))
    parser.add_argument("--bootstrap-iterations", type=int, default=2000)
    args = parser.parse_args()
    source = args.source_root.resolve()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", EXPECTED["v5Commit"], "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if ancestor.returncode != 0:
        raise SystemExit("V6_RESEARCH_BASE_COMMIT_MISMATCH")
    canonical_path = source / "data/offline_model_v3/canonical_race_results.csv"
    asof_path = source / "data/offline_model_v3/asof_features.csv"
    tree_path = source / "data/commercialization_v1/frozen_candidate/tree_15.joblib"
    hashes = {
        "canonicalDatasetSha256": sha256_file(canonical_path),
        "asofArtifactSha256": sha256_file(asof_path),
        "tree15ArtifactSha256": sha256_file(tree_path),
        "featureSchemaSha256": canonical_config_hash(STRICT_LAG_FEATURES),
    }
    if hashes != {key: EXPECTED[key] for key in hashes}:
        raise SystemExit("V6_FIXED_HASH_MISMATCH")
    features = pd.read_csv(asof_path, low_memory=False)
    audit_dataset(features)
    outer = build_walk_forward_splits(features, folds=5, validation_days=60)
    scope = 3
    first = evaluate_v6(features, outer, scope=scope)
    second = evaluate_v6(features, outer, scope=scope)
    fold_results, races, boats, selection, inner = first
    deterministic = (
        prediction_hash(boats) == prediction_hash(second[2])
        and probability_hash(boats) == probability_hash(second[2])
        and fold_results.equals(second[0])
        and races.equals(second[1])
        and selection.equals(second[3])
    )
    if not deterministic:
        raise SystemExit("V6_DETERMINISTIC_RERUN_FAILED")
    oracle = oracle_summary(races)
    selected_scope = choose_oracle_scope(
        top1=float(oracle["top1Accuracy"]),
        top2=float(oracle["winnerInTop2"]),
        top3=float(oracle["winnerInTop3"]),
    )
    if selected_scope != scope:
        raise SystemExit("V6_ORACLE_SCOPE_MISMATCH")
    probability_metrics = multiclass_metrics(boats)
    tree_probability_hash = probability_hash(boats)
    if tree_probability_hash != EXPECTED["tree15OofProbabilitySha256"]:
        raise SystemExit("V6_TREE15_OOF_PROBABILITY_HASH_MISMATCH")
    expected_probability_metrics = {
        key: EXPECTED[key] for key in ("raceLogLoss", "multiclassBrier", "top1Accuracy")
    }
    if any(
        not np.isclose(float(probability_metrics[key]), float(value), rtol=0.0, atol=1e-12)
        for key, value in expected_probability_metrics.items()
    ):
        raise SystemExit("V6_TREE15_OOF_METRICS_MISMATCH")
    oracle_segment_frame = oracle_segments(races)
    selector_segment_frame = selector_segments(races)
    bootstrap = paired_date_bootstrap(races, iterations=args.bootstrap_iterations, seed=42)
    promotion = promotion_audit(fold_results, races, selector_segment_frame, bootstrap, deterministic)
    status = "OFFLINE_TOP_PICK_CHALLENGER" if promotion["passed"] else "NO_TOP_PICK_SELECTOR_FOUND"
    report_root = ROOT / "reports/offline_model_v6"
    manifest = {
        **hashes,
        "sourceCodeSha256": source_code_hash(),
        "researchParentCommitSha": head,
        "selectorDatasetSha256": canonical_frame_hash(races, ["fold", "race_id", "date", "predictionTrainingEnd", "sourceFold", "winnerRank", "selectorLabel", *SELECTOR_FEATURES]),
        "predictionCutoffVerified": bool((pd.to_datetime(races["predictionTrainingEnd"]) < pd.to_datetime(races["date"])).all()),
        "v5ReferenceCommit": EXPECTED["v5Commit"],
        "tree15OofProbabilitySha256": tree_probability_hash,
        "outerFoldCount": 5,
        "innerFoldCount": 3,
        "selectorScope": scope,
        "selectorFamilies": 2,
        "selectorSettings": len(SELECTOR_CONFIGS),
        "prospectiveDataUsed": False,
        "productionAdoptionAllowed": False,
        "prospectiveConnected": False,
        "probabilityVectorChanged": False,
        "networkRequests": 0,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scikitLearn": sklearn.__version__,
        "joblib": joblib.__version__,
    }
    write_json(report_root / "data_manifest.json", manifest)
    write_json(report_root / "split_manifest.json", {"outer": outer, "inner": inner})
    write_json(report_root / "oracle_summary.json", oracle)
    write_csv(oracle_segment_frame, report_root / "oracle_segments.csv")
    write_csv(feature_contract(), report_root / "selector_feature_contract.csv")
    write_csv(fold_results, report_root / "fold_results.csv")
    write_csv(selection, report_root / "inner_config_selection.csv")
    write_csv(selector_segment_frame, report_root / "segment_analysis.csv")
    write_json(report_root / "candidate_manifest.json", {
        "status": status,
        "resultReason": "FIXED_EIGHT_SETTINGS_FAILED_COVERAGE_OR_PROMOTION_CONTRACT",
        "candidate": "top_pick_selector" if promotion["passed"] else None,
        "probabilityModel": "tree_15",
        "probabilityVectorChanged": False,
        "sourceCodeSha256": manifest["sourceCodeSha256"],
        "productionAdoptionAllowed": False,
        "prospectiveConnected": False,
    })
    final = {
        "status": status,
        "champion": "tree_15",
        "oracle": oracle,
        "selectedScope": scope,
        "probabilityMetrics": probability_metrics,
        "probabilityMetricsUnchanged": True,
        "probabilityHash": tree_probability_hash,
        "predictionHash": prediction_hash(boats),
        "deterministicRerun": deterministic,
        "promotionAudit": promotion,
        "fixedHashes": hashes,
        "historicalCaptureTimestampVerified": False,
        "leakageFreeEvidenceComplete": False,
        "prospectiveDataUsed": False,
        "productionAdoptionAllowed": False,
        "prospectiveConnected": False,
        "roiCalculated": False,
        "conclusionLimit": "FIXED_EIGHT_SETTINGS_FAILED_COVERAGE_OR_PROMOTION_CONTRACT",
        "scopeSelectionEvidence": "CONSUMED_CHRONOLOGICAL_OOF_ORACLE_DIAGNOSTIC",
    }
    write_json(report_root / "final_report.json", final)
    with (report_root / "oracle_error_analysis.md").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "# Oracle Error Analysis\n\n"
            f"- Races: {oracle['raceCount']}\n"
            f"- Top-1: {oracle['top1Accuracy']:.6f}\n"
            f"- Winner in Top-2: {oracle['winnerInTop2']:.6f}\n"
            f"- Winner in Top-3: {oracle['winnerInTop3']:.6f}\n"
            f"- Top-2 oracle gain: {oracle['top2OracleGain']:.6f}\n"
            f"- Top-3 oracle gain: {oracle['top3OracleGain']:.6f}\n"
            f"- Selected scope: Top-{scope}\n"
        )
    with (report_root / "final_report.md").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "# Offline Model v6 Top-pick Selector\n\n"
            f"- Status: `{status}`\n"
            "- Probability model: `tree_15` (unchanged)\n"
            f"- Selector scope: Top-{scope}\n"
            f"- Accuracy delta: {promotion['accuracyDelta']:.8f}\n"
            f"- 95% CI: [{promotion['ci95Lower']:.8f}, {promotion['ci95Upper']:.8f}]\n"
            f"- Applied races: {promotion['appliedRaceCount']}\n"
            f"- Improved folds: {promotion['improvedFolds']}/5\n"
            "- Conclusion limit: the fixed eight settings failed the coverage or promotion contract; this does not prove that no selector can exist.\n"
            "- Scope selection used the consumed chronological OOF oracle diagnostic and is not independent holdout evidence.\n"
            "- static_a10: probability research benchmark only; not a Top-pick challenger.\n"
            "- Historical capture timestamps remain unverified.\n"
            "- Prospective/production integration: none.\n"
        )
    print(json.dumps(final, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
