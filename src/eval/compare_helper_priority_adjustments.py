import argparse
import json
from pathlib import Path

import pandas as pd

from src.eval.backtest_buy_skip import build_race_outcomes, prediction_match_key, run_backtest


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE_PATH = ROOT / "reports" / "task9_payout_outlier_local_rescue" / "baseline_skip_decisions.csv"
DEFAULT_CURRENT_PATH = ROOT / "reports" / "task9_payout_outlier_local_rescue" / "current_final_score_skip_decisions.csv"
DEFAULT_HELPER_DIAG_PATH = ROOT / "reports" / "task10_payout_helper_funnel" / "helper_candidate_stage_diagnostics.csv"
DEFAULT_HELPER_IMPORTANT_PATH = ROOT / "reports" / "task10_payout_helper_funnel" / "important_helper_candidates.csv"
DEFAULT_EV_PATH = ROOT / "reports" / "task1_after_calib" / "ev_analysis.csv"
DEFAULT_HIST_PATH = ROOT / "data" / "processed" / "historical_races.csv"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "task11_helper_priority_adjustments"
MAX_BUY_COUNT = 5


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def _num(value: object, default: float = 0.0) -> float:
    num = pd.to_numeric(value, errors="coerce")
    if pd.isna(num):
        return float(default)
    return float(num)


def _load_actual_rank(ev_df: pd.DataFrame, outcomes_df: pd.DataFrame) -> pd.DataFrame:
    merged = ev_df.merge(
        outcomes_df[["race_id", "actual_trifecta"]],
        left_on=["race_id", "trifecta"],
        right_on=["race_id", "actual_trifecta"],
        how="inner",
    )
    return (
        merged[["race_id", "candidate_rank_by_sort"]]
        .rename(columns={"candidate_rank_by_sort": "actual_rank"})
        .drop_duplicates(subset=["race_id"])
    )


def _sort_key(row: pd.Series, score_col: str) -> tuple[float, ...]:
    return (
        _num(row.get(score_col), default=float("-inf")),
        _num(row.get("buy_final_score"), default=float("-inf")),
        _num(row.get("race_score")),
        _num(row.get("pre_race_score")),
        _num(row.get("first_place_score")),
        _num(row.get("ev")),
        _num(row.get("approx_prob")),
        _num(row.get("first_win_proba")),
        -_num(row.get("risk_penalty")),
    )


def _prepare_current_rows(current_df: pd.DataFrame) -> pd.DataFrame:
    work = current_df.copy()
    work["buy_eligible"] = work["buy_eligible"].apply(_bool)
    work["watch_eligible"] = work["watch_eligible"].apply(_bool)
    work["is_helper_candidate"] = False
    work["helper_priority_bonus"] = 0.0
    work["helper_priority_rule"] = ""
    work["candidate_key"] = work["race_id"].astype(str) + "|" + work["recommended_trifecta"].astype(str)
    work["normalized_race_key"] = work["race_id"].apply(prediction_match_key).fillna(work.get("normalized_race_key"))
    return work


def _prepare_helper_rows(
    helper_diag: pd.DataFrame,
    ev_df: pd.DataFrame,
    current_df: pd.DataFrame,
) -> pd.DataFrame:
    helper = helper_diag.copy()
    helper = helper.rename(columns={"combo": "recommended_trifecta"})
    if "final_score" in helper.columns and "buy_final_score" not in helper.columns:
        helper["buy_final_score"] = pd.to_numeric(helper["final_score"], errors="coerce")
    helper = helper[(helper["hard_skip"].apply(_bool)) & (helper["payout_outlier_buy"].apply(_bool))].copy()
    helper["candidate_key"] = helper["race_id"].astype(str) + "|" + helper["recommended_trifecta"].astype(str)
    ev_cols = [
        "race_id",
        "trifecta",
        "date",
        "first_win_proba",
        "approx_prob",
        "ev",
        "odds",
        "first_place_score",
    ]
    helper = helper.merge(
        ev_df[ev_cols].rename(columns={"trifecta": "recommended_trifecta"}),
        on=["race_id", "recommended_trifecta"],
        how="left",
        suffixes=("", "_ev"),
    )
    helper["recommended_trifecta"] = helper["recommended_trifecta"].astype(str)
    current_meta = current_df[
        [
            "race_id",
            "date",
            "watch_eligible",
            "normalized_race_key",
            "reason",
        ]
    ].drop_duplicates(subset=["race_id"])
    helper = helper.merge(current_meta, on="race_id", how="left", suffixes=("", "_current"))
    helper["date"] = helper["date"].fillna(helper["date_current"])
    helper["watch_eligible"] = helper["watch_eligible"].fillna(True)
    helper["buy_eligible"] = True
    helper["decision"] = "WATCH"
    helper["is_helper_candidate"] = True
    helper["helper_priority_bonus"] = 0.0
    helper["helper_priority_rule"] = "base_helper"
    helper["reason"] = helper["reason"].fillna("helper priority simulation")
    helper["payout_outlier_rescue_target"] = True
    helper["normalized_race_key"] = helper["normalized_race_key"].fillna(helper["race_id"].apply(prediction_match_key))
    helper["risk_penalty"] = 0.0
    return helper[
        [
            "race_id",
            "date",
            "decision",
            "recommended_trifecta",
            "buy_eligible",
            "watch_eligible",
            "buy_final_score",
            "race_score",
            "pre_race_score",
            "first_place_score",
            "calibrated_hit_prob",
            "odds",
            "ev",
            "approx_prob",
            "first_win_proba",
            "actual_rank",
            "candidate_rank_by_sort",
            "reason",
            "normalized_race_key",
            "candidate_key",
            "is_helper_candidate",
            "helper_priority_bonus",
            "helper_priority_rule",
            "payout_outlier_rescue_target",
            "risk_penalty",
        ]
    ].copy()


def _adjust_bonus(row: pd.Series, variant: str) -> tuple[float, str]:
    if not _bool(row.get("is_helper_candidate")):
        return 0.0, ""
    final_score = _num(row.get("buy_final_score"))
    calibrated = _num(row.get("calibrated_hit_prob"))
    odds = _num(row.get("odds"))
    if variant == "helper_soft_bonus":
        return 0.08, "helper_soft_bonus"
    if variant == "helper_low_odds_high_signal":
        if odds <= 50.0 and calibrated >= 0.18 and final_score >= 0.45:
            return 0.12, "low_odds_high_signal"
        if odds <= 60.0 and calibrated >= 0.15 and final_score >= 0.40:
            return 0.06, "near_tie_signal"
    return 0.0, ""


def _apply_variant_scores(rows: pd.DataFrame, variant: str) -> pd.DataFrame:
    work = rows.copy()
    bonuses = work.apply(lambda row: _adjust_bonus(row, variant), axis=1)
    work["helper_priority_bonus"] = [float(x[0]) for x in bonuses]
    work["helper_priority_rule"] = [str(x[1]) for x in bonuses]
    work["priority_score"] = pd.to_numeric(work["buy_final_score"], errors="coerce").fillna(0.0) + work["helper_priority_bonus"]
    return work


def _select_race_rows(current_rows: pd.DataFrame, helper_rows: pd.DataFrame, variant: str) -> pd.DataFrame:
    current = _apply_variant_scores(current_rows, variant)
    helper = _apply_variant_scores(helper_rows, variant)
    selected_rows: list[pd.Series] = []
    helper_map = {
        str(race_id): group.copy()
        for race_id, group in helper.groupby("race_id", sort=False)
    }
    for _, cur in current.iterrows():
        race_id = str(cur["race_id"])
        incumbent = cur.copy()
        incumbent["selected_source"] = "current"
        candidates = [incumbent]
        if race_id in helper_map:
            for _, helper_row in helper_map[race_id].iterrows():
                cand = helper_row.copy()
                cand["selected_source"] = "helper"
                candidates.append(cand)
        best = max(candidates, key=lambda row: _sort_key(row, "priority_score"))
        best = best.copy()
        best["incumbent_combo"] = str(cur["recommended_trifecta"])
        best["incumbent_buy_final_score"] = _num(cur.get("buy_final_score"))
        best["incumbent_priority_score"] = _num(cur.get("priority_score", cur.get("buy_final_score")))
        selected_rows.append(best)
    return pd.DataFrame(selected_rows)


def _apply_buy_cap(selected_rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = selected_rows.reset_index(drop=True).copy()
    work["decision"] = work["decision"].astype(str)
    buy_pool = work[work["buy_eligible"].apply(_bool)].copy()
    buy_pool = buy_pool.sort_values(
        by=["priority_score", "buy_final_score", "race_score", "pre_race_score", "first_place_score", "ev", "approx_prob", "first_win_proba"],
        ascending=[False, False, False, False, False, False, False, False],
        kind="mergesort",
    ).reset_index(drop=True)
    buy_pool["priority_rank"] = buy_pool.index + 1
    buy_pool["within_max_buy_count"] = buy_pool["priority_rank"] <= MAX_BUY_COUNT

    top_keys = set(buy_pool.loc[buy_pool["within_max_buy_count"], "candidate_key"].astype(str))
    for idx, row in work.iterrows():
        key = str(row["candidate_key"])
        if _bool(row["buy_eligible"]):
            if key in top_keys:
                work.at[idx, "decision"] = "BUY"
            elif _bool(row.get("watch_eligible", False)):
                work.at[idx, "decision"] = "WATCH"
            else:
                work.at[idx, "decision"] = "SKIP"
    return work, buy_pool


def _build_prediction_frame(rows: pd.DataFrame) -> pd.DataFrame:
    pred = rows.copy()
    pred["recommended_trifecta"] = pred["recommended_trifecta"].astype(str)
    pred["predicted_trifecta"] = pred["recommended_trifecta"]
    pred["normalized_race_key"] = pred["normalized_race_key"].fillna(pred["race_id"].apply(prediction_match_key))
    return pred


def _metric_pack(summary: dict) -> dict[str, object]:
    return {
        "buy_count": int(summary.get("buy_count", 0) or 0),
        "hit_count": int(summary.get("hit_count", 0) or 0),
        "hit_rate": summary.get("hit_rate"),
        "roi": summary.get("roi"),
        "unit_max_drawdown": summary.get("max_drawdown"),
        "max_losing_streak": int(summary.get("max_consecutive_loss", 0) or 0),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare local helper priority adjustments without changing buy logic")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE_PATH))
    parser.add_argument("--current", default=str(DEFAULT_CURRENT_PATH))
    parser.add_argument("--helper-diag", default=str(DEFAULT_HELPER_DIAG_PATH))
    parser.add_argument("--helper-important", default=str(DEFAULT_HELPER_IMPORTANT_PATH))
    parser.add_argument("--ev-analysis", default=str(DEFAULT_EV_PATH))
    parser.add_argument("--historical", default=str(DEFAULT_HIST_PATH))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    baseline_df = pd.read_csv(args.baseline, low_memory=False)
    current_df = pd.read_csv(args.current, low_memory=False)
    helper_diag = pd.read_csv(args.helper_diag, low_memory=False)
    helper_important = pd.read_csv(args.helper_important, low_memory=False)
    ev_df = pd.read_csv(args.ev_analysis, low_memory=False)
    outcomes = build_race_outcomes(Path(args.historical))
    actual_rank_df = _load_actual_rank(ev_df, outcomes)
    ev_df = ev_df.merge(actual_rank_df, on="race_id", how="left")

    current_rows = _prepare_current_rows(current_df)
    helper_rows = _prepare_helper_rows(helper_diag, ev_df, current_rows)

    current_buys = current_rows[current_rows["decision"].astype(str) == "BUY"].copy()
    current_buys = current_buys.sort_values("buy_final_score", ascending=False, kind="mergesort").reset_index(drop=True)
    current_buys["current_buy_rank"] = current_buys.index + 1
    current_buys["current_status"] = "current_buy"

    helper_compare = helper_rows.merge(
        current_rows[
            [
                "race_id",
                "recommended_trifecta",
                "decision",
                "buy_final_score",
                "race_score",
                "calibrated_hit_prob",
                "odds",
            ]
        ].rename(
            columns={
                "recommended_trifecta": "current_combo",
                "decision": "current_decision",
                "buy_final_score": "current_buy_final_score",
                "race_score": "current_race_score",
                "calibrated_hit_prob": "current_calibrated_hit_prob",
                "odds": "current_odds",
            }
        ),
        on="race_id",
        how="left",
    )
    cutoff_score = _num(current_buys["buy_final_score"].min()) if not current_buys.empty else 0.0
    cutoff_combo = ""
    if not current_buys.empty:
        cutoff_row = current_buys.iloc[current_buys["buy_final_score"].astype(float).idxmin() if False else len(current_buys) - 1]
        cutoff_combo = f"{cutoff_row['race_id']}:{cutoff_row['recommended_trifecta']}"
    helper_compare["current_cutoff_score"] = cutoff_score
    helper_compare["current_cutoff_combo"] = cutoff_combo
    helper_compare["global_gap_to_cutoff"] = helper_compare["buy_final_score"].apply(_num) - cutoff_score
    helper_compare["local_gap_vs_current"] = helper_compare["buy_final_score"].apply(_num) - helper_compare["current_buy_final_score"].apply(_num)
    helper_compare["is_current_buy"] = False
    helper_compare["pushed_by_current_buy5"] = " | ".join(
        f"{row['race_id']}:{row['recommended_trifecta']}({float(row['buy_final_score']):.3f})"
        for _, row in current_buys.iterrows()
    )
    helper_compare = helper_compare[
        [
            "race_id",
            "recommended_trifecta",
            "actual_rank",
            "candidate_rank_by_sort",
            "race_score",
            "buy_final_score",
            "calibrated_hit_prob",
            "odds",
            "payout_outlier_rescue_target",
            "is_current_buy",
            "current_combo",
            "current_decision",
            "current_buy_final_score",
            "current_race_score",
            "global_gap_to_cutoff",
            "local_gap_vs_current",
            "pushed_by_current_buy5",
        ]
    ].rename(
        columns={
            "recommended_trifecta": "combo",
            "buy_final_score": "final_score",
            "payout_outlier_rescue_target": "is_payout_helper",
        }
    )
    helper_compare.to_csv(output_dir / "helper_vs_current_comparison.csv", index=False)

    important_keys = set(
        helper_important.apply(lambda row: f"{row['race_id']}|{row['combo']}", axis=1).tolist()
    )
    helper_compare["candidate_key"] = helper_compare["race_id"].astype(str) + "|" + helper_compare["combo"].astype(str)
    helper_compare[helper_compare["candidate_key"].isin(important_keys)].drop(columns=["candidate_key"]).to_csv(
        output_dir / "helper_vs_current_important.csv",
        index=False,
    )

    current_buys_compare = current_buys[
        [
            "race_id",
            "recommended_trifecta",
            "buy_final_score",
            "race_score",
            "calibrated_hit_prob",
            "odds",
        ]
    ].rename(columns={"recommended_trifecta": "combo", "buy_final_score": "final_score"})
    current_buys_compare["actual_rank"] = pd.NA
    current_buys_compare["candidate_rank_by_sort"] = pd.NA
    current_buys_compare["is_payout_helper"] = False
    current_buys_compare["is_current_buy"] = True
    current_buys_compare.to_csv(output_dir / "current_buy5_comparison.csv", index=False)

    variants = {
        "current_final_score": current_rows.copy(),
        "helper_soft_bonus": None,
        "helper_low_odds_high_signal": None,
    }
    variant_buy_pools: dict[str, pd.DataFrame] = {}
    variant_rows: dict[str, pd.DataFrame] = {"current_final_score": current_rows.copy()}

    for variant_name in ["helper_soft_bonus", "helper_low_odds_high_signal"]:
        selected = _select_race_rows(current_rows, helper_rows, variant_name)
        adjusted_rows, buy_pool = _apply_buy_cap(selected)
        variant_rows[variant_name] = adjusted_rows
        variant_buy_pools[variant_name] = buy_pool
        adjusted_rows.to_csv(output_dir / f"{variant_name}_skip_decisions.csv", index=False)

    current_pred = _build_prediction_frame(current_rows)
    baseline_pred = _build_prediction_frame(baseline_df.copy())
    baseline_pred["predicted_trifecta"] = baseline_pred["recommended_trifecta"].astype(str)
    baseline_pred["normalized_race_key"] = baseline_pred["race_id"].apply(prediction_match_key).fillna(
        baseline_pred.get("normalized_race_key")
    )

    _, baseline_summary = run_backtest(baseline_pred, outcomes, stake_mode="flat", flat_stake=1.0)
    _, current_summary = run_backtest(current_pred, outcomes, stake_mode="flat", flat_stake=1.0)

    comparison_rows = [
        {"variant": "baseline", **_metric_pack(baseline_summary)},
        {"variant": "current_final_score", **_metric_pack(current_summary)},
    ]
    variant_summaries = {
        "baseline": baseline_summary,
        "current_final_score": current_summary,
    }

    helper_adoption_rows: list[dict[str, object]] = []
    current_buy_keys = set(current_buys["candidate_key"].astype(str).tolist())

    for variant_name in ["helper_soft_bonus", "helper_low_odds_high_signal"]:
        pred = _build_prediction_frame(variant_rows[variant_name])
        _, summary = run_backtest(pred, outcomes, stake_mode="flat", flat_stake=1.0)
        comparison_rows.append({"variant": variant_name, **_metric_pack(summary)})
        variant_summaries[variant_name] = summary

        variant_buy = variant_rows[variant_name][variant_rows[variant_name]["decision"] == "BUY"].copy()
        variant_buy["candidate_key"] = variant_buy["candidate_key"].astype(str)
        helper_in_buy = variant_buy[variant_buy["is_helper_candidate"].apply(_bool)].copy()
        removed_current = current_buys[~current_buys["candidate_key"].isin(set(variant_buy["candidate_key"]))].copy()
        helper_adoption_rows.append(
            {
                "variant": variant_name,
                "helper_in_buy_count": int(len(helper_in_buy)),
                "helper_in_buy_keys": " | ".join(helper_in_buy["candidate_key"].tolist()),
                "replaced_current_buy_keys": " | ".join(removed_current["candidate_key"].astype(str).tolist()),
            }
        )
        variant_buy.to_csv(output_dir / f"{variant_name}_buy_rows.csv", index=False)

    comparison_df = pd.DataFrame(comparison_rows)
    comparison_df.to_csv(output_dir / "priority_adjustment_comparison.csv", index=False)
    pd.DataFrame(helper_adoption_rows).to_csv(output_dir / "helper_adoption_summary.csv", index=False)

    helper_variant_rows = []
    for variant_name in ["current_final_score", "helper_soft_bonus", "helper_low_odds_high_signal"]:
        frame = current_rows if variant_name == "current_final_score" else variant_rows[variant_name]
        frame = frame.copy()
        frame["candidate_key"] = frame["candidate_key"].astype(str)
        for _, row in helper_rows.iterrows():
            key = str(row["candidate_key"])
            selected = frame[frame["candidate_key"] == key]
            is_final_buy = bool(not selected.empty and str(selected.iloc[0].get("decision", "")) == "BUY")
            helper_variant_rows.append(
                {
                    "variant": variant_name,
                    "race_id": row["race_id"],
                    "combo": row["recommended_trifecta"],
                    "final_score": _num(row.get("buy_final_score")),
                    "calibrated_hit_prob": _num(row.get("calibrated_hit_prob")),
                    "odds": _num(row.get("odds")),
                    "in_top5_window": is_final_buy,
                    "selected_as_race_row": bool(not selected.empty and _bool(selected.iloc[0].get("is_helper_candidate", False))),
                    "decision": "" if selected.empty else str(selected.iloc[0].get("decision", "")),
                }
            )
    pd.DataFrame(helper_variant_rows).to_csv(output_dir / "helper_variant_adoption_matrix.csv", index=False)

    gate_ranking = [
        {
            "rank": 1,
            "target": "採用順調整",
            "reason": "helper 31件の落選主因が race-level採用順 13件と max_buy_count 15件で、buy 条件側は 0件だから。",
        },
        {
            "rank": 2,
            "target": "max_buy_count",
            "reason": "局所 bonus でも helper が race を通ると、次の壁は 5件 cap になるため。",
        },
        {
            "rank": 3,
            "target": "hard skip",
            "reason": "件数は 3件だけで、全体ボトルネックとしては優先度が低いから。",
        },
    ]

    summary = {
        "inputs": {
            "baseline": str(args.baseline),
            "current": str(args.current),
            "helper_diag": str(args.helper_diag),
            "ev_analysis": str(args.ev_analysis),
        },
        "current_buy_rows": current_buys_compare.to_dict(orient="records"),
        "comparison_metrics": comparison_df.to_dict(orient="records"),
        "helper_adoption_summary": helper_adoption_rows,
        "gate_ranking": gate_ranking,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
