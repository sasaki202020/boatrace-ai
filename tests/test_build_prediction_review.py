from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts import build_prediction_review as review_mod


def test_build_prediction_review_handles_missing_actual_trifecta_dtype(tmp_path: Path, monkeypatch) -> None:
    source_date = "2026-05-17"
    pred_root = tmp_path / "reports" / "predictions"
    daily_root = tmp_path / "reports" / "daily"
    consensus_root = tmp_path / "reports" / "consensus"
    sheet_path = pred_root / source_date / "prediction_sheet.csv"
    daily_summary_path = daily_root / source_date / "daily_summary.json"
    daily_eval_path = daily_root / source_date / "daily_evaluation_race_results.csv"

    monkeypatch.setattr(review_mod, "REPORTS_PREDICTIONS_ROOT", pred_root)
    monkeypatch.setattr(review_mod, "REPORTS_DAILY_ROOT", daily_root)
    monkeypatch.setattr(review_mod, "REPORTS_CONSENSUS_ROOT", consensus_root)
    monkeypatch.setattr(
        review_mod,
        "resolve_prediction_sheet",
        lambda date_text: {
            "status": "ok",
            "sourceDate": source_date,
            "requestedDate": date_text,
            "fallbackReason": "",
        },
    )
    monkeypatch.setattr(review_mod, "_truth_map_for_date", lambda date_text: {"20260517-01-01": "1-2-3"})
    monkeypatch.setattr(review_mod, "_consensus_frame", lambda date_text: (pd.DataFrame(), {}, {}))

    sheet_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "race_id": "20260517-01-01",
                "venue": "福岡",
                "race_no": 1,
                "combo": "1-2-3",
                "paper_decision": "BUY",
                "expected_value": 1.5,
                "approx_prob": 0.42,
                "final_decision": "BUY",
                "stop_reason": "",
                "odds_status": "real_odds_available",
                "reason": "test",
            }
        ]
    ).to_csv(sheet_path, index=False, encoding="utf-8")

    daily_summary_path.parent.mkdir(parents=True, exist_ok=True)
    daily_summary_path.write_text(
        json.dumps({"results_status": "available", "results_available": True}, ensure_ascii=False),
        encoding="utf-8",
    )

    pd.DataFrame(
        [
            {
                "race_id": "20260517-01-01",
                "combo": "1-2-3",
                "result_available": 0,
                "hit": 0,
                "pnl": 0.0,
                "settled_odds": 12.3,
                "actual_trifecta": float("nan"),
                "date_result": float("nan"),
                "stake_amount": 100,
                "payout_amount": 0,
            }
        ]
    ).to_csv(daily_eval_path, index=False, encoding="utf-8")

    review = review_mod.build_prediction_review(source_date)

    assert review["status"] == "ok"
    assert review["topCandidates"][0]["resultStatus"] == "hit"
    assert review["topCandidates"][0]["paperDecision"] == "BUY"
    assert (pred_root / source_date / "prediction_review.json").exists()
