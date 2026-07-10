import argparse
import json
import os
import re
from pathlib import Path
import numpy as np
import pandas as pd

from src.pipeline.boatrace_official_pipeline import JCD_TO_VENUE
from src.strategy.probability_calibration_features import add_probability_calibration_features
from src.utils.race_id import canonical_race_id, normalize_race_id

EV_WEIGHT = 0.05
RISK_PENALTY_DEFAULTS = {
    "NO_REAL_ODDS": 2,
    "LOW_CONFIDENCE": 2,
    "HIGH_ODDS_VOLATILE": 1,
    "DATA_MISSING": 3,
    "LOW_SAMPLE_MODEL": 2,
    "STALE_ODDS": 2,
}
RISK_LABELS_JA = {
    "NO_REAL_ODDS": "実オッズ未取得",
    "LOW_CONFIDENCE": "予測信頼度低",
    "HIGH_ODDS_VOLATILE": "高配当で変動大",
    "DATA_MISSING": "データ欠損あり",
    "LOW_SAMPLE_MODEL": "学習根拠が弱い",
    "STALE_ODDS": "オッズ鮮度不足",
}

VENUE_TO_JCD = {venue: jcd for jcd, venue in JCD_TO_VENUE.items()}


def _odds_race_id_key(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return normalize_race_id(text)
    except Exception:
        pass
    m = re.match(r"^(?P<date>\d{8})[_-](?P<venue>.+?)[_-](?P<race_no>\d{1,2})$", text)
    if m:
        venue = m.group("venue").strip()
        jcd = VENUE_TO_JCD.get(venue) or (venue.zfill(2) if venue.isdigit() else "")
        if jcd:
            try:
                return canonical_race_id(m.group("date"), jcd, m.group("race_no"))
            except Exception:
                pass
    return text.replace("_", "-")


class StrategyEvaluator:
    """
    EV 分析と見送り判定を分離して実行する。
    """
    def __init__(self, config_path="config/strategy_config.json"):
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
        self.ev_config = dict(config_data.get("ev_calculation", {}) or {})
        self.buy_config = dict(config_data.get("buy_rules", config_data.get("buy_conditions", {})) or {})
        self.watch_config = dict(config_data.get("watch_rules", config_data.get("watch_conditions", {})) or {})
        self.roi_filter_config = dict(config_data.get("roi_filter_rules", config_data.get("roi_filter_conditions", {})) or {})
        self.auto_filter_config = dict(config_data.get("auto_filter_rules", config_data.get("auto_filter_conditions", {})) or {})
        self.calibration_config = config_data.get("calibration", {})
        self.skip_config = dict(config_data.get("skip_conditions", {}) or {})
        self.pipeline_health_config = dict(config_data.get("pipeline_health", {}) or {})
        self.day_mode_rules = self._normalize_day_mode_rules(config_data.get("day_mode_rules", {}))
        self.day_mode_resolution = dict(config_data.get("day_mode_resolution", {}) or {})
        self.strategy_mode = str(
            os.getenv("STRATEGY_MODE", self.buy_config.get("strategy_mode", "NORMAL")) or "NORMAL"
        ).upper()
        # 暫定運用条件（設定で上書き可能）
        self.operational_min_win_proba = float(
            self.buy_config.get(
                "operational_min_win_proba",
                self.buy_config.get("min_win_proba", self.skip_config.get("min_win_proba", 0.25)),
            )
        )
        self.exclude_risk_flag_for_buy = bool(
            self.buy_config.get("exclude_risk_flag_for_buy", self.buy_config.get("risk_flag_skip", False))
        )
        self.risk_ev_threshold = float(
            self.buy_config.get("ev_upper_threshold", self.buy_config.get("max_ev", 5.0))
        )
        self.max_odds_for_buy = self.buy_config.get("max_odds_for_buy")
        self.max_ev_for_buy = self.buy_config.get("max_ev_for_buy", self.buy_config.get("max_ev"))
        self.max_first_win_proba_for_buy = self.buy_config.get("max_first_win_proba_for_buy")
        self.max_buy_count = self.buy_config.get("max_candidates_per_day", self.buy_config.get("max_buy_count"))
        self.max_watch_count = self.watch_config.get("max_watch_count", self.buy_config.get("max_watch_count", 10))
        self.buy_requires_real_odds = bool(
            self.buy_config.get("real_odds_required_for_buy", self.buy_config.get("block_if_ev_without_real_odds", True))
        )
        self.buy_min_ev = float(self.buy_config.get("min_ev", self.buy_config.get("buy_min_ev", 0.10)))
        self.buy_min_approx_prob = float(
            self.buy_config.get("min_win_proba", self.buy_config.get("buy_min_approx_prob", 0.15))
        )
        self.buy_hard_guard_config = dict(self.buy_config.get("hard_guard", {}) or {})
        self.buy_hard_guard_min_ev = float(self.buy_hard_guard_config.get("min_ev", self.buy_min_ev))
        self.buy_hard_guard_min_data_completeness = float(
            self.buy_hard_guard_config.get("min_data_completeness", 1.0)
        )
        self.buy_hard_guard_min_odds_availability = float(
            self.buy_hard_guard_config.get("min_odds_availability", 1.0)
        )
        self.buy_hard_guard_max_stale_age_days = float(
            self.buy_hard_guard_config.get("max_stale_age_days", 14.0)
        )
        self.buy_hard_guard_min_model_confidence = float(
            self.buy_hard_guard_config.get("min_model_confidence", 0.045)
        )
        self.rescue_enabled = bool(
            self.buy_config.get("rescue_enabled", config_data.get("rescue_enabled", True))
        )
        self.use_unified_score = bool(
            self.buy_config.get("use_unified_score", config_data.get("use_unified_score", True))
        )
        self.high_ev_watch_threshold = float(
            self.buy_config.get(
                "high_ev_watch_threshold",
                config_data.get("high_ev_watch_threshold", config_data.get("risk_flags", {}).get("suspicious_ev_threshold", 4.5)),
            )
        )
        self.buy_max_risk_penalty = int(self.buy_config.get("max_risk_penalty", 1))
        self.rank_rescue_top_n = int(
            os.getenv(
                "RANK_RESCUE_TOP_N",
                self.buy_config.get("rank_rescue_top_n", 3),
            )
            or 0
        )
        self.rank_rescue_ev_relaxation = float(
            os.getenv(
                "RANK_RESCUE_EV_RELAXATION",
                self.buy_config.get("rank_rescue_ev_relaxation", 0.05),
            )
            or 0.05
        )
        self.rank_rescue_min_calibrated_hit_prob = float(
            os.getenv(
                "RANK_RESCUE_MIN_CALIBRATED_HIT_PROB",
                self.buy_config.get("rank_rescue_min_calibrated_hit_prob", 0.05),
            )
            or 0.05
        )
        self.near_cap_rescue_enabled = bool(
            int(
                os.getenv(
                    "NEAR_CAP_RESCUE_ENABLED",
                    1 if self.buy_config.get("near_cap_rescue_enabled", False) else 0,
                )
                or 0
            )
        )
        self.near_cap_rescue_window = float(
            os.getenv(
                "NEAR_CAP_RESCUE_WINDOW",
                self.buy_config.get("near_cap_rescue_window", 25.0),
            )
            or 0.0
        )
        self.near_cap_rescue_top_n = int(
            os.getenv(
                "NEAR_CAP_RESCUE_TOP_N",
                self.buy_config.get("near_cap_rescue_top_n", 3),
            )
            or 0
        )
        self.near_cap_rescue_min_calibrated_hit_prob = float(
            os.getenv(
                "NEAR_CAP_RESCUE_MIN_CALIBRATED_HIT_PROB",
                self.buy_config.get(
                    "near_cap_rescue_min_calibrated_hit_prob",
                    self.rank_rescue_min_calibrated_hit_prob,
                ),
            )
            or self.rank_rescue_min_calibrated_hit_prob
        )
        self.near_cap_rescue_min_final_score = float(
            os.getenv(
                "NEAR_CAP_RESCUE_MIN_FINAL_SCORE",
                self.buy_config.get("near_cap_rescue_min_final_score", 0.30),
            )
            or 0.0
        )
        self.payout_outlier_rescue_enabled = bool(
            int(
                os.getenv(
                    "PAYOUT_OUTLIER_RESCUE_ENABLED",
                    1 if self.buy_config.get("payout_outlier_rescue_enabled", False) else 0,
                )
                or 0
            )
        )
        self.payout_outlier_rescue_top_n = int(
            os.getenv(
                "PAYOUT_OUTLIER_RESCUE_TOP_N",
                self.buy_config.get("payout_outlier_rescue_top_n", 3),
            )
            or 0
        )
        self.payout_outlier_rescue_max_odds = float(
            os.getenv(
                "PAYOUT_OUTLIER_RESCUE_MAX_ODDS",
                self.buy_config.get("payout_outlier_rescue_max_odds", 100.0),
            )
            or 0.0
        )
        self.payout_outlier_rescue_min_calibrated_hit_prob = float(
            os.getenv(
                "PAYOUT_OUTLIER_RESCUE_MIN_CALIBRATED_HIT_PROB",
                self.buy_config.get("payout_outlier_rescue_min_calibrated_hit_prob", 0.10),
            )
            or 0.0
        )
        self.payout_outlier_rescue_max_ev_delta = float(
            os.getenv(
                "PAYOUT_OUTLIER_RESCUE_MAX_EV_DELTA",
                self.buy_config.get("payout_outlier_rescue_max_ev_delta", 1.0),
            )
            or 0.0
        )
        self.payout_outlier_rescue_min_final_score = float(
            os.getenv(
                "PAYOUT_OUTLIER_RESCUE_MIN_FINAL_SCORE",
                self.buy_config.get("payout_outlier_rescue_min_final_score", 0.30),
            )
            or 0.0
        )
        self.buy_final_score_enabled = bool(
            int(
                os.getenv(
                    "BUY_FINAL_SCORE_ENABLED",
                    1 if self.buy_config.get("buy_final_score_enabled", False) else 0,
                )
                or 0
            )
        )
        self.buy_final_score_race_weight = float(
            os.getenv(
                "BUY_FINAL_SCORE_RACE_WEIGHT",
                os.getenv(
                    "BUY_FINAL_SCORE_EV_WEIGHT",
                    self.buy_config.get("buy_final_score_race_weight", 0.60),
                ),
            )
            or 0.60
        )
        self.buy_final_score_calibrated_weight = float(
            os.getenv(
                "BUY_FINAL_SCORE_CAL_WEIGHT",
                self.buy_config.get("buy_final_score_calibrated_weight", 0.35),
            )
            or 0.35
        )
        self.buy_final_score_rank_weight = float(
            os.getenv(
                "BUY_FINAL_SCORE_RANK_WEIGHT",
                self.buy_config.get("buy_final_score_rank_weight", 0.05),
            )
            or 0.05
        )
        self.buy_final_score_rank_top_n = int(
            os.getenv(
                "BUY_FINAL_SCORE_RANK_TOP_N",
                self.buy_config.get("buy_final_score_rank_top_n", 3),
            )
            or 3
        )
        self.watch_min_ev_with_real_odds = float(
            self.watch_config.get(
                "min_ev_with_real_odds",
                self.watch_config.get("watch_min_ev_with_real_odds", 5.0),
            )
        )
        self.watch_min_approx_prob_with_real_odds = float(
            self.watch_config.get(
                "min_approx_prob_with_real_odds",
                self.watch_config.get("watch_min_approx_prob_with_real_odds", 0.12),
            )
        )
        self.watch_min_ev_without_real_odds = float(
            self.watch_config.get(
                "min_ev_without_real_odds",
                self.watch_config.get("watch_min_ev_without_real_odds", 15.0),
            )
        )
        self.watch_min_approx_prob_without_real_odds = float(
            self.watch_config.get(
                "min_approx_prob_without_real_odds",
                self.watch_config.get("watch_min_approx_prob_without_real_odds", 0.12),
            )
        )
        self.watch_max_risk_penalty = int(self.watch_config.get("watch_max_risk_penalty", 3))
        self.watch_max_odds = float(self.watch_config.get("watch_max_odds", 1000.0))
        self.bet_management = dict(config_data.get("bet_management", {}))
        self.kelly_bankroll = float(self.bet_management.get("bankroll", 100000.0))
        self.kelly_max_fraction = float(self.bet_management.get("max_kelly_fraction", 0.05))
        self.roi_filter_rules_path = Path(
            self.roi_filter_config.get(
                "rules_path",
                self.buy_config.get("roi_filter_rules_path", "data/strategy_outputs/roi_filter_rules.json"),
            )
        )
        self.auto_filter_rules_path = Path(
            self.auto_filter_config.get(
                "rules_path",
                self.buy_config.get("auto_filter_rules_path", "data/strategy_outputs/auto_filter_rules.json"),
            )
        )
        self.auto_filter_prob_metric = str(
            self.auto_filter_config.get(
                "prob_metric",
                self.buy_config.get("auto_filter_prob_metric", "calibrated_hit_prob"),
            )
            or "calibrated_hit_prob"
        )
        self.pre_race_config = dict(config_data.get("pre_race_filter", {}))
        self.pre_race_feature_path = Path(
            self.pre_race_config.get("feature_path", "data/features/today_features.csv")
        )
        self.pre_race_time_weight = float(self.pre_race_config.get("time_weight", 0.5))
        self.pre_race_motor_weight = float(self.pre_race_config.get("motor_weight", 0.3))
        self.pre_race_rank_weight = float(self.pre_race_config.get("rank_weight", 0.2))
        self.pre_race_block_threshold = float(self.pre_race_config.get("buy_block_threshold", -1.0))
        self.pre_race_boost_threshold = float(self.pre_race_config.get("boost_threshold", 1.0))
        self.pre_race_priority_threshold = float(self.pre_race_config.get("priority_threshold", 2.0))
        self.pre_race_prob_boost = float(self.pre_race_config.get("prob_boost_multiplier", 1.1))
        self.pre_race_features = pd.DataFrame()
        self.pre_race_feature_lookup: dict[str, pd.DataFrame] = {}
        self.first_place_config = dict(config_data.get("first_place_filter", {}))
        self.first_place_enabled = bool(self.first_place_config.get("enabled", True))
        self.first_place_feature_path = Path(
            self.first_place_config.get("feature_path", str(self.pre_race_feature_path))
        )
        self.first_place_course_weight = float(self.first_place_config.get("course_weight", 0.4))
        self.first_place_motor_weight = float(self.first_place_config.get("motor_weight", 0.25))
        self.first_place_time_weight = float(self.first_place_config.get("time_weight", 0.25))
        self.first_place_start_weight = float(self.first_place_config.get("start_weight", 0.1))
        self.first_place_block_threshold = float(self.first_place_config.get("buy_block_threshold", 1.0))
        self.first_place_priority_threshold = float(self.first_place_config.get("priority_threshold", 2.0))
        self.first_place_prob_boost = float(self.first_place_config.get("prob_boost_multiplier", 1.05))
        self.first_place_sort_weight = float(self.first_place_config.get("sort_weight", 0.15))
        self.place_role_config = dict(config_data.get("place_role_filter", {}))
        self.place_role_enabled = bool(self.place_role_config.get("enabled", True))
        self.place_role_feature_path = Path(
            self.place_role_config.get("feature_path", str(self.first_place_feature_path))
        )
        self.second_place_course_weight = float(self.place_role_config.get("second_course_weight", 0.5))
        self.second_place_motor_weight = float(self.place_role_config.get("second_motor_weight", 0.3))
        self.second_place_time_weight = float(self.place_role_config.get("second_time_weight", 0.2))
        self.second_place_block_threshold = float(self.place_role_config.get("second_block_threshold", -1.0))
        self.second_place_priority_threshold = float(self.place_role_config.get("second_priority_threshold", 1.5))
        self.second_place_prob_boost = float(self.place_role_config.get("second_prob_boost_multiplier", 1.03))
        self.second_place_sort_weight = float(self.place_role_config.get("second_sort_weight", 0.1))
        self.third_place_course_weight = float(self.place_role_config.get("third_course_weight", 0.5))
        self.third_place_motor_weight = float(self.place_role_config.get("third_motor_weight", 0.3))
        self.third_place_time_weight = float(self.place_role_config.get("third_time_weight", 0.2))
        self.third_place_block_threshold = float(self.place_role_config.get("third_block_threshold", -1.0))
        self.third_place_priority_threshold = float(self.place_role_config.get("third_priority_threshold", 1.5))
        self.third_place_prob_boost = float(self.place_role_config.get("third_prob_boost_multiplier", 1.03))
        self.third_place_sort_weight = float(self.place_role_config.get("third_sort_weight", 0.1))
        self.race_selection_config = dict(config_data.get("race_selection_filter", {}))
        self.race_selection_enabled = bool(self.race_selection_config.get("enabled", True))
        self.race_selection_feature_path = Path(
            self.race_selection_config.get("feature_path", str(self.pre_race_feature_path))
        )
        self.race_selection_first_weight = float(self.race_selection_config.get("first_confidence_weight", 0.4))
        self.race_selection_pre_weight = float(self.race_selection_config.get("pre_race_weight", 0.3))
        self.race_selection_odds_weight = float(self.race_selection_config.get("odds_balance_weight", 0.2))
        self.race_selection_quality_weight = float(self.race_selection_config.get("data_quality_weight", 0.1))
        self.race_selection_block_threshold = float(self.race_selection_config.get("block_threshold", -1.0))
        self.race_selection_watch_threshold = float(self.race_selection_config.get("watch_threshold", 0.0))
        self.race_selection_buy_threshold = float(self.race_selection_config.get("buy_threshold", 1.0))
        self.race_selection_max_buy_count = self.race_selection_config.get("max_buy_count")
        self.race_selection_max_watch_count = self.race_selection_config.get("max_watch_count")
        self.auto_filter_min_sample_count = int(
            self.auto_filter_config.get(
                "min_sample_count",
                self.buy_config.get("auto_filter_min_sample_count", 30),
            )
        )
        self.auto_filter_min_roi = float(
            self.auto_filter_config.get(
                "min_roi",
                self.buy_config.get("auto_filter_min_roi", 1.0),
            )
        )
        self.calibration_artifact_path = Path(
            self.calibration_config.get(
                "artifact_path",
                "models/probability_calibrator.json",
            )
        )
        self.calibration_base_prob_col = str(
            self.calibration_config.get(
                "base_prob_col",
                "approx_prob",
            )
            or "approx_prob"
        )
        self.calibration_fallback_scale = float(
            self.calibration_config.get("fallback_scale", 0.7)
        )
        self.roi_filter_prob_metric = str(
            self.roi_filter_config.get(
                "prob_metric",
                self.buy_config.get("roi_filter_prob_metric", "first_place_prob"),
            )
            or "first_place_prob"
        )
        self.roi_filter_min_sample_count = int(
            self.roi_filter_config.get(
                "min_sample_count",
                self.buy_config.get("roi_filter_min_sample_count", 30),
            )
        )
        self.roi_filter_min_roi = float(
            self.roi_filter_config.get(
                "min_roi",
                self.buy_config.get("roi_filter_min_roi", 1.0),
            )
        )
        self.roi_filter_rules = self._load_roi_filter_rules()
        self.auto_filter_rules = self._load_auto_filter_rules()
        self.auto_filter_active = self._auto_filter_rules_active(self.auto_filter_rules)
        self.calibration_artifact = self._load_probability_calibrator()
        self.calibration_method = str(
            self.calibration_artifact.get("method", "fallback") or "fallback"
        ).lower()
        self.calibration_source_col = str(
            self.calibration_artifact.get("base_prob_col", self.calibration_base_prob_col)
            or self.calibration_base_prob_col
        )
        self.high_odds_volatile_threshold = float(
            config_data.get("risk_conditions", {}).get("high_odds_volatile_threshold", 500.0)
        )
        self.low_confidence_threshold = float(
            config_data.get("risk_conditions", {}).get("low_confidence_threshold", 0.12)
        )
        self.low_sample_threshold = float(
            config_data.get("risk_conditions", {}).get("low_sample_threshold", 0.08)
        )
        scoring = dict(config_data.get("decision_scoring", {}))
        self.ev_weight = float(scoring.get("ev_weight", 40.0))
        self.approx_prob_weight = float(scoring.get("approx_prob_weight", 100.0))
        self.first_win_weight = float(scoring.get("first_win_weight", 30.0))
        self.risk_penalty_weight = float(scoring.get("risk_penalty_weight", 12.0))
        self.real_odds_bonus = float(scoring.get("real_odds_bonus", 6.0))
        self.high_odds_penalty = float(scoring.get("high_odds_penalty", 8.0))
        penalties = dict(RISK_PENALTY_DEFAULTS)
        penalties.update(config_data.get("risk_penalties", {}))
        penalties.update(config_data.get("risk_penalty_weights", {}))
        self.risk_penalties = penalties

    @staticmethod
    def _normalize_odds_source(source: str) -> str:
        s = str(source or "").lower()
        if s in {"real", "file", "official_result_odds", "real_live", "live"}:
            return "real"
        if s in {"estimated", "fallback_fixed", "fallback"}:
            return "estimated"
        return "missing"

    @staticmethod
    def _venue_name_from_race_id(race_id: object) -> str:
        text = str(race_id or "")
        if "-" not in text:
            return ""
        parts = text.split("-")
        if len(parts) < 2:
            return ""
        prefix = parts[1][:1].upper()
        return {
            "B": "大村",
            "K": "唐津",
            "S": "下関",
        }.get(prefix, "")

    @staticmethod
    def _bin_edges_to_labels(edges: list[float], digits: int = 1) -> list[str]:
        labels: list[str] = []
        for idx in range(len(edges) - 1):
            left = float(edges[idx])
            right = float(edges[idx + 1])
            if digits == 0:
                labels.append(f"{int(left)}+" if idx == len(edges) - 2 else f"{int(left)}-{int(right)}")
            else:
                fmt = f"{{:.{digits}f}}"
                labels.append(
                    f"{fmt.format(left)}+" if idx == len(edges) - 2 else f"{fmt.format(left)}-{fmt.format(right)}"
                )
        return labels

    @staticmethod
    def _bin_label(value: object, edges: list[float], digits: int = 1) -> str:
        n = pd.to_numeric(value, errors="coerce")
        if pd.isna(n):
            return "unknown"
        num = float(n)
        labels = StrategyEvaluator._bin_edges_to_labels(edges, digits=digits)
        if not labels:
            return "unknown"
        for idx in range(len(edges) - 1):
            left = float(edges[idx])
            right = float(edges[idx + 1])
            last = idx == len(edges) - 2
            if num >= left and (num < right or (last and num <= right)):
                return labels[idx]
        return labels[-1]

    @staticmethod
    def _matches_allowed_bin(value: object, allowed_bins: set[str], edges: list[float], digits: int = 1) -> bool:
        if not allowed_bins:
            return False
        n = pd.to_numeric(value, errors="coerce")
        if pd.isna(n):
            return False
        num = float(n)
        labels = StrategyEvaluator._bin_edges_to_labels(edges, digits=digits)
        if not labels:
            return False
        for idx, label in enumerate(labels):
            if label not in allowed_bins:
                continue
            left = float(edges[idx])
            right = float(edges[idx + 1])
            last = idx == len(edges) - 2
            if num >= left and (num < right or (last and num <= right)):
                return True
        return False

    @staticmethod
    def _bin_digits(edges: list[float], fallback: int = 1) -> int:
        if len(edges) < 2:
            return fallback
        step = min(abs(float(edges[i + 1]) - float(edges[i])) for i in range(len(edges) - 1) if float(edges[i + 1]) != float(edges[i]))
        return 2 if step < 0.099 else fallback

    def _load_roi_filter_rules(self) -> dict:
        default = {
            "strategy_mode": "ROI_FILTER",
            "generated_at": None,
            "prob_metric": self.roi_filter_prob_metric,
            "prob_bin_edges": [round(x / 10, 1) for x in range(0, 11)],
            "odds_bin_edges": [0, 20, 50, 100, 200, 500, 1000, 999999],
            "allowed_prob_bins": [],
            "allowed_odds_bins": [],
            "allowed_places": [],
            "min_sample_count": self.roi_filter_min_sample_count,
            "min_roi": self.roi_filter_min_roi,
        }
        if not self.roi_filter_rules_path.exists():
            return default
        try:
            payload = json.loads(self.roi_filter_rules_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return default
            for key, value in default.items():
                payload.setdefault(key, value)
            return payload
        except Exception:
            return default

    def _load_auto_filter_rules(self) -> dict:
        default = {
            "strategy_mode": "AUTO_FILTER",
            "generated_at": None,
            "enabled": False,
            "prob_metric": self.auto_filter_prob_metric,
            "prob_bin_edges": [round(x / 20, 2) for x in range(0, 21)],
            "odds_bin_edges": [0, 20, 50, 100, 200, 500, 1000, 999999],
            "allowed_prob_bins": [],
            "allowed_odds_bins": [],
            "allowed_places": [],
            "window": {},
            "window_label": "未生成",
            "min_sample_count": self.auto_filter_min_sample_count,
            "min_roi": self.auto_filter_min_roi,
        }
        if not self.auto_filter_rules_path.exists():
            return default
        try:
            payload = json.loads(self.auto_filter_rules_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return default
            for key, value in default.items():
                payload.setdefault(key, value)
            return payload
        except Exception:
            return default

    @staticmethod
    def _auto_filter_rules_active(rules: dict) -> bool:
        if not isinstance(rules, dict):
            return False
        enabled = rules.get("enabled")
        if isinstance(enabled, bool) and not enabled:
            return False
        allowed_prob_bins = rules.get("allowed_prob_bins", []) or []
        allowed_odds_bins = rules.get("allowed_odds_bins", []) or []
        allowed_places = rules.get("allowed_places", []) or []
        return bool(allowed_prob_bins and allowed_odds_bins and allowed_places)

    def _load_probability_calibrator(self) -> dict:
        default = {
            "method": "fallback",
            "base_prob_col": self.calibration_base_prob_col,
            "fallback_scale": self.calibration_fallback_scale,
        }
        if not self.calibration_artifact_path.exists():
            return default
        try:
            payload = json.loads(self.calibration_artifact_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return default
            payload.setdefault("method", "fallback")
            payload.setdefault("base_prob_col", self.calibration_base_prob_col)
            payload.setdefault("fallback_scale", self.calibration_fallback_scale)
            return payload
        except Exception:
            return default

    def _calibrate_probability_series(self, calibration_df: pd.DataFrame, base_col: str) -> np.ndarray:
        if base_col in calibration_df.columns:
            base_series = calibration_df[base_col]
        else:
            base_series = pd.Series([0.0] * len(calibration_df), index=calibration_df.index, dtype=float)
        values = pd.to_numeric(base_series, errors="coerce").fillna(0.0).clip(0.0, 1.0).to_numpy(dtype=float)
        method = str(self.calibration_artifact.get("method", "fallback") or "fallback").lower()

        if method == "logistic":
            feature_columns = [
                str(c) for c in self.calibration_artifact.get("feature_columns", []) if str(c)
            ]
            logistic = self.calibration_artifact.get("logistic", {})
            coef = np.asarray(logistic.get("coef", []), dtype=float).ravel()
            intercept = float(np.asarray(logistic.get("intercept", [0.0]), dtype=float).ravel()[0]) if logistic.get("intercept") is not None else 0.0
            means = np.asarray(logistic.get("means", []), dtype=float).ravel()
            scales = np.asarray(logistic.get("scales", []), dtype=float).ravel()
            if feature_columns and len(feature_columns) == len(coef) == len(means) == len(scales):
                feature_frame = calibration_df.copy()
                for col in feature_columns:
                    if col not in feature_frame.columns:
                        feature_frame[col] = 0.0
                x = (
                    feature_frame[feature_columns]
                    .apply(pd.to_numeric, errors="coerce")
                    .replace([np.inf, -np.inf], np.nan)
                    .fillna(0.0)
                    .to_numpy(dtype=float)
                )
                safe_scales = np.where(np.abs(scales) < 1e-12, 1.0, scales)
                x_scaled = (x - means) / safe_scales
                logits = np.clip(np.dot(x_scaled, coef) + intercept, -60.0, 60.0)
                preds = 1.0 / (1.0 + np.exp(-logits))
                return np.clip(preds, 0.0, 1.0)

        if method == "isotonic":
            iso = self.calibration_artifact.get("isotonic", {})
            x = np.asarray(iso.get("x", []), dtype=float)
            y = np.asarray(iso.get("y", []), dtype=float)
            if len(x) >= 2 and len(y) >= 2:
                order = np.argsort(x)
                x = x[order]
                y = y[order]
                preds = np.interp(values, x, y, left=float(y[0]), right=float(y[-1]))
                return np.clip(preds, 0.0, 1.0)

        if method == "platt":
            platt = self.calibration_artifact.get("platt", {})
            coef = np.asarray(platt.get("coef", [0.0]), dtype=float).ravel()
            intercept = np.asarray(platt.get("intercept", [0.0]), dtype=float).ravel()
            slope = float(coef[0]) if coef.size else 0.0
            bias = float(intercept[0]) if intercept.size else 0.0
            logits = np.clip(values * slope + bias, -60.0, 60.0)
            preds = 1.0 / (1.0 + np.exp(-logits))
            return np.clip(preds, 0.0, 1.0)

        scale = float(self.calibration_artifact.get("fallback_scale", self.calibration_fallback_scale) or self.calibration_fallback_scale)
        return np.clip(values * scale, 0.0, 1.0)

    def _build_risk_codes(self, row: pd.Series) -> list[str]:
        codes: list[str] = []
        odds_source = self._normalize_odds_source(str(row.get("odds_source", "")))
        odds = float(row.get("odds", 0.0) or 0.0)
        first_win = pd.to_numeric(row.get("first_win_proba"), errors="coerce")
        approx_prob = pd.to_numeric(row.get("approx_prob"), errors="coerce")
        ev = pd.to_numeric(row.get("ev"), errors="coerce")
        stale_age_days = pd.to_numeric(row.get("odds_stale_age_days"), errors="coerce")
        feature_missing_count = pd.to_numeric(row.get("feature_missing_count"), errors="coerce")

        if odds_source != "real":
            codes.append("NO_REAL_ODDS")
        if pd.isna(first_win) or pd.isna(approx_prob) or pd.isna(ev) or (
            not pd.isna(feature_missing_count) and float(feature_missing_count) > 0
        ):
            codes.append("DATA_MISSING")
        if not pd.isna(first_win) and float(first_win) < self.low_confidence_threshold:
            codes.append("LOW_CONFIDENCE")
        if not pd.isna(first_win) and float(first_win) < self.low_sample_threshold:
            codes.append("LOW_SAMPLE_MODEL")
        if odds >= self.high_odds_volatile_threshold:
            codes.append("HIGH_ODDS_VOLATILE")
        if not pd.isna(stale_age_days) and float(stale_age_days) > self.buy_hard_guard_max_stale_age_days:
            codes.append("STALE_ODDS")
        return sorted(set(codes))

    def _risk_penalty(self, risk_codes: list[str]) -> int:
        return int(sum(int(self.risk_penalties.get(code, 0)) for code in risk_codes))

    def _translate_risk_labels(self, risk_codes: list[str]) -> list[str]:
        return [RISK_LABELS_JA.get(code, code) for code in risk_codes]

    def _build_threshold_snapshot(
        self,
        *,
        day_mode: str,
        row_ev: float,
        row_prob: float,
        calibrated_hit_prob: float,
        row_odds: float,
        risk_penalty: int,
        has_real_odds: bool,
        high_ev_suspect_flag: bool,
    ) -> dict[str, object]:
        return {
            "day_mode": day_mode,
            "buy_min_ev": float(self.buy_min_ev),
            "buy_min_prob": float(self.buy_min_approx_prob),
            "watch_min_ev_with_real_odds": float(self.watch_min_ev_with_real_odds),
            "watch_min_prob_with_real_odds": float(self.watch_min_approx_prob_with_real_odds),
            "watch_min_ev_without_real_odds": float(self.watch_min_ev_without_real_odds),
            "watch_min_prob_without_real_odds": float(self.watch_min_approx_prob_without_real_odds),
            "high_ev_watch_threshold": float(self.high_ev_watch_threshold),
            "max_ev_for_buy": float(self.max_ev_for_buy) if self.max_ev_for_buy is not None else None,
            "buy_max_risk_penalty": int(self.buy_max_risk_penalty),
            "watch_max_risk_penalty": int(self.watch_max_risk_penalty),
            "rescue_enabled": bool(self.rescue_enabled),
            "use_unified_score": bool(self.use_unified_score),
            "has_real_odds": bool(has_real_odds),
            "risk_penalty": int(risk_penalty),
            "high_ev_suspect_flag": bool(high_ev_suspect_flag),
            "row_ev": float(row_ev),
            "row_prob": float(row_prob),
            "calibrated_hit_prob": float(calibrated_hit_prob),
            "row_odds": float(row_odds),
        }

    def _decision_score(self, row: pd.Series, risk_penalty: int, has_real_odds: bool) -> float:
        ev = max(0.0, float(pd.to_numeric(row.get("ev"), errors="coerce") or 0.0))
        approx_prob = max(
            0.0,
            float(
                pd.to_numeric(
                    row.get("calibrated_hit_prob", row.get("approx_prob")), errors="coerce"
                )
                or 0.0
            ),
        )
        first_win = max(
            0.0,
            float(
                pd.to_numeric(
                    row.get("first_place_prob", row.get("first_win_proba")), errors="coerce"
                )
                or 0.0
            ),
        )
        odds = max(0.0, float(pd.to_numeric(row.get("odds"), errors="coerce") or 0.0))

        score = (
            ev * self.ev_weight
            + approx_prob * self.approx_prob_weight
            + first_win * self.first_win_weight
            - risk_penalty * self.risk_penalty_weight
        )
        if has_real_odds:
            score += self.real_odds_bonus
        if odds >= self.high_odds_volatile_threshold:
            score -= self.high_odds_penalty
        return float(score)

    def _rank_rescue_candidate_ok(
        self,
        row: pd.Series,
        race_feat: pd.DataFrame,
        *,
        hard_skip: bool,
    ) -> tuple[bool, dict]:
        row_ev = float(pd.to_numeric(row.get("ev"), errors="coerce") or 0.0)
        row_prob = float(pd.to_numeric(row.get("approx_prob"), errors="coerce") or 0.0)
        calibrated_hit_prob = float(
            pd.to_numeric(row.get("calibrated_hit_prob", row_prob * 0.7), errors="coerce") or (row_prob * 0.7)
        )
        row_odds = float(pd.to_numeric(row.get("odds"), errors="coerce") or 0.0)
        row_odds_source = self._normalize_odds_source(str(row.get("odds_source", "")))
        has_real_odds = row_odds_source == "real"
        risk_codes = self._build_risk_codes(row)
        risk_penalty = self._risk_penalty(risk_codes)
        risk_flag = bool(row.get("risk_flag", False))

        pre_race_profile = self._compute_pre_race_profile(row, race_feat)
        pre_race_block = bool(pre_race_profile.get("pre_race_block", False))
        first_place_score = float(pd.to_numeric(row.get("first_place_score", 0.0), errors="coerce") or 0.0)
        first_place_gate = str(row.get("first_place_gate", "MISSING") or "MISSING")
        first_place_block = bool(row.get("first_place_block", False) or first_place_score < self.first_place_block_threshold)
        second_place_score = float(pd.to_numeric(row.get("second_place_score", 0.0), errors="coerce") or 0.0)
        second_place_block = bool(row.get("second_place_block", False) or second_place_score < self.second_place_block_threshold)
        third_place_score = float(pd.to_numeric(row.get("third_place_score", 0.0), errors="coerce") or 0.0)
        third_place_block = bool(row.get("third_place_block", False) or third_place_score < self.third_place_block_threshold)
        race_selection_profile = self._compute_race_selection_profile(
            row,
            race_feat,
            first_place_score,
            float(pre_race_profile.get("pre_race_score", 0.0) or 0.0),
            has_real_odds,
        )
        race_block = bool(race_selection_profile.get("race_block", False))
        race_priority = bool(race_selection_profile.get("race_priority", False))
        calibrated_floor = max(0.0, min(1.0, float(self.rank_rescue_min_calibrated_hit_prob)))
        calibrated_strength = 0.0
        if calibrated_hit_prob > calibrated_floor:
            calibrated_strength = min(
                1.0,
                (calibrated_hit_prob - calibrated_floor) / max(1e-9, 1.0 - calibrated_floor),
            )
        rescue_ev_credit = float(self.rank_rescue_ev_relaxation * calibrated_strength)
        rescue_min_ev = float(max(0.0, self.buy_min_ev - rescue_ev_credit))
        base_non_ev_ok = (
            (not hard_skip)
            and (not pre_race_block)
            and (not first_place_block)
            and first_place_gate != "MISSING"
            and (not second_place_block)
            and (not third_place_block)
            and (not race_block)
            and race_priority
            and has_real_odds
            and (not risk_flag)
            and risk_penalty <= self.buy_max_risk_penalty
            and row_prob >= self.buy_min_approx_prob
            and calibrated_hit_prob >= calibrated_floor
            and (self.max_odds_for_buy is None or row_odds <= float(self.max_odds_for_buy))
            and (not self.exclude_risk_flag_for_buy or risk_penalty == 0)
        )
        rescue_ok = (
            base_non_ev_ok
            and row_ev >= rescue_min_ev
        )
        return rescue_ok, {
            "row_ev": row_ev,
            "row_prob": row_prob,
            "calibrated_hit_prob": calibrated_hit_prob,
            "row_odds": row_odds,
            "has_real_odds": has_real_odds,
            "risk_penalty": risk_penalty,
            "risk_flag": risk_flag,
            "pre_race_block": pre_race_block,
            "first_place_block": first_place_block,
            "second_place_block": second_place_block,
            "third_place_block": third_place_block,
            "race_block": race_block,
            "race_priority": race_priority,
            "base_non_ev_ok": base_non_ev_ok,
            "calibrated_strength": calibrated_strength,
            "rescue_ev_credit": rescue_ev_credit,
            "rescue_min_ev": rescue_min_ev,
        }

    def _near_cap_rescue_candidate_ok(
        self,
        row: pd.Series,
        race_feat: pd.DataFrame,
        *,
        hard_skip: bool,
    ) -> tuple[bool, dict]:
        row_ev = float(pd.to_numeric(row.get("ev"), errors="coerce") or 0.0)
        row_prob = float(pd.to_numeric(row.get("approx_prob"), errors="coerce") or 0.0)
        calibrated_hit_prob = float(
            pd.to_numeric(row.get("calibrated_hit_prob", row_prob * 0.7), errors="coerce") or (row_prob * 0.7)
        )
        row_odds = float(pd.to_numeric(row.get("odds"), errors="coerce") or 0.0)
        row_odds_source = self._normalize_odds_source(str(row.get("odds_source", "")))
        has_real_odds = row_odds_source == "real"
        risk_codes = self._build_risk_codes(row)
        risk_penalty = self._risk_penalty(risk_codes)
        risk_flag = bool(row.get("risk_flag", False))
        candidate_rank = int(pd.to_numeric(row.get("candidate_rank_by_sort", 0), errors="coerce") or 0)

        pre_race_profile = self._compute_pre_race_profile(row, race_feat)
        pre_race_block = bool(pre_race_profile.get("pre_race_block", False))
        first_place_score = float(pd.to_numeric(row.get("first_place_score", 0.0), errors="coerce") or 0.0)
        first_place_gate = str(row.get("first_place_gate", "MISSING") or "MISSING")
        first_place_block = bool(row.get("first_place_block", False) or first_place_score < self.first_place_block_threshold)
        second_place_score = float(pd.to_numeric(row.get("second_place_score", 0.0), errors="coerce") or 0.0)
        second_place_block = bool(row.get("second_place_block", False) or second_place_score < self.second_place_block_threshold)
        third_place_score = float(pd.to_numeric(row.get("third_place_score", 0.0), errors="coerce") or 0.0)
        third_place_block = bool(row.get("third_place_block", False) or third_place_score < self.third_place_block_threshold)
        race_selection_profile = self._compute_race_selection_profile(
            row,
            race_feat,
            first_place_score,
            float(pre_race_profile.get("pre_race_score", 0.0) or 0.0),
            has_real_odds,
        )
        race_block = bool(race_selection_profile.get("race_block", False))
        race_priority = bool(race_selection_profile.get("race_priority", False))
        race_score = float(race_selection_profile.get("race_score", 0.0) or 0.0)
        cap_value = float(self.max_odds_for_buy) if self.max_odds_for_buy is not None else None
        near_cap_window = max(0.0, float(self.near_cap_rescue_window))
        near_cap_max_odds = None if cap_value is None else min(cap_value + near_cap_window, 300.0)
        near_cap_odds_gap = None if cap_value is None else (row_odds - cap_value)
        final_score_row = row.copy()
        final_score_row["race_score"] = race_score
        final_score_meta = self._buy_final_score(final_score_row)
        final_score = float(final_score_meta.get("buy_final_score", 0.0) or 0.0)

        rescue_ok = (
            self.near_cap_rescue_enabled
            and (not hard_skip)
            and self.max_odds_for_buy is not None
            and near_cap_window > 0.0
            and (not pre_race_block)
            and (not first_place_block)
            and first_place_gate != "MISSING"
            and (not second_place_block)
            and (not third_place_block)
            and (not race_block)
            and race_priority
            and has_real_odds
            and (not risk_flag)
            and risk_penalty <= self.buy_max_risk_penalty
            and row_prob >= self.buy_min_approx_prob
            and calibrated_hit_prob >= self.near_cap_rescue_min_calibrated_hit_prob
            and candidate_rank > 0
            and candidate_rank <= max(1, int(self.near_cap_rescue_top_n))
            and row_odds > float(cap_value)
            and row_odds <= float(near_cap_max_odds)
            and final_score >= self.near_cap_rescue_min_final_score
            and (not self.exclude_risk_flag_for_buy or risk_penalty == 0)
            and row_ev >= self.buy_min_ev
        )
        return rescue_ok, {
            "row_ev": row_ev,
            "row_prob": row_prob,
            "calibrated_hit_prob": calibrated_hit_prob,
            "row_odds": row_odds,
            "has_real_odds": has_real_odds,
            "risk_penalty": risk_penalty,
            "risk_flag": risk_flag,
            "candidate_rank": candidate_rank,
            "pre_race_block": pre_race_block,
            "first_place_block": first_place_block,
            "second_place_block": second_place_block,
            "third_place_block": third_place_block,
            "race_block": race_block,
            "race_priority": race_priority,
            "race_score": race_score,
            "near_cap_odds_gap": near_cap_odds_gap,
            "near_cap_max_odds": near_cap_max_odds,
            "buy_final_score": final_score,
        }

    def _payout_outlier_rescue_candidate_ok(
        self,
        row: pd.Series,
        race_feat: pd.DataFrame,
        *,
        hard_skip: bool,
    ) -> tuple[bool, dict]:
        row_ev = float(pd.to_numeric(row.get("ev"), errors="coerce") or 0.0)
        row_prob = float(pd.to_numeric(row.get("approx_prob"), errors="coerce") or 0.0)
        calibrated_hit_prob = float(
            pd.to_numeric(row.get("calibrated_hit_prob", row_prob * 0.7), errors="coerce") or (row_prob * 0.7)
        )
        row_odds = float(pd.to_numeric(row.get("odds"), errors="coerce") or 0.0)
        row_odds_source = self._normalize_odds_source(str(row.get("odds_source", "")))
        has_real_odds = row_odds_source == "real"
        risk_codes = self._build_risk_codes(row)
        risk_penalty = self._risk_penalty(risk_codes)
        risk_flag = bool(row.get("risk_flag", False))
        candidate_rank = int(pd.to_numeric(row.get("candidate_rank_by_sort", 0), errors="coerce") or 0)
        ev_delta = float(row_ev - self.risk_ev_threshold)

        pre_race_profile = self._compute_pre_race_profile(row, race_feat)
        pre_race_block = bool(pre_race_profile.get("pre_race_block", False))
        first_place_score = float(pd.to_numeric(row.get("first_place_score", 0.0), errors="coerce") or 0.0)
        first_place_gate = str(row.get("first_place_gate", "MISSING") or "MISSING")
        first_place_block = bool(row.get("first_place_block", False) or first_place_score < self.first_place_block_threshold)
        second_place_score = float(pd.to_numeric(row.get("second_place_score", 0.0), errors="coerce") or 0.0)
        second_place_block = bool(row.get("second_place_block", False) or second_place_score < self.second_place_block_threshold)
        third_place_score = float(pd.to_numeric(row.get("third_place_score", 0.0), errors="coerce") or 0.0)
        third_place_block = bool(row.get("third_place_block", False) or third_place_score < self.third_place_block_threshold)
        race_selection_profile = self._compute_race_selection_profile(
            row,
            race_feat,
            first_place_score,
            float(pre_race_profile.get("pre_race_score", 0.0) or 0.0),
            has_real_odds,
        )
        race_block = bool(race_selection_profile.get("race_block", False))
        race_priority = bool(race_selection_profile.get("race_priority", False))
        race_score = float(race_selection_profile.get("race_score", 0.0) or 0.0)
        final_score_row = row.copy()
        final_score_row["race_score"] = race_score
        final_score_meta = self._buy_final_score(final_score_row)
        final_score = float(final_score_meta.get("buy_final_score", 0.0) or 0.0)
        only_payout_outlier = risk_flag and (not risk_codes)

        rescue_ok = (
            self.payout_outlier_rescue_enabled
            and (not hard_skip)
            and risk_flag
            and only_payout_outlier
            and has_real_odds
            and (not pre_race_block)
            and (not first_place_block)
            and first_place_gate != "MISSING"
            and (not second_place_block)
            and (not third_place_block)
            and (not race_block)
            and race_priority
            and risk_penalty <= self.buy_max_risk_penalty
            and row_prob >= self.buy_min_approx_prob
            and candidate_rank > 0
            and candidate_rank <= max(1, int(self.payout_outlier_rescue_top_n))
            and row_odds <= self.payout_outlier_rescue_max_odds
            and calibrated_hit_prob >= self.payout_outlier_rescue_min_calibrated_hit_prob
            and ev_delta >= 0.0
            and ev_delta <= self.payout_outlier_rescue_max_ev_delta
            and final_score >= self.payout_outlier_rescue_min_final_score
            and (not self.exclude_risk_flag_for_buy or risk_penalty == 0)
            and row_ev >= self.buy_min_ev
        )
        return rescue_ok, {
            "row_ev": row_ev,
            "row_prob": row_prob,
            "calibrated_hit_prob": calibrated_hit_prob,
            "row_odds": row_odds,
            "has_real_odds": has_real_odds,
            "risk_penalty": risk_penalty,
            "risk_flag": risk_flag,
            "risk_codes": "|".join(risk_codes),
            "candidate_rank": candidate_rank,
            "ev_delta": ev_delta,
            "pre_race_block": pre_race_block,
            "first_place_block": first_place_block,
            "second_place_block": second_place_block,
            "third_place_block": third_place_block,
            "race_block": race_block,
            "race_priority": race_priority,
            "race_score": race_score,
            "buy_final_score": final_score,
        }

    def _kelly_fraction(self, p: object, odds: object) -> float:
        prob = pd.to_numeric(p, errors="coerce")
        odd = pd.to_numeric(odds, errors="coerce")
        if pd.isna(prob) or pd.isna(odd):
            return 0.0
        prob_f = max(0.0, min(1.0, float(prob)))
        odd_f = float(odd)
        if odd_f <= 1.0:
            return 0.0
        b = odd_f - 1.0
        fraction = (prob_f * b - (1.0 - prob_f)) / b
        if pd.isna(fraction) or not np.isfinite(fraction):
            return 0.0
        return float(max(0.0, min(float(fraction), self.kelly_max_fraction)))

    def _kelly_bet_amount(self, row: pd.Series, decision: str, has_real_odds: bool) -> tuple[float, float]:
        if decision != "BUY" or not has_real_odds:
            return 0.0, 0.0
        prob = row.get("calibrated_hit_prob", row.get("approx_prob"))
        odds = row.get("odds")
        fraction = self._kelly_fraction(prob, odds)
        if fraction <= 0.0:
            return 0.0, 0.0
        amount = round(self.kelly_bankroll * fraction, 2)
        return fraction, amount

    def _buy_final_score(self, row: pd.Series) -> dict[str, float]:
        race_score = max(0.0, float(pd.to_numeric(row.get("race_score"), errors="coerce") or 0.0))
        calibrated = max(
            0.0,
            min(
                1.0,
                float(
                    pd.to_numeric(
                        row.get("calibrated_hit_prob", row.get("approx_prob")),
                        errors="coerce",
                    )
                    or 0.0
                ),
            ),
        )
        rank = int(pd.to_numeric(row.get("candidate_rank_by_sort", 999), errors="coerce") or 999)
        rank_component = 0.0
        if 1 <= rank <= max(1, int(self.buy_final_score_rank_top_n)):
            rank_component = (
                float(max(0, self.buy_final_score_rank_top_n + 1 - rank))
                / float(max(1, self.buy_final_score_rank_top_n))
            )
        final_score = (
            self.buy_final_score_race_weight * race_score
            + self.buy_final_score_calibrated_weight * calibrated
            + self.buy_final_score_rank_weight * rank_component
        )
        return {
            "buy_final_score": float(final_score),
            "buy_final_score_race_component": float(race_score),
            "buy_final_score_calibrated_component": float(calibrated),
            "buy_final_score_rank_component": float(rank_component),
        }

    @staticmethod
    def _score_from_time_diff(time_diff: object) -> int:
        value = pd.to_numeric(time_diff, errors="coerce")
        if pd.isna(value):
            return 0
        diff = float(value)
        if diff >= 0.10:
            return 2
        if diff >= 0.05:
            return 1
        if diff > -0.05:
            return 0
        if diff > -0.10:
            return -1
        return -2

    @staticmethod
    def _score_from_percentile(percentile: object) -> int:
        value = pd.to_numeric(percentile, errors="coerce")
        if pd.isna(value):
            return 0
        pct = float(value)
        if pct <= 0.2:
            return 2
        if pct <= 0.4:
            return 1
        if pct <= 0.6:
            return 0
        if pct <= 0.8:
            return -1
        return -2

    @staticmethod
    def _score_from_course_no(course_no: object) -> int:
        value = pd.to_numeric(course_no, errors="coerce")
        if pd.isna(value):
            return 0
        course_int = int(round(float(value)))
        if course_int <= 1:
            return 2
        if course_int == 2:
            return 1
        if course_int in (3, 4):
            return 0
        if course_int == 5:
            return -1
        return -2

    @staticmethod
    def _score_from_second_course_no(course_no: object) -> int:
        value = pd.to_numeric(course_no, errors="coerce")
        if pd.isna(value):
            return 0
        course_int = int(round(float(value)))
        if course_int in (2, 3):
            return 2
        if course_int == 4:
            return 1
        if course_int == 1:
            return 0
        if course_int == 5:
            return -1
        return -2

    @staticmethod
    def _score_from_third_course_no(course_no: object) -> int:
        value = pd.to_numeric(course_no, errors="coerce")
        if pd.isna(value):
            return 0
        course_int = int(round(float(value)))
        if course_int in (5, 6):
            return 2
        if course_int == 4:
            return 1
        if course_int == 3:
            return 0
        if course_int == 2:
            return -1
        return -2

    @staticmethod
    def _score_from_display_rank(rank: object) -> int:
        value = pd.to_numeric(rank, errors="coerce")
        if pd.isna(value):
            return 0
        rank_int = int(round(float(value)))
        if rank_int <= 1:
            return 2
        if rank_int == 2:
            return 1
        if rank_int in (3, 4):
            return 0
        if rank_int == 5:
            return -1
        return -2

    def _load_pre_race_features(self) -> pd.DataFrame:
        feature_paths: list[Path] = []
        seen: set[str] = set()
        for path in (
            self.pre_race_feature_path,
            Path("data/features/train_features.csv"),
            Path("data/features/today_features.csv"),
        ):
            if not path:
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            feature_paths.append(path)

        frames: list[pd.DataFrame] = []
        for path in feature_paths:
            if not path.exists():
                continue
            try:
                df = pd.read_csv(path)
            except Exception:
                continue
            if not {"race_id", "lane"}.issubset(df.columns):
                continue
            df = df.copy()
            df["race_id"] = df["race_id"].astype(str).str.strip()
            df["lane"] = pd.to_numeric(df["lane"], errors="coerce")
            frames.append(df)

        if not frames:
            return pd.DataFrame(columns=["race_id", "lane"])

        df = pd.concat(frames, ignore_index=True, sort=False)
        df = df.dropna(subset=["race_id", "lane"])
        df["lane"] = df["lane"].astype(int)
        # 同一 race_id / lane が複数ソースに存在する場合は後勝ちにして最新側を優先する。
        df = df.drop_duplicates(subset=["race_id", "lane"], keep="last").reset_index(drop=True)
        self._refresh_pre_race_feature_lookup(df)
        return df

    def _refresh_pre_race_feature_lookup(self, df: pd.DataFrame | None) -> None:
        if df is None or df.empty or "race_id" not in df.columns:
            self.pre_race_feature_lookup = {}
            return
        work = df.copy()
        work["race_id"] = work["race_id"].astype(str).str.strip()
        self.pre_race_feature_lookup = {
            str(race_id): grp.reset_index(drop=True)
            for race_id, grp in work.groupby("race_id", sort=False)
        }

    def _get_race_features(self, race_id: object) -> pd.DataFrame:
        key = str(race_id).strip()
        if not key:
            return pd.DataFrame(columns=self.pre_race_features.columns)
        if self.pre_race_feature_lookup:
            found = self.pre_race_feature_lookup.get(key)
            if found is not None:
                return found
            return pd.DataFrame(columns=self.pre_race_features.columns)
        if self.pre_race_features.empty:
            return self.pre_race_features
        return self.pre_race_features[self.pre_race_features["race_id"] == key]

    def _compute_pre_race_profile(self, top_row: pd.Series, race_feat: pd.DataFrame) -> dict:
        default = {
            "pre_race_score": 0.0,
            "pre_race_time_score": 0.0,
            "pre_race_motor_score": 0.0,
            "pre_race_rank_score": 0.0,
            "pre_race_multiplier": 1.0,
            "pre_race_gate": "MISSING",
            "pre_race_block": False,
            "pre_race_priority": False,
            "pre_race_source": "missing",
            "pre_race_note": "直前情報欠損",
            "pre_race_lane_count": 0,
        }
        if race_feat.empty:
            return default

        lanes: list[str] = []
        for key in ("first_lane", "second_lane", "third_lane"):
            lane = top_row.get(key)
            if pd.isna(lane):
                continue
            text = str(lane).strip()
            if text:
                lanes.append(text)
        if not lanes:
            return default

        trio = race_feat.copy()
        trio["lane"] = pd.to_numeric(trio["lane"], errors="coerce")
        trio = trio[trio["lane"].astype("Int64").astype(str).isin(lanes)].copy()
        if len(trio) < 3:
            return {
                **default,
                "pre_race_note": f"直前情報不足({len(trio)}/3)",
                "pre_race_lane_count": int(len(trio)),
            }

        trio["exhibition_time"] = pd.to_numeric(trio.get("exhibition_time"), errors="coerce")
        trio["start_display_st"] = pd.to_numeric(trio.get("start_display_st"), errors="coerce")
        trio["start_timing"] = pd.to_numeric(trio.get("start_timing"), errors="coerce")
        trio["avg_st"] = pd.to_numeric(trio.get("avg_st"), errors="coerce")
        trio["st_mean_recent6"] = pd.to_numeric(trio.get("st_mean_recent6"), errors="coerce")
        trio["recent6_avg_st"] = pd.to_numeric(trio.get("recent6_avg_st"), errors="coerce")
        trio["recent3_avg_st"] = pd.to_numeric(trio.get("recent3_avg_st"), errors="coerce")
        trio["motor_2ren_rate"] = pd.to_numeric(trio.get("motor_2ren_rate"), errors="coerce")
        trio["exhibition_time_rank"] = pd.to_numeric(trio.get("exhibition_time_rank"), errors="coerce")
        trio["start_timing_rank_in_race"] = pd.to_numeric(trio.get("start_timing_rank_in_race"), errors="coerce")

        trio["timing_value"] = trio["exhibition_time"]
        trio["timing_value"] = trio["timing_value"].fillna(trio["start_display_st"])
        trio["timing_value"] = trio["timing_value"].fillna(trio["start_timing"])
        trio["timing_value"] = trio["timing_value"].fillna(trio["avg_st"])
        trio["timing_value"] = trio["timing_value"].fillna(trio["st_mean_recent6"])
        trio["timing_value"] = trio["timing_value"].fillna(trio["recent6_avg_st"])
        trio["timing_value"] = trio["timing_value"].fillna(trio["recent3_avg_st"])
        trio = trio.dropna(subset=["timing_value", "motor_2ren_rate"])
        if trio.empty:
            return {
                **default,
                "pre_race_note": "直前情報欠損",
                "pre_race_lane_count": 0,
            }

        trio["timing_rank"] = trio["exhibition_time_rank"]
        trio["timing_rank"] = trio["timing_rank"].fillna(trio["start_timing_rank_in_race"])
        trio["motor_rank"] = trio["motor_2ren_rate"].rank(method="min", ascending=False)
        trio["timing_rank"] = trio["timing_rank"].fillna(
            trio["timing_value"].rank(method="min", ascending=True)
        )

        timing_mean = float(trio["timing_value"].mean())
        lane_time_scores: list[float] = []
        lane_motor_scores: list[float] = []
        lane_rank_scores: list[float] = []
        lane_scores: list[float] = []
        details: list[str] = []
        lane_count = max(int(len(trio)), 1)
        for _, lane_row in trio.iterrows():
            time_score = self._score_from_time_diff(timing_mean - float(lane_row["timing_value"]))
            motor_pct = float(lane_row["motor_rank"]) / float(lane_count)
            motor_score = self._score_from_percentile(motor_pct)
            rank_score = self._score_from_display_rank(lane_row["timing_rank"])
            lane_score = (
                self.pre_race_time_weight * time_score
                + self.pre_race_motor_weight * motor_score
                + self.pre_race_rank_weight * rank_score
            )
            lane_time_scores.append(float(time_score))
            lane_motor_scores.append(float(motor_score))
            lane_rank_scores.append(float(rank_score))
            lane_scores.append(float(lane_score))
            details.append(
                f"lane{int(lane_row['lane'])}:T{time_score:+.0f}/M{motor_score:+.0f}/R{rank_score:+.0f}"
            )

        if not lane_scores:
            return default

        pre_score = float(np.mean(lane_scores))
        if pre_score <= self.pre_race_block_threshold:
            gate = "BLOCK"
        elif pre_score >= self.pre_race_priority_threshold:
            gate = "PRIORITY"
        elif pre_score >= self.pre_race_boost_threshold:
            gate = "BOOST"
        else:
            gate = "NORMAL"

        return {
            "pre_race_score": round(pre_score, 3),
            "pre_race_time_score": round(float(np.mean(lane_time_scores)), 3),
            "pre_race_motor_score": round(float(np.mean(lane_motor_scores)), 3),
            "pre_race_rank_score": round(float(np.mean(lane_rank_scores)), 3),
            "pre_race_multiplier": round(float(self.pre_race_prob_boost if pre_score >= self.pre_race_boost_threshold else 1.0), 3),
            "pre_race_gate": gate,
            "pre_race_block": gate == "BLOCK",
            "pre_race_priority": gate == "PRIORITY",
            "pre_race_source": "exhibition_time->start_display_st->start_timing / exhibition_time_rank->start_timing_rank_in_race",
            "pre_race_note": " / ".join(details),
            "pre_race_lane_count": int(lane_count),
        }

    def _compute_first_place_profile(self, top_row: pd.Series, race_feat: pd.DataFrame) -> dict:
        default = {
            "first_place_score": 0.0,
            "first_place_course_score": 0.0,
            "first_place_motor_score": 0.0,
            "first_place_time_score": 0.0,
            "first_place_start_score": 0.0,
            "first_place_multiplier": 1.0,
            "first_place_gate": "MISSING",
            "first_place_block": False,
            "first_place_priority": False,
            "first_place_source": "missing",
            "first_place_note": "1着情報欠損",
            "first_place_lane": None,
        }
        if not self.first_place_enabled or race_feat.empty:
            return default

        first_lane = top_row.get("first_lane")
        if pd.isna(first_lane):
            first_lane = top_row.get("lane", top_row.get("lane_num"))
        if pd.isna(first_lane) and not race_feat.empty:
            lane_candidates = race_feat.copy()
            for candidate_col in ("win_proba_norm", "first_win_proba", "calibrated_hit_prob", "approx_prob"):
                if candidate_col in lane_candidates.columns:
                    lane_values = pd.to_numeric(lane_candidates[candidate_col], errors="coerce")
                    if lane_values.notna().any():
                        best_idx = lane_values.idxmax()
                        if pd.notna(best_idx):
                            first_lane = lane_candidates.loc[best_idx].get("lane", lane_candidates.loc[best_idx].get("lane_num"))
                            break
        if pd.isna(first_lane):
            return default
        lane_value = pd.to_numeric(first_lane, errors="coerce")
        if pd.isna(lane_value):
            return default
        lane_int = int(round(float(lane_value)))

        lane_df = race_feat.copy()
        lane_df["lane"] = pd.to_numeric(lane_df.get("lane"), errors="coerce")
        lane_df = lane_df[lane_df["lane"].astype("Int64") == lane_int].copy()
        if lane_df.empty:
            return {
                **default,
                "first_place_note": f"1着情報不足(lane={lane_int})",
                "first_place_lane": lane_int,
            }

        lane_row = lane_df.iloc[0]
        lane_idx = lane_df.index[0]
        lane_count = max(int(len(race_feat)), 1)

        course_no = pd.to_numeric(lane_row.get("course_no"), errors="coerce")
        if pd.isna(course_no):
            course_no = pd.to_numeric(lane_row.get("lane_num"), errors="coerce")
        if pd.isna(course_no):
            course_no = pd.to_numeric(lane_row.get("waku_no"), errors="coerce")
        course_score = self._score_from_course_no(course_no)

        race_df = race_feat.copy()
        race_df["motor_2ren_rate"] = pd.to_numeric(race_df.get("motor_2ren_rate"), errors="coerce")
        race_df["exhibition_time"] = pd.to_numeric(race_df.get("exhibition_time"), errors="coerce")
        race_df["start_display_st"] = pd.to_numeric(race_df.get("start_display_st"), errors="coerce")
        race_df["start_timing"] = pd.to_numeric(race_df.get("start_timing"), errors="coerce")
        race_df["avg_st"] = pd.to_numeric(race_df.get("avg_st"), errors="coerce")
        race_df["start_timing_rank_in_race"] = pd.to_numeric(race_df.get("start_timing_rank_in_race"), errors="coerce")
        race_df["motor_rank"] = race_df["motor_2ren_rate"].rank(method="min", ascending=False)
        motor_rank_value = pd.to_numeric(race_df.loc[lane_idx, "motor_rank"], errors="coerce") if lane_idx in race_df.index else pd.NA
        motor_rank_pct = float(motor_rank_value if pd.notna(motor_rank_value) else 1.0) / float(lane_count)
        motor_score = self._score_from_percentile(motor_rank_pct)

        timing_value = pd.to_numeric(lane_row.get("exhibition_time"), errors="coerce")
        if pd.isna(timing_value):
            timing_value = pd.to_numeric(lane_row.get("start_display_st"), errors="coerce")
        if pd.isna(timing_value):
            timing_value = pd.to_numeric(lane_row.get("start_timing"), errors="coerce")
        if pd.isna(timing_value):
            timing_value = pd.to_numeric(lane_row.get("avg_st"), errors="coerce")
        if pd.isna(timing_value):
            timing_value = pd.to_numeric(lane_row.get("st_mean_recent6"), errors="coerce")
        if pd.isna(timing_value):
            timing_value = pd.to_numeric(lane_row.get("recent6_avg_st"), errors="coerce")
        if pd.isna(timing_value):
            timing_value = pd.to_numeric(lane_row.get("recent3_avg_st"), errors="coerce")

        if pd.isna(timing_value):
            return {
                **default,
                "first_place_note": f"1着情報不足(lane={lane_int})",
                "first_place_lane": lane_int,
            }

        timing_series = race_df["exhibition_time"].fillna(race_df["start_display_st"]).fillna(race_df["start_timing"])
        timing_series = timing_series.fillna(race_df["avg_st"])
        timing_series = timing_series.fillna(race_df.get("st_mean_recent6"))
        timing_series = timing_series.fillna(race_df.get("recent6_avg_st"))
        timing_series = timing_series.fillna(race_df.get("recent3_avg_st"))
        timing_series = timing_series.dropna()
        timing_mean = float(timing_series.mean()) if not timing_series.empty else float(timing_value)
        time_score = self._score_from_time_diff(timing_mean - float(timing_value))

        start_value = pd.to_numeric(lane_row.get("avg_st"), errors="coerce")
        if pd.isna(start_value):
            start_value = pd.to_numeric(lane_row.get("start_timing"), errors="coerce")
        if pd.isna(start_value):
            start_value = pd.to_numeric(lane_row.get("start_display_st"), errors="coerce")
        if pd.isna(start_value):
            start_value = pd.to_numeric(lane_row.get("exhibition_time"), errors="coerce")
        if pd.isna(start_value):
            start_value = pd.to_numeric(lane_row.get("st_mean_recent6"), errors="coerce")
        if pd.isna(start_value):
            start_value = pd.to_numeric(lane_row.get("recent6_avg_st"), errors="coerce")
        if pd.isna(start_value):
            start_value = pd.to_numeric(lane_row.get("recent3_avg_st"), errors="coerce")
        start_score = 0
        if not pd.isna(start_value):
            race_df["start_value"] = race_df["avg_st"].fillna(race_df["start_timing"]).fillna(race_df["start_display_st"]).fillna(race_df["exhibition_time"])
            race_df["start_value"] = race_df["start_value"].fillna(race_df.get("st_mean_recent6"))
            race_df["start_value"] = race_df["start_value"].fillna(race_df.get("recent6_avg_st"))
            race_df["start_value"] = race_df["start_value"].fillna(race_df.get("recent3_avg_st"))
            race_df["start_rank"] = race_df["start_value"].rank(method="min", ascending=True)
            start_rank_value = pd.to_numeric(race_df.loc[lane_idx, "start_rank"], errors="coerce") if lane_idx in race_df.index else pd.NA
            start_rank = float(start_rank_value if pd.notna(start_rank_value) else 1.0)
            start_score = self._score_from_percentile(start_rank / float(lane_count))

        first_place_score = (
            self.first_place_course_weight * float(course_score)
            + self.first_place_motor_weight * float(motor_score)
            + self.first_place_time_weight * float(time_score)
            + self.first_place_start_weight * float(start_score)
        )
        if first_place_score < self.first_place_block_threshold:
            gate = "BLOCK"
        elif first_place_score >= self.first_place_priority_threshold:
            gate = "PRIORITY"
        elif first_place_score >= 1.0:
            gate = "BOOST"
        else:
            gate = "NORMAL"

        return {
            "first_place_score": round(float(first_place_score), 3),
            "first_place_course_score": round(float(course_score), 3),
            "first_place_motor_score": round(float(motor_score), 3),
            "first_place_time_score": round(float(time_score), 3),
            "first_place_start_score": round(float(start_score), 3),
            "first_place_multiplier": round(float(self.first_place_prob_boost if first_place_score >= self.first_place_priority_threshold else 1.0), 3),
            "first_place_gate": gate,
            "first_place_block": gate == "BLOCK",
            "first_place_priority": gate == "PRIORITY",
            "first_place_source": "course_no / motor_2ren_rate / exhibition_time / avg_st",
            "first_place_note": f"lane{lane_int}:C{course_score:+.0f}/M{motor_score:+.0f}/T{time_score:+.0f}/S{start_score:+.0f}",
            "first_place_lane": lane_int,
        }

    def _compute_place_role_profile(self, top_row: pd.Series, race_feat: pd.DataFrame, role: str) -> dict:
        role = str(role or "").lower()
        if role not in {"second", "third"}:
            return {}

        role_label = "2着" if role == "second" else "3着"
        default = {
            f"{role}_place_score": 0.0,
            f"{role}_place_course_score": 0.0,
            f"{role}_place_motor_score": 0.0,
            f"{role}_place_time_score": 0.0,
            f"{role}_place_multiplier": 1.0,
            f"{role}_place_gate": "MISSING",
            f"{role}_place_block": False,
            f"{role}_place_priority": False,
            f"{role}_place_source": "missing",
            f"{role}_place_note": f"{role_label}情報欠損",
            f"{role}_place_lane": None,
        }
        if not self.place_role_enabled or race_feat.empty:
            return default

        lane_key = f"{role}_lane"
        lane_value = top_row.get(lane_key)
        if pd.isna(lane_value):
            return default
        lane_value_num = pd.to_numeric(lane_value, errors="coerce")
        if pd.isna(lane_value_num):
            return default
        lane_int = int(round(float(lane_value_num)))

        lane_df = race_feat.copy()
        lane_df["lane"] = pd.to_numeric(lane_df.get("lane"), errors="coerce")
        lane_df = lane_df[lane_df["lane"].astype("Int64") == lane_int].copy()
        if lane_df.empty:
            return {
                **default,
                f"{role}_place_note": f"{role_label}情報不足(lane={lane_int})",
                f"{role}_place_lane": lane_int,
            }

        lane_row = lane_df.iloc[0]
        lane_idx = lane_df.index[0]
        lane_count = max(int(len(race_feat)), 1)

        race_df = race_feat.copy()
        race_df["motor_2ren_rate"] = pd.to_numeric(race_df.get("motor_2ren_rate"), errors="coerce")
        race_df["exhibition_time"] = pd.to_numeric(race_df.get("exhibition_time"), errors="coerce")
        race_df["start_display_st"] = pd.to_numeric(race_df.get("start_display_st"), errors="coerce")
        race_df["start_timing"] = pd.to_numeric(race_df.get("start_timing"), errors="coerce")
        race_df["avg_st"] = pd.to_numeric(race_df.get("avg_st"), errors="coerce")
        race_df["motor_rank"] = race_df["motor_2ren_rate"].rank(method="min", ascending=False)

        motor_rank_value = pd.to_numeric(race_df.loc[lane_idx, "motor_rank"], errors="coerce") if lane_idx in race_df.index else pd.NA
        motor_rank_pct = float(motor_rank_value if pd.notna(motor_rank_value) else 1.0) / float(lane_count)
        motor_score = self._score_from_percentile(motor_rank_pct)

        timing_value = pd.to_numeric(lane_row.get("exhibition_time"), errors="coerce")
        if pd.isna(timing_value):
            timing_value = pd.to_numeric(lane_row.get("start_display_st"), errors="coerce")
        if pd.isna(timing_value):
            timing_value = pd.to_numeric(lane_row.get("start_timing"), errors="coerce")
        if pd.isna(timing_value):
            timing_value = pd.to_numeric(lane_row.get("avg_st"), errors="coerce")
        if pd.isna(timing_value):
            timing_value = pd.to_numeric(lane_row.get("st_mean_recent6"), errors="coerce")
        if pd.isna(timing_value):
            timing_value = pd.to_numeric(lane_row.get("recent6_avg_st"), errors="coerce")
        if pd.isna(timing_value):
            timing_value = pd.to_numeric(lane_row.get("recent3_avg_st"), errors="coerce")
        if pd.isna(timing_value):
            return {
                **default,
                f"{role}_place_note": f"{role_label}情報不足(lane={lane_int})",
                f"{role}_place_lane": lane_int,
            }

        timing_series = race_df["exhibition_time"].fillna(race_df["start_display_st"]).fillna(race_df["start_timing"])
        timing_series = timing_series.fillna(race_df["avg_st"])
        timing_series = timing_series.fillna(race_df.get("st_mean_recent6"))
        timing_series = timing_series.fillna(race_df.get("recent6_avg_st"))
        timing_series = timing_series.fillna(race_df.get("recent3_avg_st"))
        timing_series = timing_series.dropna()
        timing_mean = float(timing_series.mean()) if not timing_series.empty else float(timing_value)
        time_score = self._score_from_time_diff(timing_mean - float(timing_value))

        course_no = pd.to_numeric(lane_row.get("course_no"), errors="coerce")
        if pd.isna(course_no):
            course_no = pd.to_numeric(lane_row.get("lane_num"), errors="coerce")
        if pd.isna(course_no):
            course_no = pd.to_numeric(lane_row.get("waku_no"), errors="coerce")

        if role == "second":
            course_score = self._score_from_second_course_no(course_no)
            course_weight = self.second_place_course_weight
            motor_weight = self.second_place_motor_weight
            time_weight = self.second_place_time_weight
            block_threshold = self.second_place_block_threshold
            priority_threshold = self.second_place_priority_threshold
            prob_boost = self.second_place_prob_boost
        else:
            course_score = self._score_from_third_course_no(course_no)
            course_weight = self.third_place_course_weight
            motor_weight = self.third_place_motor_weight
            time_weight = self.third_place_time_weight
            block_threshold = self.third_place_block_threshold
            priority_threshold = self.third_place_priority_threshold
            prob_boost = self.third_place_prob_boost

        place_score = (
            course_weight * float(course_score)
            + motor_weight * float(motor_score)
            + time_weight * float(time_score)
        )
        if place_score < block_threshold:
            gate = "BLOCK"
        elif place_score >= priority_threshold:
            gate = "PRIORITY"
        elif place_score >= 1.0:
            gate = "BOOST"
        else:
            gate = "NORMAL"

        return {
            f"{role}_place_score": round(float(place_score), 3),
            f"{role}_place_course_score": round(float(course_score), 3),
            f"{role}_place_motor_score": round(float(motor_score), 3),
            f"{role}_place_time_score": round(float(time_score), 3),
            f"{role}_place_multiplier": round(float(prob_boost if place_score >= priority_threshold else 1.0), 3),
            f"{role}_place_gate": gate,
            f"{role}_place_block": gate == "BLOCK",
            f"{role}_place_priority": gate == "PRIORITY",
            f"{role}_place_source": "course_no / motor_2ren_rate / exhibition_time / avg_st",
            f"{role}_place_note": f"lane{lane_int}:C{course_score:+.0f}/M{motor_score:+.0f}/T{time_score:+.0f}",
            f"{role}_place_lane": lane_int,
        }

    def _compute_race_selection_profile(
        self,
        top_row: pd.Series,
        race_feat: pd.DataFrame,
        first_place_score: float,
        pre_race_score: float,
        has_real_odds: bool,
    ) -> dict:
        default = {
            "race_score": 0.0,
            "race_first_confidence": 0.0,
            "race_odds_balance_score": 0.0,
            "race_data_quality_score": 0.0,
            "race_gate": "MISSING",
            "race_block": False,
            "race_watch": False,
            "race_priority": False,
            "race_source": "first_place_score / pre_race_score / odds_balance / data_quality",
            "race_note": "レース判定情報欠損",
        }
        if not self.race_selection_enabled:
            return default

        row_odds = float(pd.to_numeric(top_row.get("odds", 0.0), errors="coerce") or 0.0)
        first_place_prob = float(
            pd.to_numeric(top_row.get("first_place_prob", top_row.get("first_win_proba")), errors="coerce") or 0.0
        )
        first_lane = pd.to_numeric(top_row.get("first_lane"), errors="coerce")
        first_lane_int = int(round(float(first_lane))) if pd.notna(first_lane) else None

        if first_lane_int == 1 and has_real_odds and row_odds <= 50 and first_place_prob >= 0.45:
            odds_balance_score = 2.0
        elif row_odds <= 80 and first_place_prob >= 0.30:
            odds_balance_score = 1.0
        elif row_odds <= 150:
            odds_balance_score = 0.0
        elif row_odds <= 300:
            odds_balance_score = -1.0
        else:
            odds_balance_score = -2.0

        critical_values = [
            top_row.get("first_place_score"),
            top_row.get("pre_race_score"),
            top_row.get("odds"),
            top_row.get("approx_prob"),
            top_row.get("first_win_proba"),
            top_row.get("second_place_score"),
            top_row.get("third_place_score"),
        ]
        data_quality_score = 1.0
        if (not has_real_odds) or any(pd.isna(v) for v in critical_values):
            data_quality_score = -2.0
        elif str(top_row.get("first_place_gate", "")).upper() == "MISSING":
            data_quality_score = -2.0
        elif str(top_row.get("pre_race_gate", "")).upper() == "MISSING":
            data_quality_score = -2.0

        first_confidence = float(first_place_score)
        race_score = (
            self.race_selection_first_weight * first_confidence
            + self.race_selection_pre_weight * float(pre_race_score)
            + self.race_selection_odds_weight * float(odds_balance_score)
            + self.race_selection_quality_weight * float(data_quality_score)
        )

        if (
            first_place_score < self.first_place_block_threshold
            or pre_race_score <= -1.0
            or odds_balance_score <= -3.0
            or data_quality_score < -2.0
        ):
            gate = "BLOCK"
        elif race_score >= self.race_selection_buy_threshold:
            gate = "PRIORITY"
        elif race_score >= self.race_selection_watch_threshold:
            gate = "WATCH"
        else:
            gate = "SKIP"

        return {
            "race_score": round(float(race_score), 3),
            "race_first_confidence": round(float(first_confidence), 3),
            "race_odds_balance_score": round(float(odds_balance_score), 3),
            "race_data_quality_score": round(float(data_quality_score), 3),
            "race_gate": gate,
            "race_block": gate == "BLOCK",
            "race_watch": gate == "WATCH",
            "race_priority": gate == "PRIORITY",
            "race_source": "first_place_score / pre_race_score / odds_balance / data_quality",
            "race_note": (
                f"1着{first_place_score:+.2f} / 直前{pre_race_score:+.2f} / "
                f"odds_balance{odds_balance_score:+.0f} / data_quality{data_quality_score:+.0f}"
            ),
            "race_first_lane": first_lane_int,
            "race_odds": round(float(row_odds), 3),
            "race_first_place_prob": round(float(first_place_prob), 3),
        }

    def _human_reason(
        self,
        decision: str,
        row: pd.Series,
        has_real_odds: bool,
        risk_labels: list[str],
        notes: list[str],
    ) -> str:
        parts: list[str] = []
        parts.append("実オッズあり" if has_real_odds else "実オッズ未取得のため参考判定")
        hit_prob = row.get("calibrated_hit_prob", row.get("approx_prob"))
        first_place_prob = row.get("first_place_prob", row.get("first_win_proba"))
        first_place_score = row.get("first_place_score")
        first_place_gate = row.get("first_place_gate")
        second_place_score = row.get("second_place_score")
        second_place_gate = row.get("second_place_gate")
        third_place_score = row.get("third_place_score")
        third_place_gate = row.get("third_place_gate")
        race_score = row.get("race_score")
        race_gate = row.get("race_gate")
        if pd.notna(first_place_prob):
            parts.append(f"1着確率 {float(first_place_prob):.3f}")
        if pd.notna(first_place_score):
            parts.append(f"1着スコア {float(first_place_score):.2f}")
        if first_place_gate:
            parts.append(f"1着判定 {first_place_gate}")
        if pd.notna(second_place_score):
            parts.append(f"2着スコア {float(second_place_score):.2f}")
        if second_place_gate:
            parts.append(f"2着判定 {second_place_gate}")
        if pd.notna(third_place_score):
            parts.append(f"3着スコア {float(third_place_score):.2f}")
        if third_place_gate:
            parts.append(f"3着判定 {third_place_gate}")
        if pd.notna(race_score):
            parts.append(f"レーススコア {float(race_score):.2f}")
        if race_gate:
            parts.append(f"レース判定 {race_gate}")
        if pd.notna(hit_prob):
            parts.append(f"的中確率 {float(hit_prob):.3f}")
        if pd.notna(row.get("ev")):
            parts.append(f"net EV {float(row['ev']):.3f}")
        if pd.notna(row.get("odds")):
            parts.append(f"オッズ {float(row['odds']):.1f}")
        parts.append(f"判定={decision}")
        if risk_labels:
            parts.append("リスク: " + " / ".join(risk_labels[:3]))
        parts.extend(notes)
        return " / ".join([p for p in parts if p])

    def _build_reason_lines(self, row: pd.Series, risk_labels: list[str], decision: str) -> list[str]:
        reasons: list[str] = []
        hit_prob = row.get("calibrated_hit_prob", row.get("approx_prob"))
        first_place_prob = row.get("first_place_prob", row.get("first_win_proba"))
        first_place_score = row.get("first_place_score")
        first_place_gate = row.get("first_place_gate")
        second_place_score = row.get("second_place_score")
        second_place_gate = row.get("second_place_gate")
        third_place_score = row.get("third_place_score")
        third_place_gate = row.get("third_place_gate")
        race_score = row.get("race_score")
        race_gate = row.get("race_gate")
        if pd.notna(first_place_prob):
            reasons.append(f"1着候補確率 {float(first_place_prob):.3f}")
        if pd.notna(first_place_score):
            reasons.append(f"1着スコア {float(first_place_score):.2f}")
        if first_place_gate:
            reasons.append(f"1着判定 {first_place_gate}")
        if pd.notna(second_place_score):
            reasons.append(f"2着スコア {float(second_place_score):.2f}")
        if second_place_gate:
            reasons.append(f"2着判定 {second_place_gate}")
        if pd.notna(third_place_score):
            reasons.append(f"3着スコア {float(third_place_score):.2f}")
        if third_place_gate:
            reasons.append(f"3着判定 {third_place_gate}")
        if pd.notna(race_score):
            reasons.append(f"レーススコア {float(race_score):.2f}")
        if race_gate:
            reasons.append(f"レース判定 {race_gate}")
        if pd.notna(hit_prob):
            reasons.append(f"的中確率 {float(hit_prob):.3f}")
        if pd.notna(row.get("ev")):
            reasons.append(f"EV {float(row['ev']):.3f}")
        if pd.notna(row.get("odds")):
            reasons.append(f"オッズ {float(row['odds']):.1f}")
        if decision == "BUY":
            reasons.append("実オッズあり・基準クリア")
        elif decision == "WATCH":
            reasons.append("期待値はあるが不確実性あり")
        if risk_labels:
            reasons.extend(risk_labels[:2])
        return reasons

    @staticmethod
    def _normalize_odds_fetch_status(value: object) -> str:
        if pd.isna(value):
            return ""
        s = str(value or "").strip().lower()
        if s in {"cached", "success", "partial_missing"}:
            return s
        if s in {"pending_unpublished", "unpublished"}:
            return "pending_unpublished"
        if s in {"failed", "error"}:
            return "failed"
        return s

    @classmethod
    def _derive_odds_status(
        cls,
        has_real_odds: bool,
        decision: str,
        row_odds_source: str,
        odds_fetch_status: str = "",
        odds_last_fetched_at: str = "",
    ) -> str:
        if has_real_odds:
            return "real_odds_available"
        fetch_status = cls._normalize_odds_fetch_status(odds_fetch_status)
        last_fetched = "" if pd.isna(odds_last_fetched_at) else str(odds_last_fetched_at or "").strip()
        if fetch_status == "failed":
            return "real_odds_missing_fetch_failed"
        if fetch_status in {"cached", "success", "partial_missing"}:
            return "real_odds_missing_never_fetched"
        if str(decision).upper() == "PENDING":
            return "real_odds_pending_before_deadline"
        if last_fetched:
            return "real_odds_missing_never_fetched"
        if row_odds_source in {"missing", ""}:
            return "real_odds_pending_before_deadline"
        return "real_odds_missing_never_fetched"

    @staticmethod
    def _append_unique_note(notes: list[str], note: str) -> None:
        text = str(note or "").strip()
        if text and text not in notes:
            notes.append(text)

    @staticmethod
    def _safe_float(value: object, default: float = 0.0) -> float:
        numeric = pd.to_numeric(value, errors="coerce")
        if pd.isna(numeric):
            return float(default)
        return float(numeric)

    @staticmethod
    def _safe_timestamp(value: object) -> pd.Timestamp | None:
        if pd.isna(value):
            return None
        ts = pd.to_datetime(value, errors="coerce")
        if pd.isna(ts):
            return None
        return pd.Timestamp(ts)

    def _evaluate_buy_hard_guard(
        self,
        *,
        row: pd.Series,
        row_ev: float,
        confidence_score: float,
        has_real_odds: bool,
        odds_fetch_status: str,
        odds_last_fetched_at: str,
    ) -> list[tuple[str, str]]:
        odds_fetch_status_norm = self._normalize_odds_fetch_status(odds_fetch_status)
        allow_pending_without_real_odds = (
            (not has_real_odds)
            and odds_fetch_status_norm in {"pending_unpublished", "unknown"}
        )
        required_values = {
            "approx_prob": row.get("approx_prob"),
            "first_win_proba": row.get("first_win_proba"),
            "first_place_score": row.get("first_place_score"),
            "second_place_score": row.get("second_place_score"),
            "third_place_score": row.get("third_place_score"),
            "calibrated_hit_prob": row.get("calibrated_hit_prob"),
        }
        total_fields = len(required_values)
        present_fields = sum(
            1
            for value in required_values.values()
            if not pd.isna(value) and str(value).strip() != ""
        )
        completeness = (present_fields / total_fields) if total_fields else 1.0
        odds_availability = 1.0 if has_real_odds else 0.0

        race_date = self._safe_timestamp(row.get("date"))
        fetched_at = self._safe_timestamp(odds_last_fetched_at)
        stale_age_days = float("inf")
        if has_real_odds and race_date is not None and fetched_at is not None:
            stale_age_days = abs((race_date.normalize() - fetched_at.normalize()).days)

        failures: list[tuple[str, str]] = []
        if row_ev < self.buy_hard_guard_min_ev:
            failures.append(
                (
                    "hard_guard_min_ev",
                    f"EV {row_ev:.3f} < {self.buy_hard_guard_min_ev:.3f}",
                )
            )
        if (not allow_pending_without_real_odds) and completeness < self.buy_hard_guard_min_data_completeness:
            failures.append(
                (
                    "hard_guard_min_data_completeness",
                    f"data completeness {completeness:.3f} < {self.buy_hard_guard_min_data_completeness:.3f}",
                )
            )
        if (not allow_pending_without_real_odds) and odds_availability < self.buy_hard_guard_min_odds_availability:
            failure_reason = "実オッズ未取得"
            if odds_fetch_status_norm == "failed":
                failure_reason = "実オッズ取得失敗"
            elif odds_fetch_status_norm == "pending_unpublished":
                failure_reason = "実オッズ未公表"
            failures.append(
                (
                    "hard_guard_min_odds_availability",
                    f"odds availability {odds_availability:.3f} < {self.buy_hard_guard_min_odds_availability:.3f} ({failure_reason})",
                )
            )
        if has_real_odds and stale_age_days > self.buy_hard_guard_max_stale_age_days:
            failures.append(
                (
                    "hard_guard_max_stale_age",
                    f"stale age {stale_age_days:.0f}d > {self.buy_hard_guard_max_stale_age_days:.0f}d",
                )
            )
        if (not allow_pending_without_real_odds) and confidence_score < self.buy_hard_guard_min_model_confidence:
            failures.append(
                (
                    "hard_guard_min_model_confidence",
                    f"model confidence {confidence_score:.3f} < {self.buy_hard_guard_min_model_confidence:.3f}",
                )
            )
        return failures

    @staticmethod
    def _extract_hard_guard_reason(notes: list[str]) -> str:
        for text in notes:
            note = str(text or "").strip()
            if not note.startswith("hard_guard_"):
                continue
            return note.split(":", 1)[0].strip()
        return ""

    def _derive_stop_reason(
        self,
        *,
        decision: str,
        has_real_odds: bool,
        odds_status: str,
        odds_fetch_status: str,
        hard_skip: bool,
        buy_eligible: bool,
        watch_eligible: bool,
        race_block: bool,
        race_priority: bool,
        risk_flag: bool,
        first_place_gate: str,
        pre_race_gate: str,
        notes: list[str],
    ) -> str:
        decision_upper = str(decision).upper()
        if decision_upper == "BUY":
            return "buy_eligible"
        hard_guard_reason = self._extract_hard_guard_reason(notes)
        if hard_guard_reason:
            return hard_guard_reason
        odds_status_norm = str(odds_status or "").strip()
        odds_fetch_status_norm = self._normalize_odds_fetch_status(odds_fetch_status)
        if not has_real_odds:
            if odds_fetch_status_norm == "failed":
                return "real_odds_missing_fetch_failed"
            if odds_fetch_status_norm == "pending_unpublished":
                return "real_odds_pending_unpublished"
            if odds_status_norm == "real_odds_pending_before_deadline" or decision_upper == "PENDING":
                return "real_odds_pending_before_deadline"
            return "real_odds_missing_never_fetched"
        if decision_upper == "PENDING":
            return "PENDING"
        if hard_skip:
            return "hard_skip"
        if race_block:
            return "race_gate_block"
        if first_place_gate == "MISSING":
            return "first_place_missing"
        if pre_race_gate == "MISSING":
            return "pre_race_missing"
        if risk_flag:
            return "risk_flag"
        if decision_upper == "WATCH" or watch_eligible:
            return "watch_eligible"
        if not race_priority:
            return "race_not_priority"
        for text in notes:
            if "ROI_FILTER" in text or "AUTO_FILTER" in text:
                return "roi_filter"
        if buy_eligible:
            return "buy_eligible"
        return "not_buy_eligible"

    def _load_candidates(self, candidates_path):
        if not os.path.exists(candidates_path):
            raise FileNotFoundError(f"Candidate file not found: {candidates_path}")
        return pd.read_csv(candidates_path)

    def _load_race_boat_counts(self, race_card_path):
        if not race_card_path or not os.path.exists(race_card_path):
            return {}
        df = pd.read_csv(race_card_path)
        if not {"race_id", "lane"}.issubset(df.columns):
            return {}
        counts = (
            df[["race_id", "lane"]]
            .dropna(subset=["race_id", "lane"])
            .groupby("race_id")["lane"]
            .nunique()
            .to_dict()
        )
        return {str(k): int(v) for k, v in counts.items()}

    @staticmethod
    def _normalize_day_mode_rules(rules: dict | None) -> dict:
        rules = dict(rules or {})

        def _mode_rules(mode: str) -> dict:
            return dict(rules.get(mode, {}) or {})

        return {
            "normal": _mode_rules("normal"),
            "reduced": _mode_rules("reduced"),
            "stop": _mode_rules("stop"),
        }

    @staticmethod
    def _unique_race_count(df: pd.DataFrame | None) -> int:
        if df is None or df.empty:
            return 0
        if "race_id" in df.columns:
            return int(df["race_id"].astype(str).str.strip().replace("", pd.NA).dropna().nunique())
        if "union_key" in df.columns:
            return int(df["union_key"].astype(str).str.strip().replace("", pd.NA).dropna().nunique())
        return int(df.shape[0])

    def _compute_day_mode_context(
        self,
        ev_df: pd.DataFrame,
        race_card_path: str | os.PathLike | None = None,
    ) -> tuple[str, list[str], dict]:
        buy_mode_rules = self.day_mode_rules
        stop_rules = dict(buy_mode_rules.get("stop", {}) or {})
        normal_rules = dict(buy_mode_rules.get("normal", {}) or {})
        reduced_rules = dict(buy_mode_rules.get("reduced", {}) or {})

        total_rows = int(len(ev_df))
        real_odds_available_count = 0
        if total_rows > 0:
            if "odds_source" in ev_df.columns:
                real_odds_available_count = int(
                    ev_df["odds_source"].astype(str).str.lower().eq("real").sum()
                )
            elif "has_real_odds" in ev_df.columns:
                real_odds_available_count = int(
                    pd.to_numeric(ev_df["has_real_odds"], errors="coerce").fillna(0).astype(int).sum()
                )
        real_odds_available_rate = round(real_odds_available_count / total_rows, 4) if total_rows else None

        missing_feature_rate = None
        required_columns = list(self.pipeline_health_config.get("required_non_null_columns_by_phase", {}).get("buy_phase", []))
        if total_rows > 0 and required_columns:
            missing_columns = [col for col in required_columns if col not in ev_df.columns]
            if missing_columns:
                missing_feature_rate = 1.0
            else:
                missing_feature_rate = round(
                    float(ev_df[required_columns].isna().any(axis=1).sum()) / float(total_rows),
                    4,
                )
        elif total_rows > 0 and self.pre_race_features is not None and not self.pre_race_features.empty:
            feature_cols = [
                c
                for c in [
                    "race_id",
                    "lane",
                    "exhibition_time",
                    "start_display_st",
                    "start_timing",
                    "avg_st",
                    "motor_2ren_rate",
                    "start_timing_rank_in_race",
                ]
                if c in self.pre_race_features.columns
            ]
            if feature_cols:
                missing_feature_rate = round(
                    float(self.pre_race_features[feature_cols].isna().any(axis=1).sum())
                    / float(len(self.pre_race_features)),
                    4,
                )
        elif total_rows > 0:
            missing_feature_rate = 1.0

        race_card_df = pd.DataFrame()
        if race_card_path and os.path.exists(race_card_path):
            try:
                race_card_df = pd.read_csv(race_card_path, low_memory=False)
            except Exception:
                race_card_df = pd.DataFrame()
        today_races_count = self._unique_race_count(race_card_df)
        if today_races_count <= 0 and not self.pre_race_features.empty:
            today_races_count = int(self.pre_race_features["race_id"].astype(str).str.strip().replace("", pd.NA).dropna().nunique())

        predicted_race_count = 0
        if "race_id" in ev_df.columns:
            predicted_race_count = int(ev_df["race_id"].astype(str).str.strip().replace("", pd.NA).dropna().nunique())
        race_coverage = round(predicted_race_count / today_races_count, 4) if today_races_count > 0 else None

        reasons: list[str] = []
        stop_failed = []
        if real_odds_available_rate is not None and real_odds_available_rate < float(stop_rules.get("below_real_odds_available_rate", 0.35)):
            stop_failed.append(f"実オッズ{real_odds_available_rate:.2f}<{float(stop_rules.get('below_real_odds_available_rate', 0.35)):.2f}")
        if missing_feature_rate is not None and missing_feature_rate > float(stop_rules.get("above_missing_feature_rate", 0.06)):
            stop_failed.append(f"欠損{missing_feature_rate:.2f}>{float(stop_rules.get('above_missing_feature_rate', 0.06)):.2f}")
        if today_races_count and today_races_count < int(stop_rules.get("below_min_today_races", 60)):
            stop_failed.append(f"本日{today_races_count}<{int(stop_rules.get('below_min_today_races', 60))}")
        if race_coverage is not None and race_coverage < float(stop_rules.get("below_min_race_coverage", 0.80)):
            stop_failed.append(f"網羅{race_coverage:.2f}<{float(stop_rules.get('below_min_race_coverage', 0.80)):.2f}")
        if stop_failed:
            reasons.extend(stop_failed)
            metrics = {
                "real_odds_available_rate": real_odds_available_rate,
                "missing_feature_rate": missing_feature_rate,
                "today_races": today_races_count,
                "predicted_race_count": predicted_race_count,
                "race_coverage": race_coverage,
                "threshold": {"mode": "stop", **stop_rules},
            }
            return "stop", reasons, metrics

        normal_ok = True
        if real_odds_available_rate is not None:
            normal_ok = normal_ok and real_odds_available_rate >= float(normal_rules.get("min_real_odds_available_rate", 0.48))
        if missing_feature_rate is not None:
            normal_ok = normal_ok and missing_feature_rate <= float(normal_rules.get("max_missing_feature_rate", 0.03))
        if today_races_count:
            normal_ok = normal_ok and today_races_count >= int(normal_rules.get("min_today_races", 72))
        if race_coverage is not None:
            normal_ok = normal_ok and race_coverage >= float(normal_rules.get("min_race_coverage", 0.88))
        if normal_ok:
            metrics = {
                "real_odds_available_rate": real_odds_available_rate,
                "missing_feature_rate": missing_feature_rate,
                "today_races": today_races_count,
                "predicted_race_count": predicted_race_count,
                "race_coverage": race_coverage,
                "threshold": {"mode": "normal", **normal_rules},
            }
            return "normal", ["normal条件を満たす"], metrics

        reduced_ok = True
        if real_odds_available_rate is not None:
            reduced_ok = reduced_ok and real_odds_available_rate >= float(reduced_rules.get("min_real_odds_available_rate", 0.35))
        if missing_feature_rate is not None:
            reduced_ok = reduced_ok and missing_feature_rate <= float(reduced_rules.get("max_missing_feature_rate", 0.06))
        if today_races_count:
            reduced_ok = reduced_ok and today_races_count >= int(reduced_rules.get("min_today_races", 60))
        if race_coverage is not None:
            reduced_ok = reduced_ok and race_coverage >= float(reduced_rules.get("min_race_coverage", 0.80))
        if reduced_ok:
            metrics = {
                "real_odds_available_rate": real_odds_available_rate,
                "missing_feature_rate": missing_feature_rate,
                "today_races": today_races_count,
                "predicted_race_count": predicted_race_count,
                "race_coverage": race_coverage,
                "threshold": {"mode": "reduced", **reduced_rules},
            }
            return "reduced", ["reduced条件を満たす"], metrics

        metrics = {
            "real_odds_available_rate": real_odds_available_rate,
            "missing_feature_rate": missing_feature_rate,
            "today_races": today_races_count,
            "predicted_race_count": predicted_race_count,
            "race_coverage": race_coverage,
            "threshold": {"mode": "stop", **stop_rules},
        }
        reasons.append("条件未達のため stop")
        return "stop", reasons, metrics

    def _load_odds_race_status(self, odds_path: str | os.PathLike | None) -> pd.DataFrame:
        if not odds_path:
            return pd.DataFrame()
        odds_file = Path(odds_path)
        if not odds_file.exists():
            return pd.DataFrame()
        candidates = [
            odds_file.with_name("race_status.csv"),
            odds_file.with_name("today_trifecta_odds_race_status.csv"),
        ]
        for candidate in candidates:
            if candidate.exists():
                try:
                    status_df = pd.read_csv(candidate, low_memory=False)
                except Exception:
                    continue
                if "race_id" not in status_df.columns:
                    continue
                status_df = status_df.copy()
                status_df["race_id"] = status_df["race_id"].astype(str).str.strip()
                rename_map = {
                    "fetch_status": "odds_fetch_status",
                    "used_cache": "odds_fetch_used_cache",
                    "missing_odds_cells": "odds_missing_odds_cells",
                    "target_source": "odds_target_source",
                    "failed_reason": "odds_fetch_failed_reason",
                    "fetched_at": "odds_fetch_fetched_at",
                    "source_url": "odds_fetch_source_url",
                }
                status_df = status_df.rename(columns={k: v for k, v in rename_map.items() if k in status_df.columns})
                keep_cols = ["race_id"] + [c for c in rename_map.values() if c in status_df.columns]
                return status_df[keep_cols].drop_duplicates("race_id", keep="last")
        return pd.DataFrame()

    def build_ev_analysis(self, candidates_path, odds_path=None):
        df = self._load_candidates(candidates_path).copy()
        external_odds_used = False
        self.pre_race_features = self._load_pre_race_features()
        self._refresh_pre_race_feature_lookup(self.pre_race_features)
        odds_status_df = self._load_odds_race_status(odds_path)

        if odds_path and os.path.exists(odds_path):
            odds_df = pd.read_csv(odds_path)
            external_odds_used = True
            df["odds_race_id"] = df["race_id"].map(_odds_race_id_key)
            odds_df["odds_race_id"] = odds_df["race_id"].map(_odds_race_id_key)
            if "trifecta" not in odds_df.columns and "combo" in odds_df.columns:
                odds_df["trifecta"] = odds_df["combo"]
            merge_meta_cols = [
                col for col in ["odds_source", "odds_status", "fetched_at", "source", "source_url", "raw_odds_text"]
                if col in odds_df.columns
            ]
            keep_cols = ["odds_race_id", "trifecta", "odds"] + merge_meta_cols
            if {"race_id", "trifecta", "odds"}.issubset(odds_df.columns):
                df = df.merge(
                    odds_df[keep_cols],
                    on=["odds_race_id", "trifecta"],
                    how="left",
                    suffixes=("", "_live"),
                )
            elif {"trifecta", "odds"}.issubset(odds_df.columns):
                keep_cols = ["trifecta", "odds"] + merge_meta_cols
                df = df.merge(odds_df[keep_cols], on="trifecta", how="left", suffixes=("", "_live"))
            if not odds_status_df.empty:
                missing_cols = [c for c in odds_status_df.columns if c != "race_id" and c not in df.columns]
                if missing_cols:
                    odds_status_df = odds_status_df.copy()
                    odds_status_df["odds_race_id"] = odds_status_df["race_id"].map(_odds_race_id_key)
                    df = df.merge(
                        odds_status_df[["odds_race_id"] + missing_cols],
                        on="odds_race_id",
                        how="left",
                    )

        if self.first_place_enabled and not self.pre_race_features.empty and "race_id" in df.columns:
            first_place_frames: list[pd.DataFrame] = []
            for race_id, group in df.groupby("race_id", sort=False):
                race_feat = self._get_race_features(race_id)
                if race_feat.empty:
                    default_profile = self._compute_first_place_profile(group.iloc[0], race_feat)
                    profile_df = pd.DataFrame([default_profile] * len(group))
                else:
                    profile_df = group.apply(
                        lambda row: pd.Series(self._compute_first_place_profile(row, race_feat)),
                        axis=1,
                    ).reset_index(drop=True)
                first_place_frames.append(
                    pd.concat([group.reset_index(drop=True), profile_df.reset_index(drop=True)], axis=1)
                )
            if first_place_frames:
                df = pd.concat(first_place_frames, ignore_index=True, sort=False)

        if "odds" not in df.columns:
            df["odds"] = 50.0
            df["odds_source"] = "missing"
            df["odds_last_fetched_at"] = ""
            df["odds_raw_status"] = "missing"
            df["odds_fetch_status"] = "not_requested"
            df["odds_fetch_used_cache"] = False
            df["odds_missing_odds_cells"] = 120
            df["odds_target_source"] = ""
            df["odds_fetch_failed_reason"] = ""
        else:
            # マージ成功した（＝実オッズがある）行を特定
            is_from_file = df["odds"].notna()
            df["odds"] = pd.to_numeric(df["odds"], errors="coerce").fillna(50.0)
            if "fetched_at" in df.columns:
                df["odds_last_fetched_at"] = df["fetched_at"].fillna("").astype(str)
            else:
                df["odds_last_fetched_at"] = ""
            if "odds_status" in df.columns:
                df["odds_raw_status"] = df["odds_status"].fillna("").astype(str)
            else:
                df["odds_raw_status"] = ""
            if "odds_fetch_status" not in df.columns:
                df["odds_fetch_status"] = "not_requested" if not external_odds_used else "unknown"
            else:
                df["odds_fetch_status"] = df["odds_fetch_status"].fillna("unknown").astype(str)
            if "odds_fetch_used_cache" in df.columns:
                df["odds_fetch_used_cache"] = (
                    df["odds_fetch_used_cache"]
                    .fillna(False)
                    .astype(str)
                    .str.lower()
                    .isin({"true", "1", "yes"})
                )
            else:
                df["odds_fetch_used_cache"] = False
            if "odds_missing_odds_cells" in df.columns:
                df["odds_missing_odds_cells"] = pd.to_numeric(df["odds_missing_odds_cells"], errors="coerce").fillna(0).astype(int)
            else:
                df["odds_missing_odds_cells"] = 0
            if "odds_target_source" not in df.columns:
                df["odds_target_source"] = ""
            if "odds_fetch_failed_reason" not in df.columns:
                df["odds_fetch_failed_reason"] = ""
            # 初期値は estimated。実データがある所だけ real に上書き
            if "odds_source" in df.columns:
                src = df["odds_source"].astype(str)
                df["odds_source"] = src.where(src.str.len() > 0, "estimated")
            else:
                df["odds_source"] = "estimated"
            if external_odds_used:
                df.loc[is_from_file & df["odds_source"].eq("estimated"), "odds_source"] = "real"

        df["odds_source"] = df["odds_source"].apply(self._normalize_odds_source)
        df["has_real_odds"] = df["odds_source"] == "real"
        df["odds_provider"] = df["source"].fillna("").astype(str) if "source" in df.columns else ""
        df["odds_source_url"] = df["source_url"].fillna("").astype(str) if "source_url" in df.columns else ""
        if "first_place_prob" not in df.columns:
            df["first_place_prob"] = pd.to_numeric(df["first_win_proba"], errors="coerce").fillna(0.0)
        else:
            df["first_place_prob"] = pd.to_numeric(df["first_place_prob"], errors="coerce").fillna(
                pd.to_numeric(df["first_win_proba"], errors="coerce").fillna(0.0)
            )
        df["strategy_mode"] = self.strategy_mode

        df["gross_return"] = df["approx_prob"] * df["odds"]
        df["net_ev"] = df["gross_return"] - 1.0
        df["ev"] = df["net_ev"]
        df["value_band"] = "SKIP"
        df.loc[df["ev"] >= 0.05, "value_band"] = "WATCH"
        df.loc[df["ev"] >= self.buy_min_ev, "value_band"] = "BUY"
        df.loc[df["ev"] >= max(self.buy_min_ev, 0.30), "value_band"] = "STRONG_BUY"
        df["risk_flag"] = df["ev"] > self.risk_ev_threshold
        df["high_ev_suspect_flag"] = df["ev"] >= self.high_ev_watch_threshold
        df["feature_missing_count"] = pd.DataFrame(
            {
                col: pd.to_numeric(df[col], errors="coerce").isna().astype(int)
                for col in [
                    "approx_prob",
                    "odds",
                    "first_win_proba",
                    "first_place_score",
                    "second_place_score",
                    "third_place_score",
                ]
                if col in df.columns
            }
        ).sum(axis=1)
        df["data_completeness"] = (
            1.0
            - (
                df["feature_missing_count"]
                / float(max(1, len([c for c in ["approx_prob", "odds", "first_win_proba", "first_place_score", "second_place_score", "third_place_score"] if c in df.columns])))
            )
        ).clip(lower=0.0, upper=1.0)
        df["odds_stale_age_days"] = pd.to_numeric(df.get("odds_last_fetched_at"), errors="coerce")
        if "odds_last_fetched_at" in df.columns:
            parsed_last = pd.to_datetime(df["odds_last_fetched_at"], errors="coerce", utc=True)
            now_utc = pd.Timestamp.utcnow()
            df["odds_stale_age_days"] = (now_utc - parsed_last).dt.total_seconds().div(86400.0)
        df["risk_codes"] = pd.Series([tuple(self._build_risk_codes(row)) for _, row in df.iterrows()], index=df.index)
        df["risk_penalty"] = pd.Series([self._risk_penalty(list(codes)) for codes in df["risk_codes"]], index=df.index)
        df = add_probability_calibration_features(df)
        calibration_source = self.calibration_source_col if self.calibration_source_col in df.columns else "approx_prob"
        df["calibrated_hit_prob"] = self._calibrate_probability_series(df, calibration_source)
        df["calibrated_hit_prob"] = pd.to_numeric(df["calibrated_hit_prob"], errors="coerce")
        approx_prob_series = (
            pd.to_numeric(df["approx_prob"], errors="coerce")
            if "approx_prob" in df.columns
            else pd.Series(0.0, index=df.index, dtype=float)
        )
        df["calibrated_hit_prob"] = df["calibrated_hit_prob"].fillna(
            approx_prob_series.fillna(0.0) * self.calibration_fallback_scale
        )
        df["calibration_method"] = self.calibration_method
        df["calibration_source_col"] = calibration_source
        df["risk_codes"] = df["risk_codes"].apply(lambda codes: "|".join(codes) if isinstance(codes, (list, tuple)) else str(codes))
        df["risk_penalty"] = pd.to_numeric(df["risk_penalty"], errors="coerce").fillna(0).astype(int)
        df["unified_score"] = pd.to_numeric(df["calibrated_hit_prob"], errors="coerce").fillna(0.0) * pd.to_numeric(
            df["odds"], errors="coerce"
        ).fillna(0.0)
        df["unified_score"] = pd.to_numeric(df["unified_score"], errors="coerce").fillna(0.0)
        df["adjusted_score"] = df["unified_score"] - pd.to_numeric(df["risk_penalty"], errors="coerce").fillna(0.0)
        race_max_ev = df.groupby("race_id")["ev"].transform("max").clip(lower=1e-9)
        if self.use_unified_score:
            df["sort_score"] = df["adjusted_score"] + EV_WEIGHT * (df["ev"] / race_max_ev)
        else:
            df["sort_score"] = df["approx_prob"] + EV_WEIGHT * (df["ev"] / race_max_ev)
        return df.sort_values(["race_id", "sort_score"], ascending=[True, False]).reset_index(drop=True)

    def build_skip_decisions(
        self,
        ev_df,
        race_boat_counts=None,
        race_card_path=None,
        ignore_day_mode: bool = False,
        ignore_daily_candidate_limit: bool = False,
        ignore_race_candidate_limit: bool = False,
        ignore_hard_guards: bool = False,
        ignore_priority_gates: bool = False,
    ):
        race_boat_counts = race_boat_counts or {}
        filter_mode = self.strategy_mode
        if filter_mode == "AUTO_FILTER":
            roi_rules = self.auto_filter_rules or {}
        else:
            roi_rules = self.roi_filter_rules or {}
        allowed_prob_bins = {str(v) for v in roi_rules.get("allowed_prob_bins", []) if str(v)}
        allowed_odds_bins = {str(v) for v in roi_rules.get("allowed_odds_bins", []) if str(v)}
        allowed_places = {str(v) for v in roi_rules.get("allowed_places", []) if str(v)}
        roi_prob_metric = str(roi_rules.get("prob_metric") or self.roi_filter_prob_metric)
        auto_filter_fallback = filter_mode == "AUTO_FILTER" and not self._auto_filter_rules_active(roi_rules)
        effective_mode = "NORMAL" if auto_filter_fallback else filter_mode
        if filter_mode == "AUTO_FILTER":
            prob_step = float(self.auto_filter_config.get("prob_bin_step", 0.05) or 0.05)
            prob_step = prob_step if prob_step > 0 else 0.05
            prob_edges = [round(i * prob_step, 2) for i in range(int(round(1.0 / prob_step)) + 1)]
            if prob_edges[-1] < 1.0:
                prob_edges.append(1.0)
        else:
            prob_edges = [float(v) for v in roi_rules.get("prob_bin_edges", [round(x / 10, 1) for x in range(0, 11)])]
        odds_edges = [float(v) for v in roi_rules.get("odds_bin_edges", [0, 20, 50, 100, 200, 500, 1000, 999999])]
        prob_digits = self._bin_digits(prob_edges, fallback=1)
        decisions = []
        day_mode, day_mode_reasons, day_mode_metrics = self._compute_day_mode_context(ev_df, race_card_path=race_card_path)
        applied_day_mode = "normal" if ignore_day_mode else day_mode
        day_mode_buy_cap = self.max_buy_count
        if ignore_daily_candidate_limit:
            day_mode_buy_cap = None
        elif day_mode == "stop":
            day_mode_buy_cap = 0
        elif day_mode == "reduced":
            reduced_cap = self.day_mode_rules.get("reduced", {}).get("max_candidates_per_day", day_mode_buy_cap)
            if day_mode_buy_cap is None:
                day_mode_buy_cap = reduced_cap
            elif reduced_cap is not None:
                try:
                    day_mode_buy_cap = min(int(day_mode_buy_cap), int(reduced_cap))
                except Exception:
                    day_mode_buy_cap = reduced_cap
        venue_gate_active = True
        if filter_mode == "AUTO_FILTER":
            current_venues = {
                self._venue_name_from_race_id(rid)
                for rid in ev_df.get("race_id", pd.Series(dtype=object)).dropna().astype(str).unique()
            }
            current_venues = {v for v in current_venues if v}
            venue_gate_active = bool(allowed_places & current_venues) if allowed_places else False

        for race_id, group in ev_df.groupby("race_id"):
            sort_col = "sort_score" if "sort_score" in group.columns else "ev"
            group = group.sort_values(sort_col, ascending=False).reset_index(drop=True)
            if (self.first_place_enabled or self.place_role_enabled) and not self.pre_race_features.empty:
                race_feat = self._get_race_features(race_id)
                if not race_feat.empty:
                    profile_frames: list[pd.DataFrame] = []
                    if self.first_place_enabled and "first_place_score" not in group.columns:
                        first_place_profiles = group.apply(
                            lambda row: pd.Series(self._compute_first_place_profile(row, race_feat)),
                            axis=1,
                        )
                        profile_frames.append(first_place_profiles.reset_index(drop=True))
                    if self.place_role_enabled and "second_place_score" not in group.columns and "third_place_score" not in group.columns:
                        second_place_profiles = group.apply(
                            lambda row: pd.Series(self._compute_place_role_profile(row, race_feat, "second")),
                            axis=1,
                        )
                        third_place_profiles = group.apply(
                            lambda row: pd.Series(self._compute_place_role_profile(row, race_feat, "third")),
                            axis=1,
                        )
                        profile_frames.extend(
                            [
                                second_place_profiles.reset_index(drop=True),
                                third_place_profiles.reset_index(drop=True),
                            ]
                        )
                    if profile_frames:
                        group = pd.concat([group.reset_index(drop=True), *profile_frames], axis=1)
                        for col in [
                            "first_place_score",
                            "second_place_score",
                            "third_place_score",
                        ]:
                            if col in group.columns:
                                group[col] = pd.to_numeric(group[col], errors="coerce").fillna(0.0)
                        group["sort_score"] = pd.to_numeric(group["sort_score"], errors="coerce").fillna(0.0) if "sort_score" in group.columns else 0.0
                        if self.first_place_enabled and "first_place_score" in group.columns:
                            group["sort_score"] = group["sort_score"] + (group["first_place_score"] * self.first_place_sort_weight)
                        if self.place_role_enabled and "second_place_score" in group.columns:
                            group["sort_score"] = group["sort_score"] + (group["second_place_score"] * self.second_place_sort_weight)
                        if self.place_role_enabled and "third_place_score" in group.columns:
                            group["sort_score"] = group["sort_score"] + (group["third_place_score"] * self.third_place_sort_weight)
                        group = group.sort_values("sort_score", ascending=False).reset_index(drop=True)
            actual_boats = race_boat_counts.get(str(race_id))
            if actual_boats is None:
                unique_lanes = set()
                for trifecta in group["trifecta"]:
                    unique_lanes.update(str(trifecta).split("-"))
                actual_boats = len(unique_lanes)
            race_feat = (
                self._get_race_features(race_id)
                if not self.pre_race_features.empty
                else self.pre_race_features
            )
            top_row = group.iloc[0]
            selected_row = top_row
            selected_rank = int(pd.to_numeric(top_row.get("candidate_rank_by_sort", 1), errors="coerce") or 1)
            rank_rescue_applied = False
            rank_rescue_reason = ""
            near_cap_rescue_applied = False
            near_cap_rescue_reason = ""
            near_cap_odds_gap = np.nan
            payout_outlier_rescue_applied = False
            payout_outlier_rescue_reason = ""
            payout_outlier_ev_delta = np.nan

            row_ev = float(pd.to_numeric(top_row.get("ev"), errors="coerce") or 0.0)
            row_unified_score = float(pd.to_numeric(top_row.get("unified_score"), errors="coerce") or 0.0)
            row_adjusted_score = float(pd.to_numeric(top_row.get("adjusted_score"), errors="coerce") or 0.0)
            row_prob = float(pd.to_numeric(top_row.get("approx_prob"), errors="coerce") or 0.0)
            calibrated_hit_prob = float(
                pd.to_numeric(top_row.get("calibrated_hit_prob", row_prob * 0.7), errors="coerce") or (row_prob * 0.7)
            )
            high_ev_suspect_flag = bool(top_row.get("high_ev_suspect_flag", False))
            first_place_prob = float(
                pd.to_numeric(top_row.get("first_place_prob", top_row.get("first_win_proba")), errors="coerce")
                or float(pd.to_numeric(top_row.get("first_win_proba"), errors="coerce") or 0.0)
            )
            row_odds = float(pd.to_numeric(top_row.get("odds"), errors="coerce") or 0.0)
            row_first_win = float(pd.to_numeric(top_row.get("first_win_proba"), errors="coerce") or 0.0)
            row_odds_source = self._normalize_odds_source(str(top_row.get("odds_source", "")))
            has_real_odds = row_odds_source == "real"
            odds_fetch_status = self._normalize_odds_fetch_status(top_row.get("odds_fetch_status", ""))
            odds_last_fetched_at = str(top_row.get("odds_last_fetched_at", "") or "")
            calibration_method = str(top_row.get("calibration_method", self.calibration_method) or self.calibration_method)
            calibration_source_col = str(
                top_row.get("calibration_source_col", self.calibration_source_col) or self.calibration_source_col
            )
            venue_name = self._venue_name_from_race_id(race_id)
            pre_race_profile = self._compute_pre_race_profile(top_row, race_feat)
            pre_race_score = float(pre_race_profile.get("pre_race_score", 0.0) or 0.0)
            pre_race_block = bool(pre_race_profile.get("pre_race_block", False))
            pre_race_priority = bool(pre_race_profile.get("pre_race_priority", False))
            pre_race_multiplier = float(pre_race_profile.get("pre_race_multiplier", 1.0) or 1.0)
            first_place_score = float(pd.to_numeric(top_row.get("first_place_score", 0.0), errors="coerce") or 0.0)
            first_place_gate = str(top_row.get("first_place_gate", "MISSING") or "MISSING")
            first_place_block = bool(top_row.get("first_place_block", False) or first_place_score < self.first_place_block_threshold)
            first_place_priority = bool(top_row.get("first_place_priority", False) or first_place_score >= self.first_place_priority_threshold)
            first_place_multiplier = float(top_row.get("first_place_multiplier", 1.0) or 1.0)
            first_place_note = str(top_row.get("first_place_note", "") or "")
            second_place_score = float(pd.to_numeric(top_row.get("second_place_score", 0.0), errors="coerce") or 0.0)
            second_place_gate = str(top_row.get("second_place_gate", "MISSING") or "MISSING")
            second_place_block = bool(top_row.get("second_place_block", False) or second_place_score < self.second_place_block_threshold)
            second_place_priority = bool(top_row.get("second_place_priority", False) or second_place_score >= self.second_place_priority_threshold)
            second_place_multiplier = float(top_row.get("second_place_multiplier", 1.0) or 1.0)
            second_place_note = str(top_row.get("second_place_note", "") or "")
            third_place_score = float(pd.to_numeric(top_row.get("third_place_score", 0.0), errors="coerce") or 0.0)
            third_place_gate = str(top_row.get("third_place_gate", "MISSING") or "MISSING")
            third_place_block = bool(top_row.get("third_place_block", False) or third_place_score < self.third_place_block_threshold)
            third_place_priority = bool(top_row.get("third_place_priority", False) or third_place_score >= self.third_place_priority_threshold)
            third_place_multiplier = float(top_row.get("third_place_multiplier", 1.0) or 1.0)
            third_place_note = str(top_row.get("third_place_note", "") or "")
            race_selection_profile = self._compute_race_selection_profile(top_row, race_feat, first_place_score, pre_race_score, has_real_odds)
            race_score = float(race_selection_profile.get("race_score", 0.0) or 0.0)
            race_first_confidence = float(race_selection_profile.get("race_first_confidence", 0.0) or 0.0)
            race_odds_balance_score = float(race_selection_profile.get("race_odds_balance_score", 0.0) or 0.0)
            race_data_quality_score = float(race_selection_profile.get("race_data_quality_score", 0.0) or 0.0)
            race_gate = str(race_selection_profile.get("race_gate", "MISSING") or "MISSING")
            race_block = bool(race_selection_profile.get("race_block", False))
            race_watch = bool(race_selection_profile.get("race_watch", False))
            race_priority = bool(race_selection_profile.get("race_priority", False))
            race_note = str(race_selection_profile.get("race_note", "") or "")
            adjusted_calibrated_hit_prob = min(1.0, calibrated_hit_prob * pre_race_multiplier)
            adjusted_calibrated_hit_prob = min(1.0, adjusted_calibrated_hit_prob * first_place_multiplier)
            adjusted_calibrated_hit_prob = min(1.0, adjusted_calibrated_hit_prob * second_place_multiplier)
            adjusted_calibrated_hit_prob = min(1.0, adjusted_calibrated_hit_prob * third_place_multiplier)
            buy_prob_metric = "calibrated_hit_prob_adjusted" if self.use_unified_score else "approx_prob"
            buy_prob_value = float(adjusted_calibrated_hit_prob if self.use_unified_score else row_prob)
            roi_prob_value = float(
                pd.to_numeric(
                    top_row.get(roi_prob_metric, top_row.get("first_place_prob", top_row.get("approx_prob"))),
                    errors="coerce",
                )
                or 0.0
            )
            prob_bin = self._bin_label(roi_prob_value, prob_edges, digits=prob_digits)
            odds_bin = self._bin_label(row_odds, odds_edges, digits=0)
            venue_allowed = venue_name in allowed_places if allowed_places else True
            prob_allowed = self._matches_allowed_bin(roi_prob_value, allowed_prob_bins, prob_edges, digits=prob_digits)
            odds_allowed = self._matches_allowed_bin(row_odds, allowed_odds_bins, odds_edges, digits=0)
            roi_filter_match = (
                prob_allowed
                and odds_allowed
                and venue_allowed
            )
            roi_filter_near_match = (
                prob_allowed
                or odds_allowed
                or venue_allowed
            )

            risk_codes = self._build_risk_codes(top_row)
            risk_penalty = self._risk_penalty(risk_codes)
            risk_labels = self._translate_risk_labels(risk_codes)
            decision_score = self._decision_score(top_row, risk_penalty, has_real_odds)
            confidence_score = max(
                0.0,
                min(1.0, row_prob - (risk_penalty * 0.03) + (0.02 if has_real_odds else 0.0)),
            )

            notes: list[str] = []
            decision_reasons: list[str] = []
            hard_skip = False
            rescue_applied = False
            if self.skip_config.get("exclude_non_6_boats", False):
                if int(actual_boats) < 6:
                    hard_skip = True
                    notes.append(f"6艇未満のため見送り(actual_boats={int(actual_boats)})")

            if pd.isna(top_row.get("ev")) or pd.isna(top_row.get("approx_prob")) or pd.isna(top_row.get("first_win_proba")):
                hard_skip = True
                notes.append("重要指標の欠損")

            if not has_real_odds:
                notes.append("実オッズ未取得")
            if pre_race_profile.get("pre_race_gate") == "BLOCK":
                notes.append("直前スコア<=-1のためBUY禁止")
            elif pre_race_profile.get("pre_race_gate") == "PRIORITY":
                notes.append("直前優先候補")
            elif pre_race_profile.get("pre_race_gate") == "BOOST":
                notes.append("直前スコア>=1で校正確率を1.1倍")
            else:
                notes.append(f"直前スコア {pre_race_score:+.2f}")
            if pre_race_profile.get("pre_race_note"):
                notes.append(str(pre_race_profile.get("pre_race_note")))
            if first_place_gate == "BLOCK":
                notes.append("1着スコア<1のためBUY禁止")
            elif first_place_gate == "MISSING":
                notes.append("1着情報欠損のためBUY禁止")
            elif first_place_priority:
                notes.append("1着優先候補")
            elif first_place_gate == "BOOST":
                notes.append("1着スコア>=1で校正確率を1.05倍")
            if first_place_note:
                notes.append(first_place_note)
            if second_place_gate == "BLOCK":
                notes.append("2着スコア不足でBUY禁止")
            elif second_place_priority:
                notes.append("2着優先候補")
            elif second_place_gate == "BOOST":
                notes.append("2着スコア>=1で校正確率を1.03倍")
            if second_place_note:
                notes.append(second_place_note)
            if third_place_gate == "BLOCK":
                notes.append("3着スコア不足でBUY禁止")
            elif third_place_priority:
                notes.append("3着優先候補")
            elif third_place_gate == "BOOST":
                notes.append("3着スコア>=1で校正確率を1.03倍")
            if third_place_note:
                notes.append(third_place_note)
            if race_gate == "BLOCK":
                notes.append("レーススコア不足でBUY禁止")
            elif race_priority:
                notes.append("レース優先候補")
            elif race_watch:
                notes.append("レースWATCH候補")
            if race_note:
                notes.append(race_note)
            if auto_filter_fallback:
                notes.append("AUTO_FILTER条件が未生成のためNORMALへフォールバック")
            if effective_mode not in {"AUTO_FILTER", "ROI_FILTER"} and row_odds >= self.watch_max_odds:
                hard_skip = True
                notes.append(f"高配当帯で見送り (odds={row_odds:.1f} > {self.watch_max_odds:.1f})")
            if effective_mode not in {"AUTO_FILTER", "ROI_FILTER"} and self.max_odds_for_buy is not None and row_odds > float(self.max_odds_for_buy):
                notes.append(f"BUYオッズ上限超過 ({row_odds:.1f} > {float(self.max_odds_for_buy):.1f})")
            if self.max_ev_for_buy is not None and row_ev > float(self.max_ev_for_buy):
                notes.append(f"BUY EV上限超過 ({row_ev:.2f} > {float(self.max_ev_for_buy):.2f})")
            if (
                self.max_first_win_proba_for_buy is not None
                and row_first_win > float(self.max_first_win_proba_for_buy)
            ):
                notes.append(
                    "1着候補確率の上限超過 "
                    f"({row_first_win:.3f} > {float(self.max_first_win_proba_for_buy):.3f})"
                )

            if ignore_priority_gates:
                pre_race_priority = True
                first_place_priority = True
                second_place_priority = True
                third_place_priority = True
                race_priority = True

            if effective_mode == "WINRATE":
                buy_eligible = (
                    (not hard_skip)
                    and (not pre_race_block)
                    and (not first_place_block)
                    and first_place_gate != "MISSING"
                    and (not second_place_block)
                    and (not third_place_block)
                    and (not race_block)
                    and race_priority
                    and has_real_odds
                    and row_odds <= 50.0
                    and adjusted_calibrated_hit_prob >= 0.18
                    and first_place_prob >= 0.45
                    and risk_penalty == 0
                )
                watch_eligible = (
                    (not hard_skip)
                    and (not race_block)
                    and has_real_odds
                    and row_odds <= 100.0
                    and calibrated_hit_prob >= 0.12
                    and risk_penalty <= 1
                    and (race_watch or race_priority)
                )
                if buy_eligible:
                    decision = "BUY"
                elif watch_eligible:
                    decision = "WATCH"
                else:
                    decision = "SKIP"
                if not has_real_odds:
                    notes.append("実オッズがないため WINRATE は SKIP")
                if decision == "BUY" and not buy_eligible:
                    notes.append("WINRATE BUY基準未達")
                if decision == "WATCH" and not watch_eligible:
                    notes.append("WINRATE WATCH基準未達")
            elif effective_mode == "ROI_FILTER":
                buy_eligible = (
                    (not hard_skip)
                    and (not pre_race_block)
                    and (not first_place_block)
                    and first_place_gate != "MISSING"
                    and (not second_place_block)
                    and (not third_place_block)
                    and (not race_block)
                    and race_priority
                    and has_real_odds
                    and roi_filter_match
                )
                watch_eligible = (not hard_skip) and (not race_block) and has_real_odds and roi_filter_near_match and not roi_filter_match and (race_watch or race_priority)
                if buy_eligible:
                    decision = "BUY"
                elif watch_eligible:
                    decision = "WATCH"
                else:
                    decision = "SKIP"
                if not has_real_odds:
                    notes.append("実オッズがないため ROI_FILTER は SKIP")
                if not allowed_prob_bins and not allowed_odds_bins and not allowed_places:
                    notes.append("ROI_FILTER条件が未生成")
                else:
                    notes.append(f"ROI_FILTER prob={prob_bin} / odds={odds_bin} / venue={venue_name or '不明'}")
                if decision == "BUY" and not roi_filter_match:
                    notes.append("ROI_FILTER BUY条件未達")
                if decision == "WATCH" and not roi_filter_near_match:
                    notes.append("ROI_FILTER WATCH条件未達")
            elif effective_mode == "AUTO_FILTER":
                buy_eligible = (
                    (not hard_skip)
                    and (not pre_race_block)
                    and (not first_place_block)
                    and first_place_gate != "MISSING"
                    and (not second_place_block)
                    and (not third_place_block)
                    and (not race_block)
                    and race_priority
                    and has_real_odds
                    and roi_filter_match
                )
                watch_eligible = (not hard_skip) and (not race_block) and has_real_odds and roi_filter_near_match and not roi_filter_match and (race_watch or race_priority)
                if buy_eligible:
                    decision = "BUY"
                elif watch_eligible:
                    decision = "WATCH"
                else:
                    decision = "SKIP"
                if not has_real_odds:
                    notes.append("実オッズがないため AUTO_FILTER は SKIP")
                if not allowed_prob_bins and not allowed_odds_bins and not allowed_places:
                    notes.append("AUTO_FILTER条件が未生成")
                else:
                    notes.append(f"AUTO_FILTER prob={prob_bin} / odds={odds_bin} / venue={venue_name or '不明'}")
                    if filter_mode == "AUTO_FILTER" and allowed_places and not venue_gate_active:
                        notes.append("場条件が現場に一致しないため厳格適用")
                if decision == "BUY" and not roi_filter_match:
                    notes.append("AUTO_FILTER BUY条件未達")
                if decision == "WATCH" and not roi_filter_near_match:
                    notes.append("AUTO_FILTER WATCH条件未達")
            else:
                buy_eligible = (
                    (not hard_skip)
                    and (not pre_race_block)
                    and (not first_place_block)
                    and first_place_gate != "MISSING"
                    and (not second_place_block)
                    and (not third_place_block)
                    and (not race_block)
                    and race_priority
                    and has_real_odds
                    and row_ev >= self.buy_min_ev
                    and buy_prob_value >= self.buy_min_approx_prob
                    and risk_penalty <= self.buy_max_risk_penalty
                    and (self.max_odds_for_buy is None or row_odds <= float(self.max_odds_for_buy))
                    and (not self.exclude_risk_flag_for_buy or risk_penalty == 0)
                )

                if self.rescue_enabled and (
                    (not buy_eligible)
                    and self.rank_rescue_top_n > 0
                    and (not hard_skip)
                    and int(actual_boats) >= 6
                ):
                    rescue_candidates = group.head(int(self.rank_rescue_top_n)).copy()
                    for _, candidate_row in rescue_candidates.iterrows():
                        candidate_rank = int(pd.to_numeric(candidate_row.get("candidate_rank_by_sort", 0), errors="coerce") or 0)
                        if candidate_rank <= 0:
                            continue
                        rescue_ok, rescue_meta = self._rank_rescue_candidate_ok(candidate_row, race_feat, hard_skip=hard_skip)
                        if not rescue_ok:
                            continue
                        selected_row = candidate_row
                        selected_rank = candidate_rank
                        rank_rescue_applied = True
                        rank_rescue_reason = (
                            f"rank<= {int(self.rank_rescue_top_n)} calib救済 "
                            f"(rank={candidate_rank}, ev={float(rescue_meta['row_ev']):.3f}, "
                            f"cal={float(rescue_meta['calibrated_hit_prob']):.3f}, "
                            f"ev_credit={float(rescue_meta['rescue_ev_credit']):.3f}, "
                            f"local_min_ev={float(rescue_meta['rescue_min_ev']):.3f})"
                        )
                        top_row = selected_row
                        row_ev = float(pd.to_numeric(top_row.get("ev"), errors="coerce") or 0.0)
                        row_prob = float(pd.to_numeric(top_row.get("approx_prob"), errors="coerce") or 0.0)
                        calibrated_hit_prob = float(
                            pd.to_numeric(top_row.get("calibrated_hit_prob", row_prob * 0.7), errors="coerce") or (row_prob * 0.7)
                        )
                        first_place_prob = float(
                            pd.to_numeric(top_row.get("first_place_prob", top_row.get("first_win_proba")), errors="coerce")
                            or float(pd.to_numeric(top_row.get("first_win_proba"), errors="coerce") or 0.0)
                        )
                        row_odds = float(pd.to_numeric(top_row.get("odds"), errors="coerce") or 0.0)
                        row_first_win = float(pd.to_numeric(top_row.get("first_win_proba"), errors="coerce") or 0.0)
                        row_odds_source = self._normalize_odds_source(str(top_row.get("odds_source", "")))
                        has_real_odds = row_odds_source == "real"
                        calibration_method = str(top_row.get("calibration_method", self.calibration_method) or self.calibration_method)
                        calibration_source_col = str(
                            top_row.get("calibration_source_col", self.calibration_source_col) or self.calibration_source_col
                        )
                        pre_race_profile = self._compute_pre_race_profile(top_row, race_feat)
                        pre_race_score = float(pre_race_profile.get("pre_race_score", 0.0) or 0.0)
                        pre_race_block = bool(pre_race_profile.get("pre_race_block", False))
                        pre_race_priority = bool(pre_race_profile.get("pre_race_priority", False))
                        pre_race_multiplier = float(pre_race_profile.get("pre_race_multiplier", 1.0) or 1.0)
                        first_place_score = float(pd.to_numeric(top_row.get("first_place_score", 0.0), errors="coerce") or 0.0)
                        first_place_gate = str(top_row.get("first_place_gate", "MISSING") or "MISSING")
                        first_place_block = bool(top_row.get("first_place_block", False) or first_place_score < self.first_place_block_threshold)
                        first_place_priority = bool(top_row.get("first_place_priority", False) or first_place_score >= self.first_place_priority_threshold)
                        first_place_multiplier = float(top_row.get("first_place_multiplier", 1.0) or 1.0)
                        first_place_note = str(top_row.get("first_place_note", "") or "")
                        second_place_score = float(pd.to_numeric(top_row.get("second_place_score", 0.0), errors="coerce") or 0.0)
                        second_place_gate = str(top_row.get("second_place_gate", "MISSING") or "MISSING")
                        second_place_block = bool(top_row.get("second_place_block", False) or second_place_score < self.second_place_block_threshold)
                        second_place_priority = bool(top_row.get("second_place_priority", False) or second_place_score >= self.second_place_priority_threshold)
                        second_place_multiplier = float(top_row.get("second_place_multiplier", 1.0) or 1.0)
                        second_place_note = str(top_row.get("second_place_note", "") or "")
                        third_place_score = float(pd.to_numeric(top_row.get("third_place_score", 0.0), errors="coerce") or 0.0)
                        third_place_gate = str(top_row.get("third_place_gate", "MISSING") or "MISSING")
                        third_place_block = bool(top_row.get("third_place_block", False) or third_place_score < self.third_place_block_threshold)
                        third_place_priority = bool(top_row.get("third_place_priority", False) or third_place_score >= self.third_place_priority_threshold)
                        third_place_multiplier = float(top_row.get("third_place_multiplier", 1.0) or 1.0)
                        third_place_note = str(top_row.get("third_place_note", "") or "")
                        race_selection_profile = self._compute_race_selection_profile(top_row, race_feat, first_place_score, pre_race_score, has_real_odds)
                        race_score = float(race_selection_profile.get("race_score", 0.0) or 0.0)
                        race_first_confidence = float(race_selection_profile.get("race_first_confidence", 0.0) or 0.0)
                        race_odds_balance_score = float(race_selection_profile.get("race_odds_balance_score", 0.0) or 0.0)
                        race_data_quality_score = float(race_selection_profile.get("race_data_quality_score", 0.0) or 0.0)
                        race_gate = str(race_selection_profile.get("race_gate", "MISSING") or "MISSING")
                        race_block = bool(race_selection_profile.get("race_block", False))
                        race_watch = bool(race_selection_profile.get("race_watch", False))
                        race_priority = bool(race_selection_profile.get("race_priority", False))
                        race_note = str(race_selection_profile.get("race_note", "") or "")
                        if ignore_priority_gates:
                            pre_race_priority = True
                            first_place_priority = True
                            second_place_priority = True
                            third_place_priority = True
                            race_priority = True
                        adjusted_calibrated_hit_prob = min(1.0, calibrated_hit_prob * pre_race_multiplier)
                        adjusted_calibrated_hit_prob = min(1.0, adjusted_calibrated_hit_prob * first_place_multiplier)
                        adjusted_calibrated_hit_prob = min(1.0, adjusted_calibrated_hit_prob * second_place_multiplier)
                        adjusted_calibrated_hit_prob = min(1.0, adjusted_calibrated_hit_prob * third_place_multiplier)
                        roi_prob_value = float(
                            pd.to_numeric(
                                top_row.get(roi_prob_metric, top_row.get("first_place_prob", top_row.get("approx_prob"))),
                                errors="coerce",
                            )
                            or 0.0
                        )
                        prob_bin = self._bin_label(roi_prob_value, prob_edges, digits=prob_digits)
                        odds_bin = self._bin_label(row_odds, odds_edges, digits=0)
                        risk_codes = self._build_risk_codes(top_row)
                        risk_penalty = self._risk_penalty(risk_codes)
                        risk_labels = self._translate_risk_labels(risk_codes)
                        decision_score = self._decision_score(top_row, risk_penalty, has_real_odds)
                        confidence_score = max(
                            0.0,
                            min(1.0, row_prob - (risk_penalty * 0.03) + (0.02 if has_real_odds else 0.0)),
                        )
                        threshold_snapshot = self._build_threshold_snapshot(
                            day_mode=day_mode,
                            row_ev=row_ev,
                            row_prob=row_prob,
                            calibrated_hit_prob=calibrated_hit_prob,
                            row_odds=row_odds,
                            risk_penalty=risk_penalty,
                            has_real_odds=has_real_odds,
                            high_ev_suspect_flag=high_ev_suspect_flag,
                        )
                        buy_eligible = True
                        rescue_applied = True
                        notes.append(rank_rescue_reason)
                        break

                if self.rescue_enabled and (
                    (not buy_eligible)
                    and (not rank_rescue_applied)
                    and self.near_cap_rescue_enabled
                    and self.max_odds_for_buy is not None
                    and self.near_cap_rescue_window > 0.0
                    and (not hard_skip)
                    and int(actual_boats) >= 6
                ):
                    rescue_candidates = group.head(int(max(1, self.near_cap_rescue_top_n))).copy()
                    for _, candidate_row in rescue_candidates.iterrows():
                        candidate_rank = int(pd.to_numeric(candidate_row.get("candidate_rank_by_sort", 0), errors="coerce") or 0)
                        if candidate_rank <= 0:
                            continue
                        rescue_ok, rescue_meta = self._near_cap_rescue_candidate_ok(candidate_row, race_feat, hard_skip=hard_skip)
                        if not rescue_ok:
                            continue
                        selected_row = candidate_row
                        selected_rank = candidate_rank
                        near_cap_rescue_applied = True
                        near_cap_odds_gap = float(rescue_meta.get("near_cap_odds_gap", np.nan))
                        near_cap_rescue_reason = (
                            f"near-cap救済 "
                            f"(rank={candidate_rank}, odds={float(rescue_meta['row_odds']):.1f}, "
                            f"cap_gap={float(rescue_meta['near_cap_odds_gap']):.1f}, "
                            f"cal={float(rescue_meta['calibrated_hit_prob']):.3f}, "
                            f"final_score={float(rescue_meta['buy_final_score']):.3f}, "
                            f"window={float(self.near_cap_rescue_window):.1f})"
                        )
                        top_row = selected_row
                        row_ev = float(pd.to_numeric(top_row.get("ev"), errors="coerce") or 0.0)
                        row_prob = float(pd.to_numeric(top_row.get("approx_prob"), errors="coerce") or 0.0)
                        calibrated_hit_prob = float(
                            pd.to_numeric(top_row.get("calibrated_hit_prob", row_prob * 0.7), errors="coerce") or (row_prob * 0.7)
                        )
                        first_place_prob = float(
                            pd.to_numeric(top_row.get("first_place_prob", top_row.get("first_win_proba")), errors="coerce")
                            or float(pd.to_numeric(top_row.get("first_win_proba"), errors="coerce") or 0.0)
                        )
                        row_odds = float(pd.to_numeric(top_row.get("odds"), errors="coerce") or 0.0)
                        row_first_win = float(pd.to_numeric(top_row.get("first_win_proba"), errors="coerce") or 0.0)
                        row_odds_source = self._normalize_odds_source(str(top_row.get("odds_source", "")))
                        has_real_odds = row_odds_source == "real"
                        calibration_method = str(top_row.get("calibration_method", self.calibration_method) or self.calibration_method)
                        calibration_source_col = str(
                            top_row.get("calibration_source_col", self.calibration_source_col) or self.calibration_source_col
                        )
                        pre_race_profile = self._compute_pre_race_profile(top_row, race_feat)
                        pre_race_score = float(pre_race_profile.get("pre_race_score", 0.0) or 0.0)
                        pre_race_block = bool(pre_race_profile.get("pre_race_block", False))
                        pre_race_priority = bool(pre_race_profile.get("pre_race_priority", False))
                        pre_race_multiplier = float(pre_race_profile.get("pre_race_multiplier", 1.0) or 1.0)
                        first_place_score = float(pd.to_numeric(top_row.get("first_place_score", 0.0), errors="coerce") or 0.0)
                        first_place_gate = str(top_row.get("first_place_gate", "MISSING") or "MISSING")
                        first_place_block = bool(top_row.get("first_place_block", False) or first_place_score < self.first_place_block_threshold)
                        first_place_priority = bool(top_row.get("first_place_priority", False) or first_place_score >= self.first_place_priority_threshold)
                        first_place_multiplier = float(top_row.get("first_place_multiplier", 1.0) or 1.0)
                        first_place_note = str(top_row.get("first_place_note", "") or "")
                        second_place_score = float(pd.to_numeric(top_row.get("second_place_score", 0.0), errors="coerce") or 0.0)
                        second_place_gate = str(top_row.get("second_place_gate", "MISSING") or "MISSING")
                        second_place_block = bool(top_row.get("second_place_block", False) or second_place_score < self.second_place_block_threshold)
                        second_place_priority = bool(top_row.get("second_place_priority", False) or second_place_score >= self.second_place_priority_threshold)
                        second_place_multiplier = float(top_row.get("second_place_multiplier", 1.0) or 1.0)
                        second_place_note = str(top_row.get("second_place_note", "") or "")
                        third_place_score = float(pd.to_numeric(top_row.get("third_place_score", 0.0), errors="coerce") or 0.0)
                        third_place_gate = str(top_row.get("third_place_gate", "MISSING") or "MISSING")
                        third_place_block = bool(top_row.get("third_place_block", False) or third_place_score < self.third_place_block_threshold)
                        third_place_priority = bool(top_row.get("third_place_priority", False) or third_place_score >= self.third_place_priority_threshold)
                        third_place_multiplier = float(top_row.get("third_place_multiplier", 1.0) or 1.0)
                        third_place_note = str(top_row.get("third_place_note", "") or "")
                        race_selection_profile = self._compute_race_selection_profile(top_row, race_feat, first_place_score, pre_race_score, has_real_odds)
                        race_score = float(race_selection_profile.get("race_score", 0.0) or 0.0)
                        race_first_confidence = float(race_selection_profile.get("race_first_confidence", 0.0) or 0.0)
                        race_odds_balance_score = float(race_selection_profile.get("race_odds_balance_score", 0.0) or 0.0)
                        race_data_quality_score = float(race_selection_profile.get("race_data_quality_score", 0.0) or 0.0)
                        race_gate = str(race_selection_profile.get("race_gate", "MISSING") or "MISSING")
                        race_block = bool(race_selection_profile.get("race_block", False))
                        race_watch = bool(race_selection_profile.get("race_watch", False))
                        race_priority = bool(race_selection_profile.get("race_priority", False))
                        race_note = str(race_selection_profile.get("race_note", "") or "")
                        if ignore_priority_gates:
                            pre_race_priority = True
                            first_place_priority = True
                            second_place_priority = True
                            third_place_priority = True
                            race_priority = True
                        adjusted_calibrated_hit_prob = min(1.0, calibrated_hit_prob * pre_race_multiplier)
                        adjusted_calibrated_hit_prob = min(1.0, adjusted_calibrated_hit_prob * first_place_multiplier)
                        adjusted_calibrated_hit_prob = min(1.0, adjusted_calibrated_hit_prob * second_place_multiplier)
                        adjusted_calibrated_hit_prob = min(1.0, adjusted_calibrated_hit_prob * third_place_multiplier)
                        roi_prob_value = float(
                            pd.to_numeric(
                                top_row.get(roi_prob_metric, top_row.get("first_place_prob", top_row.get("approx_prob"))),
                                errors="coerce",
                            )
                            or 0.0
                        )
                        prob_bin = self._bin_label(roi_prob_value, prob_edges, digits=prob_digits)
                        odds_bin = self._bin_label(row_odds, odds_edges, digits=0)
                        risk_codes = self._build_risk_codes(top_row)
                        risk_penalty = self._risk_penalty(risk_codes)
                        risk_labels = self._translate_risk_labels(risk_codes)
                        decision_score = self._decision_score(top_row, risk_penalty, has_real_odds)
                        confidence_score = max(
                            0.0,
                            min(1.0, row_prob - (risk_penalty * 0.03) + (0.02 if has_real_odds else 0.0)),
                        )
                        buy_eligible = True
                        rescue_applied = True
                        notes.append(near_cap_rescue_reason)
                        break

                if self.rescue_enabled and (
                    (not buy_eligible)
                    and (not rank_rescue_applied)
                    and (not near_cap_rescue_applied)
                    and self.payout_outlier_rescue_enabled
                    and (not hard_skip)
                    and int(actual_boats) >= 6
                ):
                    rescue_candidates = group.head(int(max(1, self.payout_outlier_rescue_top_n))).copy()
                    for _, candidate_row in rescue_candidates.iterrows():
                        candidate_rank = int(pd.to_numeric(candidate_row.get("candidate_rank_by_sort", 0), errors="coerce") or 0)
                        if candidate_rank <= 0:
                            continue
                        rescue_ok, rescue_meta = self._payout_outlier_rescue_candidate_ok(candidate_row, race_feat, hard_skip=hard_skip)
                        if not rescue_ok:
                            continue
                        selected_row = candidate_row
                        selected_rank = candidate_rank
                        payout_outlier_rescue_applied = True
                        payout_outlier_ev_delta = float(rescue_meta.get("ev_delta", np.nan))
                        payout_outlier_rescue_reason = (
                            f"payout_outlier境界救済 "
                            f"(rank={candidate_rank}, odds={float(rescue_meta['row_odds']):.1f}, "
                            f"cal={float(rescue_meta['calibrated_hit_prob']):.3f}, "
                            f"final_score={float(rescue_meta['buy_final_score']):.3f}, "
                            f"ev_delta={float(rescue_meta['ev_delta']):.3f})"
                        )
                        top_row = selected_row
                        row_ev = float(pd.to_numeric(top_row.get("ev"), errors="coerce") or 0.0)
                        row_prob = float(pd.to_numeric(top_row.get("approx_prob"), errors="coerce") or 0.0)
                        calibrated_hit_prob = float(
                            pd.to_numeric(top_row.get("calibrated_hit_prob", row_prob * 0.7), errors="coerce") or (row_prob * 0.7)
                        )
                        first_place_prob = float(
                            pd.to_numeric(top_row.get("first_place_prob", top_row.get("first_win_proba")), errors="coerce")
                            or float(pd.to_numeric(top_row.get("first_win_proba"), errors="coerce") or 0.0)
                        )
                        row_odds = float(pd.to_numeric(top_row.get("odds"), errors="coerce") or 0.0)
                        row_first_win = float(pd.to_numeric(top_row.get("first_win_proba"), errors="coerce") or 0.0)
                        row_odds_source = self._normalize_odds_source(str(top_row.get("odds_source", "")))
                        has_real_odds = row_odds_source == "real"
                        calibration_method = str(top_row.get("calibration_method", self.calibration_method) or self.calibration_method)
                        calibration_source_col = str(
                            top_row.get("calibration_source_col", self.calibration_source_col) or self.calibration_source_col
                        )
                        pre_race_profile = self._compute_pre_race_profile(top_row, race_feat)
                        pre_race_score = float(pre_race_profile.get("pre_race_score", 0.0) or 0.0)
                        pre_race_block = bool(pre_race_profile.get("pre_race_block", False))
                        pre_race_priority = bool(pre_race_profile.get("pre_race_priority", False))
                        pre_race_multiplier = float(pre_race_profile.get("pre_race_multiplier", 1.0) or 1.0)
                        first_place_score = float(pd.to_numeric(top_row.get("first_place_score", 0.0), errors="coerce") or 0.0)
                        first_place_gate = str(top_row.get("first_place_gate", "MISSING") or "MISSING")
                        first_place_block = bool(top_row.get("first_place_block", False) or first_place_score < self.first_place_block_threshold)
                        first_place_priority = bool(top_row.get("first_place_priority", False) or first_place_score >= self.first_place_priority_threshold)
                        first_place_multiplier = float(top_row.get("first_place_multiplier", 1.0) or 1.0)
                        first_place_note = str(top_row.get("first_place_note", "") or "")
                        second_place_score = float(pd.to_numeric(top_row.get("second_place_score", 0.0), errors="coerce") or 0.0)
                        second_place_gate = str(top_row.get("second_place_gate", "MISSING") or "MISSING")
                        second_place_block = bool(top_row.get("second_place_block", False) or second_place_score < self.second_place_block_threshold)
                        second_place_priority = bool(top_row.get("second_place_priority", False) or second_place_score >= self.second_place_priority_threshold)
                        second_place_multiplier = float(top_row.get("second_place_multiplier", 1.0) or 1.0)
                        second_place_note = str(top_row.get("second_place_note", "") or "")
                        third_place_score = float(pd.to_numeric(top_row.get("third_place_score", 0.0), errors="coerce") or 0.0)
                        third_place_gate = str(top_row.get("third_place_gate", "MISSING") or "MISSING")
                        third_place_block = bool(top_row.get("third_place_block", False) or third_place_score < self.third_place_block_threshold)
                        third_place_priority = bool(top_row.get("third_place_priority", False) or third_place_score >= self.third_place_priority_threshold)
                        third_place_multiplier = float(top_row.get("third_place_multiplier", 1.0) or 1.0)
                        third_place_note = str(top_row.get("third_place_note", "") or "")
                        race_selection_profile = self._compute_race_selection_profile(top_row, race_feat, first_place_score, pre_race_score, has_real_odds)
                        race_score = float(race_selection_profile.get("race_score", 0.0) or 0.0)
                        race_first_confidence = float(race_selection_profile.get("race_first_confidence", 0.0) or 0.0)
                        race_odds_balance_score = float(race_selection_profile.get("race_odds_balance_score", 0.0) or 0.0)
                        race_data_quality_score = float(race_selection_profile.get("race_data_quality_score", 0.0) or 0.0)
                        race_gate = str(race_selection_profile.get("race_gate", "MISSING") or "MISSING")
                        race_block = bool(race_selection_profile.get("race_block", False))
                        race_watch = bool(race_selection_profile.get("race_watch", False))
                        race_priority = bool(race_selection_profile.get("race_priority", False))
                        race_note = str(race_selection_profile.get("race_note", "") or "")
                        adjusted_calibrated_hit_prob = min(1.0, calibrated_hit_prob * pre_race_multiplier)
                        adjusted_calibrated_hit_prob = min(1.0, adjusted_calibrated_hit_prob * first_place_multiplier)
                        adjusted_calibrated_hit_prob = min(1.0, adjusted_calibrated_hit_prob * second_place_multiplier)
                        adjusted_calibrated_hit_prob = min(1.0, adjusted_calibrated_hit_prob * third_place_multiplier)
                        roi_prob_value = float(
                            pd.to_numeric(
                                top_row.get(roi_prob_metric, top_row.get("first_place_prob", top_row.get("approx_prob"))),
                                errors="coerce",
                            )
                            or 0.0
                        )
                        prob_bin = self._bin_label(roi_prob_value, prob_edges, digits=prob_digits)
                        odds_bin = self._bin_label(row_odds, odds_edges, digits=0)
                        risk_codes = self._build_risk_codes(top_row)
                        risk_penalty = self._risk_penalty(risk_codes)
                        risk_labels = self._translate_risk_labels(risk_codes)
                        decision_score = self._decision_score(top_row, risk_penalty, has_real_odds)
                        confidence_score = max(
                            0.0,
                            min(1.0, row_prob - (risk_penalty * 0.03) + (0.02 if has_real_odds else 0.0)),
                        )
                        buy_eligible = True
                        rescue_applied = True
                        notes.append(payout_outlier_rescue_reason)
                        break

                watch_eligible = (
                    (not hard_skip)
                    and (not race_block)
                    and (
                        (
                            has_real_odds
                            and row_ev >= self.watch_min_ev_with_real_odds
                            and row_prob >= self.watch_min_approx_prob_with_real_odds
                        )
                        or (
                            not has_real_odds
                            and row_ev >= self.watch_min_ev_without_real_odds
                            and row_prob >= self.watch_min_approx_prob_without_real_odds
                        )
                    )
                    and risk_penalty <= self.watch_max_risk_penalty
                    and row_odds <= self.watch_max_odds
                    and (race_watch or race_priority)
                )
                if first_place_priority and (not hard_skip) and has_real_odds:
                    watch_eligible = True

                day_mode_buy_gate = True if ignore_day_mode else (day_mode != "stop")
                if not ignore_day_mode and day_mode == "reduced":
                    reduced_rules = self.day_mode_rules.get("reduced", {}) or {}
                    day_mode_min_win_proba = float(
                        self.buy_min_approx_prob + float(reduced_rules.get("min_win_proba_add", 0.0) or 0.0)
                    )
                    day_mode_min_ev = float(
                        self.buy_min_ev + float(reduced_rules.get("min_ev_add", 0.0) or 0.0)
                    )
                    day_mode_buy_gate = (
                        day_mode_buy_gate
                        and has_real_odds
                        and row_prob >= day_mode_min_win_proba
                        and row_ev >= day_mode_min_ev
                    )
                elif not ignore_day_mode and day_mode == "normal":
                    day_mode_buy_gate = day_mode_buy_gate and has_real_odds
                buy_eligible = bool(buy_eligible and day_mode_buy_gate)

                if buy_eligible:
                    decision = "BUY"
                elif watch_eligible:
                    decision = "WATCH"
                else:
                    decision = "SKIP"

                if not has_real_odds and decision == "BUY":
                    decision = "WATCH"
                    notes.append("実オッズがないため BUY→WATCH")
                if risk_penalty > self.buy_max_risk_penalty and decision == "BUY":
                    decision = "WATCH"
                    notes.append("リスク減点により BUY→WATCH")
                if first_place_block and decision == "BUY":
                    decision = "WATCH"
                    notes.append("1着スコア不足により BUY→WATCH")
                if (second_place_block or third_place_block) and decision == "BUY":
                    decision = "WATCH"
                    notes.append("2着/3着役割不足により BUY→WATCH")
                if race_block and decision == "BUY":
                    decision = "SKIP"
                    notes.append("レーススコア不足により BUY→SKIP")
                elif not race_priority and decision == "BUY":
                    decision = "WATCH"
                    notes.append("レーススコア不足により BUY→WATCH")
                if decision == "WATCH" and not watch_eligible:
                    notes.append("WATCH基準未達")
                if decision == "BUY" and not buy_eligible:
                    notes.append("BUY基準未達")
                if decision == "BUY" and high_ev_suspect_flag:
                    decision = "WATCH"
                    notes.append("高EV疑義のため BUY→WATCH")
                # NORMAL: 暫定オッズ（実オッズなし）は PENDING 運用
                if not has_real_odds and not hard_skip:
                    decision = "PENDING"
                    notes.append("実オッズ取得待ちのため PENDING")
                if ignore_day_mode and decision == "SKIP" and not hard_skip and has_real_odds and row_prob >= self.buy_min_approx_prob:
                    notes.append("ignore_day_mode で day_mode 抑制を解除")
                if decision == "SKIP" and not notes:
                    notes.append("条件未達")

            hard_guard_failures = self._evaluate_buy_hard_guard(
                row=top_row,
                row_ev=row_ev,
                confidence_score=confidence_score,
                has_real_odds=has_real_odds,
                odds_fetch_status=odds_fetch_status,
                odds_last_fetched_at=odds_last_fetched_at,
            )
            if hard_guard_failures and not ignore_hard_guards:
                hard_skip = True
                buy_eligible = False
                watch_eligible = False
                decision = "SKIP"
                for guard_code, guard_message in hard_guard_failures:
                    self._append_unique_note(notes, f"{guard_code}: {guard_message}")
            elif hard_guard_failures and ignore_hard_guards:
                self._append_unique_note(notes, "hard guards ignored for pure evaluation")

            row_unified_score = float(pd.to_numeric(top_row.get("unified_score"), errors="coerce") or 0.0)
            row_adjusted_score = float(pd.to_numeric(top_row.get("adjusted_score"), errors="coerce") or 0.0)
            high_ev_suspect_flag = bool(top_row.get("high_ev_suspect_flag", False))
            threshold_snapshot = self._build_threshold_snapshot(
                day_mode=day_mode,
                row_ev=row_ev,
                row_prob=row_prob,
                calibrated_hit_prob=calibrated_hit_prob,
                row_odds=row_odds,
                risk_penalty=risk_penalty,
                has_real_odds=has_real_odds,
                high_ev_suspect_flag=high_ev_suspect_flag,
            )
            decision_reasons = list(notes)

            odds_status = self._derive_odds_status(
                has_real_odds,
                decision,
                row_odds_source,
                odds_fetch_status=odds_fetch_status,
                odds_last_fetched_at=odds_last_fetched_at,
            )
            stop_reason = self._derive_stop_reason(
                decision=decision,
                has_real_odds=has_real_odds,
                odds_status=odds_status,
                odds_fetch_status=odds_fetch_status,
                hard_skip=hard_skip,
                buy_eligible=bool(buy_eligible),
                watch_eligible=bool(watch_eligible),
                race_block=bool(race_block),
                race_priority=bool(race_priority),
                risk_flag=bool(risk_penalty >= 2),
                first_place_gate=first_place_gate,
                pre_race_gate=str(pre_race_profile.get("pre_race_gate", "MISSING") or "MISSING"),
                notes=notes,
            )
            reason = self._human_reason(decision, top_row, has_real_odds, risk_labels, notes)
            kelly_fraction, bet_amount = self._kelly_bet_amount(top_row, decision, has_real_odds)

            decisions.append(
                {
                    "day_mode": day_mode,
                    "applied_day_mode": applied_day_mode,
                    "ignore_day_mode": bool(ignore_day_mode),
                    "ignore_daily_candidate_limit": bool(ignore_daily_candidate_limit),
                    "ignore_race_candidate_limit": bool(ignore_race_candidate_limit),
                    "day_mode_label": {"normal": "通常", "reduced": "縮小", "stop": "停止"}.get(day_mode, "未判定"),
                    "score_mode": "unified_score" if self.use_unified_score else "approx_prob",
                    "day_mode_reasons": " / ".join(day_mode_reasons),
                    "day_mode_real_odds_available_rate": day_mode_metrics.get("real_odds_available_rate"),
                    "day_mode_missing_feature_rate": day_mode_metrics.get("missing_feature_rate"),
                    "day_mode_today_races": day_mode_metrics.get("today_races"),
                    "day_mode_predicted_race_count": day_mode_metrics.get("predicted_race_count"),
                    "day_mode_race_coverage": day_mode_metrics.get("race_coverage"),
                    "day_mode_threshold": json.dumps(day_mode_metrics.get("threshold", {}), ensure_ascii=False),
                    "strategy_mode": self.strategy_mode,
                    "effective_strategy_mode": effective_mode,
                    "race_id": race_id,
                    "date": top_row["date"] if "date" in top_row else None,
                    "decision": decision,
                    "decision_score": round(decision_score, 4),
                    "decision_reasons": json.dumps(decision_reasons, ensure_ascii=False),
                    "threshold_snapshot": json.dumps(threshold_snapshot, ensure_ascii=False),
                    "buy_eligible": bool(buy_eligible),
                    "watch_eligible": bool(watch_eligible),
                    "recommended_trifecta": top_row["trifecta"],
                    "candidate_rank_by_sort": int(selected_rank),
                    "rank_rescue_applied": bool(rank_rescue_applied),
                    "rank_rescue_top_n": int(self.rank_rescue_top_n),
                    "rank_rescue_reason": rank_rescue_reason,
                    "near_cap_rescue_applied": bool(near_cap_rescue_applied),
                    "near_cap_rescue_window": float(self.near_cap_rescue_window),
                    "near_cap_rescue_reason": near_cap_rescue_reason,
                    "near_cap_odds_gap": near_cap_odds_gap,
                    "payout_outlier_rescue_applied": bool(payout_outlier_rescue_applied),
                    "payout_outlier_rescue_reason": payout_outlier_rescue_reason,
                    "payout_outlier_ev_delta": payout_outlier_ev_delta,
                    "rescue_applied": bool(rescue_applied or rank_rescue_applied or near_cap_rescue_applied or payout_outlier_rescue_applied),
                    "high_ev_suspect_flag": bool(high_ev_suspect_flag),
                    "buy_prob_metric": buy_prob_metric,
                    "buy_prob_value": round(float(buy_prob_value), 4),
                    "low_prob_flag": bool(buy_prob_value < self.buy_min_approx_prob),
                    "low_odds_flag": bool(row_odds < float(self.buy_config.get("low_odds_threshold", 20.0) or 20.0)),
                    "missing_odds_flag": bool(not has_real_odds),
                    "risk_penalty_flag": bool(risk_penalty > 0),
                    "low_ev_flag": bool(row_ev < self.buy_min_ev),
                    "first_lane": top_row.get("first_lane"),
                    "second_lane": top_row.get("second_lane"),
                    "third_lane": top_row.get("third_lane"),
                    "top_first_prob": top_row.get("top_first_prob", np.nan),
                    "first_prob_relative_threshold": top_row.get("first_prob_relative_threshold", np.nan),
                    "legacy_first_lane_count": top_row.get("legacy_first_lane_count", np.nan),
                    "expanded_first_lane_count": top_row.get("expanded_first_lane_count", np.nan),
                    "candidate_count_before_cap": top_row.get("candidate_count_before_cap", np.nan),
                    "candidate_count_after_cap": top_row.get("candidate_count_after_cap", np.nan),
                    "candidate_pool_delta_vs_legacy": top_row.get("candidate_pool_delta_vs_legacy", np.nan),
                    "first_win_proba": row_first_win,
                    "first_place_prob": first_place_prob,
                    "calibrated_prob": calibrated_hit_prob,
                    "first_place_score": round(float(first_place_score), 3),
                    "first_place_gate": first_place_gate,
                    "first_place_block": bool(first_place_block),
                    "first_place_priority": bool(first_place_priority),
                    "first_place_multiplier": round(float(first_place_multiplier), 3),
                    "first_place_note": first_place_note,
                    "second_place_score": round(float(second_place_score), 3),
                    "second_place_gate": second_place_gate,
                    "second_place_block": bool(second_place_block),
                    "second_place_priority": bool(second_place_priority),
                    "second_place_multiplier": round(float(second_place_multiplier), 3),
                    "second_place_note": second_place_note,
                    "third_place_score": round(float(third_place_score), 3),
                    "third_place_gate": third_place_gate,
                    "third_place_block": bool(third_place_block),
                    "third_place_priority": bool(third_place_priority),
                    "third_place_multiplier": round(float(third_place_multiplier), 3),
                    "third_place_note": third_place_note,
                    "race_score": round(float(race_score), 3),
                    "race_first_confidence": round(float(race_first_confidence), 3),
                    "race_odds_balance_score": round(float(race_odds_balance_score), 3),
                    "race_data_quality_score": round(float(race_data_quality_score), 3),
                    "race_gate": race_gate,
                    "race_block": bool(race_block),
                    "race_watch": bool(race_watch),
                    "race_priority": bool(race_priority),
                    "race_note": race_note,
                    "approx_prob": row_prob,
                    "calibrated_hit_prob": calibrated_hit_prob,
                    "unified_score": round(float(row_unified_score), 6),
                    "adjusted_score": round(float(row_adjusted_score), 6),
                    "calibrated_hit_prob_adjusted": round(float(adjusted_calibrated_hit_prob), 4),
                    "calibration_method": calibration_method,
                    "calibration_source_col": calibration_source_col,
                    "roi_filter_prob_metric": roi_prob_metric,
                    "prob_bin": prob_bin,
                    "odds_bin": odds_bin,
                    "roi_filter_match": roi_filter_match,
                    "odds": row_odds,
                    "odds_source": row_odds_source,
                    "odds_status": odds_status,
                    "odds_fetch_status": odds_fetch_status,
                    "odds_last_fetched_at": odds_last_fetched_at,
                    "odds_provider": str(top_row.get("odds_provider", "") or ""),
                    "odds_source_url": str(top_row.get("odds_source_url", "") or ""),
                    "odds_raw_status": str(top_row.get("odds_raw_status", "") or ""),
                    "odds_fetch_used_cache": str(top_row.get("odds_fetch_used_cache", False)).lower() in {"true", "1", "yes"},
                    "odds_missing_odds_cells": int(
                        0
                        if pd.isna(pd.to_numeric(top_row.get("odds_missing_odds_cells", 0), errors="coerce"))
                        else float(pd.to_numeric(top_row.get("odds_missing_odds_cells", 0), errors="coerce"))
                    ),
                    "has_real_odds": has_real_odds,
                    "gross_return": top_row.get("gross_return", None),
                    "net_ev": row_ev,
                    "ev": row_ev,
                    "risk_flag": risk_penalty >= 2,
                    "risk_codes": "|".join(risk_codes),
                    "risk_labels": " / ".join(risk_labels),
                    "risk_penalty": risk_penalty,
                    "confidence_score": confidence_score,
                    "kelly_fraction": round(float(kelly_fraction), 6),
                    "bet_pct": round(float(kelly_fraction) * 100.0, 2),
                    "bet_amount": round(float(bet_amount), 2),
                    "bankroll": self.kelly_bankroll,
                    "kelly_max_fraction": self.kelly_max_fraction,
                    "pre_race_score": round(float(pre_race_score), 3),
                    "pre_race_gate": pre_race_profile.get("pre_race_gate", "MISSING"),
                    "pre_race_multiplier": round(float(pre_race_multiplier), 3),
                    "pre_race_time_score": pre_race_profile.get("pre_race_time_score", 0.0),
                    "pre_race_motor_score": pre_race_profile.get("pre_race_motor_score", 0.0),
                    "pre_race_rank_score": pre_race_profile.get("pre_race_rank_score", 0.0),
                    "pre_race_source": pre_race_profile.get("pre_race_source", "missing"),
                    "stop_reason": stop_reason,
                    "skip_reason": stop_reason,
                    "reason": reason,
                    "buy_final_score": np.nan,
                    "buy_final_score_race_component": np.nan,
                    "buy_final_score_calibrated_component": np.nan,
                    "buy_final_score_rank_component": np.nan,
                }
            )

        result = pd.DataFrame(decisions)
        if result.empty:
            return result

        if self.buy_final_score_enabled:
            buy_mask = result["decision"] == "BUY"
            if buy_mask.any():
                score_rows = result.loc[buy_mask].apply(
                    lambda row: pd.Series(self._buy_final_score(row)),
                    axis=1,
                )
                for col in score_rows.columns:
                    result.loc[buy_mask, col] = pd.to_numeric(score_rows[col], errors="coerce")

        def _cap_decisions(frame: pd.DataFrame, target: str, cap: int | None) -> pd.DataFrame:
            if cap is None:
                return frame
            try:
                cap_int = int(cap)
            except Exception:
                return frame
            if cap_int <= 0:
                return frame

            target_idx = frame.index[frame["decision"] == target].tolist()
            if not target_idx:
                return frame
            target_idx.sort(
                key=lambda idx: (
                    float(frame.at[idx, "buy_final_score"])
                    if (
                        target == "BUY"
                        and self.buy_final_score_enabled
                        and "buy_final_score" in frame.columns
                        and pd.notna(frame.at[idx, "buy_final_score"])
                    )
                    else float("-inf"),
                    float(frame.at[idx, "race_score"]) if "race_score" in frame.columns else 0.0,
                    float(frame.at[idx, "pre_race_score"]),
                    float(frame.at[idx, "first_place_score"]) if "first_place_score" in frame.columns else 0.0,
                    float(frame.at[idx, "second_place_score"]) if "second_place_score" in frame.columns else 0.0,
                    float(frame.at[idx, "third_place_score"]) if "third_place_score" in frame.columns else 0.0,
                    float(frame.at[idx, "decision_score"]),
                    float(frame.at[idx, "ev"]),
                    float(frame.at[idx, "approx_prob"]),
                    float(frame.at[idx, "first_win_proba"]),
                    -float(frame.at[idx, "risk_penalty"]),
                ),
                reverse=True,
            )
            overflow = target_idx[cap_int:]
            for idx in overflow:
                if target == "BUY" and bool(frame.at[idx, "watch_eligible"]):
                    frame.at[idx, "decision"] = "WATCH"
                    frame.at[idx, "stop_reason"] = "max_buy_count"
                    frame.at[idx, "odds_status"] = self._derive_odds_status(
                        bool(frame.at[idx, "has_real_odds"]),
                        "WATCH",
                        str(frame.at[idx, "odds_source"]),
                        odds_fetch_status=str(frame.at[idx, "odds_fetch_status"]) if "odds_fetch_status" in frame.columns else "",
                        odds_last_fetched_at=str(frame.at[idx, "odds_last_fetched_at"]) if "odds_last_fetched_at" in frame.columns else "",
                    )
                    frame.at[idx, "reason"] = f"{frame.at[idx, 'reason']} / BUY枠上限によりWATCHへ"
                else:
                    frame.at[idx, "decision"] = "SKIP"
                    frame.at[idx, "stop_reason"] = f"max_{target.lower()}_count"
                    frame.at[idx, "odds_status"] = self._derive_odds_status(
                        bool(frame.at[idx, "has_real_odds"]),
                        "SKIP",
                        str(frame.at[idx, "odds_source"]),
                        odds_fetch_status=str(frame.at[idx, "odds_fetch_status"]) if "odds_fetch_status" in frame.columns else "",
                        odds_last_fetched_at=str(frame.at[idx, "odds_last_fetched_at"]) if "odds_last_fetched_at" in frame.columns else "",
                    )
                    frame.at[idx, "reason"] = f"{frame.at[idx, 'reason']} / {target}枠上限により見送り"
            return frame

        if not ignore_daily_candidate_limit:
            result = _cap_decisions(result, "BUY", day_mode_buy_cap)
            result = _cap_decisions(result, "WATCH", self.max_watch_count)

        # Canonical output schema keeps final_decision while preserving decision for compatibility.
        if "decision" in result.columns:
            result["final_decision"] = result["decision"].astype(str)

        decision_order = {"BUY": 0, "WATCH": 1, "PENDING": 2, "SKIP": 3}
        result["_decision_order"] = result["decision"].map(decision_order).fillna(3).astype(int)
        result = result.sort_values(
            ["date", "_decision_order", "race_score", "first_place_score", "second_place_score", "third_place_score", "decision_score", "race_id"],
            ascending=[False, True, False, False, False, False, False, True],
            na_position="last",
        ).drop(columns=["_decision_order"]).reset_index(drop=True)
        return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate EV and generate skip decisions.")
    parser.add_argument("--candidates-path", default="data/strategy_outputs/trifecta_candidates.csv")
    parser.add_argument("--ev-output-path", default="data/strategy_outputs/ev_analysis.csv")
    parser.add_argument("--skip-output-path", default="data/strategy_outputs/skip_decisions.csv")
    parser.add_argument("--live-odds-path", default="data/strategy_outputs/live_odds.csv")
    parser.add_argument("--fallback-odds-path", default="data/odds/today_trifecta_odds.csv")
    parser.add_argument("--odds-path", default="", help="Optional explicit odds CSV path.")
    parser.add_argument("--race-card-path", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--config-path", default="config/strategy_config.json")
    parser.add_argument("--ignore-day-mode", action="store_true", help="Disable day-mode suppression when evaluating decisions.")
    parser.add_argument(
        "--ignore-daily-candidate-limit",
        action="store_true",
        help="Disable daily BUY/WATCH caps when evaluating decisions.",
    )
    parser.add_argument(
        "--ignore-race-candidate-limit",
        action="store_true",
        help="Bypass per-race candidate limits where supported.",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    evaluator = StrategyEvaluator(config_path=str(args.config_path))
    os.makedirs("data/strategy_outputs", exist_ok=True)

    odds_path = str(args.odds_path).strip()
    if not odds_path:
        live_odds_path = str(args.live_odds_path)
        fallback_odds_path = str(args.fallback_odds_path)
        odds_path = live_odds_path if os.path.exists(live_odds_path) else fallback_odds_path

    ev_df = evaluator.build_ev_analysis(
        str(args.candidates_path),
        odds_path=odds_path if odds_path and os.path.exists(odds_path) else None,
    )
    ev_df.to_csv(str(args.ev_output_path), index=False)
    print(f"EV analysis saved: {args.ev_output_path}")

    race_boat_counts = evaluator._load_race_boat_counts(str(args.race_card_path))
    skip_df = evaluator.build_skip_decisions(
        ev_df,
        race_boat_counts=race_boat_counts,
        race_card_path=str(args.race_card_path),
        ignore_day_mode=bool(args.ignore_day_mode),
        ignore_daily_candidate_limit=bool(args.ignore_daily_candidate_limit),
        ignore_race_candidate_limit=bool(args.ignore_race_candidate_limit),
    )
    skip_df.to_csv(str(args.skip_output_path), index=False)
    print(f"Skip decisions saved: {args.skip_output_path}")
    mode_flags_path = "data/strategy_outputs/mode_flags.json"
    existing_flags: dict[str, object] = {}
    if os.path.exists(mode_flags_path):
        try:
            with open(mode_flags_path, "r", encoding="utf-8") as f:
                existing_flags = json.load(f)
        except Exception:
            existing_flags = {}
    with open(mode_flags_path, "w", encoding="utf-8") as f:
        existing_flags["strategy_mode"] = evaluator.strategy_mode
        json.dump(
            existing_flags or {"strategy_mode": evaluator.strategy_mode},
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"Mode flags saved: {mode_flags_path}")


if __name__ == "__main__":
    main()
