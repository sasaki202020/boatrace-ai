from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MONITORING_ROOT = ROOT / "reports" / "monitoring"
DEFAULT_TRACE_ROWS = MONITORING_ROOT / "candidate_trace_rows.csv"
DEFAULT_LIVE_SUMMARY = MONITORING_ROOT / "live_operation_summary.json"
DEFAULT_TUNING_GATE = MONITORING_ROOT / "tuning_gate.json"
OUT_JSON = MONITORING_ROOT / "live_evidence_gate.json"
OUT_CSV = MONITORING_ROOT / "live_evidence_gate.csv"
OUT_MD = MONITORING_ROOT / "live_evidence_gate.md"

MIN_OBSERVATION_DAYS = 60
MIN_SETTLED_CANDIDATES = 500
MIN_TRACE_COVERAGE = 0.95
MIN_PRE_DEADLINE_COVERAGE = 0.95
MIN_SETTLEMENT_COVERAGE = 0.98
MAX_POSITIVE_PROFIT_SHARE = 0.25
STAKE_PER_CANDIDATE = 100.0
LEGACY_UNKNOWN = "legacy_unknown"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except Exception:
        return []


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "ok", "available", "settled"}


def _known(value: Any) -> bool:
    token = str(value or "").strip()
    return bool(token and token != LEGACY_UNKNOWN)


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def _dt(value: Any) -> datetime | None:
    token = str(value or "").strip()
    if not token or token == LEGACY_UNKNOWN:
        return None
    try:
        return datetime.fromisoformat(token.replace("Z", "+00:00"))
    except Exception:
        return None


def _date_from_row(row: dict[str, Any]) -> str:
    token = str(row.get("raceDate") or row.get("date") or "").strip()
    if len(token) == 8 and token.isdigit():
        return f"{token[:4]}-{token[4:6]}-{token[6:8]}"
    return token


def _race_no_band(value: Any) -> str:
    race_no = _int(value)
    if race_no <= 0:
        return "unknown"
    if race_no <= 4:
        return "R01-R04"
    if race_no <= 8:
        return "R05-R08"
    return "R09-R12"


def _is_settled(row: dict[str, Any]) -> bool:
    status = str(row.get("settlementStatus") or row.get("resultStatus") or "").strip().lower()
    if status in {"settled", "hit", "miss", "available", "ok"}:
        return True
    return _truthy(row.get("resultAvailable")) and bool(str(row.get("traceStatus") or "") == "complete")


def _is_pre_deadline(row: dict[str, Any]) -> bool:
    odds_at = _dt(row.get("oddsCapturedAt"))
    deadline_at = _dt(row.get("deadlineAt"))
    return bool(odds_at and deadline_at and odds_at < deadline_at)


def _metadata_ready(row: dict[str, Any]) -> bool:
    return all(
        _known(row.get(field))
        for field in ("candidateId", "modelVersion", "policyVersion", "predictionHash", "frozenAt")
    )


def _strict_reason(row: dict[str, Any], seen: set[str]) -> str:
    candidate_id = str(row.get("candidateId") or "").strip()
    if not candidate_id:
        return "missing_candidate_id"
    if candidate_id in seen:
        return "duplicate_candidate_id"
    if not _metadata_ready(row):
        return "missing_metadata"
    if not _is_pre_deadline(row):
        return "missing_pre_deadline_odds"
    if not _is_settled(row):
        return "settlement_waiting"
    return "strict_eligible"


def _profit(row: dict[str, Any]) -> float:
    pnl = _float(row.get("pnl"))
    if pnl is not None:
        return pnl
    payout = _float(row.get("payoutAmount") or row.get("payout")) or 0.0
    return payout - STAKE_PER_CANDIDATE


def _payout(row: dict[str, Any]) -> float:
    value = _float(row.get("payoutAmount") or row.get("payout"))
    if value is not None:
        return value
    return max(_profit(row) + STAKE_PER_CANDIDATE, 0.0)


def _max_drawdown(profits: list[float]) -> float | None:
    if not profits:
        return None
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for profit in profits:
        equity += profit
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return round(max_dd, 2)


def _concentration(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    profits: dict[str, float] = defaultdict(float)
    total_positive = 0.0
    for row in rows:
        profit = _profit(row)
        if profit <= 0:
            continue
        bucket = str(row.get(key) or _date_from_row(row) if key == "date" else row.get(key) or "unknown")
        profits[bucket] += profit
        total_positive += profit
    if total_positive <= 0:
        return {"available": False, "maxShare": None, "maxBucket": None}
    max_bucket = max(profits, key=profits.get)
    return {"available": True, "maxShare": round(profits[max_bucket] / total_positive, 6), "maxBucket": max_bucket}


def _performance(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if key == "raceNoBand":
            grouped[_race_no_band(row.get("raceNo") or row.get("race_no"))].append(row)
        elif key == "date":
            grouped[_date_from_row(row) or "unknown"].append(row)
        else:
            grouped[str(row.get(key) or "unknown")].append(row)
    out = {}
    for bucket, bucket_rows in sorted(grouped.items()):
        stake = len(bucket_rows) * STAKE_PER_CANDIDATE
        payout = sum(_payout(row) for row in bucket_rows)
        hits = sum(1 for row in bucket_rows if _truthy(row.get("hit")))
        out[bucket] = {
            "candidateCount": len(bucket_rows),
            "payout": round(payout, 2),
            "profit": round(payout - stake, 2),
            "roi": round((payout - stake) / stake, 6) if stake else None,
            "hitRate": round(hits / len(bucket_rows), 6) if bucket_rows else None,
        }
    return out


def _zero_classification(rows: list[dict[str, Any]], reason_counts: Counter[str]) -> str:
    if not rows:
        return "expected_no_candidate"
    priority = [
        ("missing_metadata", "missing_metadata"),
        ("missing_pre_deadline_odds", "missing_odds"),
        ("settlement_waiting", "settlement_waiting"),
        ("duplicate_candidate_id", "scope_mismatch"),
        ("missing_candidate_id", "missing_metadata"),
    ]
    for reason, classification in priority:
        if reason_counts.get(reason, 0):
            return classification
    return "scope_mismatch"


def build_live_evidence_gate(
    *,
    candidate_trace_rows_path: Path = DEFAULT_TRACE_ROWS,
    live_summary_path: Path = DEFAULT_LIVE_SUMMARY,
    tuning_gate_path: Path = DEFAULT_TUNING_GATE,
) -> dict[str, Any]:
    rows = _load_csv(candidate_trace_rows_path)
    live_payload = _load_json(live_summary_path)
    tuning_payload = _load_json(tuning_gate_path)
    live_summary = live_payload.get("summary") if isinstance(live_payload.get("summary"), dict) else {}
    seen: set[str] = set()
    strict_rows: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    duplicate_count = 0
    for row in rows:
        reason = _strict_reason(row, seen)
        reason_counts[reason] += 1
        candidate_id = str(row.get("candidateId") or "").strip()
        if candidate_id and candidate_id in seen:
            duplicate_count += 1
        if candidate_id:
            seen.add(candidate_id)
        if reason == "strict_eligible":
            strict_rows.append(row)

    candidate_count = len(rows)
    trace_complete_count = sum(1 for row in rows if str(row.get("traceStatus") or "") == "complete")
    pre_deadline_count = sum(1 for row in rows if _is_pre_deadline(row))
    settled_count_all = sum(1 for row in rows if _is_settled(row))
    settled_count_strict = len(strict_rows)
    observation_days = len({date for date in (_date_from_row(row) for row in rows) if date}) or _int(live_summary.get("days"))
    stake = settled_count_strict * STAKE_PER_CANDIDATE
    payout = sum(_payout(row) for row in strict_rows)
    profit = payout - stake
    hits = sum(1 for row in strict_rows if _truthy(row.get("hit")))

    trace_coverage = round(trace_complete_count / candidate_count, 6) if candidate_count else None
    pre_deadline_coverage = round(pre_deadline_count / candidate_count, 6) if candidate_count else None
    settlement_coverage = round(settled_count_all / candidate_count, 6) if candidate_count else None
    roi = round(profit / stake, 6) if stake else None
    hit_rate = round(hits / settled_count_strict, 6) if settled_count_strict else None
    concentration = {
        "date": _concentration(strict_rows, "date"),
        "venue": _concentration(strict_rows, "venueCode"),
        "raceNo": _concentration(strict_rows, "raceNo"),
    }
    max_concentration = max(
        [item["maxShare"] for item in concentration.values() if item.get("maxShare") is not None],
        default=None,
    )

    projected = {}
    rate = settled_count_strict / observation_days if observation_days else 0.0
    for target in (30, 100, 500):
        projected[f"daysTo{target}Settled"] = round(target / rate, 2) if rate > 0 else None

    blockers: list[str] = []
    if observation_days < MIN_OBSERVATION_DAYS:
        blockers.append("observation_days_below_60")
    if settled_count_strict < MIN_SETTLED_CANDIDATES:
        blockers.append("settled_candidate_count_below_500")
    if trace_coverage is None or trace_coverage < MIN_TRACE_COVERAGE:
        blockers.append("trace_coverage_below_0_95")
    if pre_deadline_coverage is None or pre_deadline_coverage < MIN_PRE_DEADLINE_COVERAGE:
        blockers.append("pre_deadline_odds_coverage_below_0_95")
    if settlement_coverage is None or settlement_coverage < MIN_SETTLEMENT_COVERAGE:
        blockers.append("settlement_coverage_below_0_98")
    if duplicate_count > 0:
        blockers.append("duplicate_candidate_id_present")
    if settled_count_strict == 0:
        blockers.append(f"strict_candidate_count_zero_{_zero_classification(rows, reason_counts)}")
    drift_status = str(live_summary.get("featureDriftStatus") or "").strip().lower()
    if drift_status and drift_status not in {"ok", "none"}:
        blockers.append("severe_drift_present")
    if max_concentration is None:
        blockers.append("profit_concentration_unavailable")
    elif max_concentration > MAX_POSITIVE_PROFIT_SHARE:
        blockers.append("profit_concentration_above_25pct")
    if roi is None:
        blockers.append("roi_ci_uncalculable")

    classification = "live_evidence_ready" if not blockers else "live_evidence_blocked"
    return {
        "reportType": "live_evidence_gate",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "dateRange": live_summary.get("dateRange"),
        "sources": {
            "candidateTraceRows": str(candidate_trace_rows_path),
            "liveOperationSummary": str(live_summary_path),
            "tuningGate": str(tuning_gate_path),
        },
        "thresholds": {
            "minimumObservationDays": MIN_OBSERVATION_DAYS,
            "minimumSettledCandidateCount": MIN_SETTLED_CANDIDATES,
            "minimumTraceCoverage": MIN_TRACE_COVERAGE,
            "minimumPreDeadlineOddsCoverage": MIN_PRE_DEADLINE_COVERAGE,
            "minimumSettlementCoverage": MIN_SETTLEMENT_COVERAGE,
            "maximumPositiveProfitShare": MAX_POSITIVE_PROFIT_SHARE,
        },
        "counts": {
            "observationDays": observation_days,
            "shadowCandidateCount": candidate_count,
            "frozenCandidateCount": candidate_count - reason_counts.get("missing_metadata", 0),
            "settledCandidateCount": settled_count_strict,
            "strictEligibleCandidateCount": settled_count_strict,
            "duplicateCandidateIdCount": duplicate_count,
            "preDeadlineTrueCount": pre_deadline_count,
            "settledCandidateCountAll": settled_count_all,
        },
        "metrics": {
            "traceCoverage": trace_coverage,
            "preDeadlineOddsCoverage": pre_deadline_coverage,
            "settlementCoverage": settlement_coverage,
            "hitRate": hit_rate,
            "maxDrawdown": _max_drawdown([_profit(row) for row in strict_rows]),
        },
        "profitability": {
            "stake": round(stake, 2),
            "payout": round(payout, 2),
            "profit": round(profit, 2),
            "roi": roi,
            "hitCount": hits,
        },
        "concentration": concentration,
        "performance": {
            "byDate": _performance(strict_rows, "date"),
            "byVenue": _performance(strict_rows, "venueCode"),
            "byRaceNoBand": _performance(strict_rows, "raceNoBand"),
            "byModelVersion": _performance(strict_rows, "modelVersion"),
            "byPolicyVersion": _performance(strict_rows, "policyVersion"),
        },
        "projection": {
            **projected,
            "zeroStrictCandidateClassification": None if settled_count_strict else _zero_classification(rows, reason_counts),
            "strictCandidateRatePerObservationDay": round(rate, 6) if observation_days else None,
        },
        "strictReasonCounts": dict(sorted(reason_counts.items())),
        "quality": {
            "classification": classification,
            "shadowProfitabilityReady": not blockers,
            "productionAdoptionAllowed": False,
            "buyEvVotingChanged": False,
        },
        "blockers": blockers,
        "notes": [
            "Only rows with oddsCapturedAt < deadlineAt and settled official results are counted as strict evidence.",
            "Legacy rows are not backfilled with guessed metadata.",
            "BUY / EV / voting / production adoption remain disabled.",
        ],
    }


def _write_outputs(payload: dict[str, Any]) -> None:
    MONITORING_ROOT.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = []
    for section in ("counts", "metrics", "profitability", "quality"):
        for key, value in payload[section].items():
            rows.append({"section": section, "metric": key, "value": value})
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["section", "metric", "value"])
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# Live Evidence Gate",
        "",
        f"- classification: {payload['quality']['classification']}",
        f"- productionAdoptionAllowed: {payload['quality']['productionAdoptionAllowed']}",
        f"- observationDays: {payload['counts']['observationDays']}",
        f"- shadowCandidateCount: {payload['counts']['shadowCandidateCount']}",
        f"- settledCandidateCount: {payload['counts']['settledCandidateCount']}",
        f"- traceCoverage: {payload['metrics']['traceCoverage']}",
        f"- preDeadlineOddsCoverage: {payload['metrics']['preDeadlineOddsCoverage']}",
        f"- settlementCoverage: {payload['metrics']['settlementCoverage']}",
        f"- roi: {payload['profitability']['roi']}",
        f"- hitRate: {payload['metrics']['hitRate']}",
        f"- maxDrawdown: {payload['metrics']['maxDrawdown']}",
        "",
        "## Blockers",
    ]
    lines.extend([f"- {item}" for item in payload["blockers"]] or ["- none"])
    lines.extend(["", "## Strict reason counts"])
    lines.extend(f"- {key}: {value}" for key, value in payload["strictReasonCounts"].items())
    lines.extend(["", "## Notes"])
    lines.extend(f"- {item}" for item in payload["notes"])
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build strict live evidence gate")
    parser.add_argument("--candidate-trace-rows", type=Path, default=DEFAULT_TRACE_ROWS)
    parser.add_argument("--live-summary", type=Path, default=DEFAULT_LIVE_SUMMARY)
    parser.add_argument("--tuning-gate", type=Path, default=DEFAULT_TUNING_GATE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    payload = build_live_evidence_gate(
        candidate_trace_rows_path=args.candidate_trace_rows,
        live_summary_path=args.live_summary,
        tuning_gate_path=args.tuning_gate,
    )
    if not args.dry_run:
        _write_outputs(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
