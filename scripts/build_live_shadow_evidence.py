from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MONITORING_ROOT = ROOT / "reports" / "monitoring"
DEFAULT_LIVE_SUMMARY = MONITORING_ROOT / "live_operation_summary.json"
DEFAULT_TUNING_GATE = MONITORING_ROOT / "tuning_gate.json"
DEFAULT_CANDIDATE_TRACE = MONITORING_ROOT / "candidate_trace_audit.json"
OUT_JSON = MONITORING_ROOT / "live_shadow_evidence.json"
OUT_CSV = MONITORING_ROOT / "live_shadow_evidence.csv"
OUT_MD = MONITORING_ROOT / "live_shadow_evidence.md"

MIN_OBSERVATION_DAYS = 60
MIN_SETTLED_CANDIDATES = 500
MAX_POSITIVE_PROFIT_SHARE = 0.25


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive_profit_share(rows: list[dict[str, Any]]) -> tuple[bool, float | None, str | None]:
    by_bucket: dict[str, float] = {}
    for index, row in enumerate(rows):
        profit = _float_or_none(row.get("liveProfit", row.get("profit")))
        if profit is None or profit <= 0:
            continue
        bucket = str(row.get("date") or row.get("venue") or index)
        by_bucket[bucket] = by_bucket.get(bucket, 0.0) + profit
    total = sum(by_bucket.values())
    if total <= 0:
        return False, None, None
    max_bucket = max(by_bucket, key=by_bucket.get)
    return True, round(by_bucket[max_bucket] / total, 6), max_bucket


def build_live_shadow_evidence(
    *,
    live_summary_path: Path = DEFAULT_LIVE_SUMMARY,
    tuning_gate_path: Path = DEFAULT_TUNING_GATE,
    candidate_trace_path: Path = DEFAULT_CANDIDATE_TRACE,
) -> dict[str, Any]:
    live_payload = _load_json(live_summary_path)
    tuning_payload = _load_json(tuning_gate_path)
    trace_payload = _load_json(candidate_trace_path)
    summary = live_payload.get("summary") if isinstance(live_payload.get("summary"), dict) else {}
    rows = [row for row in live_payload.get("rows", []) if isinstance(row, dict)]
    trace_counts = trace_payload.get("counts") if isinstance(trace_payload.get("counts"), dict) else {}

    observation_days = _int(summary.get("days"))
    settled_count = _int(summary.get("liveSettledBetCount"))
    unresolved_count = _int(summary.get("liveUnresolvedBetCount"))
    settlement_coverage = _float_or_none(summary.get("liveSettlementCoverage"))
    pre_deadline_coverage = _float_or_none(summary.get("preDeadlineOddsCoverage"))
    live_roi = _float_or_none(summary.get("liveSettledRoi"))
    live_hit_rate = _float_or_none(summary.get("liveHitRate"))
    drift_status = str(summary.get("featureDriftStatus") or "").strip().lower() or None
    minimum_coverage = _float_or_none(tuning_payload.get("minimumLiveSettlementCoverage"))
    if minimum_coverage is None:
        minimum_coverage = 0.5
    trace_complete_rows = _int(trace_counts.get("completeRows"))
    trace_duplicate_count = _int(trace_counts.get("candidateIdDuplicateCount"))
    concentration_available, max_profit_share, max_profit_bucket = _positive_profit_share(rows)

    blockers: list[str] = []
    if observation_days < MIN_OBSERVATION_DAYS:
        blockers.append(f"observation_days_below_{MIN_OBSERVATION_DAYS}")
    if settled_count < MIN_SETTLED_CANDIDATES:
        blockers.append(f"settled_candidate_count_below_{MIN_SETTLED_CANDIDATES}")
    if unresolved_count > 0:
        blockers.append("unresolved_live_candidates_present")
    if settlement_coverage is None:
        blockers.append("settlement_coverage_unavailable")
    elif settlement_coverage < minimum_coverage:
        blockers.append("settlement_coverage_below_gate")
    if pre_deadline_coverage is None:
        blockers.append("pre_deadline_odds_coverage_unavailable")
    elif pre_deadline_coverage < minimum_coverage:
        blockers.append("pre_deadline_odds_coverage_below_gate")
    if live_roi is None:
        blockers.append("profitability_evidence_unavailable")
    if not concentration_available:
        blockers.append("profit_concentration_unavailable")
    elif max_profit_share is not None and max_profit_share > MAX_POSITIVE_PROFIT_SHARE:
        blockers.append("profit_concentration_above_25pct")
    if drift_status is None:
        blockers.append("feature_drift_unavailable")
    elif drift_status != "ok":
        blockers.append("feature_drift_not_ok")
    if trace_duplicate_count > 0:
        blockers.append("candidate_trace_duplicate_present")
    if settled_count > trace_complete_rows:
        blockers.append("candidate_trace_complete_rows_below_settled_count")

    ready = not blockers
    classification = "live_shadow_ready" if ready else "live_shadow_blocked"
    return {
        "reportType": "live_shadow_evidence",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "dateRange": summary.get("dateRange"),
        "thresholds": {
            "minimumObservationDays": MIN_OBSERVATION_DAYS,
            "minimumSettledCandidateCount": MIN_SETTLED_CANDIDATES,
            "minimumSettlementCoverage": minimum_coverage,
            "maximumPositiveProfitShare": MAX_POSITIVE_PROFIT_SHARE,
        },
        "counts": {
            "observationDays": observation_days,
            "settledCandidateCount": settled_count,
            "unresolvedCandidateCount": unresolved_count,
            "candidateTraceCompleteRows": trace_complete_rows,
            "candidateTraceDuplicateCount": trace_duplicate_count,
        },
        "metrics": {
            "settlementCoverage": settlement_coverage,
            "preDeadlineOddsCoverage": pre_deadline_coverage,
            "liveSettledRoi": live_roi,
            "liveHitRate": live_hit_rate,
        },
        "stability": {
            "profitConcentrationAvailable": concentration_available,
            "maxPositiveProfitShare": max_profit_share,
            "maxPositiveProfitBucket": max_profit_bucket,
            "featureDriftStatus": drift_status,
        },
        "quality": {
            "classification": classification,
            "liveShadowReady": ready,
            "paperValidationIsNotLiveProof": True,
            "productionBehaviorChanged": False,
        },
        "blockers": blockers,
        "sources": {
            "liveOperationSummary": str(live_summary_path),
            "tuningGate": str(tuning_gate_path),
            "candidateTrace": str(candidate_trace_path),
        },
    }


def _write_outputs(payload: dict[str, Any]) -> None:
    MONITORING_ROOT.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = []
    for section in ("counts", "metrics", "stability", "quality"):
        for key, value in payload[section].items():
            rows.append({"section": section, "metric": key, "value": value})
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["section", "metric", "value"])
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# Live Shadow Evidence",
        "",
        f"- classification: {payload['quality']['classification']}",
        f"- liveShadowReady: {payload['quality']['liveShadowReady']}",
        f"- dateRange: {payload['dateRange']}",
        f"- observationDays: {payload['counts']['observationDays']}",
        f"- settledCandidateCount: {payload['counts']['settledCandidateCount']}",
        f"- settlementCoverage: {payload['metrics']['settlementCoverage']}",
        f"- preDeadlineOddsCoverage: {payload['metrics']['preDeadlineOddsCoverage']}",
        f"- liveSettledRoi: {payload['metrics']['liveSettledRoi']}",
        f"- maxPositiveProfitShare: {payload['stability']['maxPositiveProfitShare']}",
        f"- featureDriftStatus: {payload['stability']['featureDriftStatus']}",
        "",
        "## Blockers",
    ]
    if payload["blockers"]:
        lines.extend(f"- {blocker}" for blocker in payload["blockers"])
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Interpretation",
            "- paperValidationReady is not accepted as live profitability proof.",
            "- Null coverage, ROI, concentration, or drift evidence remains unavailable.",
            "- BUY / EV / voting / production tuning remains disabled.",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build architecture v2 live shadow evidence gate")
    parser.add_argument("--live-summary", type=Path, default=DEFAULT_LIVE_SUMMARY)
    parser.add_argument("--tuning-gate", type=Path, default=DEFAULT_TUNING_GATE)
    parser.add_argument("--candidate-trace", type=Path, default=DEFAULT_CANDIDATE_TRACE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    payload = build_live_shadow_evidence(
        live_summary_path=args.live_summary,
        tuning_gate_path=args.tuning_gate,
        candidate_trace_path=args.candidate_trace,
    )
    if not args.dry_run:
        _write_outputs(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
