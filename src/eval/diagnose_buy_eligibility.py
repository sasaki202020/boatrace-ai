import argparse
import json
from pathlib import Path

import pandas as pd

from src.strategy.evaluate_ev_and_skip import StrategyEvaluator


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREDICTIONS = ROOT / "reports" / "task4_final_score" / "final_score_race_safe_skip_decisions.csv"
DEFAULT_RANK_ROWS = ROOT / "reports" / "task1_after_calib" / "diagnostics" / "trifecta_rank_race_rows.csv"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "task5_buy_eligible_diagnostic"


PRIMARY_REASON_ORDER = [
    "hard_skip_lt6",
    "no_real_odds",
    "odds_cap",
    "race_gate_block",
    "not_race_priority",
    "pre_race_block",
    "first_place_block",
    "first_place_missing",
    "second_place_block",
    "third_place_block",
    "risk_flag",
    "risk_penalty",
    "min_ev",
    "approx_prob",
    "calibrated_hit_prob",
    "other_internal",
]


def _bool_series(df: pd.DataFrame, col: str) -> pd.Series:
    return df.get(col, pd.Series(False, index=df.index)).fillna(False).astype(bool)


def _numeric_series(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df.get(col, pd.Series(0.0, index=df.index)), errors="coerce").fillna(0.0)


def _summarize_reason_counts(df: pd.DataFrame, reason_cols: list[str]) -> dict[str, int]:
    code_map = {
        "reason_min_ev": "min_ev",
        "reason_approx_prob": "approx_prob",
        "reason_calibrated_hit_prob": "calibrated_hit_prob",
    }
    return {code_map.get(col, col): int(_bool_series(df, col).sum()) for col in reason_cols}


def _primary_reason_counts(df: pd.DataFrame) -> dict[str, int]:
    counts = (
        df.get("primary_reason", pd.Series(dtype=object))
        .fillna("other_internal")
        .astype(str)
        .value_counts()
        .to_dict()
    )
    return {str(k): int(v) for k, v in counts.items()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose buy_eligible=False reasons without changing selection logic")
    parser.add_argument("--predictions", default=str(DEFAULT_PREDICTIONS))
    parser.add_argument("--rank-rows", default=str(DEFAULT_RANK_ROWS))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    pred_path = Path(args.predictions)
    rank_path = Path(args.rank_rows)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    evaluator = StrategyEvaluator(config_path=str(ROOT / "config" / "strategy_config.json"))
    pred = pd.read_csv(pred_path, low_memory=False)
    rank_rows = pd.read_csv(rank_path, low_memory=False)

    if "actual_rank" not in pred.columns:
        pred = pred.merge(
            rank_rows[["race_id", "actual_trifecta", "actual_rank"]],
            on="race_id",
            how="left",
        )

    pred["actual_rank"] = pd.to_numeric(pred.get("actual_rank"), errors="coerce")
    pred["candidate_rank_by_sort"] = pd.to_numeric(pred.get("candidate_rank_by_sort"), errors="coerce")
    pred["buy_eligible"] = _bool_series(pred, "buy_eligible")
    pred["watch_eligible"] = _bool_series(pred, "watch_eligible")
    pred["has_real_odds"] = _bool_series(pred, "has_real_odds")
    pred["risk_flag"] = _bool_series(pred, "risk_flag")
    pred["race_block"] = _bool_series(pred, "race_block")
    pred["race_priority"] = _bool_series(pred, "race_priority")
    pred["pre_race_block"] = pred["reason"].astype(str).str.contains("直前.*BUY禁止", na=False)
    pred["first_place_block"] = _bool_series(pred, "first_place_block")
    pred["second_place_block"] = _bool_series(pred, "second_place_block")
    pred["third_place_block"] = _bool_series(pred, "third_place_block")
    pred["first_place_missing"] = pred.get("first_place_gate", pd.Series("", index=pred.index)).astype(str).eq("MISSING")
    pred["hard_skip_lt6"] = pred["reason"].astype(str).str.contains("6艇未満", na=False)
    pred["race_gate_block"] = pred.get("race_gate", pd.Series("", index=pred.index)).astype(str).eq("BLOCK") | pred["race_block"]
    pred["not_race_priority"] = ~pred["race_priority"]
    max_odds_for_buy = evaluator.max_odds_for_buy
    if max_odds_for_buy is None:
        pred["odds_cap"] = False
    else:
        pred["odds_cap"] = pred["has_real_odds"] & (
            _numeric_series(pred, "odds") > float(max_odds_for_buy)
        )
    pred["risk_penalty_fail"] = _numeric_series(pred, "risk_penalty") > float(evaluator.buy_max_risk_penalty)
    pred["approx_prob_value"] = _numeric_series(pred, "approx_prob")
    pred["calibrated_hit_prob_value"] = _numeric_series(pred, "calibrated_hit_prob")
    pred["min_ev_gap"] = float(evaluator.buy_min_ev) - _numeric_series(pred, "ev")
    pred["approx_prob_gap"] = float(evaluator.buy_min_approx_prob) - pred["approx_prob_value"]
    pred["calibrated_gap"] = float(evaluator.rank_rescue_min_calibrated_hit_prob) - pred["calibrated_hit_prob_value"]
    pred["odds_gap"] = _numeric_series(pred, "odds") - (
        float(max_odds_for_buy) if max_odds_for_buy is not None else 0.0
    )
    pred["reason_min_ev"] = pred["min_ev_gap"] > 0
    pred["reason_approx_prob"] = pred["approx_prob_gap"] > 0
    pred["reason_calibrated_hit_prob"] = pred["calibrated_gap"] > 0
    pred["no_real_odds"] = ~pred["has_real_odds"]

    prospective_scores = pred.apply(lambda row: pd.Series(evaluator._buy_final_score(row)), axis=1)
    for col in prospective_scores.columns:
        pred[col] = pd.to_numeric(prospective_scores[col], errors="coerce")

    reason_cols = [
        "hard_skip_lt6",
        "no_real_odds",
        "odds_cap",
        "race_gate_block",
        "not_race_priority",
        "pre_race_block",
        "first_place_block",
        "first_place_missing",
        "second_place_block",
        "third_place_block",
        "risk_flag",
        "risk_penalty_fail",
        "reason_min_ev",
        "reason_approx_prob",
        "reason_calibrated_hit_prob",
    ]

    pred["other_internal"] = (~pred["buy_eligible"]) & (~pred[reason_cols].any(axis=1))
    pred["primary_reason"] = "other_internal"
    for col in PRIMARY_REASON_ORDER:
        source_col = {
            "risk_penalty": "risk_penalty_fail",
            "min_ev": "reason_min_ev",
            "approx_prob": "reason_approx_prob",
            "calibrated_hit_prob": "reason_calibrated_hit_prob",
        }.get(col, col)
        mask = (~pred["buy_eligible"]) & _bool_series(pred, source_col) & pred["primary_reason"].eq("other_internal")
        pred.loc[mask, "primary_reason"] = col

    buy_false = pred[~pred["buy_eligible"]].copy()
    rank_le5 = buy_false[buy_false["actual_rank"].le(5)].copy()
    rank_le3 = buy_false[buy_false["actual_rank"].le(3)].copy()

    near_miss = buy_false[
        (~buy_false["hard_skip_lt6"])
        & (~buy_false["no_real_odds"])
        & (~buy_false["race_gate_block"])
        & (~buy_false["risk_flag"])
        & (~buy_false["risk_penalty_fail"])
        & buy_false["actual_rank"].le(5)
    ].copy()
    near_miss["soft_fail_count"] = near_miss[
        ["reason_min_ev", "reason_approx_prob", "reason_calibrated_hit_prob", "not_race_priority", "odds_cap"]
    ].sum(axis=1)
    near_miss = near_miss[
        (near_miss["soft_fail_count"] > 0)
        & (
            (near_miss["min_ev_gap"].between(0.0, 1.0))
            | (near_miss["approx_prob_gap"].between(0.0, 0.01))
            | (near_miss["calibrated_gap"].between(0.0, 0.02))
            | (near_miss["odds_gap"].between(0.0, 50.0))
            | near_miss["not_race_priority"]
        )
    ].copy()
    near_miss["drop_conditions"] = near_miss.apply(
        lambda row: "|".join(
            [
                label
                for label, flag in [
                    ("min_ev", bool(row["reason_min_ev"])),
                    ("approx_prob", bool(row["reason_approx_prob"])),
                    ("calibrated_hit_prob", bool(row["reason_calibrated_hit_prob"])),
                    ("odds_cap", bool(row["odds_cap"])),
                    ("not_race_priority", bool(row["not_race_priority"])),
                ]
                if flag
            ]
        ),
        axis=1,
    )
    near_miss["threshold_gap"] = near_miss.apply(
        lambda row: ", ".join(
            [
                item
                for item in [
                    f"min_ev_gap={row['min_ev_gap']:.3f}" if bool(row["reason_min_ev"]) else "",
                    f"approx_prob_gap={row['approx_prob_gap']:.4f}" if bool(row["reason_approx_prob"]) else "",
                    f"calibrated_gap={row['calibrated_gap']:.4f}" if bool(row["reason_calibrated_hit_prob"]) else "",
                    f"odds_gap={row['odds_gap']:.1f}" if bool(row["odds_cap"]) else "",
                ]
                if item
            ]
        ),
        axis=1,
    )
    near_miss = near_miss.sort_values(
        ["actual_rank", "soft_fail_count", "buy_final_score", "calibrated_hit_prob_value", "odds_gap", "min_ev_gap"],
        ascending=[True, True, False, False, True, True],
    )

    count_summary = {
        "all_buy_eligible_false": {
            "rows": int(len(buy_false)),
            "reason_counts": _summarize_reason_counts(
                buy_false,
                reason_cols + ["other_internal"],
            ),
            "primary_reason_counts": _primary_reason_counts(buy_false),
        },
        "actual_rank_le5": {
            "rows": int(len(rank_le5)),
            "reason_counts": _summarize_reason_counts(
                rank_le5,
                reason_cols + ["other_internal"],
            ),
            "primary_reason_counts": _primary_reason_counts(rank_le5),
        },
        "actual_rank_le3": {
            "rows": int(len(rank_le3)),
            "reason_counts": _summarize_reason_counts(
                rank_le3,
                reason_cols + ["other_internal"],
            ),
            "primary_reason_counts": _primary_reason_counts(rank_le3),
        },
    }

    gate_ranking = [
        {
            "rank": 1,
            "gate": "6艇未満 hard skip",
            "reason": f"actual_rank<=5 の buy_eligible=False {int(rank_le5['hard_skip_lt6'].sum())} 件を占め、最大の残存要因。",
        },
        {
            "rank": 2,
            "gate": "BUYオッズ上限",
            "reason": f"actual_rank<=5 の buy_eligible=False で {int(rank_le5['odds_cap'].sum())} 件あり、hard skip を触らずに次点で効き得る制約。",
        },
        {
            "rank": 3,
            "gate": "race_gate / race_priority",
            "reason": f"actual_rank<=5 で BLOCK が {int(rank_le5['race_gate_block'].sum())} 件、priority不足も {int(rank_le5['not_race_priority'].sum())} 件あり、最終選抜前の質ゲートとして残っている。",
        },
    ]

    summary = {
        "inputs": {
            "predictions": str(pred_path),
            "rank_rows": str(rank_path),
        },
        "thresholds": {
            "buy_min_ev": float(evaluator.buy_min_ev),
            "buy_min_approx_prob": float(evaluator.buy_min_approx_prob),
            "rank_rescue_min_calibrated_hit_prob": float(evaluator.rank_rescue_min_calibrated_hit_prob),
            "max_odds_for_buy": None if evaluator.max_odds_for_buy is None else float(evaluator.max_odds_for_buy),
            "max_buy_count": None if evaluator.max_buy_count is None else int(evaluator.max_buy_count),
        },
        "count_summary": count_summary,
        "actual_rank_le5_special": {
            "odds_cap_count": int(rank_le5["odds_cap"].sum()),
            "hard_skip_lt6_count": int(rank_le5["hard_skip_lt6"].sum()),
        },
        "actual_rank_le3_special": {
            "odds_cap_count": int(rank_le3["odds_cap"].sum()),
            "hard_skip_lt6_count": int(rank_le3["hard_skip_lt6"].sum()),
        },
        "gate_ranking": gate_ranking,
        "near_miss_count": int(len(near_miss)),
    }

    near_miss_cols = [
        "race_id",
        "recommended_trifecta",
        "actual_trifecta",
        "actual_rank",
        "candidate_rank_by_sort",
        "calibrated_hit_prob_value",
        "buy_final_score",
        "odds",
        "drop_conditions",
        "primary_reason",
        "threshold_gap",
        "min_ev_gap",
        "approx_prob_gap",
        "calibrated_gap",
        "odds_gap",
        "race_score",
        "ev",
    ]
    near_miss_output = near_miss[near_miss_cols].rename(
        columns={
            "recommended_trifecta": "combo",
            "calibrated_hit_prob_value": "calibrated_hit_prob",
        }
    )

    count_rows = []
    for subset_name, subset in [
        ("all_buy_eligible_false", buy_false),
        ("actual_rank_le5", rank_le5),
        ("actual_rank_le3", rank_le3),
    ]:
        for col in reason_cols + ["other_internal"]:
            count_rows.append(
                {
                    "subset": subset_name,
                    "reason_code": {
                        "reason_min_ev": "min_ev",
                        "reason_approx_prob": "approx_prob",
                        "reason_calibrated_hit_prob": "calibrated_hit_prob",
                    }.get(col, col),
                    "count": int(_bool_series(subset, col).sum()),
                }
            )
    count_df = pd.DataFrame(count_rows).sort_values(["subset", "count", "reason_code"], ascending=[True, False, True])

    detail_reason_map = {col: f"reason_{col}" for col in reason_cols + ["other_internal"]}
    details = buy_false[
        [
            "race_id",
            "decision",
            "buy_eligible",
            "actual_rank",
            "candidate_rank_by_sort",
            "recommended_trifecta",
            "actual_trifecta",
            "primary_reason",
            "buy_final_score",
            "calibrated_hit_prob_value",
            "odds",
            "ev",
            "min_ev_gap",
            "approx_prob_gap",
            "calibrated_gap",
        ]
        + reason_cols
        + ["other_internal"]
    ].copy()
    details = details.rename(
        columns={
            **detail_reason_map,
            "calibrated_hit_prob_value": "calibrated_hit_prob",
        }
    )

    with open(output_dir / "buy_eligible_diagnostic_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    count_df.to_csv(output_dir / "buy_eligible_reason_counts.csv", index=False)
    near_miss_output.to_csv(output_dir / "buy_eligible_near_miss_rows.csv", index=False)
    details.to_csv(output_dir / "buy_eligible_reason_rows.csv", index=False)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(near_miss_output.head(15).to_string(index=False))


if __name__ == "__main__":
    main()
