from __future__ import annotations

import csv
import json
from pathlib import Path

import scripts.build_live_evidence_gate as module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def test_live_evidence_gate_requires_strict_trace_and_pre_deadline_odds(tmp_path: Path) -> None:
    trace_path = tmp_path / "candidate_trace_rows.csv"
    live_path = tmp_path / "live.json"
    tuning_path = tmp_path / "tuning.json"
    _write_csv(
        trace_path,
        [
            {
                "candidateId": "c1",
                "raceDate": "2026-07-01",
                "raceId": "20260701-24-01",
                "raceNo": "1",
                "venueCode": "24",
                "combination": "1-2-3",
                "modelVersion": "baseline_rule_v1",
                "policyVersion": "paper_shadow_policy_v1",
                "predictionHash": "h1",
                "oddsCapturedAt": "2026-07-01T11:55:00",
                "deadlineAt": "2026-07-01T12:00:00",
                "frozenAt": "2026-07-01T11:56:00",
                "settlementStatus": "settled",
                "traceStatus": "complete",
                "hit": "true",
                "payoutAmount": "850",
                "pnl": "750",
            },
            {
                "candidateId": "c2",
                "raceDate": "2026-07-02",
                "raceId": "20260702-24-01",
                "raceNo": "1",
                "venueCode": "24",
                "combination": "1-2-3",
                "modelVersion": "baseline_rule_v1",
                "policyVersion": "paper_shadow_policy_v1",
                "predictionHash": "h2",
                "oddsCapturedAt": "2026-07-02T12:01:00",
                "deadlineAt": "2026-07-02T12:00:00",
                "frozenAt": "2026-07-02T11:56:00",
                "settlementStatus": "settled",
                "traceStatus": "complete",
                "hit": "false",
                "payoutAmount": "0",
                "pnl": "-100",
            },
        ],
    )
    _write_json(live_path, {"summary": {"dateRange": "20260701_20260702", "days": 2}})
    _write_json(tuning_path, {})

    payload = module.build_live_evidence_gate(
        candidate_trace_rows_path=trace_path,
        live_summary_path=live_path,
        tuning_gate_path=tuning_path,
    )

    assert payload["quality"]["productionAdoptionAllowed"] is False
    assert payload["quality"]["classification"] == "live_evidence_blocked"
    assert payload["counts"]["shadowCandidateCount"] == 2
    assert payload["counts"]["strictEligibleCandidateCount"] == 1
    assert payload["metrics"]["traceCoverage"] == 1.0
    assert payload["metrics"]["preDeadlineOddsCoverage"] == 0.5
    assert payload["metrics"]["settlementCoverage"] == 1.0
    assert payload["profitability"]["profit"] == 750.0
    assert payload["profitability"]["roi"] == 7.5
    assert "pre_deadline_odds_coverage_below_0_95" in payload["blockers"]


def test_live_evidence_gate_classifies_zero_strict_candidates(tmp_path: Path) -> None:
    trace_path = tmp_path / "candidate_trace_rows.csv"
    live_path = tmp_path / "live.json"
    tuning_path = tmp_path / "tuning.json"
    _write_csv(
        trace_path,
        [
            {
                "candidateId": "c1",
                "raceDate": "2026-07-01",
                "raceId": "20260701-24-01",
                "raceNo": "1",
                "venueCode": "24",
                "combination": "1-2-3",
                "modelVersion": "legacy_unknown",
                "policyVersion": "legacy_unknown",
                "predictionHash": "",
                "oddsCapturedAt": "",
                "deadlineAt": "2026-07-01T12:00:00",
                "frozenAt": "",
                "settlementStatus": "settled",
                "traceStatus": "complete",
                "hit": "false",
                "payoutAmount": "0",
                "pnl": "-100",
            }
        ],
    )
    _write_json(live_path, {"summary": {"dateRange": "20260701_20260701", "days": 1}})
    _write_json(tuning_path, {})

    payload = module.build_live_evidence_gate(
        candidate_trace_rows_path=trace_path,
        live_summary_path=live_path,
        tuning_gate_path=tuning_path,
    )

    assert payload["counts"]["strictEligibleCandidateCount"] == 0
    assert payload["projection"]["zeroStrictCandidateClassification"] == "missing_metadata"
    assert "strict_candidate_count_zero_missing_metadata" in payload["blockers"]
