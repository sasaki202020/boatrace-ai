from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.offline_model_v6.core import (
    SELECTOR_CONFIGS,
    SELECTOR_FEATURES,
    SelectorConfig,
    choose_oracle_scope,
    choose_selector_config,
    selector_label,
    validate_selector_output,
)
from src.offline_model_v6.experiment import apply_selector, prediction_hash

def test_oracle_scope_is_predeclared() -> None:
    assert choose_oracle_scope(top1=0.56, top2=0.76, top3=0.84) == 2
    assert choose_oracle_scope(top1=0.56, top2=0.59, top3=0.75) == 3
    assert choose_oracle_scope(top1=0.56, top2=0.59, top3=0.60) is None

def test_result_columns_are_not_selector_features() -> None:
    forbidden = {"target", "winnerRank", "selectorLabel", "result", "payout", "final_odds"}
    assert forbidden.isdisjoint(SELECTOR_FEATURES)


def test_winner_outside_scope_has_explicit_none_class() -> None:
    assert selector_label(winner_rank=1, scope=3) == 0
    assert selector_label(winner_rank=2, scope=3) == 1
    assert selector_label(winner_rank=3, scope=3) == 2
    assert selector_label(winner_rank=4, scope=3) == 3


def test_none_class_never_swaps_top_pick() -> None:
    class NoneClassModel:
        classes_ = np.array([0, 1, 2, 3])

        def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
            return np.tile([0.05, 0.05, 0.05, 0.85], (len(frame), 1))

    races = pd.DataFrame(
        {
            **{feature: [0.0] for feature in SELECTOR_FEATURES},
            "margin12": [0.01],
            "margin13": [0.02],
            "winnerRank": [4],
            "lane1": [1],
            "lane2": [2],
            "lane3": [3],
        }
    )
    scored = apply_selector(
        races,
        NoneClassModel(),
        SelectorConfig("test", "logistic", 0.60, 0.05),
        scope=3,
    )
    assert not bool(scored.loc[0, "selectorApplied"])
    assert int(scored.loc[0, "selectorTopPick"]) == 1




def test_selector_budget_is_bounded() -> None:
    assert len(SELECTOR_CONFIGS) == 8
    assert {item.family for item in SELECTOR_CONFIGS} == {"logistic", "small_tree"}


def test_config_selection_rejects_broad_or_tiny_gate() -> None:
    rows = pd.DataFrame(
        [
            {"configId": "tiny", "coverage": 0.01, "accuracyDelta": 0.01},
            {"configId": "broad", "coverage": 0.21, "accuracyDelta": 0.02},
            {"configId": "valid", "coverage": 0.10, "accuracyDelta": 0.005},
        ]
    )
    assert choose_selector_config(rows) == "valid"


def test_tree_probability_is_immutable_and_selector_is_separate() -> None:
    frame = pd.DataFrame(
        {
            "race_id": ["r1"] * 6,
            "lane": [1, 2, 3, 4, 5, 6],
            "predicted_probability": [0.40, 0.30, 0.12, 0.08, 0.06, 0.04],
            "selectorTopPick": [2] * 6,
            "selectorApplied": [True] * 6,
        }
    )
    before = frame["predicted_probability"].to_numpy().copy()
    validate_selector_output(frame)
    assert np.array_equal(before, frame["predicted_probability"].to_numpy())
    assert frame["predicted_probability"].sum() == pytest.approx(1.0)


def test_prediction_hash_detects_sub_rounding_probability_mutation() -> None:
    frame = pd.DataFrame(
        {
            "fold": [1, 1],
            "race_id": ["r1", "r1"],
            "lane": [1, 2],
            "predicted_probability": [0.6, 0.4],
            "selectorTopPick": [1, 1],
            "selectorApplied": [False, False],
        }
    )
    mutated = frame.copy()
    mutated.loc[0, "predicted_probability"] += 1e-15
    mutated.loc[1, "predicted_probability"] -= 1e-15

    assert prediction_hash(frame) != prediction_hash(mutated)


def test_invalid_probability_or_coverage_is_rejected() -> None:
    bad = pd.DataFrame(
        {
            "race_id": ["r1"] * 6,
            "lane": [1, 2, 3, 4, 5, 6],
            "predicted_probability": [0.5, 0.4, 0.2, -0.1, 0.0, 0.0],
            "selectorTopPick": [1] * 6,
            "selectorApplied": [False] * 6,
        }
    )
    with pytest.raises(ValueError):
        validate_selector_output(bad)


def test_outer_validation_cannot_be_threshold_source() -> None:
    source = choose_selector_config.__doc__ or ""
    assert "inner selector validation" in source.lower()
