from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MONITORING_ROOT = ROOT / "reports" / "monitoring"
MODEL_EVAL_ROOT = ROOT / "reports" / "model_eval"
DEFAULT_CANDIDATE_TRACE = MONITORING_ROOT / "candidate_trace_audit.json"
DEFAULT_SEPARATION = MONITORING_ROOT / "model_policy_separation_audit.json"
DEFAULT_WALK_FORWARD = MODEL_EVAL_ROOT / "architecture_v2_walk_forward_validation.json"
DEFAULT_LIVE_SHADOW = MONITORING_ROOT / "live_shadow_evidence.json"
OUT_JSON = MONITORING_ROOT / "architecture_v2_completion.json"
OUT_MD = MONITORING_ROOT / "architecture_v2_completion.md"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def build_completion_report(
    *,
    candidate_trace_path: Path = DEFAULT_CANDIDATE_TRACE,
    separation_path: Path = DEFAULT_SEPARATION,
    walk_forward_path: Path = DEFAULT_WALK_FORWARD,
    live_shadow_path: Path = DEFAULT_LIVE_SHADOW,
) -> dict[str, Any]:
    paths = {
        "candidate_trace": candidate_trace_path,
        "model_policy_separation": separation_path,
        "walk_forward": walk_forward_path,
        "live_shadow": live_shadow_path,
    }
    payloads = {name: _load_json(path) for name, path in paths.items()}
    missing_artifacts = [str(path) for name, path in paths.items() if not payloads[name]]

    trace = payloads["candidate_trace"]
    trace_counts = trace.get("counts") if isinstance(trace.get("counts"), dict) else {}
    trace_quality = trace.get("quality") if isinstance(trace.get("quality"), dict) else {}
    trace_implementation = bool(trace) and int(trace_counts.get("candidateIdDuplicateCount") or 0) == 0
    trace_evidence = trace_quality.get("classification") == "trace_ready"

    separation = payloads["model_policy_separation"]
    separation_counts = separation.get("counts") if isinstance(separation.get("counts"), dict) else {}
    separation_quality = separation.get("quality") if isinstance(separation.get("quality"), dict) else {}
    separation_implementation = bool(separation) and int(separation_counts.get("reverseDependencyViolationCount") or 0) == 0
    separation_evidence = separation_quality.get("classification") == "separation_ready"

    walk_forward = payloads["walk_forward"]
    walk_counts = walk_forward.get("counts") if isinstance(walk_forward.get("counts"), dict) else {}
    walk_quality = walk_forward.get("quality") if isinstance(walk_forward.get("quality"), dict) else {}
    walk_implementation = bool(walk_forward) and int(walk_counts.get("foldCount") or 0) > 0 and bool(
        walk_quality.get("samePeriodModelComparison")
    ) and not bool(walk_quality.get("futureLeakageDetected"))
    walk_evidence = walk_quality.get("classification") == "validation_ready"

    live = payloads["live_shadow"]
    live_quality = live.get("quality") if isinstance(live.get("quality"), dict) else {}
    live_implementation = bool(live) and "liveShadowReady" in live_quality
    live_evidence = bool(live_quality.get("liveShadowReady"))

    phases = {
        "A_candidate_trace": {
            "implementationComplete": trace_implementation,
            "evidenceReady": trace_evidence,
            "classification": trace_quality.get("classification", "missing"),
            "keyMetrics": {
                "candidateRowsScanned": trace_counts.get("candidateRowsScanned"),
                "candidateIdDuplicateCount": trace_counts.get("candidateIdDuplicateCount"),
                "traceCoverage": trace_counts.get("traceCoverage"),
            },
        },
        "B_model_policy_separation": {
            "implementationComplete": separation_implementation,
            "evidenceReady": separation_evidence,
            "classification": separation_quality.get("classification", "missing"),
            "keyMetrics": separation_counts,
        },
        "C_walk_forward": {
            "implementationComplete": walk_implementation,
            "evidenceReady": walk_evidence,
            "classification": walk_quality.get("classification", "missing"),
            "keyMetrics": {
                "foldCount": walk_counts.get("foldCount"),
                "samePeriodModelComparison": walk_quality.get("samePeriodModelComparison"),
                "futureLeakageDetected": walk_quality.get("futureLeakageDetected"),
                "samePeriodCrossLayerValidation": (walk_forward.get("crossLayer") or {}).get("samePeriodCrossLayerValidation"),
            },
        },
        "D_live_shadow": {
            "implementationComplete": live_implementation,
            "evidenceReady": live_evidence,
            "classification": live_quality.get("classification", "missing"),
            "keyMetrics": live.get("counts", {}),
            "blockers": live.get("blockers", []),
        },
    }
    implementation_complete = all(item["implementationComplete"] for item in phases.values())
    evidence_complete = all(item["evidenceReady"] for item in phases.values())
    if not implementation_complete:
        classification = "architecture_v2_implementation_blocked"
    elif evidence_complete:
        classification = "architecture_v2_evidence_ready"
    else:
        classification = "architecture_v2_implementation_complete_evidence_blocked"

    next_actions: list[str] = []
    if not trace_evidence:
        next_actions.append("persist real modelVersion/policyVersion/oddsCapturedAt and close settlement trace gaps")
    if not separation_evidence:
        next_actions.append("extract calibration from legacy StrategyEvaluator only after behavior-parity tests exist")
    if not walk_evidence:
        next_actions.append("collect a candidate trace period overlapping a fixed model holdout without retuning")
    if not live_evidence:
        next_actions.append("continue live shadow until 60 days and 500 settled candidates with coverage, drift, and concentration evidence")

    return {
        "reportType": "architecture_v2_completion",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "overall": {
            "classification": classification,
            "implementationComplete": implementation_complete,
            "evidenceComplete": evidence_complete,
            "productionAdoptionAllowed": False,
        },
        "phases": phases,
        "missingArtifacts": missing_artifacts,
        "nextActions": next_actions,
        "safety": {
            "buyChanged": False,
            "evChanged": False,
            "votingConnected": False,
            "predictionLogicChanged": False,
            "frozenBetsOverwritten": False,
            "dailyOpsChanged": False,
        },
        "sources": {name: str(path) for name, path in paths.items()},
    }


def _write_outputs(payload: dict[str, Any]) -> None:
    MONITORING_ROOT.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Architecture V2 Completion",
        "",
        f"- classification: {payload['overall']['classification']}",
        f"- implementationComplete: {payload['overall']['implementationComplete']}",
        f"- evidenceComplete: {payload['overall']['evidenceComplete']}",
        f"- productionAdoptionAllowed: {payload['overall']['productionAdoptionAllowed']}",
        "",
        "## Phases",
    ]
    for name, phase in payload["phases"].items():
        lines.append(
            f"- {name}: implementationComplete={phase['implementationComplete']}, "
            f"evidenceReady={phase['evidenceReady']}, classification={phase['classification']}"
        )
    lines.extend(["", "## Next actions"])
    if payload["nextActions"]:
        lines.extend(f"- {item}" for item in payload["nextActions"])
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Safety",
            *[f"- {key}: {value}" for key, value in payload["safety"].items()],
            "",
            "## Interpretation",
            "- Implementation completion means the audits and gates are reproducible.",
            "- Evidence completion requires every A-D evidence gate to pass.",
            "- Evidence blocked never authorizes BUY / EV / voting / production adoption.",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build architecture v2 A-D completion report")
    parser.add_argument("--candidate-trace", type=Path, default=DEFAULT_CANDIDATE_TRACE)
    parser.add_argument("--separation", type=Path, default=DEFAULT_SEPARATION)
    parser.add_argument("--walk-forward", type=Path, default=DEFAULT_WALK_FORWARD)
    parser.add_argument("--live-shadow", type=Path, default=DEFAULT_LIVE_SHADOW)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    payload = build_completion_report(
        candidate_trace_path=args.candidate_trace,
        separation_path=args.separation,
        walk_forward_path=args.walk_forward,
        live_shadow_path=args.live_shadow,
    )
    if not args.dry_run:
        _write_outputs(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
