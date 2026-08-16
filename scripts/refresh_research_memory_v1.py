from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_memory_v1.store import (
    append_experiment,
    canonical_json,
    initialize_registry,
    read_experiments,
    validate_research_state,
    verify_registry,
)


MODEL_SHA256 = "a2f11bf69c1b4b7ea47cca847dbe0a46f076f7c08d3361ba9e30b43f12d65da0"
FEATURE_SCHEMA_SHA256 = "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd"
DEFAULT_REPORT_ROOT = ROOT / "reports" / "research_memory_v1"
DEFAULT_REGISTRY = ROOT / "data" / "research" / "research_memory_v1" / "experiment_registry.sqlite3"
DEFAULT_READINESS = ROOT / "reports" / "feature_forward" / "course_start_challenger_readiness.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _experiment_payload(
    experiment_id: str,
    hypothesis: str,
    decision: str,
    reason: str,
    source_report: str,
    result: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    return {
        "experimentId": experiment_id,
        "hypothesis": hypothesis,
        "datasetPeriod": "CONSUMED_DIAGNOSTIC_WINDOW",
        "modelVersion": "tree_15",
        "baseline": {"model": "tree_15", "productionAdoptionAllowed": False},
        "result": result,
        "decision": decision,
        "reason": reason,
        "sourceReports": [source_report],
        "createdAt": now,
    }


def refresh(
    *,
    report_root: Path,
    registry_path: Path,
    model_path: Path,
    feature_order_path: Path,
    readiness_path: Path,
) -> dict[str, Any]:
    if not model_path.is_file() or _file_sha256(model_path) != MODEL_SHA256:
        raise ValueError("tree_15_model_hash_mismatch")
    active_features = _read_json(feature_order_path)
    if not isinstance(active_features, list) or not all(isinstance(value, str) for value in active_features):
        raise ValueError("feature_order_invalid")
    readiness = _read_json(readiness_path) if readiness_path.is_file() else {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    initialize_registry(registry_path)
    historical = [
        _experiment_payload(
            "EXP-OFFLINE-V4",
            "既存featureのranking/residual候補が固定tree_15を改善する",
            "rejected",
            "NO_CHALLENGER_FOUND; consumed diagnostic window only",
            "reports/offline_model_v4/final_report.json",
            {"status": "NO_CHALLENGER_FOUND", "deterministicRerun": True},
            now,
        ),
        _experiment_payload(
            "EXP-OFFLINE-V5",
            "conservative gated residual correctionがtree_15を改善する",
            "rejected",
            "NO_CHALLENGER_FOUND; historical evidence is not unused holdout",
            "reports/offline_model_v5/final_report.json",
            {"status": "NO_CHALLENGER_FOUND", "candidateAcceptedForProspective": False},
            now,
        ),
        _experiment_payload(
            "EXP-OFFLINE-V6",
            "Top-pick selectorがtree_15の選択を改善する",
            "rejected",
            "NO_TOP_PICK_SELECTOR_FOUND; appliedRaceCount is zero",
            "reports/offline_model_v6/final_report.json",
            {"status": "NO_TOP_PICK_SELECTOR_FOUND", "prospectiveConnected": False},
            now,
        ),
    ]
    for payload in historical:
        existing = next(
            (
                row["payload"]
                for row in read_experiments(registry_path)
                if row["payload"].get("experimentId") == payload["experimentId"]
            ),
            None,
        )
        if existing is not None:
            comparable = dict(payload)
            comparable["createdAt"] = existing.get("createdAt")
            if canonical_json(comparable) != canonical_json(existing):
                raise ValueError("experiment_registry_existing_payload_mismatch")
            payload = existing
        append_experiment(registry_path, payload)
    chain = verify_registry(registry_path)

    model_version = {
        "modelId": "tree_15",
        "role": "champion",
        "modelSha256": MODEL_SHA256,
        "featureSchemaSha256": FEATURE_SCHEMA_SHA256,
        "status": "fixed",
        "productionAdoptionAllowed": False,
        "prospectiveConnected": False,
    }
    blocked = list(readiness.get("blockedReasons") or [])
    known_problems = [
        "historical_capture_timestamps_unverified",
        "2020-03_through_2023-12_coverage_gap",
        "v4_to_v6_no_challenger_found",
    ]
    if blocked:
        known_problems.append("course_start_gate_blocked:" + ",".join(sorted(str(value) for value in blocked)))
    state = {
        "schemaVersion": 1,
        "usageMode": "RESEARCH_ONLY",
        "generatedAt": now,
        "productionConnected": False,
        "prospectiveConnected": False,
        "productionAdoptionAllowed": False,
        "currentModelVersion": model_version,
        "activeFeatures": active_features,
        "knownProblems": known_problems,
        "nextHypotheses": [
            {
                "hypothesisId": "HYP-COURSE-START-V1",
                "statement": "forward-only course/start exhibition features add incremental value over frozen tree_15",
                "status": "blocked_until_data_gate",
                "requiredForwardDays": 30,
                "requiredJoinedSettledRaces": 1500,
                "requiredCoverage": 0.8,
                "evaluation": "chronological_5_fold_oof",
            }
        ],
        "experimentRegistry": "data/research/research_memory_v1/experiment_registry.sqlite3",
        "registryIntegrity": chain,
    }
    validate_research_state(state)
    report_root.mkdir(parents=True, exist_ok=True)
    _write_json(report_root / "research_state.json", state)
    _write_json(
        report_root / "model_versions.json",
        {
            "schemaVersion": 1,
            "generatedAt": now,
            "productionAdoptionAllowed": False,
            "models": [model_version],
            "fixedHashes": {
                "modelSha256": MODEL_SHA256,
                "featureSchemaSha256": FEATURE_SCHEMA_SHA256,
            },
        },
    )
    summary = [
        "# Research Memory v1",
        "",
        "- usageMode: `RESEARCH_ONLY`",
        "- productionConnected: `false`",
        "- prospectiveConnected: `false`",
        "- productionAdoptionAllowed: `false`",
        f"- current model: `{model_version['modelId']}` (fixed)",
        f"- active feature count: `{len(active_features)}`",
        f"- experiment registry records: `{chain['experimentCount']}`",
        f"- registry integrity: `{chain['valid']}`",
        "",
        "## Current Problems",
        "",
    ]
    summary.extend(f"- {problem}" for problem in known_problems)
    summary.extend([
        "",
        "## Next Hypothesis",
        "",
        "- HYP-COURSE-START-V1 is blocked until 30 forward days, 1,500 joined settled races, and 80% coverage.",
        "- Research memory is not a prediction source and does not modify tree_15.",
    ])
    (report_root / "daily_summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8", newline="\n")
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh research-only memory without touching prediction or settlement stores.")
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--model", type=Path, required=True, help="Path to the frozen tree_15 artifact")
    parser.add_argument("--feature-order", type=Path, required=True, help="Path to the matching feature_order.json")
    parser.add_argument("--readiness", type=Path, default=DEFAULT_READINESS)
    args = parser.parse_args(argv)
    report_root = args.report_root.resolve()
    if report_root != DEFAULT_REPORT_ROOT.resolve():
        raise ValueError("report_root_not_allowlisted")
    state = refresh(
        report_root=report_root,
        registry_path=args.registry.resolve(),
        model_path=args.model.resolve(),
        feature_order_path=args.feature_order.resolve(),
        readiness_path=args.readiness.resolve(),
    )
    print(json.dumps({"status": "RESEARCH_MEMORY_READY", "experimentCount": state["registryIntegrity"]["experimentCount"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
