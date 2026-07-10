from __future__ import annotations

import json
from pathlib import Path

import scripts.build_architecture_v2_completion_report as module


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_completion_report_separates_implementation_from_evidence(tmp_path: Path) -> None:
    trace = tmp_path / "trace.json"
    separation = tmp_path / "separation.json"
    walk_forward = tmp_path / "walk_forward.json"
    live = tmp_path / "live.json"
    _write(
        trace,
        {
            "quality": {"classification": "trace_warning"},
            "counts": {"candidateIdDuplicateCount": 0, "candidateRowsScanned": 450},
            "canonicalMissingCounts": {"modelVersionMissing": 450},
        },
    )
    _write(
        separation,
        {
            "quality": {"classification": "separation_warning"},
            "counts": {"reverseDependencyViolationCount": 0, "legacyCouplingCount": 2},
        },
    )
    _write(
        walk_forward,
        {
            "quality": {
                "classification": "validation_warning",
                "samePeriodModelComparison": True,
                "futureLeakageDetected": False,
            },
            "counts": {"foldCount": 3},
            "crossLayer": {"samePeriodCrossLayerValidation": False},
        },
    )
    _write(
        live,
        {
            "quality": {"classification": "live_shadow_blocked", "liveShadowReady": False},
            "counts": {"observationDays": 7, "settledCandidateCount": 0},
            "blockers": ["settled_candidate_count_below_500"],
        },
    )

    report = module.build_completion_report(
        candidate_trace_path=trace,
        separation_path=separation,
        walk_forward_path=walk_forward,
        live_shadow_path=live,
    )

    assert report["overall"]["implementationComplete"] is True
    assert report["overall"]["evidenceComplete"] is False
    assert report["overall"]["classification"] == "architecture_v2_implementation_complete_evidence_blocked"
    assert report["phases"]["A_candidate_trace"]["implementationComplete"] is True
    assert report["phases"]["B_model_policy_separation"]["implementationComplete"] is True
    assert report["phases"]["C_walk_forward"]["implementationComplete"] is True
    assert report["phases"]["D_live_shadow"]["implementationComplete"] is True
    assert report["safety"]["buyChanged"] is False
    assert report["safety"]["evChanged"] is False
    assert report["safety"]["votingConnected"] is False


def test_completion_report_blocks_when_required_artifact_is_missing(tmp_path: Path) -> None:
    report = module.build_completion_report(
        candidate_trace_path=tmp_path / "missing_trace.json",
        separation_path=tmp_path / "missing_separation.json",
        walk_forward_path=tmp_path / "missing_walk_forward.json",
        live_shadow_path=tmp_path / "missing_live.json",
    )

    assert report["overall"]["implementationComplete"] is False
    assert report["overall"]["classification"] == "architecture_v2_implementation_blocked"
    assert len(report["missingArtifacts"]) == 4
