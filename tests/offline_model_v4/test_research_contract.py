from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.offline_model_v4.core import (
    ExperimentSpec,
    assert_feature_contract,
    build_walk_forward_splits,
    canonical_config_hash,
    eligible_challengers,
    normalize_race_logits,
    normalize_race_probabilities,
    promotion_passes,
    select_challenger,
)


def sample_frame(days: int = 20) -> pd.DataFrame:
    rows = []
    for day in pd.date_range("2026-01-01", periods=days):
        for race_no in (1, 2):
            for lane in range(1, 7):
                rows.append({
                    "date": day.strftime("%Y-%m-%d"),
                    "race_id": f"{day:%Y%m%d}-01-{race_no:02d}",
                    "jcd": 1, "race_no": race_no, "lane": lane,
                    "target": int(lane == 1), "score": 7 - lane,
                })
    return pd.DataFrame(rows)


def test_walk_forward_is_chronological_and_has_no_race_overlap() -> None:
    manifest = build_walk_forward_splits(sample_frame(), folds=5, validation_days=2)
    assert manifest["evaluationLabel"] == "RESEARCH_WALK_FORWARD"
    assert "holdout" not in str(manifest).lower()
    for fold in manifest["folds"]:
        assert fold["trainEnd"] < fold["validationStart"]
        assert fold["raceOverlapCount"] == 0


def test_probability_normalization_is_race_local_and_finite() -> None:
    frame = sample_frame(days=1)
    probability = normalize_race_probabilities(frame, frame["score"].to_numpy(float))
    sums = pd.Series(probability, index=frame.index).groupby(frame["race_id"]).sum()
    assert np.allclose(sums.to_numpy(), 1.0)
    assert np.isfinite(probability).all()
    with pytest.raises(ValueError, match="nonfinite_score"):
        normalize_race_probabilities(frame, np.full(len(frame), np.nan))


def test_ranking_logits_use_race_local_softmax() -> None:
    frame = sample_frame(days=1)
    logits = np.tile(np.array([6, 5, 4, 3, 2, 1], dtype=float), 2)
    probability = normalize_race_logits(frame, logits)
    expected = np.exp(np.arange(6, 0, -1) - 6)
    expected = expected / expected.sum()
    assert np.allclose(probability[:6], expected)
    assert np.allclose(probability[6:], expected)


def test_feature_contract_rejects_result_and_unknown_timing_columns() -> None:
    assert_feature_contract(["lane", "racer_prior_win_rate"])
    for forbidden in (["finish_position"], ["target"], ["exhibition_time"], ["final_odds"]):
        with pytest.raises(ValueError, match="feature_timing_violation"):
            assert_feature_contract(forbidden)


def test_experiment_budget_is_bounded_to_three_families_and_twelve_settings() -> None:
    valid = [ExperimentSpec("ranking", f"r{i}", {}) for i in range(4)]
    valid += [ExperimentSpec("residual", f"x{i}", {}) for i in range(4)]
    valid += [ExperimentSpec("calibration", f"c{i}", {}) for i in range(4)]
    ExperimentSpec.validate_budget(valid)
    with pytest.raises(ValueError, match="experiment_setting_budget_exceeded"):
        ExperimentSpec.validate_budget(valid + [ExperimentSpec("ranking", "extra", {})])
    with pytest.raises(ValueError, match="experiment_family_budget_exceeded"):
        ExperimentSpec.validate_budget(valid + [ExperimentSpec("other", "other", {})])


def test_challenger_requires_four_folds_brier_stability_and_no_ece_regression() -> None:
    rows = []
    for fold in range(1, 6):
        rows.append({"fold": fold, "modelName": "tree_15", "raceLogLoss": 1.3,
                     "multiclassBrier": 0.63, "ece10": 0.02})
        rows.append({"fold": fold, "modelName": "candidate", "raceLogLoss": 1.2,
                     "multiclassBrier": 0.60, "ece10": 0.021})
    assert select_challenger(pd.DataFrame(rows), baseline="tree_15") == "candidate"
    rows[-1]["ece10"] = 0.20
    assert select_challenger(pd.DataFrame(rows), baseline="tree_15") is None


def test_challenger_allowlist_excludes_comparison_baselines() -> None:
    rows = []
    for fold in range(1, 6):
        rows.append({"fold": fold, "modelName": "tree_15", "raceLogLoss": 1.3,
                     "multiclassBrier": 0.63, "ece10": 0.02})
        rows.append({"fold": fold, "modelName": "lane_frequency", "raceLogLoss": 1.2,
                     "multiclassBrier": 0.60, "ece10": 0.02})
        rows.append({"fold": fold, "modelName": "candidate", "raceLogLoss": 1.2,
                     "multiclassBrier": 0.60, "ece10": 0.02})
    assert eligible_challengers(
        pd.DataFrame(rows), baseline="tree_15", candidate_names={"candidate"}
    ) == ["candidate"]


def test_promotion_requires_fresh_deterministic_rerun() -> None:
    assert promotion_passes(deterministic=True, ci_pass=True, segment_pass=True, gap_reset_pass=True)
    assert not promotion_passes(deterministic=False, ci_pass=True, segment_pass=True, gap_reset_pass=True)


def test_config_hash_is_deterministic() -> None:
    left = canonical_config_hash({"seed": 42, "features": ["lane", "jcd"]})
    right = canonical_config_hash({"features": ["lane", "jcd"], "seed": 42})
    assert left == right
