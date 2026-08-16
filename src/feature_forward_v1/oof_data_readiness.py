from __future__ import annotations

from typing import Any, Iterable


METRIC_FIELDS = (
    ("forwardCollectionDays", "minimumForwardDays"),
    ("matureCaptureCoverage", "minimumCoverage"),
    ("featureSettledRaceCount", "minimumFeatureSettledRaces"),
    ("oofDateCount", "minimumOofDates"),
    ("oofRaceCount", "minimumOofRaces"),
)
INTEGRITY_FIELDS = (
    ("newUnknownCount", "newUnknownCount"),
    ("terminalConflictCount", "terminalConflictCount"),
    ("leakageCount", "leakageCount"),
    ("productionRelevantFailureCount", "productionRelevantFailureCount"),
)


def _number(value: object, default: float = 0) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _remaining(gate: dict[str, Any], current: dict[str, Any], folds: list[dict[str, Any]]) -> dict[str, float | int]:
    result: dict[str, float | int] = {}
    for current_key, gate_key in METRIC_FIELDS:
        required = _number(gate[gate_key])
        actual = _number(current.get(current_key), default=-1 if current_key == "matureCaptureCoverage" else 0)
        remaining = max(required - actual, 0)
        result[current_key] = round(remaining, 12) if current_key == "matureCaptureCoverage" else int(remaining)
    minimum_fold = int(gate["minimumValidationRacesPerFold"])
    observed_fold = min((int(fold.get("validationRaceCount") or 0) for fold in folds), default=0)
    result["minimumFoldValidationRaceCount"] = max(minimum_fold - observed_fold, 0)
    for current_key, gate_key in INTEGRITY_FIELDS:
        result[current_key] = max(int(_number(current.get(current_key))) - int(gate[gate_key]), 0)
    result["hashChainValid"] = 0 if current.get("hashChainValid") is gate["hashChainValid"] else 1
    return result


def _reasons(gate: dict[str, Any], current: dict[str, Any], folds: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    if int(_number(current.get("forwardCollectionDays"))) < int(gate["minimumForwardDays"]):
        reasons.append("minimum_forward_days_not_met")
    coverage = current.get("matureCaptureCoverage")
    if coverage is None or _number(coverage, default=-1) < _number(gate["minimumCoverage"]):
        reasons.append("minimum_coverage_not_met")
    if int(_number(current.get("featureSettledRaceCount"))) < int(gate["minimumFeatureSettledRaces"]):
        reasons.append("minimum_feature_settled_races_not_met")
    if int(_number(current.get("oofDateCount"))) < int(gate["minimumOofDates"]):
        reasons.append("minimum_oof_dates_not_met")
    if int(_number(current.get("oofRaceCount"))) < int(gate["minimumOofRaces"]):
        reasons.append("minimum_oof_races_not_met")
    if len(folds) != 5:
        reasons.append("five_folds_not_constructible")
    elif any(int(fold.get("validationRaceCount") or 0) < int(gate["minimumValidationRacesPerFold"]) for fold in folds):
        reasons.append("minimum_validation_races_per_fold_not_met")
    for current_key, gate_key in INTEGRITY_FIELDS:
        if int(_number(current.get(current_key))) != int(gate[gate_key]):
            reasons.append({
                "newUnknownCount": "new_unknown_count_nonzero",
                "terminalConflictCount": "terminal_status_conflict_nonzero",
                "leakageCount": "leakage_count_nonzero",
                "productionRelevantFailureCount": "production_relevant_failures_present",
            }[current_key])
    if current.get("hashChainValid") is not gate["hashChainValid"]:
        reasons.append("hash_chain_invalid")
    total = int(_number(current.get("totalEligibleRaceCount")))
    initial = int(_number(current.get("initialTrainRaceCount")))
    validation = int(_number(current.get("validationRaceCount")))
    if total != initial + validation:
        reasons.append("cohort_accounting_mismatch")
    return sorted(set(reasons))


def _fold_rows(gates: dict[str, dict[str, Any]], folds: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, raw in enumerate(folds, start=1):
        row = dict(raw)
        count = int(row.get("validationRaceCount") or 0)
        row["fold"] = int(row.get("fold") or index)
        row["remaining"] = {
            name: max(int(gate["minimumValidationRacesPerFold"]) - count, 0)
            for name, gate in gates.items()
        }
        row["diagnosticReady"] = row["remaining"]["diagnostic"] == 0
        row["decisionReady"] = row["remaining"]["decision"] == 0
        output.append(row)
    return output


def build_oof_data_readiness(
    *,
    spec: dict[str, Any],
    current: dict[str, Any],
    folds: Iterable[dict[str, Any]],
    manual_ingest: dict[str, Any],
    evidence_errors: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a no-evaluation OOF data-readiness report from fresh local evidence."""
    gates = {
        "diagnostic": dict(spec["diagnosticGate"]),
        "decision": dict(spec["decisionGate"]),
    }
    fold_rows = _fold_rows(gates, list(folds))
    diagnostic_reasons = _reasons(gates["diagnostic"], current, fold_rows)
    decision_reasons = _reasons(gates["decision"], current, fold_rows)
    diagnostic_reasons = sorted(set(diagnostic_reasons) | {str(reason) for reason in evidence_errors})
    decision_reasons = sorted(set(decision_reasons) | {str(reason) for reason in evidence_errors})
    diagnostic_ready = not diagnostic_reasons
    decision_ready = not decision_reasons
    if decision_ready:
        status = "DECISION_DATA_READY_AWAITING_EXPLICIT_APPROVAL"
    elif diagnostic_ready:
        status = "DIAGNOSTIC_READY_AWAITING_DECISION_DATA"
    else:
        status = "BLOCKED_WAITING_FOR_EXTERNAL_DATA"
    return {
        "schemaVersion": 1,
        "status": status,
        "diagnosticReady": diagnostic_ready,
        "decisionReady": decision_ready,
        "current": dict(current),
        "required": gates,
        "remaining": {
            name: _remaining(gate, current, fold_rows)
            for name, gate in gates.items()
        },
        "folds": fold_rows,
        "integrity": {
            key: current.get(key)
            for key in (
                "newUnknownCount",
                "terminalConflictCount",
                "leakageCount",
                "timeOrderViolationCount",
                "hashChainValid",
                "productionRelevantFailureCount",
            )
        },
        "manualIngest": dict(manual_ingest),
        "blockedReasons": diagnostic_reasons if not diagnostic_ready else decision_reasons,
        "diagnosticBlockedReasons": diagnostic_reasons,
        "decisionBlockedReasons": decision_reasons,
        "predictionEdgeStatus": "PREDICTION_EDGE_UNPROVEN",
        "oofExecution": {"executed": False, "permitted": False},
        "productionAdoptionAllowed": False,
        "automatedNetworkFetchAllowed": False,
    }


def render_oof_data_readiness_markdown(report: dict[str, Any]) -> str:
    current = report["current"]
    diagnostic_remaining = report["remaining"]["diagnostic"]
    weakest_fold = min(
        report["folds"],
        key=lambda fold: int(fold.get("validationRaceCount") or 0),
        default=None,
    )
    lines = [
        "# OOF Data Readiness",
        "",
        f"- status: `{report['status']}`",
        f"- diagnosticReady: `{str(report['diagnosticReady']).lower()}`",
        f"- decisionReady: `{str(report['decisionReady']).lower()}`",
        f"- predictionEdgeStatus: `{report['predictionEdgeStatus']}`",
        "",
        "## 1. 現在値",
        "",
        f"- forwardCollectionDays: `{current.get('forwardCollectionDays')}`",
        f"- validCaptureCount: `{current.get('validCaptureCount')}`",
        f"- featureSettledRaceCount: `{current.get('featureSettledRaceCount')}`",
        f"- totalEligibleRaceCount: `{current.get('totalEligibleRaceCount')}`",
        f"- initialTrainRaceCount: `{current.get('initialTrainRaceCount')}`",
        f"- validationRaceCount: `{current.get('validationRaceCount')}`",
        f"- oofDateCount: `{current.get('oofDateCount')}`",
        f"- oofRaceCount: `{current.get('oofRaceCount')}`",
        "",
        "## 2. 必要値",
        "",
        f"- diagnostic: `{report['required']['diagnostic']}`",
        f"- decision: `{report['required']['decision']}`",
        "",
        "## 3. あと何日",
        "",
        f"- diagnostic forward days remaining: `{diagnostic_remaining['forwardCollectionDays']}`",
        f"- decision forward days remaining: `{report['remaining']['decision']['forwardCollectionDays']}`",
        f"- diagnostic OOF dates remaining: `{diagnostic_remaining['oofDateCount']}`",
        f"- decision OOF dates remaining: `{report['remaining']['decision']['oofDateCount']}`",
        "",
        "## 4. あと何race",
        "",
        f"- diagnostic settled races remaining: `{diagnostic_remaining['featureSettledRaceCount']}`",
        f"- diagnostic OOF races remaining: `{diagnostic_remaining['oofRaceCount']}`",
        f"- decision settled races remaining: `{report['remaining']['decision']['featureSettledRaceCount']}`",
        f"- decision OOF races remaining: `{report['remaining']['decision']['oofRaceCount']}`",
        f"- diagnostic minimum-fold races remaining: `{diagnostic_remaining['minimumFoldValidationRaceCount']}`",
        f"- decision minimum-fold races remaining: `{report['remaining']['decision']['minimumFoldValidationRaceCount']}`",
        "",
        "## 5. 最弱fold",
        "",
        (
            f"- fold `{weakest_fold['fold']}`: `{weakest_fold.get('validationRaceCount')}` validation races"
            if weakest_fold is not None
            else "- fold: unavailable"
        ),
        "",
        "## 6. Coverage",
        "",
        f"- matureCaptureCoverage: `{current.get('matureCaptureCoverage')}`",
        f"- diagnostic coverage remaining: `{diagnostic_remaining['matureCaptureCoverage']}`",
        "",
        "## 7. Integrity",
        "",
    ]
    lines.extend(f"- {key}: `{value}`" for key, value in report["integrity"].items())
    lines.extend(
        [
            "",
            "## 8. 次に必要なデータ",
            "",
            f"- blockedReasons: `{', '.join(report['blockedReasons']) or 'none'}`",
            f"- manualIngest: `{report['manualIngest'].get('status')}`",
            "- OOF評価は実行していない。診断・本判定とも明示承認があるまでモデル比較を開始しない。",
            "- 自動ネットワーク取得、BUY、EV、投票、production adoptionはこの処理で変更しない。",
            "",
        ]
    )
    return "\n".join(lines)
