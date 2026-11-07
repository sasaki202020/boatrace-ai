import json
from pathlib import Path

import pandas as pd

from src.eval.backtest_buy_skip import build_race_outcomes
from src.strategy.evaluate_ev_and_skip import StrategyEvaluator


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "reports" / "task7_risk_flag_diagnostic_candidates"
EV_PATH = ROOT / "reports" / "task1_after_calib" / "ev_analysis.csv"
HIST_PATH = ROOT / "data" / "processed" / "historical_races.csv"
NEAR_MISS_PATH = ROOT / "reports" / "task5_buy_eligible_diagnostic" / "buy_eligible_near_miss_rows.csv"

REASON_ORDER = [
    "extremely_high_odds",
    "low_confidence",
    "payout_outlier",
    "sparse_signal",
    "no_real_odds",
    "data_missing",
    "low_ev_quality",
    "high_variance",
    "other_internal",
]


def _split_codes(value: object) -> list[str]:
    if pd.isna(value):
        return []
    text = str(value).strip()
    if not text:
        return []
    return [part for part in text.split("|") if part]


def _reason_frame(df: pd.DataFrame) -> pd.DataFrame:
    codes = df["risk_codes"].apply(_split_codes)
    out = df.copy()
    out["reason_extremely_high_odds"] = codes.apply(lambda xs: "HIGH_ODDS_VOLATILE" in xs)
    out["reason_low_confidence"] = codes.apply(lambda xs: "LOW_CONFIDENCE" in xs)
    out["reason_sparse_signal"] = codes.apply(lambda xs: "LOW_SAMPLE_MODEL" in xs)
    out["reason_no_real_odds"] = codes.apply(lambda xs: "NO_REAL_ODDS" in xs)
    out["reason_data_missing"] = codes.apply(lambda xs: "DATA_MISSING" in xs)
    out["reason_payout_outlier"] = out["ev_risk_flag"]
    out["reason_low_ev_quality"] = False
    out["reason_high_variance"] = False
    out["reason_other_internal"] = False
    out["risk_reason_codes"] = out.apply(
        lambda row: "|".join(reason for reason in REASON_ORDER if bool(row.get(f"reason_{reason}", False))),
        axis=1,
    )
    return out


def _count_scope(df: pd.DataFrame, scope: str) -> list[dict[str, object]]:
    rows = []
    for reason in REASON_ORDER:
        rows.append(
            {
                "scope": scope,
                "reason_code": reason,
                "count": int(df[f"reason_{reason}"].sum()) if f"reason_{reason}" in df.columns else 0,
            }
        )
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ev_df = pd.read_csv(EV_PATH, low_memory=False)
    outcomes = build_race_outcomes(HIST_PATH)
    evaluator = StrategyEvaluator(config_path=str(ROOT / "config" / "strategy_config.json"))

    actual_rank_df = (
        ev_df.merge(
            outcomes[["race_id", "actual_trifecta"]],
            left_on=["race_id", "trifecta"],
            right_on=["race_id", "actual_trifecta"],
            how="inner",
        )[["race_id", "candidate_rank_by_sort"]]
        .rename(columns={"candidate_rank_by_sort": "actual_rank"})
        .drop_duplicates(subset=["race_id"])
    )

    candidates = ev_df.merge(actual_rank_df, on="race_id", how="left").copy()
    candidates["ev_risk_flag"] = candidates["risk_flag"].fillna(False).astype(bool)
    candidates["risk_codes"] = candidates.apply(lambda row: "|".join(evaluator._build_risk_codes(row)), axis=1)
    candidates["risk_penalty"] = candidates["risk_codes"].apply(
        lambda text: evaluator._risk_penalty(_split_codes(text))
    )
    candidates = _reason_frame(candidates)
    candidates["actual_rank_le5"] = pd.to_numeric(candidates["actual_rank"], errors="coerce").fillna(999) <= 5
    candidates["actual_rank_le3"] = pd.to_numeric(candidates["actual_rank"], errors="coerce").fillna(999) <= 3
    candidates["candidate_rank_top3"] = (
        pd.to_numeric(candidates["candidate_rank_by_sort"], errors="coerce").fillna(999).astype(int) <= 3
    )
    candidates["calibrated_high"] = pd.to_numeric(candidates["calibrated_hit_prob"], errors="coerce").fillna(0.0) >= 0.05
    candidates["odds_over_200"] = pd.to_numeric(candidates["odds"], errors="coerce").fillna(0.0) > 200.0

    risk_rows = candidates[candidates["ev_risk_flag"]].copy()
    risk_rows.to_csv(OUT_DIR / "risk_flag_candidate_rows.csv", index=False)

    scopes = {
        "all": risk_rows,
        "actual_rank_le5": risk_rows[risk_rows["actual_rank_le5"]],
        "actual_rank_le3": risk_rows[risk_rows["actual_rank_le3"]],
        "candidate_rank_top3": risk_rows[risk_rows["candidate_rank_top3"]],
        "calibrated_high": risk_rows[risk_rows["calibrated_high"]],
        "odds_over_200": risk_rows[risk_rows["odds_over_200"]],
    }

    count_rows = []
    for scope_name, scope_df in scopes.items():
        count_rows.extend(_count_scope(scope_df, scope_name))
    counts_df = pd.DataFrame(count_rows)
    counts_df.to_csv(OUT_DIR / "risk_reason_counts.csv", index=False)

    near_miss = pd.read_csv(NEAR_MISS_PATH).rename(columns={"combo": "trifecta", "buy_final_score": "final_score"})
    near_miss_risk = risk_rows.merge(
        near_miss[
            [
                "race_id",
                "trifecta",
                "drop_conditions",
                "primary_reason",
                "threshold_gap",
                "final_score",
            ]
        ],
        on=["race_id", "trifecta"],
        how="inner",
    )
    near_miss_risk["other_gates"] = near_miss_risk.apply(
        lambda row: "|".join(
            gate
            for gate, cond in [
                ("odds_cap", pd.to_numeric(row.get("odds"), errors="coerce") > 200.0),
                ("calibrated_hit_prob", pd.to_numeric(row.get("calibrated_hit_prob"), errors="coerce") < 0.05),
                ("candidate_rank_gt3", pd.to_numeric(row.get("candidate_rank_by_sort"), errors="coerce") > 3),
            ]
            if bool(cond)
        ),
        axis=1,
    )
    near_miss_risk = near_miss_risk[
        [
            "race_id",
            "trifecta",
            "actual_rank",
            "candidate_rank_by_sort",
            "calibrated_hit_prob",
            "final_score",
            "odds",
            "risk_reason_codes",
            "other_gates",
            "drop_conditions",
            "primary_reason",
            "threshold_gap",
        ]
    ].rename(columns={"trifecta": "combo", "risk_reason_codes": "risk_reasons"})
    near_miss_risk.to_csv(OUT_DIR / "near_miss_risk_rows.csv", index=False)

    high_signal = risk_rows[
        risk_rows["actual_rank_le5"] & risk_rows["candidate_rank_top3"]
    ].merge(
        near_miss[["race_id", "trifecta", "final_score", "drop_conditions", "primary_reason", "threshold_gap"]],
        on=["race_id", "trifecta"],
        how="left",
    )
    high_signal["other_gates"] = high_signal.apply(
        lambda row: "|".join(
            gate
            for gate, cond in [
                ("odds_cap", pd.to_numeric(row.get("odds"), errors="coerce") > 200.0),
                ("calibrated_hit_prob", pd.to_numeric(row.get("calibrated_hit_prob"), errors="coerce") < 0.05),
                ("candidate_rank_gt3", pd.to_numeric(row.get("candidate_rank_by_sort"), errors="coerce") > 3),
            ]
            if bool(cond)
        ),
        axis=1,
    )
    high_signal = high_signal[
        [
            "race_id",
            "trifecta",
            "actual_rank",
            "candidate_rank_by_sort",
            "calibrated_hit_prob",
            "final_score",
            "odds",
            "risk_reason_codes",
            "other_gates",
            "drop_conditions",
            "primary_reason",
            "threshold_gap",
        ]
    ].rename(columns={"trifecta": "combo", "risk_reason_codes": "risk_reasons"})
    high_signal.to_csv(OUT_DIR / "high_signal_risk_rows.csv", index=False)

    summary = {
        "risk_flag_true_count": int(len(risk_rows)),
        "scope_sizes": {name: int(len(df)) for name, df in scopes.items()},
        "near_miss_risk_count": int(len(near_miss_risk)),
        "high_signal_risk_count": int(len(high_signal)),
    }
    (OUT_DIR / "risk_flag_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
