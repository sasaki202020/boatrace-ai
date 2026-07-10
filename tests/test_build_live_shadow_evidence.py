from __future__ import annotations

import json
from pathlib import Path

import scripts.build_live_shadow_evidence as module


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_live_shadow_evidence_is_ready_only_with_complete_stable_evidence(tmp_path: Path) -> None:
    live_path = tmp_path / "live.json"
    gate_path = tmp_path / "gate.json"
    trace_path = tmp_path / "trace.json"
    _write(
        live_path,
        {
            "summary": {
                "dateRange": "20260101_20260301",
                "days": 60,
                "liveSettledBetCount": 600,
                "liveUnresolvedBetCount": 0,
                "liveSettlementCoverage": 0.95,
                "preDeadlineOddsCoverage": 0.99,
                "liveSettledRoi": 0.08,
                "liveHitRate": 0.12,
                "featureDriftStatus": "ok",
            },
            "rows": [
                {"date": f"2026010{index}", "venue": f"0{index}", "liveProfit": 100.0}
                for index in range(1, 6)
            ],
        },
    )
    _write(gate_path, {"minimumLiveSettlementCoverage": 0.5})
    _write(trace_path, {"counts": {"candidateIdDuplicateCount": 0, "completeRows": 600}})

    report = module.build_live_shadow_evidence(
        live_summary_path=live_path,
        tuning_gate_path=gate_path,
        candidate_trace_path=trace_path,
    )

    assert report["quality"]["classification"] == "live_shadow_ready"
    assert report["quality"]["liveShadowReady"] is True
    assert report["blockers"] == []
    assert report["stability"]["maxPositiveProfitShare"] == 0.2


def test_live_shadow_evidence_blocks_zero_settlement_and_unknown_metrics(tmp_path: Path) -> None:
    live_path = tmp_path / "live.json"
    gate_path = tmp_path / "gate.json"
    trace_path = tmp_path / "trace.json"
    _write(
        live_path,
        {
            "summary": {
                "dateRange": "20260703_20260709",
                "days": 7,
                "liveSettledBetCount": 0,
                "liveSettlementCoverage": None,
                "liveSettledRoi": None,
            },
            "rows": [],
        },
    )
    _write(gate_path, {"minimumLiveSettlementCoverage": 0.5})
    _write(trace_path, {"counts": {"candidateIdDuplicateCount": 0, "completeRows": 378}})

    report = module.build_live_shadow_evidence(
        live_summary_path=live_path,
        tuning_gate_path=gate_path,
        candidate_trace_path=trace_path,
    )

    assert report["quality"]["classification"] == "live_shadow_blocked"
    assert report["quality"]["liveShadowReady"] is False
    assert "observation_days_below_60" in report["blockers"]
    assert "settled_candidate_count_below_500" in report["blockers"]
    assert "settlement_coverage_unavailable" in report["blockers"]
    assert "pre_deadline_odds_coverage_unavailable" in report["blockers"]
    assert "profitability_evidence_unavailable" in report["blockers"]
    assert "feature_drift_unavailable" in report["blockers"]
