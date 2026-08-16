from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

RACE_KEY = ["date", "jcd", "race_no"]
STRICT_LAG_FEATURES = ["lane", "jcd", "race_no", "lane_prior_count", "lane_prior_win_rate", "venue_lane_prior_count", "venue_lane_prior_win_rate", "racer_prior_count", "racer_prior_win_rate", "racer_prior_top2_rate", "racer_prior_mean_finish", "racer_prior5_win_rate", "racer_prior10_win_rate", "days_since_previous_race", "feature_availability_count"]
FORBIDDEN_FEATURES = {"target", "finish_position", "st", "exhibition_time", "final_odds", "odds", "payout", "result", "winner", "refund", "actual_time"}


def canonical_config_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def assert_feature_contract(features: list[str]) -> None:
    invalid = sorted((set(features) & FORBIDDEN_FEATURES) | (set(features) - set(STRICT_LAG_FEATURES)))
    if invalid:
        raise ValueError(f"feature_timing_violation:{','.join(invalid)}")


@dataclass(frozen=True)
class ExperimentSpec:
    family: str
    name: str
    parameters: dict[str, Any]

    @staticmethod
    def validate_budget(specs: list["ExperimentSpec"]) -> None:
        if len({spec.family for spec in specs}) > 3:
            raise ValueError("experiment_family_budget_exceeded")
        if len(specs) > 12:
            raise ValueError("experiment_setting_budget_exceeded")


def build_walk_forward_splits(frame: pd.DataFrame, *, folds: int = 5, validation_days: int = 60) -> dict[str, Any]:
    if folds != 5:
        raise ValueError("five_folds_required")
    normalized = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    dates = sorted(normalized.unique())
    if len(dates) <= folds * validation_days:
        raise ValueError("insufficient_dates")
    entries = []
    for index in range(folds):
        start = len(dates) - (folds - index) * validation_days
        valid_dates = dates[start:start + validation_days]
        train_dates = dates[:start]
        train = frame[normalized.isin(train_dates)]
        valid = frame[normalized.isin(valid_dates)]
        overlap = set(train["race_id"]) & set(valid["race_id"])
        entries.append({"fold": index + 1, "trainStart": str(pd.Timestamp(train_dates[0]).date()), "trainEnd": str(pd.Timestamp(train_dates[-1]).date()), "validationStart": str(pd.Timestamp(valid_dates[0]).date()), "validationEnd": str(pd.Timestamp(valid_dates[-1]).date()), "trainRaceCount": int(train["race_id"].nunique()), "validationRaceCount": int(valid["race_id"].nunique()), "raceOverlapCount": len(overlap)})
    return {"evaluationLabel": "RESEARCH_WALK_FORWARD", "foldCount": folds, "validationDays": validation_days, "folds": entries, "consumedDiagnosticWindow": {"start": entries[0]["validationStart"], "end": entries[-1]["validationEnd"]}}


def normalize_race_probabilities(frame: pd.DataFrame, score: np.ndarray | pd.Series, *, temperature: float = 1.0) -> np.ndarray:
    values = np.asarray(score, dtype=float)
    if len(values) != len(frame) or not np.isfinite(values).all():
        raise ValueError("nonfinite_score")
    if temperature <= 0:
        raise ValueError("invalid_temperature")
    weighted = np.clip(values, 1e-12, None) ** (1.0 / temperature)
    series = pd.Series(weighted, index=frame.index)
    probability = (series / series.groupby(frame["race_id"]).transform("sum")).to_numpy()
    sums = pd.Series(probability, index=frame.index).groupby(frame["race_id"]).sum().to_numpy()
    if not np.isfinite(probability).all() or (probability < 0).any() or (probability > 1).any() or not np.allclose(sums, 1.0, atol=1e-10):
        raise ValueError("probability_contract_violation")
    return probability


def normalize_race_logits(frame: pd.DataFrame, logits: np.ndarray | pd.Series, *, temperature: float = 1.0) -> np.ndarray:
    values = np.asarray(logits, dtype=float)
    if len(values) != len(frame) or not np.isfinite(values).all():
        raise ValueError("nonfinite_score")
    if temperature <= 0:
        raise ValueError("invalid_temperature")
    series = pd.Series(values / temperature, index=frame.index)
    centered = series - series.groupby(frame["race_id"]).transform("max")
    exponential = np.exp(centered)
    probability = (exponential / exponential.groupby(frame["race_id"]).transform("sum")).to_numpy()
    sums = pd.Series(probability, index=frame.index).groupby(frame["race_id"]).sum().to_numpy()
    if not np.isfinite(probability).all() or not np.allclose(sums, 1.0, atol=1e-10):
        raise ValueError("probability_contract_violation")
    return probability


def multiclass_metrics(frame: pd.DataFrame, probability_column: str = "predicted_probability") -> dict[str, float | int]:
    probability = frame[probability_column].to_numpy(float)
    if not np.isfinite(probability).all():
        raise ValueError("nonfinite_probability")
    winners = frame[frame["target"] == 1]
    if winners["race_id"].nunique() != frame["race_id"].nunique():
        raise ValueError("winner_contract_violation")
    winner_probability = winners[probability_column].clip(lower=1e-15).to_numpy()
    squared = (probability - frame["target"].to_numpy(float)) ** 2
    race_brier = pd.Series(squared, index=frame.index).groupby(frame["race_id"]).sum()
    top = frame.loc[frame.groupby("race_id")[probability_column].idxmax()].copy()
    bins = pd.cut(top[probability_column], bins=np.linspace(0, 1, 11), include_lowest=True)
    ece = sum(len(group) / len(top) * abs(group[probability_column].mean() - group["target"].mean()) for _, group in top.groupby(bins, observed=False) if len(group))
    return {"raceCount": int(frame["race_id"].nunique()), "raceLogLoss": float(-np.log(winner_probability).mean()), "multiclassBrier": float(race_brier.mean()), "top1Accuracy": float(top["target"].mean()), "ece10": float(ece)}


def paired_date_block_bootstrap(baseline: pd.DataFrame, candidate: pd.DataFrame, *, iterations: int = 1000, seed: int = 42) -> dict[str, float | int]:
    def daily(frame: pd.DataFrame) -> pd.DataFrame:
        winners = frame[frame["target"] == 1].copy(); winners["log"] = -np.log(winners["predicted_probability"].clip(lower=1e-15))
        work = frame.copy(); work["sq"] = (work["predicted_probability"] - work["target"]) ** 2
        brier = work.groupby(["date", "race_id"])["sq"].sum().groupby("date").mean()
        top = work.loc[work.groupby("race_id")["predicted_probability"].idxmax()].groupby("date")["target"].mean()
        return pd.DataFrame({"log": winners.groupby("date")["log"].mean(), "brier": brier, "top1": top}).dropna()
    delta = daily(candidate).join(daily(baseline), lsuffix="Candidate", rsuffix="Baseline", how="inner")
    if delta.empty:
        raise ValueError("bootstrap_no_common_dates")
    rng = np.random.default_rng(seed); samples = {key: [] for key in ("logLoss", "brier", "top1")}
    columns = {"logLoss": ("logCandidate", "logBaseline"), "brier": ("brierCandidate", "brierBaseline"), "top1": ("top1Candidate", "top1Baseline")}
    for _ in range(iterations):
        sample = delta.iloc[rng.integers(0, len(delta), len(delta))]
        for key, (candidate_column, baseline_column) in columns.items():
            samples[key].append(float((sample[candidate_column] - sample[baseline_column]).mean()))
    result: dict[str, float | int] = {"iterations": iterations, "dateBlockCount": len(delta), "seed": seed}
    for key, values in samples.items():
        result[f"{key}Delta"] = float(np.mean(values)); result[f"{key}Ci95Lower"] = float(np.quantile(values, 0.025)); result[f"{key}Ci95Upper"] = float(np.quantile(values, 0.975))
    return result


def eligible_challengers(results: pd.DataFrame, *, baseline: str = "tree_15", candidate_names: set[str] | None = None, ece_tolerance: float = 0.002) -> list[str]:
    base = results[results["modelName"] == baseline].set_index("fold")
    eligible: list[tuple[float, str]] = []
    for name, group in results[results["modelName"] != baseline].groupby("modelName"):
        if candidate_names is not None and name not in candidate_names:
            continue
        joined = group.set_index("fold").join(base[["raceLogLoss", "multiclassBrier", "ece10"]], rsuffix="Baseline")
        log_wins = int((joined["raceLogLoss"] < joined["raceLogLossBaseline"]).sum()); brier_wins = int((joined["multiclassBrier"] < joined["multiclassBrierBaseline"]).sum())
        if log_wins >= 4 and brier_wins >= 4 and float((joined["ece10"] - joined["ece10Baseline"]).max()) <= ece_tolerance:
            eligible.append((float(group["raceLogLoss"].mean()), str(name)))
    return [name for _, name in sorted(eligible)]


def select_challenger(results: pd.DataFrame, *, baseline: str = "tree_15", ece_tolerance: float = 0.002) -> str | None:
    eligible = eligible_challengers(results, baseline=baseline, ece_tolerance=ece_tolerance)
    return eligible[0] if eligible else None


def promotion_passes(*, deterministic: bool, ci_pass: bool, segment_pass: bool, gap_reset_pass: bool) -> bool:
    return deterministic and ci_pass and segment_pass and gap_reset_pass
