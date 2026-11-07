from __future__ import annotations

from src.predict.trifecta_builder import build_trifecta_candidates


def test_trifecta_builder_scores_and_ev() -> None:
    boat_scores = [
        {"boat_no": 1, "boat_score": 3.0},
        {"boat_no": 2, "boat_score": 2.0},
        {"boat_no": 3, "boat_score": 1.0},
    ]
    odds = [{"combo": "1-2-3", "odds": 12.0}]
    preds = build_trifecta_candidates(boat_scores, odds3t=odds)
    assert len(preds) == 6
    top = preds[0]
    assert top.combo
    assert top.prob > 0
    assert top.expected_value is not None
    assert top.edge is not None
