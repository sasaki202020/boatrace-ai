"""Fail-closed gates for future pre-race feature research."""
from __future__ import annotations
from typing import Any, Mapping


def evaluate_feature_candidate(feature: Mapping[str, Any]) -> dict[str, Any]:
    conditions = {
        "predictionTimestampVerified": feature.get("predictionTimestampVerified") is True,
        "resultLeakageCount": feature.get("resultLeakageCount") == 0,
        "coverage": isinstance(feature.get("coverage"), (int, float)) and feature["coverage"] >= 0.80,
        "deterministicParsing": feature.get("deterministicParsing") is True,
        "provenanceStored": feature.get("provenanceStored") is True,
        "forwardCollectionDays": isinstance(feature.get("forwardCollectionDays"), int)
        and feature["forwardCollectionDays"] >= 30,
        "existingPathChanged": feature.get("existingPathChanged") is False,
    }
    failed = [name for name, passed in conditions.items() if not passed]
    return {
        "status": "PROSPECTIVE_FEATURE_CANDIDATE" if not failed else "FEATURE_SOURCE_NOT_READY",
        "failedConditions": failed,
        "conditions": conditions,
    }


def evaluate_research_start_gate(
    *, settled_races: int, recurring_error_segment: bool,
    feature_candidates: list[Mapping[str, Any]],
) -> dict[str, Any]:
    condition_a = settled_races >= 1_500 and recurring_error_segment
    condition_b = any(
        item.get("status") == "PROSPECTIVE_FEATURE_CANDIDATE"
        and item.get("forwardCollectionDays", 0) >= 30
        for item in feature_candidates
    )
    allowed = condition_a or condition_b
    return {
        "researchStartAllowed": allowed,
        "trigger": "A" if condition_a else "B" if condition_b else None,
        "settledRaces": settled_races,
        "stageA300RacesDiagnosticOnly": True,
        "automaticTrainingAllowed": False,
    }
