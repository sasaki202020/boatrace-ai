from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.offline_model_v5.core import (
    GATE_FEATURES,
    build_gate_features,
    build_inner_splits,
    gated_blend,
    gate_weights,
    select_best_passing,
    validate_experiment_budget,
)


def race_frame(days: int = 12) -> pd.DataFrame:
    rows = []
    for day in pd.date_range("2026-01-01", periods=days):
        for race_no in (1, 2):
            for lane in range(1, 7):
                rows.append({
                    "date": day.strftime("%Y-%m-%d"),
                    "race_id": f"{day:%Y%m%d}-01-{race_no:02d}",
                    "jcd": 1,
                    "race_no": race_no,
                    "lane": lane,
                    "target": int(lane == 1),
                    "feature_availability_count": 3,
                    "missingness_count": 0,
                })
    return pd.DataFrame(rows)


def probabilities(frame: pd.DataFrame, reverse: bool = False) -> np.ndarray:
    base = np.array([0.50, 0.20, 0.12, 0.08, 0.06, 0.04])
    if reverse:
        base = base[::-1]
    return np.tile(base, frame["race_id"].nunique())


def test_inner_splits_are_chronological_and_exclude_outer_validation() -> None:
    frame = race_frame()
    outer_validation_start = "2026-01-11"
    splits = build_inner_splits(frame[frame["date"] < outer_validation_start], folds=3, validation_days=2)
    for fold in splits["folds"]:
        assert fold["trainEnd"] < fold["validationStart"]
        assert fold["validationEnd"] < outer_validation_start
        assert fold["raceOverlapCount"] == 0


def test_gate_features_are_result_free_and_limited_to_six() -> None:
    frame = race_frame(days=1)
    gate = build_gate_features(frame, probabilities(frame), probabilities(frame, reverse=True))
    assert list(gate.columns) == ["race_id", *GATE_FEATURES]
    assert len(GATE_FEATURES) == 6
    forbidden = {"target", "winner", "finish_position", "payout", "result", "final_odds"}
    assert forbidden.isdisjoint(gate.columns)


def test_blend_is_finite_normalized_and_zero_gate_matches_tree() -> None:
    frame = race_frame(days=1)
    tree = probabilities(frame)
    residual = probabilities(frame, reverse=True)
    same = gated_blend(frame, tree, residual, np.zeros(frame["race_id"].nunique()))
    assert np.array_equal(same, tree)
    blended = gated_blend(frame, tree, residual, np.full(frame["race_id"].nunique(), 0.1))
    sums = pd.Series(blended).groupby(frame["race_id"].to_numpy()).sum()
    assert np.isfinite(blended).all()
    assert np.allclose(sums, 1.0)


def test_gate_range_and_shape_are_enforced() -> None:
    frame = race_frame(days=1)
    tree = probabilities(frame)
    residual = probabilities(frame, reverse=True)
    with pytest.raises(ValueError, match="gate_range_violation"):
        gated_blend(frame, tree, residual, np.full(2, 0.11), g_max=0.10)
    with pytest.raises(ValueError, match="gate_shape_violation"):
        gated_blend(frame, tree, residual, np.array([0.1]))


def test_gate_weights_have_real_zero_activation_and_respect_maximum() -> None:
    weights = gate_weights(np.array([0.2, 0.5, 0.6, 1.0]), g_max=0.2)
    assert np.array_equal(weights[:2], np.zeros(2))
    assert np.allclose(weights[2:], [0.04, 0.20])
    assert (weights >= 0).all() and (weights <= 0.2).all()


def test_experiment_budget_is_two_families_and_twelve_settings() -> None:
    validate_experiment_budget({"static": [0.02, 0.05, 0.10], "gated": [0.05, 0.10, 0.20]})
    with pytest.raises(ValueError, match="family_budget_exceeded"):
        validate_experiment_budget({"a": [1], "b": [1], "c": [1]})
    with pytest.raises(ValueError, match="setting_budget_exceeded"):
        validate_experiment_budget({"a": list(range(7)), "b": list(range(6))})


def test_best_passing_candidate_uses_primary_metric_not_name_order() -> None:
    aggregate = pd.DataFrame({"modelName": ["static_a02", "static_a10", "gated_g20"], "raceLogLoss": [1.20, 1.19, 1.18]})
    assert select_best_passing({"static_a02", "static_a10"}, aggregate) == "static_a10"
    assert select_best_passing(set(), aggregate) is None
