from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


REQUIRED_TOP_LEVEL_KEYS = {
    "schemaVersion",
    "mode",
    "baselines",
    "challenger",
    "featureGroup",
    "split",
    "comparison",
    "diagnosticGate",
    "decisionGate",
    "adoption",
}


def load_oof_spec(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != REQUIRED_TOP_LEVEL_KEYS:
        raise ValueError("oof_spec_schema_invalid")
    split = payload.get("split")
    if not isinstance(split, dict):
        raise ValueError("oof_spec_split_invalid")
    if (
        split.get("method") != "chronological_5_fold"
        or split.get("sameRaceSingleFold") is not True
        or split.get("groupBy") != "targetDate"
        or split.get("gapDays") != 0
        or split.get("validationBalancing") != "minimize_max_race_count_deviation"
        or split.get("randomSplit") is not False
        or split.get("preprocessingFit") != "train_only"
        or split.get("calibrationFit") != "train_only"
    ):
        raise ValueError("oof_spec_split_not_fail_closed")
    comparison = payload.get("comparison")
    if not isinstance(comparison, dict) or comparison.get("pairedByRace") is not True:
        raise ValueError("oof_spec_comparison_invalid")
    if comparison.get("primaryMetric") != "log_loss":
        raise ValueError("oof_spec_primary_metric_invalid")
    if comparison.get("secondaryMetrics") != ["brier", "top1", "ece"]:
        raise ValueError("oof_spec_secondary_metrics_invalid")
    adoption = payload.get("adoption")
    if (
        not isinstance(adoption, dict)
        or adoption.get("requiresExplicitApproval") is not True
        or adoption.get("productionAdoptionAllowed") is not False
        or adoption.get("personalAdoptionAllowed") is not False
    ):
        raise ValueError("oof_spec_adoption_not_fail_closed")
    for name in ("diagnosticGate", "decisionGate"):
        gate = payload.get(name)
        required_gate = {
            "minimumForwardDays",
            "minimumCoverage",
            "minimumFeatureSettledRaces",
            "minimumValidationRacesPerFold",
            "minimumOofRaces",
            "minimumOofDates",
            "newUnknownCount",
            "terminalConflictCount",
            "leakageCount",
            "hashChainValid",
            "productionRelevantFailureCount",
        }
        if not isinstance(gate, dict) or set(gate) != required_gate:
            raise ValueError(f"oof_spec_{name}_invalid")
    if int(payload["diagnosticGate"]["minimumValidationRacesPerFold"]) >= int(
        payload["decisionGate"]["minimumValidationRacesPerFold"]
    ):
        raise ValueError("oof_spec_gate_tiers_not_separated")
    return payload


def oof_execution_allowed(requested: bool, preflight: dict[str, Any]) -> bool:
    """Allow execution only after the decision-sized gate has passed."""
    return bool(
        requested
        and preflight.get("decisionGateEligible") is True
        and preflight.get("requiresExplicitApproval") is True
        and preflight.get("productionAdoptionAllowed") is False
    )


def _date_groups(races: Iterable[dict[str, Any]]) -> tuple[list[str], dict[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in races:
        grouped.setdefault(str(row["raceDate"]), []).append(row)
    dates = sorted(grouped)
    return dates, grouped


def plan_chronological_fold_groups(
    races: Iterable[dict[str, Any]],
    *,
    fold_count: int = 5,
) -> dict[str, Any]:
    """Plan expanding-window folds by targetDate without splitting a date."""
    dates, grouped = _date_groups(races)
    if len(dates) < fold_count + 1:
        return {"initialTrainDates": [], "foldDates": [], "blockedReasons": ["insufficient_dates_for_five_fold_preflight"]}

    # Keep the earliest sixth as initial train, matching the existing evaluator,
    # then choose contiguous boundaries that minimize validation-count imbalance.
    initial_end = max(1, len(dates) // (fold_count + 1))
    counts = [len(grouped[date]) for date in dates]
    prefix = [0]
    for count in counts:
        prefix.append(prefix[-1] + count)
    remaining_total = prefix[len(dates)] - prefix[initial_end]
    target = remaining_total / fold_count

    # Dynamic programming is used instead of greedy cuts so a locally good
    # boundary cannot leave the final fold with a disproportionate race count.
    # Each state stores (maximum deviation, total deviation, boundaries).
    states: list[dict[int, tuple[float, float, list[int]]]] = [
        {} for _ in range(fold_count + 1)
    ]
    states[0][initial_end] = (0.0, 0.0, [initial_end])
    for fold_number in range(1, fold_count + 1):
        min_end = initial_end + fold_number
        max_end = len(dates) - (fold_count - fold_number)
        for end in range(min_end, max_end + 1):
            best: tuple[float, float, list[int]] | None = None
            min_start = initial_end + fold_number - 1
            for start in range(min_start, end):
                previous = states[fold_number - 1].get(start)
                if previous is None:
                    continue
                deviation = abs((prefix[end] - prefix[start]) - target)
                candidate = (
                    max(previous[0], deviation),
                    previous[1] + deviation,
                    previous[2] + [end],
                )
                if best is None or candidate[:2] < best[:2] or (
                    candidate[:2] == best[:2] and candidate[2] < best[2]
                ):
                    best = candidate
            if best is not None:
                states[fold_number][end] = best

    final_state = states[fold_count].get(len(dates))
    if final_state is None:
        return {
            "initialTrainDates": dates[:initial_end],
            "foldDates": [],
            "boundaries": [initial_end],
            "blockedReasons": ["unable_to_balance_chronological_fold_groups"],
        }
    boundaries = final_state[2]
    fold_dates = [dates[boundaries[index] : boundaries[index + 1]] for index in range(fold_count)]
    return {
        "initialTrainDates": dates[:initial_end],
        "foldDates": fold_dates,
        "boundaries": boundaries,
        "blockedReasons": [],
    }


def build_fold_preflight(
    races: Iterable[dict[str, Any]],
    *,
    minimum_validation_races_per_fold: int,
) -> dict[str, Any]:
    """Describe chronological folds without fitting or evaluating a model."""
    ordered = sorted(
        {str(row["raceKey"]): row for row in races}.values(),
        key=lambda row: (str(row["raceDate"]), str(row["raceKey"])),
    )
    dates, grouped = _date_groups(ordered)
    plan = plan_chronological_fold_groups(ordered)
    if not plan["foldDates"]:
        return {
            "method": "chronological_5_fold",
            "groupBy": "targetDate",
            "randomSplit": False,
            "foldCount": 0,
            "folds": [],
            "minimumValidationRacesPerFold": minimum_validation_races_per_fold,
            "minimumsMet": False,
            "blockedReasons": plan["blockedReasons"],
            "accounting": {
                "totalEligibleRaceCount": len(ordered),
                "initialTrainRaceCount": 0,
                "gapExcludedRaceCount": 0,
                "validationRaceCount": 0,
                "otherExcludedRaceCount": len(ordered),
                "exclusionReasonCounts": {"insufficient_dates": len(ordered)},
                "accountingPass": False,
            },
        }

    initial_dates = set(plan["initialTrainDates"])
    validation_dates = {date for fold in plan["foldDates"] for date in fold}
    initial_rows = [row for row in ordered if str(row["raceDate"]) in initial_dates]
    validation_rows = [row for row in ordered if str(row["raceDate"]) in validation_dates]
    gap_rows: list[dict[str, Any]] = []
    other_rows = [
        row for row in ordered
        if str(row["raceDate"]) not in initial_dates
        and str(row["raceDate"]) not in validation_dates
    ]
    folds: list[dict[str, Any]] = []
    for number, fold_dates in enumerate(plan["foldDates"], start=1):
        fold_rows = [row for row in ordered if str(row["raceDate"]) in set(fold_dates)]
        train_rows = initial_rows + [
            row for row in ordered
            if str(row["raceDate"]) in {
                date for prior in plan["foldDates"][: number - 1] for date in prior
            }
        ]
        folds.append({
            "fold": number,
            "trainStart": str(train_rows[0]["raceDate"]) if train_rows else None,
            "trainEnd": str(train_rows[-1]["raceDate"]) if train_rows else None,
            "validationStart": str(fold_rows[0]["raceDate"]) if fold_rows else None,
            "validationEnd": str(fold_rows[-1]["raceDate"]) if fold_rows else None,
            "trainDateCount": len({str(row["raceDate"]) for row in train_rows}),
            "validationDateCount": len(fold_dates),
            "trainRaceCount": len(train_rows),
            "validationRaceCount": len(fold_rows),
            "raceOverlap": len(
                {str(row["raceKey"]) for row in train_rows}
                & {str(row["raceKey"]) for row in fold_rows}
            ),
            "dateOverlap": len(
                {str(row["raceDate"]) for row in train_rows}
                & {str(row["raceDate"]) for row in fold_rows}
            ),
        })
    accounting = {
        "totalEligibleRaceCount": len(ordered),
        "initialTrainRaceCount": len(initial_rows),
        "gapExcludedRaceCount": len(gap_rows),
        "validationRaceCount": len(validation_rows),
        "otherExcludedRaceCount": len(other_rows),
        "exclusionReasonCounts": {
            "initial_train": len(initial_rows),
            "gap_days": len(gap_rows),
            "other": len(other_rows),
        },
    }
    accounting["accountingPass"] = (
        accounting["totalEligibleRaceCount"]
        == accounting["initialTrainRaceCount"]
        + accounting["gapExcludedRaceCount"]
        + accounting["validationRaceCount"]
        + accounting["otherExcludedRaceCount"]
    )
    blocked = list(plan["blockedReasons"])
    if len(folds) != 5:
        blocked.append("five_folds_not_constructible")
    if any(int(fold["validationRaceCount"]) < minimum_validation_races_per_fold for fold in folds):
        blocked.append("minimum_validation_races_per_fold_not_met")
    if any(int(fold["raceOverlap"]) != 0 for fold in folds):
        blocked.append("race_overlap_present")
    if any(int(fold["dateOverlap"]) != 0 for fold in folds):
        blocked.append("date_overlap_present")
    if not accounting["accountingPass"]:
        blocked.append("fold_accounting_mismatch")
    return {
        "method": "chronological_5_fold",
        "groupBy": "targetDate",
        "randomSplit": False,
        "foldCount": len(folds),
        "folds": folds,
        "boundaries": plan.get("boundaries", []),
        "initialTrainDates": plan["initialTrainDates"],
        "minimumValidationRacesPerFold": minimum_validation_races_per_fold,
        "minimumsMet": not blocked,
        "blockedReasons": sorted(set(blocked)),
        "accounting": accounting,
    }


def _gate_reasons(
    gate: dict[str, Any],
    *,
    forward_days: int,
    coverage: float | None,
    feature_settled_races: int,
    new_unknown_count: int,
    terminal_conflict_count: int,
    leakage_count: int,
    hash_chain_valid: bool,
    production_relevant_failure_count: int,
    fold_preflight: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if forward_days < int(gate["minimumForwardDays"]):
        reasons.append("minimum_forward_days_not_met")
    if coverage is None or float(coverage) < float(gate["minimumCoverage"]):
        reasons.append("minimum_coverage_not_met")
    if feature_settled_races < int(gate["minimumFeatureSettledRaces"]):
        reasons.append("minimum_feature_settled_races_not_met")
    if new_unknown_count != int(gate["newUnknownCount"]):
        reasons.append("new_unknown_count_nonzero")
    if terminal_conflict_count != int(gate["terminalConflictCount"]):
        reasons.append("terminal_status_conflict_nonzero")
    if leakage_count != int(gate["leakageCount"]):
        reasons.append("leakage_count_nonzero")
    if bool(hash_chain_valid) is not bool(gate["hashChainValid"]):
        reasons.append("hash_chain_invalid")
    if production_relevant_failure_count != int(gate["productionRelevantFailureCount"]):
        reasons.append("production_relevant_failures_present")
    if any(
        int(fold["validationRaceCount"]) < int(gate["minimumValidationRacesPerFold"])
        for fold in fold_preflight.get("folds", [])
    ):
        reasons.append("minimum_validation_races_per_fold_not_met")
    if int(fold_preflight.get("foldCount") or 0) != 5:
        reasons.append("five_folds_not_constructible")
    if int(fold_preflight.get("accounting", {}).get("validationRaceCount") or 0) < int(gate["minimumOofRaces"]):
        reasons.append("minimum_oof_races_not_met")
    validation_dates = sum(int(fold.get("validationDateCount") or 0) for fold in fold_preflight.get("folds", []))
    if validation_dates < int(gate["minimumOofDates"]):
        reasons.append("minimum_oof_dates_not_met")
    reasons.extend(str(reason) for reason in fold_preflight.get("blockedReasons", []))
    return sorted(set(reasons))


def build_oof_preflight(
    *,
    spec: dict[str, Any],
    forward_days: int,
    coverage: float | None,
    feature_settled_races: int,
    new_unknown_count: int,
    terminal_conflict_count: int,
    leakage_count: int,
    hash_chain_valid: bool,
    fold_preflight: dict[str, Any],
    snapshot: dict[str, Any],
    production_relevant_failure_count: int = 0,
) -> dict[str, Any]:
    diagnostic_reasons = _gate_reasons(
        spec["diagnosticGate"],
        forward_days=forward_days,
        coverage=coverage,
        feature_settled_races=feature_settled_races,
        new_unknown_count=new_unknown_count,
        terminal_conflict_count=terminal_conflict_count,
        leakage_count=leakage_count,
        hash_chain_valid=hash_chain_valid,
        production_relevant_failure_count=production_relevant_failure_count,
        fold_preflight=fold_preflight,
    )
    decision_reasons = _gate_reasons(
        spec["decisionGate"],
        forward_days=forward_days,
        coverage=coverage,
        feature_settled_races=feature_settled_races,
        new_unknown_count=new_unknown_count,
        terminal_conflict_count=terminal_conflict_count,
        leakage_count=leakage_count,
        hash_chain_valid=hash_chain_valid,
        production_relevant_failure_count=production_relevant_failure_count,
        fold_preflight=fold_preflight,
    )
    diagnostic_eligible = not diagnostic_reasons
    decision_eligible = not decision_reasons
    if not diagnostic_eligible:
        status = "BLOCKED_WAITING_FOR_EXTERNAL_DATA"
    elif not decision_eligible:
        status = "DIAGNOSTIC_OOF_READY_AWAITING_DECISION_SAMPLE"
    else:
        status = "DECISION_OOF_READY_AWAITING_APPROVAL"
    blocked = diagnostic_reasons if not diagnostic_eligible else decision_reasons
    blocked = sorted(set(blocked) | {"explicit_oof_execution_approval_required"})
    return {
        "schemaVersion": 2,
        "status": status,
        "executionAllowed": False,
        "executionRequested": False,
        "dataGateEligible": diagnostic_eligible,
        "diagnosticGateEligible": diagnostic_eligible,
        "decisionGateEligible": decision_eligible,
        "requiresExplicitApproval": bool(spec["adoption"].get("requiresExplicitApproval")),
        "featureGroup": spec["featureGroup"],
        "primaryMetric": spec["comparison"]["primaryMetric"],
        "forwardDays": forward_days,
        "coverage": coverage,
        "featureSettledRaces": feature_settled_races,
        "newUnknownCount": new_unknown_count,
        "terminalConflictCount": terminal_conflict_count,
        "leakageCount": leakage_count,
        "hashChainValid": hash_chain_valid,
        "productionRelevantFailureCount": production_relevant_failure_count,
        "snapshot": snapshot,
        "foldPreflight": fold_preflight,
        "diagnosticGate": {"eligible": diagnostic_eligible, "blockedReasons": diagnostic_reasons},
        "decisionGate": {"eligible": decision_eligible, "blockedReasons": decision_reasons},
        "blockedReasons": blocked,
        "productionAdoptionAllowed": False,
    }
