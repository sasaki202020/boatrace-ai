from __future__ import annotations

from collections.abc import Iterable, Mapping
from math import isnan
from typing import Any

import pandas as pd


DayMode = str


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, set, dict)):
        return False
    try:
        result = pd.isna(value)
    except Exception:
        return False
    if isinstance(result, (bool,)):
        return result
    try:
        return bool(result)
    except Exception:
        return False


def _as_float(value: object) -> float | None:
    if _is_missing(value):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if isnan(numeric):
        return None
    return numeric


def _as_int(value: object) -> int | None:
    numeric = _as_float(value)
    if numeric is None:
        return None
    try:
        return int(numeric)
    except (TypeError, ValueError, OverflowError):
        return None


def _as_bool(value: object) -> bool:
    if _is_missing(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _string_list(value: object) -> list[str]:
    if _is_missing(value):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, pd.Index):
        return [str(item) for item in value.tolist() if not _is_missing(item)]
    if isinstance(value, Iterable):
        return [str(item) for item in value if not _is_missing(item)]
    return [str(value)]


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _config_section(config: Mapping[str, Any], key: str) -> dict[str, Any]:
    section = config.get(key, {})
    return dict(section) if isinstance(section, Mapping) else {}


def check_required_columns(metrics: Mapping[str, Any], config: Mapping[str, Any]) -> list[str]:
    pipeline_health = _config_section(config, "pipeline_health")
    required_columns = _string_list(pipeline_health.get("required_columns", []))
    available_columns = set(_string_list(metrics.get("available_columns", [])))

    missing_columns = [column for column in required_columns if column not in available_columns]
    if not missing_columns:
        return []
    return [f"missing_required_columns:{column}" for column in missing_columns]


def check_phase_non_null(phase: str, metrics: Mapping[str, Any], config: Mapping[str, Any]) -> list[str]:
    pipeline_health = _config_section(config, "pipeline_health")
    required_by_phase = pipeline_health.get("required_non_null_columns_by_phase", {})
    required_columns = _string_list(required_by_phase.get(phase, [])) if isinstance(required_by_phase, Mapping) else []
    if phase == "buy_phase" and "real_odds" not in required_columns:
        required_columns = required_columns + ["real_odds"]

    non_null_columns = set(_string_list(metrics.get("non_null_columns", [])))
    missing_columns = [column for column in required_columns if column not in non_null_columns]
    if not missing_columns:
        return []
    return [f"phase_non_null_violation:{phase}:{column}" for column in missing_columns]


def check_fatal_pipeline_errors(runtime_flags: Mapping[str, Any] | None) -> list[str]:
    flags = dict(runtime_flags or {})
    reasons: list[str] = []
    if _as_bool(flags.get("result_import_failed")):
        reasons.append("result_import_failed")
    if _as_bool(flags.get("roi_not_computable")):
        reasons.append("roi_not_computable")
    return reasons


def _threshold_value(section: Mapping[str, Any], key: str, default: float | None = None) -> float | None:
    numeric = _as_float(section.get(key))
    return numeric if numeric is not None else default


def resolve_day_mode(metrics: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[DayMode, list[str]]:
    day_mode_rules = _config_section(config, "day_mode_rules")
    day_mode_resolution = _config_section(config, "day_mode_resolution")
    stop_rules = _config_section(day_mode_rules, "stop")
    normal_rules = _config_section(day_mode_rules, "normal")
    reduced_rules = _config_section(day_mode_rules, "reduced")
    fallback_mode = str(day_mode_resolution.get("fallback_mode", "stop") or "stop")

    real_odds_available_rate = _as_float(metrics.get("real_odds_available_rate"))
    missing_feature_rate = _as_float(metrics.get("missing_feature_rate"))
    today_races = _as_int(metrics.get("today_races"))
    race_coverage = _as_float(metrics.get("race_coverage"))

    stop_reasons: list[str] = []
    stop_real_odds = _threshold_value(stop_rules, "below_real_odds_available_rate", 0.35)
    stop_missing_feature = _threshold_value(stop_rules, "above_missing_feature_rate", 0.06)
    stop_today_races = _as_int(stop_rules.get("below_min_today_races"))
    stop_race_coverage = _threshold_value(stop_rules, "below_min_race_coverage", 0.80)

    if real_odds_available_rate is None:
        stop_reasons.append("missing_metric:real_odds_available_rate")
    elif stop_real_odds is not None and real_odds_available_rate < stop_real_odds:
        stop_reasons.append(
            f"real_odds_available_rate_below_stop_threshold:{real_odds_available_rate:.4f}<{stop_real_odds:.4f}"
        )

    if missing_feature_rate is None:
        stop_reasons.append("missing_metric:missing_feature_rate")
    elif stop_missing_feature is not None and missing_feature_rate > stop_missing_feature:
        stop_reasons.append(
            f"missing_feature_rate_above_stop_threshold:{missing_feature_rate:.4f}>{stop_missing_feature:.4f}"
        )

    if today_races is None:
        stop_reasons.append("missing_metric:today_races")
    elif stop_today_races is not None and today_races < stop_today_races:
        stop_reasons.append(f"today_races_below_stop_threshold:{today_races}<{stop_today_races}")

    if race_coverage is None:
        stop_reasons.append("missing_metric:race_coverage")
    elif stop_race_coverage is not None and race_coverage < stop_race_coverage:
        stop_reasons.append(f"race_coverage_below_stop_threshold:{race_coverage:.4f}<{stop_race_coverage:.4f}")

    if stop_reasons:
        return "stop", _dedupe_preserve_order(stop_reasons)

    normal_ok = True
    normal_failures: list[str] = []
    normal_real_odds = _threshold_value(normal_rules, "min_real_odds_available_rate", 0.48)
    normal_missing_feature = _threshold_value(normal_rules, "max_missing_feature_rate", 0.03)
    normal_today_races = _as_int(normal_rules.get("min_today_races"))
    normal_race_coverage = _threshold_value(normal_rules, "min_race_coverage", 0.88)

    if real_odds_available_rate is None or normal_real_odds is None or real_odds_available_rate < normal_real_odds:
        normal_ok = False
        normal_failures.append("normal_real_odds_threshold_not_met")
    if missing_feature_rate is None or normal_missing_feature is None or missing_feature_rate > normal_missing_feature:
        normal_ok = False
        normal_failures.append("normal_missing_feature_threshold_not_met")
    if today_races is None or normal_today_races is None or today_races < normal_today_races:
        normal_ok = False
        normal_failures.append("normal_today_races_threshold_not_met")
    if race_coverage is None or normal_race_coverage is None or race_coverage < normal_race_coverage:
        normal_ok = False
        normal_failures.append("normal_race_coverage_threshold_not_met")

    if normal_ok:
        return "normal", []

    reduced_ok = True
    reduced_failures: list[str] = []
    reduced_real_odds = _threshold_value(reduced_rules, "min_real_odds_available_rate", 0.35)
    reduced_missing_feature = _threshold_value(reduced_rules, "max_missing_feature_rate", 0.06)
    reduced_today_races = _as_int(reduced_rules.get("min_today_races"))
    reduced_race_coverage = _threshold_value(reduced_rules, "min_race_coverage", 0.80)

    if real_odds_available_rate is None or reduced_real_odds is None or real_odds_available_rate < reduced_real_odds:
        reduced_ok = False
        reduced_failures.append("reduced_real_odds_threshold_not_met")
    if missing_feature_rate is None or reduced_missing_feature is None or missing_feature_rate > reduced_missing_feature:
        reduced_ok = False
        reduced_failures.append("reduced_missing_feature_threshold_not_met")
    if today_races is None or reduced_today_races is None or today_races < reduced_today_races:
        reduced_ok = False
        reduced_failures.append("reduced_today_races_threshold_not_met")
    if race_coverage is None or reduced_race_coverage is None or race_coverage < reduced_race_coverage:
        reduced_ok = False
        reduced_failures.append("reduced_race_coverage_threshold_not_met")

    if reduced_ok:
        return "reduced", []

    reasons = normal_failures + reduced_failures
    if not reasons:
        reasons.append("day_mode_fallback_stop")
    else:
        reasons.append(f"day_mode_fallback_stop:{fallback_mode}")
    return fallback_mode if fallback_mode else "stop", _dedupe_preserve_order(reasons)


def collect_alerts(metrics: Mapping[str, Any], runtime_flags: Mapping[str, Any] | None, config: Mapping[str, Any]) -> list[str]:
    odds_health = _config_section(config, "odds_health")
    performance_health = _config_section(config, "performance_health")
    buy_health = _config_section(config, "buy_health")

    pending_unpublished_rate = _as_float(metrics.get("pending_unpublished_rate"))
    daily_roi_drop = _as_float(metrics.get("daily_roi_drop"))
    consecutive_zero_buy_days = _as_int(metrics.get("consecutive_zero_buy_days"))
    hit_rate_drop = _as_float(metrics.get("hit_rate_drop"))
    flags = dict(runtime_flags or {})

    reasons: list[str] = []
    max_pending = _threshold_value(odds_health, "max_pending_unpublished_rate", 0.10)
    if pending_unpublished_rate is not None and max_pending is not None and pending_unpublished_rate > max_pending:
        reasons.append(f"pending_unpublished_rate_exceeds_threshold:{pending_unpublished_rate:.4f}>{max_pending:.4f}")

    max_roi_drop = _threshold_value(performance_health, "max_daily_roi_drop", 0.40)
    if daily_roi_drop is not None and max_roi_drop is not None and daily_roi_drop > max_roi_drop:
        reasons.append(f"daily_roi_drop_exceeds_threshold:{daily_roi_drop:.4f}>{max_roi_drop:.4f}")

    max_zero_buy_days = _as_int(buy_health.get("max_consecutive_zero_buy_days"))
    if consecutive_zero_buy_days is not None and max_zero_buy_days is not None and consecutive_zero_buy_days >= max_zero_buy_days:
        reasons.append(
            f"consecutive_zero_buy_days_exceeds_threshold:{consecutive_zero_buy_days}>={max_zero_buy_days}"
        )

    max_hit_rate_drop = _threshold_value(performance_health, "max_hit_rate_drop", 0.12)
    if hit_rate_drop is not None and max_hit_rate_drop is not None and hit_rate_drop > max_hit_rate_drop:
        reasons.append(f"hit_rate_drop_exceeds_threshold:{hit_rate_drop:.4f}>{max_hit_rate_drop:.4f}")

    if _as_bool(flags.get("single_venue_abnormal")):
        reasons.append("single_venue_abnormal")

    return _dedupe_preserve_order(reasons)


def get_effective_rules(mode: DayMode, config: Mapping[str, Any]) -> dict[str, Any] | None:
    if mode not in {"normal", "reduced"}:
        return None

    buy_rules = _config_section(config, "buy_rules")
    if not buy_rules:
        return None

    min_win_proba = _as_float(buy_rules.get("min_win_proba"))
    min_ev = _as_float(buy_rules.get("min_ev"))
    max_ev = _as_float(buy_rules.get("max_ev"))
    max_candidates_per_day = _as_int(buy_rules.get("max_candidates_per_day"))
    max_candidates_per_race = _as_int(buy_rules.get("max_candidates_per_race"))

    if None in {min_win_proba, min_ev, max_ev, max_candidates_per_day, max_candidates_per_race}:
        return None

    effective_rules = {
        "min_win_proba": float(min_win_proba),
        "min_ev": float(min_ev),
        "max_ev": float(max_ev),
        "max_candidates_per_day": int(max_candidates_per_day),
        "max_candidates_per_race": int(max_candidates_per_race),
    }

    if mode == "reduced":
        day_mode_rules = _config_section(config, "day_mode_rules")
        reduced_rules = _config_section(day_mode_rules, "reduced")
        min_win_proba_add = _as_float(reduced_rules.get("min_win_proba_add")) or 0.0
        min_ev_add = _as_float(reduced_rules.get("min_ev_add")) or 0.0
        reduced_max_candidates_per_day = _as_int(reduced_rules.get("max_candidates_per_day"))
        if reduced_max_candidates_per_day is None:
            return None
        effective_rules["min_win_proba"] = float(min_win_proba) + min_win_proba_add
        effective_rules["min_ev"] = float(min_ev) + min_ev_add
        effective_rules["max_candidates_per_day"] = reduced_max_candidates_per_day

    return effective_rules


def build_decision(
    phase: str,
    metrics: Mapping[str, Any],
    config: Mapping[str, Any],
    stop_reasons: list[str],
    alert_reasons: list[str],
    mode: DayMode,
) -> dict[str, Any]:
    is_stop = bool(stop_reasons) or mode == "stop"
    if is_stop:
        mode = "stop"
    effective_rules = None if is_stop else get_effective_rules(mode, config)
    if not is_stop and effective_rules is None:
        is_stop = True
        stop_reasons = _dedupe_preserve_order(stop_reasons + ["effective_rules_unavailable"])
        mode = "stop"

    allow_buy = bool(phase == "buy_phase" and not is_stop)
    if is_stop:
        effective_rules = None

    return {
        "phase": phase,
        "is_stop": is_stop,
        "mode": mode,
        "allow_buy": allow_buy,
        "stop_reasons": _dedupe_preserve_order(stop_reasons),
        "alert_reasons": _dedupe_preserve_order(alert_reasons),
        "metrics": dict(metrics),
        "effective_rules": effective_rules,
    }


def gatekeeper_decide(
    phase: str,
    metrics: Mapping[str, Any],
    config: Mapping[str, Any],
    runtime_flags: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        stop_reasons: list[str] = []
        stop_reasons.extend(check_required_columns(metrics, config))
        stop_reasons.extend(check_phase_non_null(phase, metrics, config))
        stop_reasons.extend(check_fatal_pipeline_errors(runtime_flags))

        mode, mode_reasons = resolve_day_mode(metrics, config)
        if mode == "stop":
            stop_reasons.extend(mode_reasons)

        alert_reasons = collect_alerts(metrics, runtime_flags, config)
        return build_decision(
            phase=phase,
            metrics=metrics,
            config=config,
            stop_reasons=_dedupe_preserve_order(stop_reasons),
            alert_reasons=alert_reasons,
            mode=mode,
        )
    except Exception as exc:
        return {
            "phase": phase,
            "is_stop": True,
            "mode": "stop",
            "allow_buy": False,
            "stop_reasons": ["gatekeeper_exception", f"{type(exc).__name__}:{exc}"],
            "alert_reasons": [],
            "metrics": dict(metrics),
            "effective_rules": None,
        }
