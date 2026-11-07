import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.eval.backtest_buy_skip import build_race_outcomes
from src.strategy.evaluate_ev_and_skip import StrategyEvaluator


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "reports" / "task8_payout_outlier_diagnostic"
EV_PATH = ROOT / "reports" / "task1_after_calib" / "ev_analysis.csv"
HIST_PATH = ROOT / "data" / "processed" / "historical_races.csv"
NEAR_MISS_PATH = ROOT / "reports" / "task5_buy_eligible_diagnostic" / "buy_eligible_near_miss_rows.csv"


def _split_codes(value: object) -> list[str]:
    if pd.isna(value):
        return []
    text = str(value).strip()
    if not text:
        return []
    return [part for part in text.split("|") if part]


def _odds_band(odds: object) -> str:
    value = pd.to_numeric(odds, errors="coerce")
    if pd.isna(value):
        return "unknown"
    num = float(value)
    if num <= 100:
        return "odds_le_100"
    if num <= 200:
        return "odds_100_200"
    if num <= 300:
        return "odds_200_300"
    return "odds_gt_300"


def _ev_band(delta: object) -> str:
    value = pd.to_numeric(delta, errors="coerce")
    if pd.isna(value):
        return "unknown"
    num = float(value)
    if num <= 1.0:
        return "delta_le_1"
    if num <= 5.0:
        return "delta_1_5"
    if num <= 20.0:
        return "delta_5_20"
    return "delta_gt_20"


def _final_score_band(score: object) -> str:
    value = pd.to_numeric(score, errors="coerce")
    if pd.isna(value):
        return "unknown"
    num = float(value)
    if num < 0.10:
        return "fs_lt_0_10"
    if num < 0.30:
        return "fs_0_10_0_30"
    if num < 0.50:
        return "fs_0_30_0_50"
    return "fs_ge_0_50"


def _build_actual_rank_df(ev_df: pd.DataFrame, outcomes: pd.DataFrame) -> pd.DataFrame:
    merged = ev_df.merge(
        outcomes[["race_id", "actual_trifecta"]],
        left_on=["race_id", "trifecta"],
        right_on=["race_id", "actual_trifecta"],
        how="inner",
    )
    return (
        merged[["race_id", "candidate_rank_by_sort"]]
        .rename(columns={"candidate_rank_by_sort": "actual_rank"})
        .drop_duplicates(subset=["race_id"])
    )


def _compute_final_score(row: pd.Series, evaluator: StrategyEvaluator) -> float:
    race_feat = pd.DataFrame()
    pre = evaluator._compute_pre_race_profile(row, race_feat)
    row_for_score = row.copy()
    row_for_score["pre_race_score"] = float(pre.get("pre_race_score", 0.0) or 0.0)
    row_for_score["pre_race_gate"] = pre.get("pre_race_gate", "MISSING")
    race_selection = evaluator._compute_race_selection_profile(
        row_for_score,
        race_feat,
        float(pd.to_numeric(row.get("first_place_score", 0.0), errors="coerce") or 0.0),
        float(pre.get("pre_race_score", 0.0) or 0.0),
        str(row.get("odds_source", "")).lower() == "real",
    )
    row_for_score["race_score"] = float(race_selection.get("race_score", 0.0) or 0.0)
    return float(evaluator._buy_final_score(row_for_score).get("buy_final_score", 0.0) or 0.0)


def _other_gates(row: pd.Series) -> str:
    gates: list[str] = []
    odds = float(pd.to_numeric(row.get("odds"), errors="coerce") or 0.0)
    calibrated = float(pd.to_numeric(row.get("calibrated_hit_prob"), errors="coerce") or 0.0)
    rank = int(pd.to_numeric(row.get("candidate_rank_by_sort"), errors="coerce") or 999)
    risk_codes = _split_codes(row.get("risk_codes"))
    if odds > 200.0:
        gates.append("odds_cap")
    if calibrated < 0.05:
        gates.append("calibrated_hit_prob")
    if rank > 3:
        gates.append("candidate_rank_gt3")
    if "HIGH_ODDS_VOLATILE" in risk_codes:
        gates.append("extremely_high_odds")
    if "LOW_CONFIDENCE" in risk_codes:
        gates.append("low_confidence")
    return "|".join(gates)


def _scope_count_rows(payout_df: pd.DataFrame) -> list[dict[str, object]]:
    scopes = {
        "all": payout_df,
        "actual_rank_le5": payout_df[payout_df["actual_rank_le5"]],
        "actual_rank_le3": payout_df[payout_df["actual_rank_le3"]],
        "candidate_rank_top3": payout_df[payout_df["candidate_rank_top3"]],
        "calibrated_ge_0_05": payout_df[payout_df["calibrated_ge_0_05"]],
        "calibrated_ge_0_10": payout_df[payout_df["calibrated_ge_0_10"]],
        "odds_le_100": payout_df[payout_df["odds_band"] == "odds_le_100"],
        "odds_100_200": payout_df[payout_df["odds_band"] == "odds_100_200"],
        "odds_200_300": payout_df[payout_df["odds_band"] == "odds_200_300"],
        "odds_gt_300": payout_df[payout_df["odds_band"] == "odds_gt_300"],
    }
    return [{"scope": name, "count": int(len(df))} for name, df in scopes.items()]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ev_df = pd.read_csv(EV_PATH, low_memory=False)
    outcomes = build_race_outcomes(HIST_PATH)
    evaluator = StrategyEvaluator(config_path=str(ROOT / "config" / "strategy_config.json"))
    actual_rank_df = _build_actual_rank_df(ev_df, outcomes)

    df = ev_df.merge(actual_rank_df, on="race_id", how="left").copy()
    df["risk_codes"] = df.apply(lambda row: "|".join(evaluator._build_risk_codes(row)), axis=1)
    df["risk_penalty"] = df["risk_codes"].apply(lambda x: evaluator._risk_penalty(_split_codes(x)))
    df["risk_ev_threshold"] = float(evaluator.risk_ev_threshold)
    df["payout_outlier"] = df["risk_flag"].fillna(False).astype(bool)
    df["ev_minus_risk_threshold"] = pd.to_numeric(df["ev"], errors="coerce").fillna(0.0) - float(evaluator.risk_ev_threshold)
    df["final_score"] = df.apply(lambda row: _compute_final_score(row, evaluator), axis=1)
    df["actual_rank_le5"] = pd.to_numeric(df["actual_rank"], errors="coerce").fillna(999) <= 5
    df["actual_rank_le3"] = pd.to_numeric(df["actual_rank"], errors="coerce").fillna(999) <= 3
    df["candidate_rank_top3"] = pd.to_numeric(df["candidate_rank_by_sort"], errors="coerce").fillna(999).astype(int) <= 3
    df["calibrated_ge_0_05"] = pd.to_numeric(df["calibrated_hit_prob"], errors="coerce").fillna(0.0) >= 0.05
    df["calibrated_ge_0_10"] = pd.to_numeric(df["calibrated_hit_prob"], errors="coerce").fillna(0.0) >= 0.10
    df["odds_band"] = df["odds"].apply(_odds_band)
    df["ev_minus_band"] = df["ev_minus_risk_threshold"].apply(_ev_band)
    df["final_score_band"] = df["final_score"].apply(_final_score_band)
    df["other_gates"] = df.apply(_other_gates, axis=1)

    payout_df = df[df["payout_outlier"]].copy()
    payout_df.to_csv(OUT_DIR / "payout_outlier_rows.csv", index=False)

    scope_counts = pd.DataFrame(_scope_count_rows(payout_df))
    scope_counts.to_csv(OUT_DIR / "payout_outlier_scope_counts.csv", index=False)

    odds_band_counts = (
        payout_df.groupby("odds_band", dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values("odds_band", kind="mergesort")
    )
    odds_band_counts.to_csv(OUT_DIR / "payout_outlier_odds_band_counts.csv", index=False)

    high_signal = payout_df[
        payout_df["actual_rank_le3"]
        & payout_df["candidate_rank_top3"]
        & payout_df["calibrated_ge_0_05"]
    ].copy()
    high_signal = high_signal[
        [
            "race_id",
            "trifecta",
            "actual_rank",
            "candidate_rank_by_sort",
            "approx_prob",
            "calibrated_hit_prob",
            "final_score",
            "odds",
            "ev",
            "risk_ev_threshold",
            "ev_minus_risk_threshold",
            "other_gates",
            "odds_band",
            "ev_minus_band",
            "final_score_band",
        ]
    ].rename(columns={"trifecta": "combo"})
    high_signal = high_signal.sort_values(
        ["actual_rank", "candidate_rank_by_sort", "calibrated_hit_prob", "ev_minus_risk_threshold"],
        ascending=[True, True, False, True],
        kind="mergesort",
    )
    high_signal.to_csv(OUT_DIR / "high_signal_payout_outlier_rows.csv", index=False)

    high_signal_odds = (
        high_signal.groupby("odds_band", dropna=False).size().reset_index(name="count").sort_values("odds_band")
    )
    high_signal_odds.to_csv(OUT_DIR / "high_signal_odds_band_counts.csv", index=False)
    high_signal_ev = (
        high_signal.groupby("ev_minus_band", dropna=False).size().reset_index(name="count").sort_values("ev_minus_band")
    )
    high_signal_ev.to_csv(OUT_DIR / "high_signal_ev_band_counts.csv", index=False)
    high_signal_final = (
        high_signal.groupby("final_score_band", dropna=False).size().reset_index(name="count").sort_values("final_score_band")
    )
    high_signal_final.to_csv(OUT_DIR / "high_signal_final_score_band_counts.csv", index=False)

    near_pass = high_signal.sort_values(
        ["ev_minus_risk_threshold", "actual_rank", "candidate_rank_by_sort", "calibrated_hit_prob"],
        ascending=[True, True, True, False],
        kind="mergesort",
    ).copy()
    near_pass.to_csv(OUT_DIR / "payout_outlier_near_pass_rows.csv", index=False)

    near_miss = pd.read_csv(NEAR_MISS_PATH).rename(columns={"combo": "combo", "buy_final_score": "near_miss_final_score"})
    near_miss_rows = payout_df.merge(
        near_miss[
            [
                "race_id",
                "combo",
                "drop_conditions",
                "primary_reason",
                "threshold_gap",
            ]
        ],
        left_on=["race_id", "trifecta"],
        right_on=["race_id", "combo"],
        how="inner",
    )
    near_miss_rows = near_miss_rows[
        [
            "race_id",
            "combo",
            "actual_rank",
            "candidate_rank_by_sort",
            "approx_prob",
            "calibrated_hit_prob",
            "final_score",
            "odds",
            "ev",
            "risk_ev_threshold",
            "ev_minus_risk_threshold",
            "other_gates",
            "drop_conditions",
            "primary_reason",
            "threshold_gap",
        ]
    ]
    near_miss_rows.to_csv(OUT_DIR / "near_miss_payout_outlier_rows.csv", index=False)

    summary = {
        "total_payout_outlier_count": int(len(payout_df)),
        "scope_counts": {row["scope"]: int(row["count"]) for row in scope_counts.to_dict(orient="records")},
        "high_signal_count": int(len(high_signal)),
        "near_miss_count": int(len(near_miss_rows)),
        "high_signal_odds_band_counts": {
            str(row["odds_band"]): int(row["count"]) for row in high_signal_odds.to_dict(orient="records")
        },
        "high_signal_ev_band_counts": {
            str(row["ev_minus_band"]): int(row["count"]) for row in high_signal_ev.to_dict(orient="records")
        },
        "high_signal_final_score_band_counts": {
            str(row["final_score_band"]): int(row["count"]) for row in high_signal_final.to_dict(orient="records")
        },
    }
    (OUT_DIR / "payout_outlier_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
