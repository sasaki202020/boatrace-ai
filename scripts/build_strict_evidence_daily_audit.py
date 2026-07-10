from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
REPORTS_ROOT = ROOT / "reports"
PREDICTIONS_ROOT = REPORTS_ROOT / "predictions"
DAILY_ROOT = REPORTS_ROOT / "daily"
MONITORING_ROOT = REPORTS_ROOT / "monitoring"
TRACE_AUDIT = MONITORING_ROOT / "candidate_trace_audit.json"
TRACE_ROWS = MONITORING_ROOT / "candidate_trace_rows.csv"
LIVE_GATE = MONITORING_ROOT / "live_evidence_gate.json"
LIVE_SUMMARY = MONITORING_ROOT / "live_operation_summary.json"

AUDIT_SCHEMA_VERSION = "1.0"
LEGACY_UNKNOWN = "legacy_unknown"
MAIN_REASON_PRIORITY = (
    "scope_mismatch",
    "missing_metadata",
    "missing_odds",
    "policy_filtered_all",
    "guard_filtered_all",
    "freeze_not_run",
    "expected_no_candidate",
)
LIFECYCLE_VALUES = (
    "candidate_created",
    "metadata_complete",
    "pre_deadline_odds_confirmed",
    "frozen",
    "result_waiting",
    "settled",
    "settlement_join_failure",
)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except (OSError, csv.Error):
        return []


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _known(value: Any) -> bool:
    token = _text(value)
    return bool(token and token != LEGACY_UNKNOWN)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "ok", "available", "settled", "complete"}


def _parse_datetime(value: Any) -> datetime | None:
    token = _text(value)
    if not token or token == LEGACY_UNKNOWN:
        return None
    try:
        return datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_date(value: Any) -> str:
    token = _text(value)
    if len(token) == 8 and token.isdigit():
        return f"{token[:4]}-{token[4:6]}-{token[6:8]}"
    if len(token) >= 10 and token[4] == "-" and token[7] == "-":
        return token[:10]
    return token


def _hash_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _source_hashes(paths: Iterable[Path]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in paths:
        digest = _hash_file(path)
        if digest:
            result[str(path)] = digest
    return result


def _row_date(row: dict[str, Any]) -> str:
    return _parse_date(row.get("raceDate") or row.get("date") or row.get("targetDate"))


def _extract_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("rows", "candidates", "predictions", "bets"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    result: list[dict[str, Any]] = []
    for value in payload.values():
        if isinstance(value, dict):
            result.extend(_extract_list(value))
        elif isinstance(value, list):
            result.extend(_extract_list(value))
    return result


def _load_trace_rows(trace_audit_path: Path, trace_rows_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = _load_json(trace_audit_path)
    rows = payload.get("rows")
    if isinstance(rows, list):
        return payload, [row for row in rows if isinstance(row, dict)]
    return payload, _load_csv(trace_rows_path)


def _load_optional_rows(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    if path.suffix.lower() == ".csv":
        return _load_csv(path)
    return _extract_list(_load_json(path))


def _metadata_complete(row: dict[str, Any]) -> bool:
    return all(
        _known(row.get(field))
        for field in ("candidateId", "modelVersion", "policyVersion", "predictionHash")
    )


def _pre_deadline(row: dict[str, Any]) -> bool:
    captured = _parse_datetime(row.get("oddsCapturedAt"))
    deadline = _parse_datetime(row.get("deadlineAt"))
    return bool(captured and deadline and captured < deadline)


def _deadline_violation(row: dict[str, Any]) -> bool:
    captured = _parse_datetime(row.get("oddsCapturedAt"))
    deadline = _parse_datetime(row.get("deadlineAt"))
    return bool(captured and deadline and captured >= deadline)


def _policy_passed(row: dict[str, Any]) -> bool:
    decision = _text(row.get("policyDecision") or row.get("paperDecision") or row.get("decision")).upper()
    return bool(decision) and decision not in {"SKIP", "NO_BET", "REJECT", "BLOCK", "STOP"}


def _guard_passed(row: dict[str, Any]) -> bool:
    decision = _text(row.get("guardDecision") or row.get("finalDecision")).upper()
    return bool(decision) and decision not in {"SKIP", "NO_BET", "REJECT", "BLOCK", "STOP", "FALSE"}


def _frozen(row: dict[str, Any]) -> bool:
    return _truthy(row.get("frozenExists")) or _known(row.get("frozenAt"))


def _settled(row: dict[str, Any]) -> bool:
    if _truthy(row.get("settlementExists")) and _truthy(row.get("resultAvailable")):
        return True
    status = _text(row.get("settlementStatus") or row.get("resultStatus")).lower()
    return status in {"settled", "available", "complete", "ok", "hit", "miss"}


def _settlement_exists(row: dict[str, Any]) -> bool:
    if _truthy(row.get("settlementExists")):
        return True
    status = _text(row.get("settlementStatus") or row.get("resultStatus")).lower()
    return status in {"settled", "available", "complete", "ok", "hit", "miss", "error", "parse_error"}


def _normalize_row(row: dict[str, Any], target_date: str) -> dict[str, Any]:
    normalized = dict(row)
    normalized["targetDate"] = target_date
    normalized["raceDate"] = _row_date(row) or target_date
    normalized["candidateId"] = _text(row.get("candidateId"))
    normalized["raceId"] = _text(row.get("raceId") or row.get("race_id"))
    normalized["policyDecision"] = _text(row.get("policyDecision") or row.get("paperDecision") or row.get("decision"))
    normalized["guardDecision"] = _text(row.get("guardDecision") or row.get("finalDecision"))
    return normalized


def _source_scope_ok(trace_payload: dict[str, Any], target_date: str, daily_ops_payload: dict[str, Any]) -> bool:
    start = _parse_date(trace_payload.get("startDate"))
    end = _parse_date(trace_payload.get("endDate"))
    if start and end and start <= target_date <= end:
        return True
    if _parse_date(daily_ops_payload.get("date")) == target_date:
        return bool(
            daily_ops_payload.get("predictionSheetGenerated")
            or daily_ops_payload.get("frozenBetsGenerated")
            or daily_ops_payload.get("paperPredictionGenerated")
        )
    return False


def _lifecycle(row: dict[str, Any], *, duplicate: bool, now: datetime) -> str:
    if not _metadata_complete(row):
        return "candidate_created"
    if not _pre_deadline(row):
        return "metadata_complete"
    if not _policy_passed(row):
        return "metadata_complete"
    if not _guard_passed(row):
        return "pre_deadline_odds_confirmed"
    if not _frozen(row):
        deadline = _parse_datetime(row.get("deadlineAt"))
        if deadline and now > deadline:
            return "pre_deadline_odds_confirmed"
        return "candidate_created"
    if _settled(row):
        return "settled"
    if _settlement_exists(row):
        return "settlement_join_failure"
    return "result_waiting"


def _reason_flags(rows: list[dict[str, Any]], *, scope_ok: bool, now: datetime) -> set[str]:
    flags: set[str] = set()
    if not scope_ok:
        flags.add("scope_mismatch")
    if not rows:
        if scope_ok:
            flags.add("expected_no_candidate")
        return flags

    metadata_rows = [row for row in rows if _metadata_complete(row)]
    if len(metadata_rows) < len(rows):
        flags.add("missing_metadata")
    odds_rows = [row for row in metadata_rows if _pre_deadline(row)]
    if len(odds_rows) < len(metadata_rows):
        flags.add("missing_odds")
    policy_rows = [row for row in odds_rows if _policy_passed(row)]
    if odds_rows and not policy_rows:
        flags.add("policy_filtered_all")
    guard_rows = [row for row in policy_rows if _guard_passed(row)]
    if policy_rows and not guard_rows:
        flags.add("guard_filtered_all")
    freeze_rows = [row for row in guard_rows if not _frozen(row)]
    if any(
        (_parse_datetime(row.get("deadlineAt")) is not None and now > _parse_datetime(row.get("deadlineAt")))
        for row in freeze_rows
    ):
        flags.add("freeze_not_run")
    if any(_frozen(row) and not _settled(row) and not _settlement_exists(row) for row in guard_rows):
        flags.add("result_waiting")
    if any(_frozen(row) and _settlement_exists(row) and not _settled(row) for row in guard_rows):
        flags.add("settlement_join_failure")
    return flags


def _choose_primary(flags: set[str], *, strict_count: int, settled_count: int) -> str:
    for reason in MAIN_REASON_PRIORITY:
        if reason in flags:
            return reason
    if "settlement_join_failure" in flags:
        return "settlement_join_failure"
    if "result_waiting" in flags:
        return "result_waiting"
    if strict_count > settled_count:
        return "result_waiting"
    return "none"


def _row_fieldnames() -> list[str]:
    return [
        "targetDate",
        "raceDate",
        "raceId",
        "candidateId",
        "modelVersion",
        "policyVersion",
        "predictionHash",
        "oddsCapturedAt",
        "deadlineAt",
        "frozenAt",
        "policyDecision",
        "guardDecision",
        "metadataComplete",
        "preDeadlineOdds",
        "deadlineViolation",
        "policyPassed",
        "guardPassed",
        "frozen",
        "settlementExists",
        "settled",
        "lifecycle",
        "duplicateCandidateId",
    ]


def _audit_row(row: dict[str, Any], target_date: str, *, duplicate: bool, now: datetime) -> dict[str, Any]:
    lifecycle = _lifecycle(row, duplicate=duplicate, now=now)
    return {
        "targetDate": target_date,
        "raceDate": _row_date(row) or target_date,
        "raceId": _text(row.get("raceId") or row.get("race_id")),
        "candidateId": _text(row.get("candidateId")),
        "modelVersion": _text(row.get("modelVersion")),
        "policyVersion": _text(row.get("policyVersion")),
        "predictionHash": _text(row.get("predictionHash")),
        "oddsCapturedAt": _text(row.get("oddsCapturedAt")),
        "deadlineAt": _text(row.get("deadlineAt")),
        "frozenAt": _text(row.get("frozenAt")),
        "policyDecision": _text(row.get("policyDecision")),
        "guardDecision": _text(row.get("guardDecision")),
        "metadataComplete": _metadata_complete(row),
        "preDeadlineOdds": _pre_deadline(row),
        "deadlineViolation": _deadline_violation(row),
        "policyPassed": _policy_passed(row),
        "guardPassed": _guard_passed(row),
        "frozen": _frozen(row),
        "settlementExists": _settlement_exists(row),
        "settled": _settled(row),
        "lifecycle": lifecycle,
        "duplicateCandidateId": duplicate,
    }


def _legacy_reference(trace_payload: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    legacy_rows = [row for row in rows if not _metadata_complete(row)]
    counts = trace_payload.get("counts") if isinstance(trace_payload.get("counts"), dict) else {}
    return {
        "legacyCandidateCount": len(legacy_rows),
        "legacySettledCandidateCount": sum(1 for row in legacy_rows if _settled(row)),
        "legacySettlementJoinFailedCount": sum(1 for row in legacy_rows if _frozen(row) and _settlement_exists(row) and not _settled(row)),
        "legacyTraceCoverage": counts.get("traceCoverage") or counts.get("traceCoveragePct"),
        "legacySourceDateRange": f"{trace_payload.get('startDate', '')}_{trace_payload.get('endDate', '')}".strip("_"),
        "legacyOverallCandidateRows": counts.get("candidateRowsScanned", 0),
        "legacyMixedIntoStrict": False,
    }


def _latest_row_timestamp(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> str | None:
    values: list[datetime] = []
    for row in rows:
        for field in fields:
            parsed = _parse_datetime(row.get(field))
            if parsed:
                values.append(parsed)
    return max(values).isoformat(timespec="seconds") if values else None


def build_strict_evidence_daily_audit(
    *,
    target_date: str,
    candidate_trace_audit_path: Path = TRACE_AUDIT,
    candidate_trace_rows_path: Path = TRACE_ROWS,
    live_evidence_gate_path: Path = LIVE_GATE,
    live_summary_path: Path = LIVE_SUMMARY,
    prediction_path: Path | None = None,
    frozen_path: Path | None = None,
    daily_report_path: Path | None = None,
    daily_evaluation_path: Path | None = None,
    daily_ops_check_path: Path | None = None,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    target_date = _parse_date(target_date)
    if not target_date:
        raise ValueError("target_date must be YYYY-MM-DD")
    trace_payload, trace_rows = _load_trace_rows(candidate_trace_audit_path, candidate_trace_rows_path)
    gate_payload = _load_json(live_evidence_gate_path)
    live_summary_payload = _load_json(live_summary_path)
    daily_ops_payload = _load_json(daily_ops_check_path) if daily_ops_check_path else {}

    default_prediction = PREDICTIONS_ROOT / target_date / "prediction_sheet.json"
    default_frozen = PREDICTIONS_ROOT / target_date / "frozen_bets.json"
    default_daily = DAILY_ROOT / target_date / "daily_report.json"
    default_evaluation = DAILY_ROOT / target_date / "daily_evaluation_race_results.csv"
    prediction_path = prediction_path or default_prediction
    frozen_path = frozen_path or default_frozen
    daily_report_path = daily_report_path or default_daily
    daily_evaluation_path = daily_evaluation_path or default_evaluation
    if daily_ops_check_path is None:
        default_ops = DAILY_ROOT / target_date / "daily_paper_ops_check.json"
        daily_ops_check_path = default_ops
        daily_ops_payload = _load_json(default_ops)

    target_trace_rows = [_normalize_row(row, target_date) for row in trace_rows if _row_date(row) == target_date]
    supplemental_rows: list[dict[str, Any]] = []
    if not target_trace_rows:
        supplemental_rows.extend(_normalize_row(row, target_date) for row in _load_optional_rows(prediction_path))
        supplemental_rows.extend(_normalize_row(row, target_date) for row in _load_optional_rows(frozen_path))
    observed_rows = target_trace_rows or supplemental_rows

    source_paths = [
        candidate_trace_audit_path,
        candidate_trace_rows_path,
        live_evidence_gate_path,
        live_summary_path,
        prediction_path,
        frozen_path,
        daily_report_path,
        daily_evaluation_path,
        daily_ops_check_path,
    ]
    source_files = {str(path): path.exists() for path in source_paths if path is not None}
    source_hashes = _source_hashes(path for path in source_paths if path is not None)
    scope_ok = _source_scope_ok(trace_payload, target_date, daily_ops_payload)
    current_now = _parse_datetime(now) if isinstance(now, str) else now
    current_now = current_now or datetime.now()

    candidate_ids = [_text(row.get("candidateId")) for row in observed_rows if _known(row.get("candidateId"))]
    duplicate_ids = {candidate_id for candidate_id in candidate_ids if candidate_ids.count(candidate_id) > 1}
    audit_rows = [
        _audit_row(row, target_date, duplicate=_text(row.get("candidateId")) in duplicate_ids, now=current_now)
        for row in observed_rows
    ]
    metadata_rows = [row for row in observed_rows if _metadata_complete(row)]
    unique_metadata_rows = [row for row in metadata_rows if _text(row.get("candidateId")) not in duplicate_ids]
    predeadline_rows = [row for row in unique_metadata_rows if _pre_deadline(row)]
    strict_rows = [
        row
        for row in unique_metadata_rows
        if _pre_deadline(row) and _policy_passed(row) and _guard_passed(row) and _frozen(row)
    ]
    frozen_rows = strict_rows
    settled_rows = [row for row in strict_rows if _settled(row)]
    strict_join_failed_rows = [row for row in strict_rows if _settlement_exists(row) and not _settled(row)]
    flags = _reason_flags(observed_rows, scope_ok=scope_ok, now=current_now)
    primary_reason = _choose_primary(flags, strict_count=len(strict_rows), settled_count=len(settled_rows))
    secondary_reasons = sorted(flags - {primary_reason})
    if primary_reason == "none" and not observed_rows and not scope_ok:
        primary_reason = "scope_mismatch"

    gate_metrics = gate_payload.get("metrics") if isinstance(gate_payload.get("metrics"), dict) else {}
    summary_payload = live_summary_payload.get("summary") if isinstance(live_summary_payload.get("summary"), dict) else {}
    legacy_reference = _legacy_reference(trace_payload, observed_rows)
    trace_date_range = f"{trace_payload.get('startDate', '')}_{trace_payload.get('endDate', '')}".strip("_")
    current_stage = primary_reason
    if primary_reason == "none" and strict_rows and len(settled_rows) < len(strict_rows):
        current_stage = "result_waiting"

    summary = {
        "auditSchemaVersion": AUDIT_SCHEMA_VERSION,
        "targetDate": target_date,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "sourceFiles": source_files,
        "sourceHashes": source_hashes,
        "primaryBlockingReason": primary_reason,
        "secondaryBlockingReasons": secondary_reasons,
        "currentBlockingStage": current_stage,
        "newMetadataCandidateCount": len(metadata_rows),
        "strictEligibleCandidateCount": len(strict_rows),
        "frozenCandidateCount": len(frozen_rows),
        "settledCandidateCount": len(settled_rows),
        "preDeadlineOddsCoverage": round(len(predeadline_rows) / len(unique_metadata_rows), 6) if unique_metadata_rows else 0.0,
        "settlementCoverage": round(len(settled_rows) / len(frozen_rows), 6) if frozen_rows else 0.0,
        "duplicateCandidateIdCount": len(duplicate_ids),
        "deadlineViolationCount": sum(1 for row in metadata_rows if _deadline_violation(row)),
        "strictSettlementJoinFailedCount": len(strict_join_failed_rows),
        "legacyReference": legacy_reference,
        "inputSanity": {
            "scopeCovered": scope_ok,
            "traceDateRange": trace_date_range,
            "gatePhase6Status": gate_payload.get("quality", {}).get("classification", "")
            if isinstance(gate_payload.get("quality"), dict)
            else "",
            "liveSummaryDays": summary_payload.get("days", 0),
            "legacyMixedIntoStrict": False,
        },
        "lifecycleCounts": {
            lifecycle: sum(1 for row in audit_rows if row["lifecycle"] == lifecycle)
            for lifecycle in LIFECYCLE_VALUES
        },
        "watchdog": {
            "lastCandidateCreatedAt": _latest_row_timestamp(observed_rows, ("snapshotCapturedAt", "frozenAt", "settledAt")),
            "lastMetadataCompleteCandidateAt": _latest_row_timestamp(
                [row for row in observed_rows if _metadata_complete(row)],
                ("snapshotCapturedAt", "frozenAt", "settledAt"),
            ),
            "lastPreDeadlineOddsAt": _latest_row_timestamp(
                [row for row in observed_rows if _metadata_complete(row) and _pre_deadline(row)],
                ("oddsCapturedAt",),
            ),
            "lastFrozenCandidateAt": _latest_row_timestamp(
                [row for row in observed_rows if _metadata_complete(row) and _frozen(row)],
                ("frozenAt",),
            ),
            "lastSettledStrictCandidateAt": _latest_row_timestamp(settled_rows, ("settledAt",)),
        },
        "notes": [
            "Read-only audit; prediction and freeze sources were not rewritten.",
            "Legacy rows are reference-only and are excluded from strict denominators and blockers.",
            "BUY / EV / voting / external transmission remain disabled.",
        ],
    }

    markdown = _build_markdown(summary, audit_rows)
    return {"summary": summary, "rows": audit_rows, "markdown": markdown}


def _build_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Strict Evidence Daily Audit",
        "",
        f"- targetDate: {summary['targetDate']}",
        f"- generatedAt: {summary['generatedAt']}",
        f"- primaryBlockingReason: {summary['primaryBlockingReason']}",
        f"- secondaryBlockingReasons: {', '.join(summary['secondaryBlockingReasons']) or 'none'}",
        f"- currentBlockingStage: {summary['currentBlockingStage']}",
        "",
        "## Strict counts",
        f"- newMetadataCandidateCount: {summary['newMetadataCandidateCount']}",
        f"- strictEligibleCandidateCount: {summary['strictEligibleCandidateCount']}",
        f"- frozenCandidateCount: {summary['frozenCandidateCount']}",
        f"- settledCandidateCount: {summary['settledCandidateCount']}",
        f"- preDeadlineOddsCoverage: {summary['preDeadlineOddsCoverage']}",
        f"- settlementCoverage: {summary['settlementCoverage']}",
        f"- duplicateCandidateIdCount: {summary['duplicateCandidateIdCount']}",
        f"- deadlineViolationCount: {summary['deadlineViolationCount']}",
        f"- strictSettlementJoinFailedCount: {summary['strictSettlementJoinFailedCount']}",
        "",
        "## Lifecycle counts",
    ]
    for key, value in summary["lifecycleCounts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Legacy reference only",
            f"- legacyCandidateCount: {summary['legacyReference']['legacyCandidateCount']}",
            f"- legacySettledCandidateCount: {summary['legacyReference']['legacySettledCandidateCount']}",
            f"- legacySettlementJoinFailedCount: {summary['legacyReference']['legacySettlementJoinFailedCount']}",
            f"- legacyTraceCoverage: {summary['legacyReference']['legacyTraceCoverage']}",
            "- legacyMixedIntoStrict: False",
            "",
            "## Safety",
            "- prediction logic changed: False",
            "- BUY / EV / voting: disabled",
            "- frozen_bets overwritten: False",
            "- external access: False",
        ]
    )
    if not rows:
        lines.extend(["", "No target-date candidate rows were available."])
    return "\n".join(lines) + "\n"


def _output_paths(target_date: str) -> tuple[Path, Path, Path]:
    stem = MONITORING_ROOT / f"strict_evidence_daily_audit_{target_date}"
    return stem.with_suffix(".json"), stem.with_suffix(".csv"), stem.with_suffix(".md")


def _write_outputs(target_date: str, payload: dict[str, Any]) -> tuple[Path, Path, Path]:
    json_path, csv_path, md_path = _output_paths(target_date)
    _write_json(json_path, payload["summary"])
    _write_csv(csv_path, payload["rows"], _row_fieldnames())
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(payload["markdown"], encoding="utf-8")
    return json_path, csv_path, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build read-only strict evidence daily audit")
    parser.add_argument("--date", dest="target_date")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--candidate-trace-audit", type=Path, default=TRACE_AUDIT)
    parser.add_argument("--candidate-trace-rows", type=Path, default=TRACE_ROWS)
    parser.add_argument("--live-evidence-gate", type=Path, default=LIVE_GATE)
    parser.add_argument("--live-summary", type=Path, default=LIVE_SUMMARY)
    parser.add_argument("--daily-ops-check", type=Path)
    args = parser.parse_args(argv)
    target_date = args.target_date or (date.today() - timedelta(days=1)).isoformat()
    payload = build_strict_evidence_daily_audit(
        target_date=target_date,
        candidate_trace_audit_path=args.candidate_trace_audit,
        candidate_trace_rows_path=args.candidate_trace_rows,
        live_evidence_gate_path=args.live_evidence_gate,
        live_summary_path=args.live_summary,
        daily_ops_check_path=args.daily_ops_check,
    )
    paths = None if args.dry_run else _write_outputs(target_date, payload)
    print(
        json.dumps(
            {
                "targetDate": target_date,
                "primaryBlockingReason": payload["summary"]["primaryBlockingReason"],
                "currentBlockingStage": payload["summary"]["currentBlockingStage"],
                "newMetadataCandidateCount": payload["summary"]["newMetadataCandidateCount"],
                "strictEligibleCandidateCount": payload["summary"]["strictEligibleCandidateCount"],
                "frozenCandidateCount": payload["summary"]["frozenCandidateCount"],
                "settledCandidateCount": payload["summary"]["settledCandidateCount"],
                "outputPaths": [str(path) for path in paths] if paths else [],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
