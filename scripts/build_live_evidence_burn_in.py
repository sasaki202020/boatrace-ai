from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MONITORING_ROOT = ROOT / "reports" / "monitoring"
DEFAULT_TRACE_AUDIT = MONITORING_ROOT / "candidate_trace_audit.json"
DEFAULT_TRACE_ROWS = MONITORING_ROOT / "candidate_trace_rows.csv"
DEFAULT_LIVE_GATE = MONITORING_ROOT / "live_evidence_gate.json"
DEFAULT_LIVE_SUMMARY = MONITORING_ROOT / "live_operation_summary.json"
OUT_JSON = MONITORING_ROOT / "live_evidence_burn_in.json"
OUT_CSV = MONITORING_ROOT / "live_evidence_burn_in.csv"
OUT_MD = MONITORING_ROOT / "live_evidence_burn_in.md"

LEGACY_UNKNOWN = "legacy_unknown"
MIN_STRICT_SETTLED_FOR_READY = 10
MIN_OBSERVATION_DAYS = 60
MIN_SETTLED_CANDIDATES = 500
MIN_TRACE_COVERAGE = 0.95
MIN_PRE_DEADLINE_COVERAGE = 0.95
MIN_SETTLEMENT_COVERAGE = 0.98


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


def _latest_strict_daily_audit() -> tuple[Path | None, dict[str, Any]]:
    """Select a valid daily audit by targetDate, never by filesystem mtime."""
    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    for path in MONITORING_ROOT.glob("strict_evidence_daily_audit_*.json"):
        payload = _load_json(path)
        target_date = str(payload.get("targetDate") or "").strip()
        if payload.get("auditSchemaVersion") and len(target_date) == 10 and target_date[4] == "-" and target_date[7] == "-":
            candidates.append((target_date, path, payload))
    if not candidates:
        return None, {}
    target_date, path, payload = max(candidates, key=lambda item: item[0])
    _ = target_date
    return path, payload


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    return token in {"1", "true", "yes", "y", "ok", "available", "settled", "complete"}


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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _load_trace_rows(trace_audit_path: Path, trace_rows_path: Path) -> list[dict[str, Any]]:
    audit_payload = _load_json(trace_audit_path)
    rows = audit_payload.get("rows")
    if isinstance(rows, list) and rows:
        return [row for row in rows if isinstance(row, dict)]
    return _load_csv(trace_rows_path)


def _date_range_from_payload(*payloads: dict[str, Any]) -> str | None:
    for payload in payloads:
        token = str(payload.get("dateRange") or payload.get("summary", {}).get("dateRange") or "").strip()
        if token:
            return token
    return None


def _forward_metadata_ready(row: dict[str, Any]) -> bool:
    required = (
        "candidateId",
        "modelVersion",
        "calibratorVersion",
        "policyVersion",
        "predictionHash",
        "snapshotHash",
        "featureVersion",
        "odds",
        "oddsCapturedAt",
        "deadlineAt",
        "policyDecision",
        "guardDecision",
        "guardReason",
        "frozenAt",
    )
    return all(_known(row.get(field)) for field in required)


def _is_pre_deadline(row: dict[str, Any]) -> bool:
    odds_at = _dt(row.get("oddsCapturedAt"))
    deadline_at = _dt(row.get("deadlineAt"))
    return bool(odds_at and deadline_at and odds_at < deadline_at)


def _is_settled(row: dict[str, Any]) -> bool:
    if _truthy(row.get("settlementExists")) and _truthy(row.get("resultAvailable")):
        return True
    status = _text(row.get("settlementStatus") or row.get("resultStatus")).lower()
    if status in {"settled", "available", "complete", "ok", "hit", "miss"}:
        return True
    return False


def _partition_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    strict_rows = [row for row in rows if _forward_metadata_ready(row)]
    legacy_rows = [row for row in rows if not _forward_metadata_ready(row)]
    return strict_rows, legacy_rows


def _latest_timestamp(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> str | None:
    best: datetime | None = None
    for row in rows:
        for field in fields:
            value = _dt(row.get(field))
            if value and (best is None or value > best):
                best = value
    return best.isoformat(timespec="seconds") if best else None


def _count_join_failed(rows: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if (_truthy(row.get("frozenExists")) or _known(row.get("frozenAt")))
        and not _is_settled(row)
    )


def _count_trace_complete(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if _text(row.get("traceStatus")).lower() == "complete")


def _count_frozen(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if _truthy(row.get("frozenExists")) or _known(row.get("frozenAt")))


def _latest_settled_at(rows: list[dict[str, Any]]) -> str | None:
    return _latest_timestamp([row for row in rows if _is_settled(row)], ("settledAt",))


def _latest_frozen_at(rows: list[dict[str, Any]]) -> str | None:
    return _latest_timestamp(rows, ("frozenAt",))


def _latest_odds_at(rows: list[dict[str, Any]]) -> str | None:
    return _latest_timestamp([row for row in rows if _is_pre_deadline(row)], ("oddsCapturedAt",))


def _forward_path_audit() -> dict[str, dict[str, Any]]:
    routes: list[tuple[str, Path, tuple[str, ...], str, str]] = [
        (
            "morning_prediction",
            ROOT / "src" / "pipeline" / "run_today.py",
            ("from src.pipeline.candidate_metadata import", "enrich_candidate_metadata(", "assert_unique_candidate_ids("),
            "connected",
            "live morning path writes forward metadata",
        ),
        (
            "prediction_sheet",
            ROOT / "src" / "pipeline" / "prediction_sheet.py",
            ("from src.pipeline.candidate_metadata import", "enrich_candidate_metadata(", "assert_unique_candidate_ids("),
            "connected",
            "prediction sheet writes forward metadata before frozen append",
        ),
        (
            "paper_shadow_candidate_generation",
            ROOT / "src" / "pipeline" / "prediction_sheet.py",
            ("enrich_candidate_metadata(", "assert_unique_candidate_ids("),
            "connected",
            "paper/shadow candidate rows are enriched before freeze",
        ),
        (
            "freeze_precheck",
            ROOT / "src" / "pipeline" / "run_today.py",
            ("enrich_candidate_metadata(", "assert_unique_candidate_ids("),
            "connected",
            "duplicate candidate id guard runs before freeze",
        ),
        (
            "frozen_ledger_append",
            ROOT / "src" / "pipeline" / "prediction_sheet.py",
            ("_rows_to_frozen_payload(", "enrich_candidate_metadata(", "assert_unique_candidate_ids("),
            "connected",
            "frozen ledger append persists forward metadata",
        ),
        (
            "settlement_join",
            ROOT / "src" / "evaluation" / "settle_results.py",
            ("candidateId", "predictionHash", "settlementExists"),
            "conditional",
            "settlement joins consume forwarded metadata but do not write it",
        ),
        (
            "backfill_prediction_generation",
            ROOT / "src" / "pipeline" / "backfill_predictions.py",
            ("from src.pipeline.candidate_metadata import", "enrich_candidate_metadata("),
            "not_applicable",
            "backfill path is not part of live burn-in forward path",
        ),
    ]
    audit: dict[str, dict[str, Any]] = {}
    for name, path, markers, connected_status, note in routes:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        hit_count = sum(1 for marker in markers if marker in text)
        if connected_status == "conditional":
            status = "conditional" if hit_count else "disconnected"
        elif connected_status == "not_applicable":
            status = "not_applicable"
        else:
            status = connected_status if hit_count == len(markers) else ("conditional" if hit_count else "disconnected")
        audit[name] = {
            "status": status,
            "path": str(path),
            "evidence": [marker for marker in markers if marker in text],
            "note": note,
        }
    return audit


def _burn_in_state(
    *,
    strict_settled_count: int,
    strict_metadata_coverage: float | None,
    strict_deadline_violation_count: int,
    strict_join_failed_count: int,
    duplicate_candidate_id_count: int,
) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    if strict_settled_count < MIN_STRICT_SETTLED_FOR_READY:
        blockers.append(f"strict_settled_candidate_count_below_{MIN_STRICT_SETTLED_FOR_READY}")
    if strict_metadata_coverage is None or strict_metadata_coverage < 1.0:
        blockers.append("strict_metadata_coverage_below_1_0")
    if strict_deadline_violation_count > 0:
        blockers.append("deadline_violation_present")
    if strict_join_failed_count > 0:
        blockers.append("strict_settlement_join_failure_present")
    if duplicate_candidate_id_count > 0:
        blockers.append("duplicate_candidate_id_present")
    return (not blockers, blockers)


def _production_blockers(
    *,
    observation_days: int,
    strict_settled_count: int,
    trace_coverage: float | None,
    pre_deadline_odds_coverage: float | None,
    settlement_coverage: float | None,
) -> list[str]:
    blockers: list[str] = []
    if observation_days < MIN_OBSERVATION_DAYS:
        blockers.append(f"observation_days_below_{MIN_OBSERVATION_DAYS}")
    if strict_settled_count < MIN_SETTLED_CANDIDATES:
        blockers.append(f"strict_candidate_count_below_{MIN_SETTLED_CANDIDATES}")
    if trace_coverage is None or trace_coverage < MIN_TRACE_COVERAGE:
        blockers.append("trace_coverage_below_0_95")
    if pre_deadline_odds_coverage is None or pre_deadline_odds_coverage < MIN_PRE_DEADLINE_COVERAGE:
        blockers.append("pre_deadline_odds_coverage_below_0_95")
    if settlement_coverage is None or settlement_coverage < MIN_SETTLEMENT_COVERAGE:
        blockers.append("settlement_coverage_below_0_98")
    return blockers


def _current_blocking_stage(
    *,
    strict_candidate_count: int,
    strict_settled_count: int,
    strict_frozen_count: int,
    strict_metadata_coverage: float | None,
    strict_join_failed_count: int,
    strict_deadline_violation_count: int,
    duplicate_candidate_id_count: int,
    burn_in_ready: bool,
    legacy_candidate_count: int,
) -> str:
    if strict_candidate_count <= 0 and legacy_candidate_count <= 0:
        return "awaiting_first_candidate"
    if strict_candidate_count <= 0 and legacy_candidate_count > 0:
        return "legacy_only_flow"
    if strict_frozen_count <= 0 and strict_settled_count <= 0:
        return "first_candidate_traced"
    if strict_frozen_count > 0 and strict_settled_count <= 0:
        return "first_candidate_frozen"
    if strict_settled_count < MIN_STRICT_SETTLED_FOR_READY:
        return "strict_candidate_count_below_10"
    if strict_metadata_coverage is None or strict_metadata_coverage < 1.0:
        return "strict_metadata_incomplete"
    if strict_join_failed_count > 0:
        return "strict_settlement_join_failure"
    if strict_deadline_violation_count > 0:
        return "deadline_violation_present"
    if duplicate_candidate_id_count > 0:
        return "duplicate_candidate_id_present"
    if burn_in_ready:
        return "ready_for_burn_in"
    return "burn_in_warning"


def build_live_evidence_burn_in(
    *,
    candidate_trace_audit_path: Path = DEFAULT_TRACE_AUDIT,
    candidate_trace_rows_path: Path = DEFAULT_TRACE_ROWS,
    live_evidence_gate_path: Path = DEFAULT_LIVE_GATE,
    live_summary_path: Path = DEFAULT_LIVE_SUMMARY,
    strict_daily_audit_path: Path | None = None,
) -> dict[str, Any]:
    audit_payload = _load_json(candidate_trace_audit_path)
    gate_payload = _load_json(live_evidence_gate_path)
    live_summary_payload = _load_json(live_summary_path)
    if strict_daily_audit_path is None and candidate_trace_audit_path == DEFAULT_TRACE_AUDIT:
        strict_daily_audit_path, strict_daily_audit_payload = _latest_strict_daily_audit()
    elif strict_daily_audit_path is None:
        strict_daily_audit_payload = {}
    else:
        strict_daily_audit_payload = _load_json(strict_daily_audit_path)
    rows = _load_trace_rows(candidate_trace_audit_path, candidate_trace_rows_path)

    audit_counts = audit_payload.get("counts") if isinstance(audit_payload.get("counts"), dict) else {}
    audit_quality = audit_payload.get("quality") if isinstance(audit_payload.get("quality"), dict) else {}
    gate_counts = gate_payload.get("counts") if isinstance(gate_payload.get("counts"), dict) else {}
    gate_metrics = gate_payload.get("metrics") if isinstance(gate_payload.get("metrics"), dict) else {}
    live_summary = live_summary_payload.get("summary") if isinstance(live_summary_payload.get("summary"), dict) else {}

    strict_rows, legacy_rows = _partition_rows(rows)
    strict_candidate_count = len(strict_rows)
    legacy_candidate_count = len(legacy_rows)
    strict_settled_rows = [row for row in strict_rows if _is_settled(row)]
    legacy_settled_rows = [row for row in legacy_rows if _is_settled(row)]
    strict_frozen_rows = [row for row in strict_rows if _truthy(row.get("frozenExists")) or _known(row.get("frozenAt"))]
    legacy_frozen_rows = [row for row in legacy_rows if _truthy(row.get("frozenExists")) or _known(row.get("frozenAt"))]
    strict_join_failed_count = _count_join_failed(strict_rows)
    legacy_join_failed_count = _count_join_failed(legacy_rows)
    strict_deadline_violation_count = sum(1 for row in strict_rows if _dt(row.get("oddsCapturedAt")) and _dt(row.get("deadlineAt")) and _dt(row.get("oddsCapturedAt")) >= _dt(row.get("deadlineAt")))
    legacy_deadline_violation_count = sum(1 for row in legacy_rows if _dt(row.get("oddsCapturedAt")) and _dt(row.get("deadlineAt")) and _dt(row.get("oddsCapturedAt")) >= _dt(row.get("deadlineAt")))
    duplicate_candidate_id_count = _int(gate_counts.get("duplicateCandidateIdCount")) or max(
        0, len([row for row in rows if _known(row.get("candidateId"))]) - len({row.get("candidateId") for row in rows if _known(row.get("candidateId"))})
    )

    shadow_candidate_count = _int(gate_counts.get("shadowCandidateCount")) or len(rows)
    observation_days = _int(gate_counts.get("observationDays")) or _int(live_summary.get("days")) or len(
        {str(row.get("raceDate") or row.get("date") or "").strip() for row in rows if str(row.get("raceDate") or row.get("date") or "").strip()}
    )
    trace_complete_rows = _int(audit_counts.get("completeRows")) or _count_trace_complete(rows)
    trace_coverage = _float(gate_metrics.get("traceCoverage"))
    if trace_coverage is None and shadow_candidate_count:
        trace_coverage = round(trace_complete_rows / shadow_candidate_count, 6)
    pre_deadline_odds_coverage = _float(gate_metrics.get("preDeadlineOddsCoverage"))
    settlement_coverage = _float(gate_metrics.get("settlementCoverage"))
    legacy_trace_complete_rows = _count_trace_complete(legacy_rows)
    strict_trace_complete_rows = _count_trace_complete(strict_rows)

    strict_metadata_coverage = 1.0 if strict_candidate_count > 0 else 0.0
    legacy_trace_coverage = round(legacy_trace_complete_rows / legacy_candidate_count, 6) if legacy_candidate_count else None

    strict_settled_candidate_count = len(strict_settled_rows)
    legacy_settled_candidate_count = len(legacy_settled_rows)
    strict_frozen_candidate_count = len(strict_frozen_rows)
    legacy_frozen_candidate_count = len(legacy_frozen_rows)
    strict_candidate_rate = round(strict_settled_candidate_count / observation_days, 6) if observation_days else None

    strict_ready, burn_in_blockers = _burn_in_state(
        strict_settled_count=strict_settled_candidate_count,
        strict_metadata_coverage=strict_metadata_coverage,
        strict_deadline_violation_count=strict_deadline_violation_count,
        strict_join_failed_count=strict_join_failed_count,
        duplicate_candidate_id_count=duplicate_candidate_id_count,
    )
    production_blockers = _production_blockers(
        observation_days=observation_days,
        strict_settled_count=strict_settled_candidate_count,
        trace_coverage=trace_coverage,
        pre_deadline_odds_coverage=pre_deadline_odds_coverage,
        settlement_coverage=settlement_coverage,
    )

    legacy_warnings: list[str] = []
    if legacy_join_failed_count > 0:
        legacy_warnings.append("legacy_settlement_join_failure_present")
    if legacy_deadline_violation_count > 0:
        legacy_warnings.append("legacy_deadline_violation_present")

    burn_in_ready = strict_ready
    overall_lifecycle_state = "burn_in_ready" if burn_in_ready else (
        "legacy_only_flow" if strict_candidate_count == 0 and legacy_candidate_count > 0 else "burn_in_warning"
    )
    legacy_lifecycle_state = (
        "awaiting_first_candidate"
        if legacy_candidate_count == 0
        else ("first_candidate_settled" if legacy_settled_candidate_count > 0 else ("first_candidate_frozen" if legacy_frozen_candidate_count > 0 else "first_candidate_traced"))
    )
    strict_lifecycle_state = (
        "awaiting_first_candidate"
        if strict_candidate_count == 0
        else ("burn_in_ready" if burn_in_ready else ("first_candidate_settled" if strict_settled_candidate_count > 0 else ("first_candidate_frozen" if strict_frozen_candidate_count > 0 else "first_candidate_traced")))
    )
    current_blocking_stage = _current_blocking_stage(
        strict_candidate_count=strict_candidate_count,
        strict_settled_count=strict_settled_candidate_count,
        strict_frozen_count=strict_frozen_candidate_count,
        strict_metadata_coverage=strict_metadata_coverage,
        strict_join_failed_count=strict_join_failed_count,
        strict_deadline_violation_count=strict_deadline_violation_count,
        duplicate_candidate_id_count=duplicate_candidate_id_count,
        burn_in_ready=burn_in_ready,
        legacy_candidate_count=legacy_candidate_count,
    )
    daily_audit_stage = str(strict_daily_audit_payload.get("currentBlockingStage") or "").strip()
    if daily_audit_stage:
        current_blocking_stage = daily_audit_stage

    last_candidate_created_at = _latest_timestamp(rows, ("snapshotCapturedAt", "frozenAt", "settledAt"))
    last_metadata_complete_candidate_at = _latest_timestamp(strict_rows, ("snapshotCapturedAt", "frozenAt", "settledAt"))
    last_pre_deadline_odds_at = _latest_odds_at(strict_rows)
    last_frozen_candidate_at = _latest_frozen_at(rows)
    last_settled_strict_candidate_at = _latest_settled_at(strict_rows)

    trace_join_warning = legacy_join_failed_count
    freeze_coverage = round(strict_frozen_candidate_count / strict_candidate_count, 6) if strict_candidate_count else 0.0

    phase6_gate_status = str(gate_payload.get("quality", {}).get("classification") or "missing")
    forward_path_audit = _forward_path_audit()

    daily_audit_watchdog = strict_daily_audit_payload.get("watchdog") if isinstance(strict_daily_audit_payload.get("watchdog"), dict) else {}
    if strict_daily_audit_payload:
        watchdog = {
            "lastCandidateCreatedAt": daily_audit_watchdog.get("lastCandidateCreatedAt"),
            "lastMetadataCompleteCandidateAt": daily_audit_watchdog.get("lastMetadataCompleteCandidateAt"),
            "lastPreDeadlineOddsAt": daily_audit_watchdog.get("lastPreDeadlineOddsAt"),
            "lastFrozenCandidateAt": daily_audit_watchdog.get("lastFrozenCandidateAt"),
            "lastSettledStrictCandidateAt": daily_audit_watchdog.get("lastSettledStrictCandidateAt"),
        }
    else:
        watchdog = {
            "lastCandidateCreatedAt": last_candidate_created_at,
            "lastMetadataCompleteCandidateAt": last_metadata_complete_candidate_at,
            "lastPreDeadlineOddsAt": last_pre_deadline_odds_at,
            "lastFrozenCandidateAt": last_frozen_candidate_at,
            "lastSettledStrictCandidateAt": last_settled_strict_candidate_at,
        }
    payload: dict[str, Any] = {
        "reportType": "live_evidence_burn_in",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "dateRange": _date_range_from_payload(gate_payload, audit_payload, live_summary_payload),
        "sources": {
            "candidateTraceAudit": str(candidate_trace_audit_path),
            "candidateTraceRows": str(candidate_trace_rows_path),
            "liveEvidenceGate": str(live_evidence_gate_path),
            "liveOperationSummary": str(live_summary_path),
            "strictDailyAudit": str(strict_daily_audit_path) if strict_daily_audit_path else None,
        },
        "inputSanity": {
            "phase6GateStatus": phase6_gate_status,
            "shadowCandidateCount": shadow_candidate_count,
            "legacyCandidateCount": legacy_candidate_count,
            "strictCandidateCount": strict_candidate_count,
            "duplicateCandidateIdCount": duplicate_candidate_id_count,
            "traceCoverage": trace_coverage,
            "legacyTraceCoverage": legacy_trace_coverage,
            "strictMetadataCoverage": strict_metadata_coverage,
            "preDeadlineOddsCoverage": pre_deadline_odds_coverage,
            "settlementCoverage": settlement_coverage,
            "strictDailyAuditTargetDate": strict_daily_audit_payload.get("targetDate"),
            "strictDailyAuditPrimaryBlockingReason": strict_daily_audit_payload.get("primaryBlockingReason"),
            "strictDailyAuditSecondaryBlockingReasons": strict_daily_audit_payload.get("secondaryBlockingReasons", []),
            "strictDailyAuditCurrentBlockingStage": daily_audit_stage or None,
        },
        "counts": {
            "observationDays": observation_days,
            "shadowCandidateCount": shadow_candidate_count,
            "legacyCandidateCount": legacy_candidate_count,
            "strictCandidateCount": strict_candidate_count,
            "legacySettlementJoinFailedCount": legacy_join_failed_count,
            "strictSettlementJoinFailedCount": strict_join_failed_count,
            "legacySettledCandidateCount": legacy_settled_candidate_count,
            "strictSettledCandidateCount": strict_settled_candidate_count,
            "legacyTraceCoverageRows": legacy_trace_complete_rows,
            "strictTraceCoverageRows": strict_trace_complete_rows,
            "legacyFrozenCandidateCount": legacy_frozen_candidate_count,
            "strictFrozenCandidateCount": strict_frozen_candidate_count,
            "duplicateCandidateIdCount": duplicate_candidate_id_count,
            "deadlineViolationCount": strict_deadline_violation_count,
            "legacyDeadlineViolationCount": legacy_deadline_violation_count,
            "preDeadlineTrueCount": _int(gate_counts.get("preDeadlineTrueCount")),
            "settledCandidateCountAll": _int(gate_counts.get("settledCandidateCountAll")) or len([row for row in rows if _is_settled(row)]),
        },
        "coverage": {
            "traceCoverage": trace_coverage,
            "legacyTraceCoverage": legacy_trace_coverage,
            "strictMetadataCoverage": strict_metadata_coverage,
            "preDeadlineOddsCoverage": pre_deadline_odds_coverage,
            "settlementCoverage": settlement_coverage,
            "strictCandidateRate": strict_candidate_rate,
            "freezeCoverage": freeze_coverage,
        },
        "projection": {
            "daysTo30StrictSettled": round(30 / strict_candidate_rate, 2) if strict_candidate_rate and strict_candidate_rate > 0 else None,
            "daysTo100StrictSettled": round(100 / strict_candidate_rate, 2) if strict_candidate_rate and strict_candidate_rate > 0 else None,
            "daysTo500StrictSettled": round(500 / strict_candidate_rate, 2) if strict_candidate_rate and strict_candidate_rate > 0 else None,
            "strictSettledRatePerObservationDay": strict_candidate_rate,
            "strictSettledProjectedAt30Days": round(strict_candidate_rate * 30, 6) if strict_candidate_rate else 0.0,
            "strictSettledProjectedAt100Days": round(strict_candidate_rate * 100, 6) if strict_candidate_rate else 0.0,
            "strictSettledProjectedAt500Days": round(strict_candidate_rate * 500, 6) if strict_candidate_rate else 0.0,
        },
        "lifecycle": {
            "shadowLifecycleState": legacy_lifecycle_state,
            "legacyLifecycleState": legacy_lifecycle_state,
            "strictLifecycleState": strict_lifecycle_state,
            "overallLifecycleState": overall_lifecycle_state,
            "burnInState": "burn_in_ready" if burn_in_ready else "burn_in_warning",
            "burnInReady": burn_in_ready,
            "currentBlockingStage": current_blocking_stage,
            "shadowOnlyLegacyFlow": strict_candidate_count == 0 and legacy_candidate_count > 0,
        },
        "quality": {
            "classification": "burn_in_ready" if burn_in_ready else "burn_in_warning",
            "productionAdoptionAllowed": False,
            "strictOnly": True,
            "liveShadowReady": burn_in_ready,
            "liveEvidenceGateClassification": phase6_gate_status,
            "auditClassification": str(audit_payload.get("quality", {}).get("classification") or "missing"),
            "strictDailyAuditClassification": "available" if strict_daily_audit_payload else "missing",
        },
        "burnInBlockers": burn_in_blockers,
        "legacyWarnings": legacy_warnings,
        "productionBlockers": production_blockers,
        "blockers": burn_in_blockers + production_blockers,
        "watchdog": {
            "lastCandidateCreatedAt": watchdog["lastCandidateCreatedAt"],
            "lastMetadataCompleteCandidateAt": watchdog["lastMetadataCompleteCandidateAt"],
            "lastPreDeadlineOddsAt": watchdog["lastPreDeadlineOddsAt"],
            "lastFrozenCandidateAt": watchdog["lastFrozenCandidateAt"],
            "lastSettledStrictCandidateAt": watchdog["lastSettledStrictCandidateAt"],
        },
        "forwardPathAudit": forward_path_audit,
        "legacyEvidence": {
            "traceCoverage": trace_coverage,
            "completeRows": trace_complete_rows,
            "settledRows": legacy_settled_candidate_count + strict_settled_candidate_count,
            "frozenRows": legacy_frozen_candidate_count + strict_frozen_candidate_count,
            "joinFailedRows": legacy_join_failed_count + strict_join_failed_count,
        },
        "notes": [
            "Strict evidence counts only forward-only metadata rows with oddsCapturedAt < deadlineAt and settled official results.",
            "Legacy rows are visible for continuity but do not block strict burn-in readiness.",
            "BUY / EV / voting / production adoption remain disabled.",
        ],
        "strictDailyAudit": {
            "targetDate": strict_daily_audit_payload.get("targetDate"),
            "primaryBlockingReason": strict_daily_audit_payload.get("primaryBlockingReason"),
            "secondaryBlockingReasons": strict_daily_audit_payload.get("secondaryBlockingReasons", []),
            "currentBlockingStage": daily_audit_stage or None,
        },
    }
    return payload


def _write_outputs(payload: dict[str, Any]) -> None:
    MONITORING_ROOT.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = []
    for section in ("inputSanity", "counts", "coverage", "projection", "lifecycle", "quality", "watchdog"):
        for key, value in payload.get(section, {}).items():
            rows.append({"section": section, "metric": key, "value": value})
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["section", "metric", "value"])
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Live Evidence Burn-in",
        "",
        f"- classification: {payload['quality']['classification']}",
        f"- productionAdoptionAllowed: {payload['quality']['productionAdoptionAllowed']}",
        f"- phase6GateStatus: {payload['inputSanity']['phase6GateStatus']}",
        f"- shadowCandidateCount: {payload['counts']['shadowCandidateCount']}",
        f"- legacyCandidateCount: {payload['counts']['legacyCandidateCount']}",
        f"- strictCandidateCount: {payload['counts']['strictCandidateCount']}",
        f"- strictSettledCandidateCount: {payload['counts']['strictSettledCandidateCount']}",
        f"- strictSettlementJoinFailedCount: {payload['counts']['strictSettlementJoinFailedCount']}",
        f"- legacySettlementJoinFailedCount: {payload['counts']['legacySettlementJoinFailedCount']}",
        f"- traceCoverage: {payload['coverage']['traceCoverage']}",
        f"- legacyTraceCoverage: {payload['coverage']['legacyTraceCoverage']}",
        f"- strictMetadataCoverage: {payload['coverage']['strictMetadataCoverage']}",
        f"- preDeadlineOddsCoverage: {payload['coverage']['preDeadlineOddsCoverage']}",
        f"- settlementCoverage: {payload['coverage']['settlementCoverage']}",
        f"- burnInState: {payload['lifecycle']['burnInState']}",
        f"- strictLifecycleState: {payload['lifecycle']['strictLifecycleState']}",
        f"- legacyLifecycleState: {payload['lifecycle']['legacyLifecycleState']}",
        f"- overallLifecycleState: {payload['lifecycle']['overallLifecycleState']}",
        f"- currentBlockingStage: {payload['lifecycle']['currentBlockingStage']}",
        "",
        "## Burn-in blockers",
    ]
    if payload["burnInBlockers"]:
        lines.extend(f"- {item}" for item in payload["burnInBlockers"])
    else:
        lines.append("- none")
    lines.extend(["", "## Production blockers"])
    if payload["productionBlockers"]:
        lines.extend(f"- {item}" for item in payload["productionBlockers"])
    else:
        lines.append("- none")
    lines.extend(["", "## Legacy warnings"])
    if payload["legacyWarnings"]:
        lines.extend(f"- {item}" for item in payload["legacyWarnings"])
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Watchdog",
            f"- lastCandidateCreatedAt: {payload['watchdog']['lastCandidateCreatedAt']}",
            f"- lastMetadataCompleteCandidateAt: {payload['watchdog']['lastMetadataCompleteCandidateAt']}",
            f"- lastPreDeadlineOddsAt: {payload['watchdog']['lastPreDeadlineOddsAt']}",
            f"- lastFrozenCandidateAt: {payload['watchdog']['lastFrozenCandidateAt']}",
            f"- lastSettledStrictCandidateAt: {payload['watchdog']['lastSettledStrictCandidateAt']}",
            "",
            "## Forward path audit",
        ]
    )
    for route_name, route_payload in payload["forwardPathAudit"].items():
        lines.append(f"- {route_name}: {route_payload['status']} ({', '.join(route_payload.get('evidence') or []) or 'no markers'})")
    lines.extend(
        [
            "",
            "## Legacy evidence",
            f"- legacyTraceCoverage: {payload['legacyEvidence']['traceCoverage']}",
            f"- legacyTraceRows: {payload['legacyEvidence']['completeRows']}",
            f"- legacySettledRows: {payload['legacyEvidence']['settledRows']}",
            f"- legacyFrozenRows: {payload['legacyEvidence']['frozenRows']}",
            "",
            "## Projection",
            f"- daysTo30StrictSettled: {payload['projection']['daysTo30StrictSettled']}",
            f"- daysTo100StrictSettled: {payload['projection']['daysTo100StrictSettled']}",
            f"- daysTo500StrictSettled: {payload['projection']['daysTo500StrictSettled']}",
            f"- strictSettledRatePerObservationDay: {payload['projection']['strictSettledRatePerObservationDay']}",
            "",
            "## Notes",
            *[f"- {item}" for item in payload["notes"]],
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build live evidence burn-in report")
    parser.add_argument("--candidate-trace-audit", type=Path, default=DEFAULT_TRACE_AUDIT)
    parser.add_argument("--candidate-trace-rows", type=Path, default=DEFAULT_TRACE_ROWS)
    parser.add_argument("--live-evidence-gate", type=Path, default=DEFAULT_LIVE_GATE)
    parser.add_argument("--live-summary", type=Path, default=DEFAULT_LIVE_SUMMARY)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    payload = build_live_evidence_burn_in(
        candidate_trace_audit_path=args.candidate_trace_audit,
        candidate_trace_rows_path=args.candidate_trace_rows,
        live_evidence_gate_path=args.live_evidence_gate,
        live_summary_path=args.live_summary,
    )
    if not args.dry_run:
        _write_outputs(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
