from __future__ import annotations

import sqlite3

import pytest

from src.research_memory_v1.store import (
    append_experiment,
    initialize_registry,
    validate_research_state,
    verify_registry,
)


def _experiment(experiment_id: str = "EXP-TEST-001") -> dict:
    return {
        "experimentId": experiment_id,
        "hypothesis": "forward feature improves log loss",
        "datasetPeriod": "CONSUMED_DIAGNOSTIC_WINDOW",
        "modelVersion": "tree_15",
        "baseline": {"logLoss": 1.24},
        "result": {"logLoss": 1.25, "top1Accuracy": 0.55},
        "decision": "rejected",
        "reason": "baseline_not_beaten",
        "sourceReports": ["reports/offline_model_v5/final_report.json"],
        "createdAt": "2026-07-31T00:00:00+00:00",
    }


def test_registry_is_append_only_and_idempotent(tmp_path):
    path = tmp_path / "experiment_registry.sqlite3"
    payload = _experiment()

    first = append_experiment(path, payload)
    second = append_experiment(path, payload)

    assert first == second
    assert verify_registry(path) == {"valid": True, "experimentCount": 1, "tailHash": first}

    with pytest.raises(ValueError, match="experiment_id_conflict"):
        append_experiment(path, {**payload, "reason": "changed"})

    connection = sqlite3.connect(path)
    with pytest.raises(sqlite3.DatabaseError, match="append_only"):
        connection.execute(
            "UPDATE experiments SET payload_json = payload_json WHERE experiment_id = 'EXP-TEST-001'"
        )
    with pytest.raises(sqlite3.DatabaseError, match="append_only"):
        connection.execute("DELETE FROM experiments WHERE experiment_id = 'EXP-TEST-001'")
    connection.close()


def test_research_state_rejects_production_connections():
    state = {
        "schemaVersion": 1,
        "usageMode": "RESEARCH_ONLY",
        "productionConnected": False,
        "prospectiveConnected": False,
        "productionAdoptionAllowed": False,
        "currentModelVersion": {"modelId": "tree_15"},
        "activeFeatures": ["lane"],
        "knownProblems": [],
        "nextHypotheses": [],
    }
    validate_research_state(state)
    with pytest.raises(ValueError, match="productionConnected"):
        validate_research_state({**state, "productionConnected": True})


def test_research_memory_allows_evaluation_but_rejects_betting_and_roi_fields(tmp_path):
    state = {
        "schemaVersion": 1,
        "usageMode": "RESEARCH_ONLY",
        "productionConnected": False,
        "prospectiveConnected": False,
        "productionAdoptionAllowed": False,
        "currentModelVersion": {"modelId": "tree_15"},
        "activeFeatures": ["lane"],
        "knownProblems": [],
        "nextHypotheses": [{"evaluation": "chronological_5_fold_oof"}],
    }
    validate_research_state(state)
    with pytest.raises(ValueError, match="prohibited_key"):
        append_experiment(
            tmp_path / "experiment_registry.sqlite3",
            {**_experiment(), "result": {"roi": 1.0}},
        )
    with pytest.raises(ValueError, match="prohibited_key"):
        append_experiment(
            tmp_path / "experiment_registry.sqlite3",
            {**_experiment(), "result": {"evScore": 0.1}},
        )
