from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.monitor.gatekeeper import gatekeeper_decide


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_config() -> dict:
    return json.loads((REPO_ROOT / "config" / "strategy_config.json").read_text(encoding="utf-8"))


def _base_metrics() -> dict:
    return {
        "available_columns": [
            "race_id",
            "date",
            "venue",
            "race_number",
            "pred_top1",
            "win_proba",
            "ev",
            "real_odds",
        ],
        "non_null_columns": [
            "race_id",
            "date",
            "venue",
            "race_number",
            "pred_top1",
            "win_proba",
            "ev",
            "real_odds",
        ],
        "real_odds_available_rate": 0.5,
        "missing_feature_rate": 0.02,
        "race_coverage": 0.9,
        "today_races": 80,
        "pending_unpublished_rate": 0.0,
        "daily_roi_drop": 0.0,
        "consecutive_zero_buy_days": 0,
        "hit_rate_drop": 0.0,
    }


def test_gatekeeper_decide_normal_buy_phase_allows_buy() -> None:
    config = _load_config()
    metrics = _base_metrics()

    decision = gatekeeper_decide("buy_phase", metrics, config)

    assert decision["is_stop"] is False
    assert decision["mode"] == "normal"
    assert decision["allow_buy"] is True
    assert decision["stop_reasons"] == []
    assert decision["alert_reasons"] == []
    assert decision["effective_rules"] == {
        "min_win_proba": pytest.approx(0.27),
        "min_ev": pytest.approx(1.22),
        "max_ev": pytest.approx(3.6),
        "max_candidates_per_day": 12,
        "max_candidates_per_race": 1,
    }


def test_gatekeeper_decide_reduced_mode_adjusts_effective_rules() -> None:
    config = _load_config()
    metrics = _base_metrics()
    metrics.update(
        {
            "real_odds_available_rate": 0.38,
            "missing_feature_rate": 0.05,
            "race_coverage": 0.83,
            "today_races": 65,
        }
    )

    decision = gatekeeper_decide("buy_phase", metrics, config)

    assert decision["is_stop"] is False
    assert decision["mode"] == "reduced"
    assert decision["allow_buy"] is True
    assert decision["effective_rules"] == {
        "min_win_proba": pytest.approx(0.29),
        "min_ev": pytest.approx(1.27),
        "max_ev": pytest.approx(3.6),
        "max_candidates_per_day": 7,
        "max_candidates_per_race": 1,
    }


def test_gatekeeper_decide_separates_stop_and_alerts() -> None:
    config = _load_config()
    metrics = _base_metrics()
    metrics.update(
        {
            "real_odds_available_rate": 0.2,
            "missing_feature_rate": 0.08,
            "race_coverage": 0.75,
            "today_races": 50,
            "pending_unpublished_rate": 0.2,
            "daily_roi_drop": 0.5,
            "consecutive_zero_buy_days": 4,
            "hit_rate_drop": 0.2,
        }
    )

    decision = gatekeeper_decide("buy_phase", metrics, config, {"single_venue_abnormal": True})

    assert decision["is_stop"] is True
    assert decision["mode"] == "stop"
    assert decision["allow_buy"] is False
    assert decision["effective_rules"] is None
    assert any(reason.startswith("real_odds_available_rate_below_stop_threshold") for reason in decision["stop_reasons"])
    assert "pending_unpublished_rate_exceeds_threshold:0.2000>0.1000" in decision["alert_reasons"]
    assert "daily_roi_drop_exceeds_threshold:0.5000>0.4000" in decision["alert_reasons"]
    assert "consecutive_zero_buy_days_exceeds_threshold:4>=4" in decision["alert_reasons"]
    assert "hit_rate_drop_exceeds_threshold:0.2000>0.1200" in decision["alert_reasons"]
    assert "single_venue_abnormal" in decision["alert_reasons"]


def test_gatekeeper_decide_buy_phase_requires_real_odds_non_null() -> None:
    config = _load_config()
    metrics = _base_metrics()
    metrics["non_null_columns"] = [
        "race_id",
        "date",
        "venue",
        "race_number",
        "pred_top1",
        "win_proba",
        "ev",
    ]

    decision = gatekeeper_decide("buy_phase", metrics, config)

    assert decision["is_stop"] is True
    assert any("phase_non_null_violation:buy_phase:real_odds" == reason for reason in decision["stop_reasons"])
    assert decision["allow_buy"] is False


def test_gatekeeper_decide_pre_race_does_not_require_real_odds_non_null() -> None:
    config = _load_config()
    metrics = _base_metrics()
    metrics["non_null_columns"] = [
        "race_id",
        "date",
        "venue",
        "race_number",
        "pred_top1",
        "win_proba",
        "ev",
    ]

    decision = gatekeeper_decide("pre_race", metrics, config)

    assert decision["is_stop"] is False
    assert decision["mode"] == "normal"
    assert decision["allow_buy"] is False
    assert decision["stop_reasons"] == []
