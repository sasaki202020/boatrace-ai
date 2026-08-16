from __future__ import annotations

import pytest

from src.market_evaluation_v1.odds_snapshots import compute_ev_band_metrics
from src.market_evaluation_v1.registry import append_experiment, verify_registry


def test_ev_is_blocked_until_payout_unit_is_verified() -> None:
    result = compute_ev_band_metrics(
        [{"modelProbability": 0.2, "decisionOdds": 6.0}],
    )
    assert result["status"] == "BLOCKED_PAYOUT_UNIT_UNVERIFIED"


def test_ev_band_uses_explicit_settlement_amount_only() -> None:
    result = compute_ev_band_metrics(
        [
            {
                "modelProbability": 0.2,
                "decisionOdds": 6.0,
                "payoutUnitVerified": True,
                "stake": 100.0,
                "realizedReturn": 0.0,
            }
        ]
    )
    assert result["status"] == "OK"
    assert result["rows"][0]["rawEV"] == pytest.approx(0.2)
    assert result["bands"]["20%+"]["roi"] == -1.0
    assert result["topPayoutExcluded"]["1"]["settledCount"] == 0


def _experiment() -> dict[str, object]:
    return {
        "experimentId": "MARKET-20260802-001",
        "featureSet": "market_probability_only",
        "modelVersion": "tree_15",
        "policyVersion": "research_only_v1",
        "codeCommit": "a" * 40,
        "dataSnapshot": "b" * 64,
        "trainPeriod": "UNKNOWN",
        "validationPeriod": "UNKNOWN",
        "holdoutPeriod": "UNKNOWN",
        "trialNumber": 1,
        "metrics": {"status": "blocked_no_local_complete_odds"},
        "decision": "blocked",
        "reason": "No complete 120-way local odds snapshot supplied.",
        "createdAt": "2026-08-02T00:00:00+09:00",
        "researchOnly": True,
        "productionAdoptionAllowed": False,
    }


def test_experiment_registry_is_append_only_and_isolated(tmp_path) -> None:
    path = tmp_path / "experiments.sqlite3"
    record_hash = append_experiment(path, _experiment())
    assert len(record_hash) == 64
    assert verify_registry(path)["experimentCount"] == 1
    assert append_experiment(path, _experiment()) == record_hash
