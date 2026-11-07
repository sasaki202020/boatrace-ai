import json
import os
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from src.eval.backtest_buy_skip import build_race_outcomes
from src.strategy.evaluate_ev_and_skip import StrategyEvaluator


ROOT = Path(__file__).resolve().parents[2]
EV_PATH = ROOT / "reports" / "task1_after_calib" / "ev_analysis.csv"
HIST_PATH = ROOT / "data" / "processed" / "historical_races.csv"
NEAR_MISS_PATH = ROOT / "reports" / "task5_buy_eligible_diagnostic" / "buy_eligible_near_miss_rows.csv"
OUT_DIR = ROOT / "reports" / "task7_risk_flag_diagnostic"


REASON_CODE_ORDER = [
    "extremely_high_odds",
    "low_confidence",
    "low_ev_quality",
    "high_variance",
    "payout_outlier",
    "sparse_signal",
    "no_real_odds",
    "data_missing",
    "other_internal",
]


def _safe_bool_series(frame: pd.DataFrame, col: str) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(False, index=frame.index, dtype=bool)
    return frame[col].fillna(False).astype(bool)


@contextmanager
def _env_override(mapping: dict[str, object]):
    old: dict[str, object] = {}
    missing = object()
    for key, value in mapping.items():
        old[key] = os.environ.get(key, missing)
        os.environ[key] = str(value)
    try:
        yield
    finally:
        for key, previous in old.items():
            if previous is missing:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def _split_codes(value: object) -> list[str]:
    if pd.isna(value):
        return []
    text = str(value).strip()
    if not text:
        return []
    return [part for part in text.split("|") if part]


def _map_reason_flags(frame: pd.DataFrame) -> pd.DataFrame:
    codes_series = frame["risk_codes"].apply(_split_codes)
    frame["reason_extremely_high_odds"] = codes_series.apply(lambda xs: "HIGH_ODDS_VOLATILE" in xs)
    frame["reason_low_confidence"] = codes_series.apply(lambda xs: "LOW_CONFIDENCE" in xs)
    frame["reason_sparse_signal"] = codes_series.apply(lambda xs: "LOW_SAMPLE_MODEL" in xs)
    frame["reason_no_real_odds"] = codes_series.apply(lambda xs: "NO_REAL_ODDS" in xs)
    frame["reason_data_missing"] = codes_series.apply(lambda xs: "DATA_MISSING" in xs)
    frame["reason_low_ev_quality"] = False
    frame["reason_high_variance"] = False
    frame["reason_payout_outlier"] = _safe_bool_series(frame, "ev_risk_flag")
    frame["reason_other_internal"] = False
    frame["risk_reason_codes"] = frame.apply(
        lambda row: "|".join(
            reason
            for reason in REASON_CODE_ORDER
            if bool(row.get(f"reason_{reason}", False))
        ),
        axis=1,
    )
    return frame


def _reason_count_rows(frame: pd.DataFrame, scope: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for reason in REASON_CODE_ORDER:
        rows.append(
            {
                "scope": scope,
                "reason_code": reason,
                "count": int(_safe_bool_series(frame, f"reason_{reason}").sum()),
            }
        )
    return rows


def _primary_reason_counts(frame: pd.DataFrame) -> dict[str, int]:
    counts = {reason: 0 for reason in REASON_CODE_ORDER}
    for codes in frame["risk_reason_codes"].fillna(""):
        first = next((part for part in str(codes).split("|") if part), "other_internal")
        if first not in counts:
            first = "other_internal"
        counts[first] += 1
    return counts


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


def _collect_current_decisions(
    ev_df: pd.DataFrame, race_boat_counts: dict[str, int]
) -> tuple[StrategyEvaluator, pd.DataFrame]:
    envs = {
        "RANK_RESCUE_TOP_N": 3,
        "RANK_RESCUE_EV_RELAXATION": 0.05,
        "RANK_RESCUE_MIN_CALIBRATED_HIT_PROB": 0.05,
        "BUY_FINAL_SCORE_ENABLED": 1,
        "BUY_FINAL_SCORE_RACE_WEIGHT": 0.70,
        "BUY_FINAL_SCORE_CAL_WEIGHT": 0.20,
        "BUY_FINAL_SCORE_RANK_WEIGHT": 0.10,
        "BUY_FINAL_SCORE_RANK_TOP_N": 3,
        "NEAR_CAP_RESCUE_ENABLED": 0,
    }
    with _env_override(envs):
        evaluator = StrategyEvaluator(config_path=str(ROOT / "config" / "strategy_config.json"))
        decisions = evaluator.build_skip_decisions(ev_df.copy(), race_boat_counts=race_boat_counts)
    return evaluator, decisions


def _fill_final_score_columns(frame: pd.DataFrame, evaluator: StrategyEvaluator) -> pd.DataFrame:
    if "buy_final_score" not in frame.columns:
        frame["buy_final_score"] = pd.NA
    if "buy_final_score_race_component" not in frame.columns:
        frame["buy_final_score_race_component"] = pd.NA
    if "buy_final_score_calibrated_component" not in frame.columns:
        frame["buy_final_score_calibrated_component"] = pd.NA
    if "buy_final_score_rank_component" not in frame.columns:
        frame["buy_final_score_rank_component"] = pd.NA

    for idx in frame.index[frame["buy_final_score"].isna()]:
        meta = evaluator._buy_final_score(frame.loc[idx])
        frame.at[idx, "buy_final_score"] = float(meta.get("buy_final_score", 0.0) or 0.0)
        frame.at[idx, "buy_final_score_race_component"] = float(
            meta.get("buy_final_score_race_component", 0.0) or 0.0
        )
        frame.at[idx, "buy_final_score_calibrated_component"] = float(
            meta.get("buy_final_score_calibrated_component", 0.0) or 0.0
        )
        frame.at[idx, "buy_final_score_rank_component"] = float(
            meta.get("buy_final_score_rank_component", 0.0) or 0.0
        )
    return frame


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ev_df = pd.read_csv(EV_PATH, low_memory=False)
    hist = pd.read_csv(HIST_PATH, low_memory=False)
    outcomes = build_race_outcomes(HIST_PATH)
    actual_rank_df = _build_actual_rank_df(ev_df, outcomes)
    race_boat_counts = hist.groupby("race_id")["lane"].nunique().to_dict()

    evaluator, decisions = _collect_current_decisions(ev_df, race_boat_counts)
    decisions = _fill_final_score_columns(
        decisions.merge(actual_rank_df, on="race_id", how="left"),
        evaluator,
    )

    candidate_df = ev_df.merge(actual_rank_df, on="race_id", how="left").copy()
    candidate_df["ev_risk_flag"] = _safe_bool_series(candidate_df, "risk_flag")
    candidate_df["risk_codes"] = candidate_df.apply(lambda row: "|".join(evaluator._build_risk_codes(row)), axis=1)
    candidate_df["risk_penalty"] = candidate_df["risk_codes"].apply(
        lambda text: evaluator._risk_penalty(_split_codes(text))
    )
    candidate_df = _map_reason_flags(candidate_df)
    candidate_df["is_high_calibrated"] = pd.to_numeric(
        candidate_df.get("calibrated_hit_prob"), errors="coerce"
    ).fillna(0.0) >= 0.05
    candidate_df["is_odds_over_200"] = pd.to_numeric(candidate_df.get("odds"), errors="coerce").fillna(0.0) > 200.0
    candidate_df["is_rank_top3"] = pd.to_numeric(
        candidate_df.get("candidate_rank_by_sort"), errors="coerce"
    ).fillna(999).astype(int) <= 3
    candidate_df["actual_rank_le5"] = pd.to_numeric(candidate_df.get("actual_rank"), errors="coerce").fillna(999) <= 5
    candidate_df["actual_rank_le3"] = pd.to_numeric(candidate_df.get("actual_rank"), errors="coerce").fillna(999) <= 3

    risk_rows = candidate_df[_safe_bool_series(candidate_df, "ev_risk_flag")].copy()

    scopes = {
        "all": risk_rows,
        "actual_rank_le5": risk_rows[risk_rows["actual_rank_le5"]].copy(),
        "actual_rank_le3": risk_rows[risk_rows["actual_rank_le3"]].copy(),
        "candidate_rank_top3": risk_rows[risk_rows["is_rank_top3"]].copy(),
        "calibrated_high": risk_rows[risk_rows["is_high_calibrated"]].copy(),
        "odds_over_200": risk_rows[risk_rows["is_odds_over_200"]].copy(),
    }

    reason_count_rows: list[dict[str, object]] = []
    primary_reason_counts: dict[str, dict[str, int]] = {}
    for scope_name, scope_df in scopes.items():
        reason_count_rows.extend(_reason_count_rows(scope_df, scope_name))
        primary_reason_counts[scope_name] = _primary_reason_counts(scope_df)

    reason_counts_df = pd.DataFrame(reason_count_rows)
    reason_counts_df.to_csv(OUT_DIR / "risk_reason_counts.csv", index=False)

    near_miss = pd.read_csv(NEAR_MISS_PATH).rename(columns={"combo": "trifecta"})
    high_signal = risk_rows[
        risk_rows["actual_rank_le5"]
        & risk_rows["is_rank_top3"]
    ].copy()
    high_signal = high_signal.merge(
        near_miss[
            [
                "race_id",
                "trifecta",
                "drop_conditions",
                "primary_reason",
                "threshold_gap",
                "buy_final_score",
            ]
        ],
        on=["race_id", "trifecta"],
        how="left",
    )
    high_signal["other_gates"] = high_signal.apply(
        lambda row: "|".join(
            gate
            for gate, cond in [
                ("odds_cap", pd.to_numeric(row.get("odds"), errors="coerce") > 200.0),
                ("race_gate_block", str(row.get("race_gate", "")) == "BLOCK"),
                ("first_place_block", bool(row.get("first_place_block", False))),
                ("pre_race_block", str(row.get("pre_race_gate", "")) == "BLOCK"),
                ("buy_eligible_false", not bool(row.get("buy_eligible", False))),
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
            "buy_final_score",
            "odds",
            "risk_reason_codes",
            "other_gates",
            "drop_conditions",
            "primary_reason",
            "threshold_gap",
        ]
    ].rename(
        columns={
            "trifecta": "combo",
            "buy_final_score": "final_score",
            "risk_reason_codes": "risk_reasons",
        }
    )
    high_signal = high_signal.sort_values(
        ["actual_rank", "candidate_rank_by_sort", "calibrated_hit_prob", "final_score"],
        ascending=[True, True, False, False],
        kind="mergesort",
    )
    high_signal.to_csv(OUT_DIR / "high_signal_risk_blocked_rows.csv", index=False)

    near_miss_risk = risk_rows.copy()
    near_miss_risk = near_miss_risk.merge(
        near_miss[
            [
                "race_id",
                "trifecta",
                "drop_conditions",
                "primary_reason",
                "threshold_gap",
                "buy_final_score",
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
                ("race_gate_block", str(row.get("race_gate", "")) == "BLOCK"),
                ("first_place_block", bool(row.get("first_place_block", False))),
                ("pre_race_block", str(row.get("pre_race_gate", "")) == "BLOCK"),
                ("buy_eligible_false", not bool(row.get("buy_eligible", False))),
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
            "buy_final_score",
            "odds",
            "risk_reason_codes",
            "other_gates",
            "drop_conditions",
            "primary_reason",
            "threshold_gap",
        ]
    ].rename(
        columns={
            "trifecta": "combo",
            "buy_final_score": "final_score",
            "risk_reason_codes": "risk_reasons",
        }
    )
    near_miss_risk.to_csv(OUT_DIR / "near_miss_risk_flag_rows.csv", index=False)

    risk_rows.to_csv(OUT_DIR / "risk_flag_rows.csv", index=False)

    top_risk_conditions = []
    for reason in REASON_CODE_ORDER:
        count = int(
            reason_counts_df[
                (reason_counts_df["scope"] == "actual_rank_le5")
                & (reason_counts_df["reason_code"] == reason)
            ]["count"].sum()
        )
        if count <= 0:
            continue
        top_risk_conditions.append({"reason_code": reason, "count": count})
    top_risk_conditions = sorted(top_risk_conditions, key=lambda x: (-x["count"], x["reason_code"]))[:3]

    summary = {
        "total_risk_flag_count": int(len(risk_rows)),
        "scope_sizes": {name: int(len(df)) for name, df in scopes.items()},
        "primary_reason_counts": primary_reason_counts,
        "top_risk_conditions_actual_rank_le5": top_risk_conditions,
        "near_miss_risk_flag_count": int(len(near_miss_risk)),
    }
    (OUT_DIR / "risk_flag_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
