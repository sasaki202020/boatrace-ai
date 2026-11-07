import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.eval.backtest_buy_skip import build_race_outcomes
from src.strategy.evaluate_ev_and_skip import StrategyEvaluator


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HELPER_PATH = ROOT / "reports" / "task9_payout_outlier_local_rescue" / "top3_d1_helper_candidates.csv"
DEFAULT_SKIP_PATH = ROOT / "reports" / "task9_payout_outlier_local_rescue" / "payout_rescue_top3_d1_skip_decisions.csv"
DEFAULT_EV_PATH = ROOT / "reports" / "task1_after_calib" / "ev_analysis.csv"
DEFAULT_HIST_PATH = ROOT / "data" / "processed" / "historical_races.csv"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "task10_payout_helper_funnel"

DROP_STAGE_ORDER = [
    "hard skip",
    "has_real_odds",
    "6艇立て条件",
    "odds cap",
    "race_gate",
    "pre_race_block",
    "first_place_block",
    "payout_outlier救済後buy判定",
    "race-level採用順",
    "max_buy_count=5",
    "adopted",
]


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "t", "yes", "y"}


def _to_num(value: object, default: float = 0.0) -> float:
    num = pd.to_numeric(value, errors="coerce")
    if pd.isna(num):
        return float(default)
    return float(num)


def _split_codes(value: object) -> list[str]:
    if pd.isna(value):
        return []
    text = str(value).strip()
    if not text:
        return []
    return [part for part in text.split("|") if part]


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


def _race_boat_counts_from_history(hist_path: Path, ev_df: pd.DataFrame) -> dict[str, int]:
    hist = pd.read_csv(hist_path, low_memory=False)
    if {"race_id", "lane"}.issubset(hist.columns):
        work = hist[["race_id", "lane"]].copy()
        work["lane"] = pd.to_numeric(work["lane"], errors="coerce")
        work = work.dropna(subset=["race_id", "lane"])
        counts = work.groupby("race_id")["lane"].nunique().astype(int).to_dict()
    else:
        counts = {}

    for race_id, group in ev_df.groupby("race_id", sort=False):
        if str(race_id) in counts:
            continue
        lanes: set[str] = set()
        for trifecta in group["trifecta"].dropna():
            lanes.update(str(trifecta).split("-"))
        counts[str(race_id)] = len(lanes)
    return {str(k): int(v) for k, v in counts.items()}


def _buy_sort_key(row: pd.Series) -> tuple[float, ...]:
    return (
        _to_num(row.get("buy_final_score"), default=float("-inf")),
        _to_num(row.get("race_score")),
        _to_num(row.get("pre_race_score")),
        _to_num(row.get("first_place_score")),
        _to_num(row.get("second_place_score")),
        _to_num(row.get("third_place_score")),
        _to_num(row.get("decision_score")),
        _to_num(row.get("ev")),
        _to_num(row.get("approx_prob")),
        _to_num(row.get("first_win_proba")),
        -_to_num(row.get("risk_penalty")),
    )


def _rescue_label(row: pd.Series) -> str:
    if _bool(row.get("payout_outlier_rescue_applied")):
        return "payout_outlier_rescue"
    if _bool(row.get("near_cap_rescue_applied")):
        return "near_cap_rescue"
    if _bool(row.get("rank_rescue_applied")):
        return "rank_rescue"
    return "top_row_or_normal"


def _first_false_name(flags: list[tuple[str, bool]]) -> str:
    for name, passed in flags:
        if not passed:
            return name
    return "adopted"


def _count_drop_stages(df: pd.DataFrame, scope: str) -> pd.DataFrame:
    work = (
        df["drop_stage"]
        .value_counts()
        .reindex(DROP_STAGE_ORDER, fill_value=0)
        .reset_index()
        .rename(columns={"index": "drop_stage", "count": "count"})
    )
    work["scope"] = scope
    return work[["scope", "drop_stage", "count"]]


def _top_stage_lines(df: pd.DataFrame) -> list[dict[str, object]]:
    counts = (
        df["drop_stage"]
        .value_counts()
        .reindex(DROP_STAGE_ORDER, fill_value=0)
        .reset_index()
        .rename(columns={"index": "drop_stage", "count": "count"})
    )
    counts["order"] = counts["drop_stage"].map({name: idx for idx, name in enumerate(DROP_STAGE_ORDER)})
    counts = counts[counts["count"] > 0].sort_values(["count", "order"], ascending=[False, True], kind="mergesort")
    return counts[["drop_stage", "count"]].to_dict(orient="records")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose payout helper funnel without changing selection logic")
    parser.add_argument("--helpers", default=str(DEFAULT_HELPER_PATH))
    parser.add_argument("--skip-decisions", default=str(DEFAULT_SKIP_PATH))
    parser.add_argument("--ev-analysis", default=str(DEFAULT_EV_PATH))
    parser.add_argument("--historical", default=str(DEFAULT_HIST_PATH))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    helper_path = Path(args.helpers)
    skip_path = Path(args.skip_decisions)
    ev_path = Path(args.ev_analysis)
    hist_path = Path(args.historical)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    evaluator = StrategyEvaluator(config_path=str(ROOT / "config" / "strategy_config.json"))
    evaluator.payout_outlier_rescue_enabled = True
    helper_df = pd.read_csv(helper_path, low_memory=False)
    helper_df = helper_df.rename(columns={"combo": "trifecta"})
    skip_df = pd.read_csv(skip_path, low_memory=False)
    ev_df = pd.read_csv(ev_path, low_memory=False)
    outcomes = build_race_outcomes(hist_path)

    actual_rank_df = _build_actual_rank_df(ev_df, outcomes)
    race_boat_counts = _race_boat_counts_from_history(hist_path, ev_df)
    ev_df = ev_df.merge(actual_rank_df, on="race_id", how="left").copy()

    helper_rows = helper_df.merge(
        ev_df,
        on=["race_id", "trifecta"],
        how="left",
        suffixes=("_helper", ""),
    ).copy()
    if helper_rows["date"].isna().any():
        missing = helper_rows[helper_rows["date"].isna()][["race_id", "trifecta"]]
        raise ValueError(f"helper rows missing in ev_analysis: {missing.to_dict(orient='records')}")

    skip_df["buy_eligible"] = skip_df["buy_eligible"].apply(_bool)
    skip_df["watch_eligible"] = skip_df["watch_eligible"].apply(_bool)
    skip_df["payout_outlier_rescue_applied"] = skip_df["payout_outlier_rescue_applied"].apply(_bool)
    skip_df["near_cap_rescue_applied"] = skip_df["near_cap_rescue_applied"].apply(_bool)
    skip_df["rank_rescue_applied"] = skip_df["rank_rescue_applied"].apply(_bool)

    skip_by_race = {str(row["race_id"]): row for _, row in skip_df.iterrows()}

    buy_pool = skip_df[skip_df["buy_eligible"]].copy()
    buy_pool["_sort_key"] = buy_pool.apply(_buy_sort_key, axis=1)
    buy_pool = buy_pool.sort_values("_sort_key", ascending=False, kind="mergesort").reset_index(drop=True)
    buy_pool["global_buy_rank"] = buy_pool.index + 1
    max_buy_count = int(evaluator.max_buy_count or 0)
    buy_pool["within_max_buy_count"] = buy_pool["global_buy_rank"] <= max_buy_count
    buy_pool_lookup = {
        (str(row["race_id"]), str(row["recommended_trifecta"])): row for _, row in buy_pool.iterrows()
    }
    top5_pushers = buy_pool.head(max_buy_count).copy()
    top5_pusher_text = " | ".join(
        f"{row['race_id']}:{row['recommended_trifecta']}({float(row['buy_final_score']):.3f})"
        for _, row in top5_pushers.iterrows()
    )
    cutoff_text = ""
    if len(top5_pushers) > 0:
        cutoff = top5_pushers.iloc[-1]
        cutoff_text = (
            f"{cutoff['race_id']}:{cutoff['recommended_trifecta']}"
            f"(buy_final_score={float(cutoff['buy_final_score']):.3f}, rank={int(cutoff['global_buy_rank'])})"
        )

    rows: list[dict[str, object]] = []
    for _, row in helper_rows.iterrows():
        race_id = str(row["race_id"])
        trifecta = str(row["trifecta"])
        race_group = ev_df[ev_df["race_id"] == race_id].copy()
        race_feat = (
            evaluator.pre_race_features[evaluator.pre_race_features["race_id"] == race_id]
            if not evaluator.pre_race_features.empty
            else evaluator.pre_race_features
        )
        actual_boats = int(race_boat_counts.get(race_id, 0))
        hard_skip_lt6 = bool(evaluator.skip_config.get("exclude_non_6_boats", False) and actual_boats < 6)
        hard_skip_missing = bool(
            pd.isna(row.get("ev")) or pd.isna(row.get("approx_prob")) or pd.isna(row.get("first_win_proba"))
        )
        hard_skip = hard_skip_lt6 or hard_skip_missing

        row_prob = _to_num(row.get("approx_prob"))
        calibrated_hit_prob = _to_num(row.get("calibrated_hit_prob"), default=row_prob * 0.7)
        row_ev = _to_num(row.get("ev"))
        row_odds = _to_num(row.get("odds"))
        row_odds_source = evaluator._normalize_odds_source(str(row.get("odds_source", "")))
        has_real_odds = row_odds_source == "real"

        pre_race_profile = evaluator._compute_pre_race_profile(row, race_feat)
        pre_race_block = _bool(pre_race_profile.get("pre_race_block", False))
        first_place_score = _to_num(row.get("first_place_score"))
        first_place_gate = str(row.get("first_place_gate", "MISSING") or "MISSING")
        first_place_block = bool(
            _bool(row.get("first_place_block", False)) or first_place_score < float(evaluator.first_place_block_threshold)
        )
        second_place_score = _to_num(row.get("second_place_score"))
        second_place_block = bool(
            _bool(row.get("second_place_block", False)) or second_place_score < float(evaluator.second_place_block_threshold)
        )
        third_place_score = _to_num(row.get("third_place_score"))
        third_place_block = bool(
            _bool(row.get("third_place_block", False)) or third_place_score < float(evaluator.third_place_block_threshold)
        )
        race_selection = evaluator._compute_race_selection_profile(
            row,
            race_feat,
            first_place_score,
            _to_num(pre_race_profile.get("pre_race_score")),
            has_real_odds,
        )
        race_gate = str(race_selection.get("race_gate", "MISSING") or "MISSING")
        race_block = _bool(race_selection.get("race_block", False))
        race_priority = _bool(race_selection.get("race_priority", False))
        race_score = _to_num(race_selection.get("race_score"))

        risk_codes = evaluator._build_risk_codes(row)
        risk_penalty = evaluator._risk_penalty(risk_codes)
        risk_flag = bool(row.get("risk_flag", False))
        only_payout_outlier = risk_flag and (len(risk_codes) == 0)

        odds_cap_pass = evaluator.max_odds_for_buy is None or row_odds <= float(evaluator.max_odds_for_buy)
        base_buy_eligible = (
            (not hard_skip)
            and (not pre_race_block)
            and (not first_place_block)
            and first_place_gate != "MISSING"
            and (not second_place_block)
            and (not third_place_block)
            and (not race_block)
            and race_priority
            and has_real_odds
            and row_ev >= float(evaluator.buy_min_ev)
            and row_prob >= float(evaluator.buy_min_approx_prob)
            and risk_penalty <= float(evaluator.buy_max_risk_penalty)
            and odds_cap_pass
            and (not evaluator.exclude_risk_flag_for_buy or risk_penalty == 0)
        )

        payout_rescue_ok, rescue_meta = evaluator._payout_outlier_rescue_candidate_ok(
            row,
            race_feat,
            hard_skip=hard_skip,
        )

        stage_flags = [
            ("hard skip", not hard_skip),
            ("has_real_odds", has_real_odds),
            ("6艇立て条件", actual_boats >= 6),
            ("odds cap", bool(odds_cap_pass)),
            ("race_gate", (not race_block) and race_priority),
            ("pre_race_block", not pre_race_block),
            ("first_place_block", (not first_place_block) and first_place_gate != "MISSING"),
            ("payout_outlier救済後buy判定", payout_rescue_ok),
        ]
        first_failed_stage = _first_false_name(stage_flags)

        selected_row = skip_by_race.get(race_id)
        if selected_row is None:
            raise ValueError(f"race_id missing in skip decisions: {race_id}")
        selected_combo = str(selected_row.get("recommended_trifecta", ""))
        same_combo_in_result = selected_combo == trifecta
        selected_by_race = same_combo_in_result and _bool(selected_row.get("payout_outlier_rescue_applied", False))
        final_compare_target = same_combo_in_result and _bool(selected_row.get("buy_eligible", False))

        buy_pool_row = buy_pool_lookup.get((race_id, trifecta))
        final_score_global_rank = int(buy_pool_row["global_buy_rank"]) if buy_pool_row is not None else np.nan
        within_max_buy_count = bool(buy_pool_row["within_max_buy_count"]) if buy_pool_row is not None else False

        if first_failed_stage != "adopted":
            drop_stage = first_failed_stage
        elif not same_combo_in_result:
            drop_stage = "race-level採用順"
        elif final_compare_target and (not within_max_buy_count):
            drop_stage = "max_buy_count=5"
        else:
            drop_stage = "adopted"

        rescue_fail_detail = "|".join(
            name
            for name, passed in [
                ("risk_flag", risk_flag),
                ("only_payout_outlier", only_payout_outlier),
                ("first_place_gate_present", first_place_gate != "MISSING"),
                ("second_place_block", not second_place_block),
                ("third_place_block", not third_place_block),
                ("race_priority", race_priority),
                ("row_prob", row_prob >= float(evaluator.buy_min_approx_prob)),
                ("candidate_rank_top_n", int(_to_num(row.get("candidate_rank_by_sort"), default=999)) <= int(evaluator.payout_outlier_rescue_top_n)),
                ("rescue_odds_cap", row_odds <= float(evaluator.payout_outlier_rescue_max_odds)),
                ("rescue_calibrated", calibrated_hit_prob >= float(evaluator.payout_outlier_rescue_min_calibrated_hit_prob)),
                ("ev_delta", 0.0 <= float(rescue_meta.get("ev_delta", np.nan)) <= float(evaluator.payout_outlier_rescue_max_ev_delta)),
                ("rescue_final_score", float(rescue_meta.get("buy_final_score", 0.0)) >= float(evaluator.payout_outlier_rescue_min_final_score)),
            ]
            if not passed
        )

        if drop_stage == "race-level採用順":
            pushed_out_by = (
                f"{selected_combo} / decision={selected_row.get('decision')} / "
                f"selector={_rescue_label(selected_row)} / buy_eligible={selected_row.get('buy_eligible')}"
            )
        elif drop_stage == "max_buy_count=5":
            pushed_out_by = f"cutoff={cutoff_text} / top5={top5_pusher_text}"
        elif drop_stage == "payout_outlier救済後buy判定":
            pushed_out_by = rescue_fail_detail or "rescue helper unmet"
        elif drop_stage == "hard skip":
            pushed_out_by = f"actual_boats={actual_boats}, missing_core={hard_skip_missing}"
        else:
            pushed_out_by = ""

        rows.append(
            {
                "race_id": race_id,
                "combo": trifecta,
                "actual_rank": _to_num(row.get("actual_rank"), default=np.nan),
                "candidate_rank_by_sort": int(_to_num(row.get("candidate_rank_by_sort"), default=999)),
                "calibrated_hit_prob": calibrated_hit_prob,
                "final_score": _to_num(row.get("final_score_helper", row.get("final_score", rescue_meta.get("buy_final_score", 0.0)))),
                "odds": row_odds,
                "hard_skip": not hard_skip,
                "has_real_odds": has_real_odds,
                "six_boats": actual_boats >= 6,
                "odds_cap": bool(odds_cap_pass),
                "race_gate": (not race_block) and race_priority,
                "pre_race_block": not pre_race_block,
                "first_place_block": (not first_place_block) and first_place_gate != "MISSING",
                "buy_eligible": bool(base_buy_eligible),
                "risk_flag": bool(only_payout_outlier),
                "payout_outlier_buy": bool(payout_rescue_ok),
                "selected_by_race": bool(selected_by_race),
                "same_combo_in_result": bool(same_combo_in_result),
                "race_selected_combo": selected_combo,
                "race_selected_decision": str(selected_row.get("decision", "")),
                "race_selected_rescue": _rescue_label(selected_row),
                "final_score_compare_target": bool(final_compare_target),
                "final_score_global_rank": final_score_global_rank,
                "within_max_buy_count": bool(within_max_buy_count),
                "drop_stage": drop_stage,
                "pushed_out_by": pushed_out_by,
                "actual_boats": actual_boats,
                "race_score": race_score,
                "pre_race_score": _to_num(pre_race_profile.get("pre_race_score")),
                "first_place_score": first_place_score,
                "risk_codes": "|".join(risk_codes),
                "risk_penalty": risk_penalty,
                "rescue_fail_detail": rescue_fail_detail,
            }
        )

    diag_df = pd.DataFrame(rows)
    diag_df["actual_rank"] = pd.to_numeric(diag_df["actual_rank"], errors="coerce")
    diag_df = diag_df.sort_values(
        ["actual_rank", "candidate_rank_by_sort", "final_score", "race_id", "combo"],
        ascending=[True, True, False, True, True],
        kind="mergesort",
    ).reset_index(drop=True)
    diag_df.to_csv(output_dir / "helper_candidate_stage_diagnostics.csv", index=False)

    funnel_summary = pd.DataFrame(
        [
            {"stage": "helper_total", "count": int(len(diag_df))},
            {"stage": "buy判定まで通過", "count": int(diag_df["payout_outlier_buy"].sum())},
            {"stage": "final_score比較対象", "count": int(diag_df["final_score_compare_target"].sum())},
            {"stage": "max_buy_count=5内", "count": int(diag_df["within_max_buy_count"].sum())},
        ]
    )
    funnel_summary.to_csv(output_dir / "helper_funnel_summary.csv", index=False)

    drop_counts = pd.concat(
        [
            _count_drop_stages(diag_df, "all"),
            _count_drop_stages(diag_df[diag_df["actual_rank"].le(5)], "actual_rank<=5"),
            _count_drop_stages(diag_df[diag_df["actual_rank"].le(3)], "actual_rank<=3"),
        ],
        ignore_index=True,
    )
    drop_counts.to_csv(output_dir / "helper_drop_stage_counts.csv", index=False)

    buy_vs_ranking = pd.DataFrame(
        [
            {
                "bucket": "buy判定で落ちた",
                "count": int((diag_df["hard_skip"] & (~diag_df["payout_outlier_buy"])).sum()),
            },
            {
                "bucket": "race-level採用順で落ちた",
                "count": int((diag_df["drop_stage"] == "race-level採用順").sum()),
            },
            {
                "bucket": "max_buy_countで落ちた",
                "count": int((diag_df["drop_stage"] == "max_buy_count=5").sum()),
            },
        ]
    )
    buy_vs_ranking.to_csv(output_dir / "helper_buy_vs_ranking_counts.csv", index=False)

    high_final_score_pushed = diag_df[
        diag_df["payout_outlier_buy"]
        & diag_df["selected_by_race"]
        & (~diag_df["within_max_buy_count"])
        & (diag_df["final_score"] >= float(evaluator.payout_outlier_rescue_min_final_score))
    ].copy()
    high_final_score_pushed.to_csv(output_dir / "high_final_score_but_pushed_out.csv", index=False)

    important_cols = [
        "race_id",
        "combo",
        "actual_rank",
        "candidate_rank_by_sort",
        "calibrated_hit_prob",
        "final_score",
        "odds",
        "drop_stage",
        "pushed_out_by",
    ]
    diag_df[important_cols].to_csv(output_dir / "important_helper_candidates.csv", index=False)

    gate_ranking = []
    top_stage_rows = _top_stage_lines(diag_df)
    for idx, item in enumerate(top_stage_rows[:3], start=1):
        stage = str(item["drop_stage"])
        count = int(item["count"])
        if stage == "race-level採用順":
            reason = "helper 条件は通っても、その race で別 combo が残り rescue 採用に到達していない。"
        elif stage == "max_buy_count=5":
            reason = "race 採用までは進んだが、全体 BUY cap の上位 final_score に負けている。"
        elif stage == "payout_outlier救済後buy判定":
            reason = "helper 以外の rescue 必須条件が満たせず、BUY に変換できていない。"
        else:
            reason = "早い段階の gate で多数が止まっており、後段を見る前に落ちている。"
        gate_ranking.append({"rank": idx, "stage": stage, "count": count, "reason": reason})

    summary = {
        "inputs": {
            "helpers": str(helper_path),
            "skip_decisions": str(skip_path),
            "ev_analysis": str(ev_path),
            "historical": str(hist_path),
        },
        "thresholds": {
            "max_buy_count": max_buy_count,
            "max_odds_for_buy": None if evaluator.max_odds_for_buy is None else float(evaluator.max_odds_for_buy),
            "payout_outlier_rescue_top_n": int(evaluator.payout_outlier_rescue_top_n),
            "payout_outlier_rescue_max_odds": float(evaluator.payout_outlier_rescue_max_odds),
            "payout_outlier_rescue_min_calibrated_hit_prob": float(
                evaluator.payout_outlier_rescue_min_calibrated_hit_prob
            ),
            "payout_outlier_rescue_max_ev_delta": float(evaluator.payout_outlier_rescue_max_ev_delta),
            "payout_outlier_rescue_min_final_score": float(evaluator.payout_outlier_rescue_min_final_score),
        },
        "funnel": {row["stage"]: int(row["count"]) for row in funnel_summary.to_dict(orient="records")},
        "drop_stage_top_all": _top_stage_lines(diag_df),
        "drop_stage_top_actual_rank_le5": _top_stage_lines(diag_df[diag_df["actual_rank"].le(5)]),
        "drop_stage_top_actual_rank_le3": _top_stage_lines(diag_df[diag_df["actual_rank"].le(3)]),
        "buy_vs_ranking": {row["bucket"]: int(row["count"]) for row in buy_vs_ranking.to_dict(orient="records")},
        "high_final_score_but_pushed_count": int(len(high_final_score_pushed)),
        "gate_ranking": gate_ranking,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
