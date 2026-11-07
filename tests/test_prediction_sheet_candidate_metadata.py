from __future__ import annotations

from src.pipeline.prediction_sheet import _rows_to_frozen_payload


def test_prediction_sheet_frozen_payload_adds_forward_metadata() -> None:
    payload = _rows_to_frozen_payload(
        "2026-07-10",
        [
            {
                "date": "2026-07-10",
                "venue": "大村",
                "jcd": "24",
                "race_no": 1,
                "race_id": "20260710-24-01",
                "deadline": "2026-07-10T12:00:00",
                "combo": "1-2-3",
                "paper_decision": "WATCH",
                "final_decision": "SKIP",
                "reason": "paper_only",
                "approx_prob": 0.123,
                "real_odds": 8.1,
                "predictionHash": "hash-1",
            }
        ],
    )

    bet = payload["races"][0]["bets"][0]
    assert bet["candidateId"]
    assert bet["modelVersion"] == "baseline_rule_v1"
    assert bet["policyVersion"] == "paper_shadow_policy_v1"
    assert bet["predictionHash"] == "hash-1"
    assert bet["rawProbability"] == 0.123
    assert bet["calibratedProbability"] == 0.123
    assert bet["odds"] == 8.1
    assert bet["deadlineAt"] == "2026-07-10T12:00:00"
    assert bet["frozenAt"] != "legacy_unknown"
