import argparse
import json
import itertools
from pathlib import Path

import pandas as pd


# TASK-016 offline evaluation snapshot.
# 20260311 is a frozen comparison snapshot for shadow analysis only.
# Do not treat these defaults as current `today_*` inputs, and do not let the daily pipeline
# pick them up implicitly as production data.
PROBA_CSV = Path("data/tmp/20260311_eval/today_win_proba.csv")
FEATURES_CSV = Path("data/tmp/20260311_eval/today_features.csv")
BACKTEST_CSV = Path("reports/t016_backtest_race_results.csv")
CONFIG_CSV = Path("config/strategy_config.json")
OUT_ABLATION = Path("reports/ablation_result.json")
OUT_BOTTLENECK = Path("reports/bottleneck_analysis.json")
OUT_BREAKDOWN = Path("reports/bottleneck_ranking_breakdown.json")
OUT_ORDER_ADJUST = Path("reports/order_adjustment_comparison.json")
OUT_APPROX_RESCORE = Path("reports/approx_rescore_comparison.json")
OUT_NOT_IN_60_STAGE = Path("reports/not_in_60_stage_breakdown.json")
OUT_CUT_BY_TOP60_RANK = Path("reports/cut_by_top60_rank_distribution.json")
OUT_MAX_TRIFECTA_COMPARISON = Path("reports/max_trifecta_comparison.json")
OUT_TOP60_SELECTION_COMPARISON = Path("reports/top60_selection_comparison.json")
OUT_PER_FIRST_TUNING = Path("reports/per_first_balanced_tuning.json")
OUT_PER_FIRST_M12_REPRO = Path("reports/per_first_m12_global_repro.json")
OUT_PER_FIRST_M12_INTEGRATION_CHECK = Path("reports/per_first_m12_global_integration_check.json")
OUT_ROI_DRAWDOWN_EVALUATION = Path("reports/roi_drawdown_evaluation.json")
OUT_METRIC_DEFINITION_CHECK = Path("reports/metric_definition_check.json")
OUT_BETTING_STRATEGY_COMPARISON = Path("reports/betting_strategy_comparison.json")
OUT_RACE_FILTER_COMPARISON = Path("reports/race_filter_comparison.json")

TIEBREAK_FEATS = ["national_win_rate", "local_2ren_rate"]
BETA = 0.2
EPS = 1e-10
ORDER_ADJUST_ALPHA = 0.002


def to_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def build_actual_map(backtest_df: pd.DataFrame) -> dict[str, dict[str, str]]:
    bt = backtest_df[["race_id", "actual_trifecta", "result_available"]].drop_duplicates("race_id").copy()
    bt["result_available"] = to_bool(bt["result_available"])
    bt = bt[bt["result_available"]].copy()
    bt["actual_winner"] = bt["actual_trifecta"].astype(str).str.split("-").str[0].str.strip()
    return (
        bt[["race_id", "actual_winner", "actual_trifecta"]]
        .set_index("race_id")
        .to_dict(orient="index")
    )


def load_candidate_config() -> tuple[int, int]:
    config = json.loads(CONFIG_CSV.read_text(encoding="utf-8"))
    candidate_cfg = config.get("candidate_generation", {})
    return (
        int(candidate_cfg.get("top_n_win", 6)),
        int(candidate_cfg.get("max_trifecta_combinations", 60)),
    )


def load_candidate_generation_config() -> dict[str, object]:
    config = json.loads(CONFIG_CSV.read_text(encoding="utf-8"))
    return dict(config.get("candidate_generation", {}))


def generate_trifecta_candidates_for_race(
    race_data: pd.DataFrame,
    top_n_win: int,
    max_trifecta_combinations: int,
) -> list[dict[str, object]]:
    race_data = race_data.copy()
    race_data["win_proba_norm"] = pd.to_numeric(race_data["win_proba_norm"], errors="coerce").fillna(0.0)
    total_prob = float(race_data["win_proba_norm"].sum())
    if total_prob <= 0:
        return []

    race_data["win_proba_norm"] = race_data["win_proba_norm"] / total_prob
    sorted_boats = race_data.sort_values("win_proba_norm", ascending=False)
    top_boats = sorted_boats.head(top_n_win)["lane"].astype(int).tolist()
    lanes = race_data["lane"].astype(int).tolist()
    probs = race_data.set_index("lane")["win_proba_norm"].to_dict()

    race_results: list[dict[str, object]] = []
    for first_lane, second_lane, third_lane in itertools.permutations(lanes, 3):
        if first_lane not in top_boats:
            continue

        p1 = float(probs[first_lane])
        remain_after_first = sum(float(probs[lane]) for lane in lanes if lane != first_lane)
        remain_after_second = sum(
            float(probs[lane]) for lane in lanes if lane not in (first_lane, second_lane)
        )
        if p1 <= 0 or remain_after_first <= EPS or remain_after_second <= EPS:
            continue

        p2 = float(probs[second_lane]) / remain_after_first
        p3 = float(probs[third_lane]) / remain_after_second
        approx_prob = min(p1 * p2 * p3, 1.0)
        if approx_prob <= 0:
            continue

        race_results.append(
            {
                "trifecta": f"{first_lane}-{second_lane}-{third_lane}",
                "approx_prob": approx_prob,
                "first_lane": first_lane,
                "second_lane": second_lane,
                "third_lane": third_lane,
                "second_win_proba": float(probs[second_lane]),
                "third_win_proba": float(probs[third_lane]),
            }
        )

    race_results.sort(key=lambda row: row["approx_prob"], reverse=True)
    return race_results[:max_trifecta_combinations]


def apply_order_adjustment(
    candidate_rows: list[dict[str, object]],
    alpha: float,
) -> list[dict[str, object]]:
    adjusted_rows: list[dict[str, object]] = []
    for row in candidate_rows:
        adjusted = dict(row)
        second_prob = float(adjusted.get("second_win_proba", 0.0))
        third_prob = float(adjusted.get("third_win_proba", 0.0))
        adjusted["order_adjustment"] = alpha * (second_prob - third_prob)
        adjusted["adjusted_score"] = float(adjusted["approx_prob"]) + float(adjusted["order_adjustment"])
        adjusted_rows.append(adjusted)
    adjusted_rows.sort(key=lambda x: float(x["adjusted_score"]), reverse=True)
    return adjusted_rows


def apply_rescore_candidates(
    candidate_rows: list[dict[str, object]],
    mode: str,
    gamma: float,
) -> list[dict[str, object]]:
    rescored_rows: list[dict[str, object]] = []
    for row in candidate_rows:
        rescored = dict(row)
        base = float(rescored.get("approx_prob", 0.0))
        first_prob = float(rescored.get("first_win_proba", 0.0))
        second_prob = float(rescored.get("second_win_proba", 0.0))
        third_prob = float(rescored.get("third_win_proba", 0.0))

        if mode == "baseline":
            bonus = 0.0
        elif mode == "sum23":
            bonus = gamma * (second_prob + third_prob)
        elif mode == "prod23":
            bonus = gamma * (second_prob * third_prob)
        elif mode == "first_and_prod23":
            bonus = gamma * (first_prob + (second_prob * third_prob))
        else:
            bonus = 0.0

        rescored["rescore_bonus"] = bonus
        rescored["rescore_score"] = base + bonus
        rescored_rows.append(rescored)
    rescored_rows.sort(key=lambda x: float(x["rescore_score"]), reverse=True)
    return rescored_rows


def select_top60_baseline(candidate_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return list(candidate_rows[:60])


def select_top60_per_first_balanced(candidate_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    lanes = sorted({int(row["first_lane"]) for row in candidate_rows})
    if not lanes:
        return []
    quota = 60 // len(lanes)
    selected: list[dict[str, object]] = []
    selected_keys = set()
    grouped = {lane: [] for lane in lanes}
    for row in candidate_rows:
        grouped[int(row["first_lane"])].append(row)

    for lane in lanes:
        for row in grouped[lane][:quota]:
            key = str(row["trifecta"])
            if key not in selected_keys:
                selected.append(row)
                selected_keys.add(key)

    if len(selected) < 60:
        for row in candidate_rows:
            key = str(row["trifecta"])
            if key in selected_keys:
                continue
            selected.append(row)
            selected_keys.add(key)
            if len(selected) >= 60:
                break
    return selected[:60]


def select_top60_per_first_pattern(
    candidate_rows: list[dict[str, object]],
    min_per_first: int,
    fill_mode: str,
) -> list[dict[str, object]]:
    lanes = sorted({int(row["first_lane"]) for row in candidate_rows})
    if not lanes:
        return []
    selected: list[dict[str, object]] = []
    selected_keys = set()
    grouped = {lane: [] for lane in lanes}
    for row in candidate_rows:
        grouped[int(row["first_lane"])].append(row)

    # Stage 1: ensure minimum picks per first-lane bucket.
    for lane in lanes:
        for row in grouped[lane][:max(min_per_first, 0)]:
            key = str(row["trifecta"])
            if key in selected_keys:
                continue
            selected.append(row)
            selected_keys.add(key)
            if len(selected) >= 60:
                return selected[:60]

    # Stage 2: fill the remaining slots.
    if fill_mode == "lane_round_robin":
        lane_idx = 0
        cursor = {lane: max(min_per_first, 0) for lane in lanes}
        while len(selected) < 60:
            lane = lanes[lane_idx % len(lanes)]
            lane_idx += 1
            idx = cursor[lane]
            if idx >= len(grouped[lane]):
                if all(cursor[l] >= len(grouped[l]) for l in lanes):
                    break
                continue
            row = grouped[lane][idx]
            cursor[lane] += 1
            key = str(row["trifecta"])
            if key in selected_keys:
                continue
            selected.append(row)
            selected_keys.add(key)
    else:
        # Default: global score-ordered fill.
        for row in candidate_rows:
            key = str(row["trifecta"])
            if key in selected_keys:
                continue
            selected.append(row)
            selected_keys.add(key)
            if len(selected) >= 60:
                break
    return selected[:60]


def select_top60_diverse_pair(candidate_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    selected_keys = set()
    used_first_pair = set()
    for row in candidate_rows:
        first = int(row["first_lane"])
        second = int(row["second_lane"])
        third = int(row["third_lane"])
        pair_key = (first, tuple(sorted((second, third))))
        if pair_key in used_first_pair:
            continue
        key = str(row["trifecta"])
        if key in selected_keys:
            continue
        selected.append(row)
        selected_keys.add(key)
        used_first_pair.add(pair_key)
        if len(selected) >= 60:
            return selected[:60]

    if len(selected) < 60:
        for row in candidate_rows:
            key = str(row["trifecta"])
            if key in selected_keys:
                continue
            selected.append(row)
            selected_keys.add(key)
            if len(selected) >= 60:
                break
    return selected[:60]


def init_rank_stats() -> dict[str, object]:
    return {
        "total_races": 0,
        "exact_hits": 0,
        "winner_top1_hits": 0,
        "top1_hits": 0,
        "in_set_count": 0,
        "rank_sum": 0,
        "rank_dist": {
            "rank_1_5": 0,
            "rank_6_10": 0,
            "rank_11_20": 0,
            "rank_21_40": 0,
            "rank_41_60": 0,
            "not_in_60": 0,
        },
    }


def update_rank_stats(
    stats: dict[str, object],
    candidate_rows: list[dict[str, object]],
    actual_trifecta: str,
) -> None:
    stats["total_races"] += 1

    # winner_top1: predicted top trifecta's first lane == actual winner lane.
    actual_winner = str(actual_trifecta).split("-")[0].strip()
    if len(candidate_rows) > 0:
        predicted_top1_first = str(candidate_rows[0]["trifecta"]).split("-")[0].strip()
        if predicted_top1_first == actual_winner:
            stats["winner_top1_hits"] += 1

    found_rank = None
    for idx, candidate in enumerate(candidate_rows, start=1):
        if str(candidate["trifecta"]).strip() == actual_trifecta:
            found_rank = idx
            break

    if found_rank is None:
        stats["rank_dist"]["not_in_60"] += 1
        return

    stats["in_set_count"] += 1
    stats["rank_sum"] += found_rank
    if found_rank == 1:
        stats["exact_hits"] += 1
        stats["top1_hits"] += 1
    if found_rank <= 5:
        stats["rank_dist"]["rank_1_5"] += 1
    elif found_rank <= 10:
        stats["rank_dist"]["rank_6_10"] += 1
    elif found_rank <= 20:
        stats["rank_dist"]["rank_11_20"] += 1
    elif found_rank <= 40:
        stats["rank_dist"]["rank_21_40"] += 1
    else:
        stats["rank_dist"]["rank_41_60"] += 1


def finalize_rank_stats(stats: dict[str, object]) -> dict[str, object]:
    total = int(stats["total_races"])
    in_set_count = int(stats["in_set_count"])
    exact_hits = int(stats["exact_hits"])
    winner_top1_hits = int(stats["winner_top1_hits"])
    top1_hits = int(stats["top1_hits"])
    out = {
        "total_races": total,
        "exact_hits": exact_hits,
        "exact_hitrate": round(exact_hits / total, 4) if total else 0.0,
        "winner_top1_hits": winner_top1_hits,
        "winner_top1_hitrate": round(winner_top1_hits / total, 4) if total else 0.0,
        "top1_hits": top1_hits,
        "top1_hitrate": round(top1_hits / total, 4) if total else 0.0,
        "in_set_count": in_set_count,
        "in_set_rate": round(in_set_count / total, 4) if total else 0.0,
        "trifecta_avg_rank": round(stats["rank_sum"] / in_set_count, 2) if in_set_count else None,
        "rank_dist": stats["rank_dist"],
    }
    return out


def build_date_windows(race_date_df: pd.DataFrame) -> list[dict[str, object]]:
    date_series = pd.to_datetime(race_date_df["date"], errors="coerce").dropna().sort_values().unique().tolist()
    if len(date_series) == 0:
        return []

    n = len(date_series)
    # 3-way split by date order: early / mid / recent.
    i1 = max(1, n // 3)
    i2 = max(i1 + 1, (2 * n) // 3)
    if i2 > n:
        i2 = n

    early_dates = set(date_series[:i1])
    mid_dates = set(date_series[i1:i2])
    recent_dates = set(date_series[i2:])

    windows = [
        {"name": "all_dates", "date_set": set(date_series)},
        {"name": "early_window", "date_set": early_dates},
        {"name": "mid_window", "date_set": mid_dates},
        {"name": "recent_window", "date_set": recent_dates},
    ]
    return [w for w in windows if len(w["date_set"]) > 0]


def _max_drawdown(profits: list[float]) -> float:
    peak = 0.0
    cum = 0.0
    max_dd = 0.0
    for p in profits:
        cum += float(p)
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return round(float(max_dd), 4)


def _longest_losing_streak(profits: list[float]) -> int:
    streak = 0
    best = 0
    for p in profits:
        if float(p) <= 0:
            streak += 1
            if streak > best:
                best = streak
        else:
            streak = 0
    return int(best)


def _normalize_positive_weights(weights: list[float]) -> list[float]:
    clamped = [max(float(w), 0.0) for w in weights]
    total = float(sum(clamped))
    if total <= 0:
        n = len(clamped)
        return [1.0 / n] * n if n > 0 else []
    return [w / total for w in clamped]


def main():
    global PROBA_CSV, FEATURES_CSV, BACKTEST_CSV, CONFIG_CSV

    parser = argparse.ArgumentParser(description="Run ablation and bottleneck analysis.")
    parser.add_argument("--proba-csv", default=str(PROBA_CSV))
    parser.add_argument("--features-csv", default=str(FEATURES_CSV))
    parser.add_argument("--backtest-csv", default=str(BACKTEST_CSV))
    parser.add_argument("--config-csv", default=str(CONFIG_CSV))
    parser.add_argument("--race-filter-output", default=str(OUT_RACE_FILTER_COMPARISON))
    args = parser.parse_args()

    proba_path = Path(args.proba_csv)
    feat_path = Path(args.features_csv)
    backtest_path = Path(args.backtest_csv)
    config_path = Path(args.config_csv)
    race_filter_output_path = Path(args.race_filter_output)
    PROBA_CSV = proba_path
    FEATURES_CSV = feat_path
    BACKTEST_CSV = backtest_path
    CONFIG_CSV = config_path

    proba_df = pd.read_csv(PROBA_CSV)
    feat_df = pd.read_csv(FEATURES_CSV)
    backtest_df = pd.read_csv(BACKTEST_CSV)

    proba_df["lane"] = pd.to_numeric(proba_df["lane"], errors="coerce")
    feat_df["lane"] = pd.to_numeric(feat_df["lane"], errors="coerce")
    proba_df = proba_df.dropna(subset=["race_id", "lane"]).copy()
    feat_df = feat_df.dropna(subset=["race_id", "lane"]).copy()
    proba_df["lane"] = proba_df["lane"].astype(int)
    feat_df["lane"] = feat_df["lane"].astype(int)

    merged_df = proba_df.merge(feat_df, on=["race_id", "lane"], how="left")
    for feat in TIEBREAK_FEATS:
        merged_df[feat] = pd.to_numeric(merged_df[feat], errors="coerce")
        col_min = merged_df[feat].min()
        col_max = merged_df[feat].max()
        merged_df[f"{feat}_scaled"] = (merged_df[feat] - col_min) / (col_max - col_min + 1e-9)
        merged_df[f"{feat}_scaled"] = merged_df[f"{feat}_scaled"].fillna(0.0)

    actual_map = build_actual_map(backtest_df)
    race_ids = list(merged_df["race_id"].astype(str).unique())
    candidate_cfg = load_candidate_generation_config()
    top_n_win = int(candidate_cfg.get("top_n_win", 6))
    max_trifecta_combinations = int(candidate_cfg.get("max_trifecta_combinations", 60))

    configs = {
        "all_off": {"use_rerank": False, "top_n": 3},
        "A_only": {"use_rerank": False, "top_n": 6},
        "B_only": {"use_rerank": True, "top_n": 3},
        "AB": {"use_rerank": True, "top_n": 6},
        "all_on": {"use_rerank": True, "top_n": 6},
    }

    ablation_results = {}
    for config_name, cfg in configs.items():
        top1_hits = 0
        trifecta_hits = 0
        candidate_include = 0
        total = 0

        for race_id in race_ids:
            actual = actual_map.get(race_id)
            if actual is None:
                continue

            actual_winner = str(actual["actual_winner"]).strip()
            actual_trifecta = str(actual["actual_trifecta"]).strip()
            race_data = merged_df[merged_df["race_id"].astype(str) == race_id].copy()
            if race_data.empty:
                continue

            if cfg["use_rerank"]:
                race_data["score"] = race_data["win_proba_norm"].astype(float)
                for feat in TIEBREAK_FEATS:
                    race_data["score"] += BETA * race_data[f"{feat}_scaled"]
            else:
                race_data["score"] = pd.to_numeric(race_data["win_proba_norm"], errors="coerce").fillna(0.0)

            race_data = race_data.sort_values("score", ascending=False).reset_index(drop=True)
            ranked = race_data["lane"].astype(int).astype(str).tolist()
            if len(ranked) < 3:
                continue

            if ranked[0] == actual_winner:
                top1_hits += 1
            if actual_winner in ranked[: cfg["top_n"]]:
                candidate_include += 1
            if "-".join(ranked[:3]) == actual_trifecta:
                trifecta_hits += 1
            total += 1

        ablation_results[config_name] = {
            "top1_hitrate": round(top1_hits / total, 4) if total else 0.0,
            "trifecta_exact_hitrate": round(trifecta_hits / total, 4) if total else 0.0,
            "candidate_include_rate": round(candidate_include / total, 4) if total else 0.0,
            "total_races": int(total),
        }

    OUT_ABLATION.write_text(json.dumps(ablation_results, ensure_ascii=False, indent=2), encoding="utf-8")

    # bottleneck on actual candidate set generated from today_win_proba
    rank_buckets = {
        "rank_1_5": 0,
        "rank_6_10": 0,
        "rank_11_20": 0,
        "rank_21_40": 0,
        "rank_41_60": 0,
        "not_in_60": 0,
    }
    winner_rank_when_missed = []
    candidate_ranks = []
    total_races = 0
    ranking_breakdown_rows: list[dict[str, object]] = []
    not_in_60_stage_rows: list[dict[str, object]] = []

    for race_id in race_ids:
        actual = actual_map.get(race_id)
        if actual is None:
            continue
        actual_trifecta = str(actual["actual_trifecta"]).strip()
        actual_winner = str(actual["actual_winner"]).strip()

        race_data = merged_df[merged_df["race_id"].astype(str) == race_id].copy()
        if race_data.empty:
            continue
        candidate_rows = generate_trifecta_candidates_for_race(
            race_data,
            top_n_win=top_n_win,
            max_trifecta_combinations=max_trifecta_combinations,
        )
        all_candidate_rows = generate_trifecta_candidates_for_race(
            race_data,
            top_n_win=top_n_win,
            max_trifecta_combinations=100000,
        )
        if not candidate_rows:
            continue

        found_rank = None
        found_approx_prob = None
        candidate_index_map: dict[str, dict[str, object]] = {}
        for idx, candidate in enumerate(candidate_rows, start=1):
            trifecta = str(candidate["trifecta"]).strip()
            candidate_index_map[trifecta] = {
                "rank": idx,
                "approx_prob": float(candidate["approx_prob"]),
            }
            if str(candidate["trifecta"]).strip() == actual_trifecta:
                found_rank = idx
                found_approx_prob = float(candidate["approx_prob"])
                candidate_ranks.append(idx)
        top1_approx_prob = float(candidate_rows[0]["approx_prob"])
        top5_approx_mean = float(
            pd.Series([float(row["approx_prob"]) for row in candidate_rows[:5]], dtype=float).mean()
        )

        ranked_winners = (
            race_data.assign(
                score=pd.to_numeric(race_data["win_proba_norm"], errors="coerce").fillna(0.0)
            )
            .sort_values("score", ascending=False)["lane"]
            .astype(int)
            .astype(str)
            .tolist()
        )

        if found_rank is None:
            rank_buckets["not_in_60"] += 1
            if actual_winner in ranked_winners:
                winner_rank_when_missed.append(ranked_winners.index(actual_winner) + 1)

            parts = actual_trifecta.split("-")
            actual_first = parts[0].strip() if len(parts) == 3 else None
            actual_second = parts[1].strip() if len(parts) == 3 else None
            actual_third = parts[2].strip() if len(parts) == 3 else None
            top_boats = set(
                race_data.sort_values("win_proba_norm", ascending=False)["lane"]
                .astype(int)
                .astype(str)
                .head(top_n_win)
                .tolist()
            )
            first_rank = None
            if actual_first in ranked_winners:
                first_rank = int(ranked_winners.index(actual_first) + 1)

            all_index = {
                str(row["trifecta"]).strip(): idx
                for idx, row in enumerate(all_candidate_rows, start=1)
            }
            actual_in_all = actual_trifecta in all_index

            stage = "other_or_unknown"
            if actual_first is None or actual_second is None or actual_third is None:
                stage = "other_or_unknown"
            elif actual_first not in top_boats:
                stage = "first_miss"
            elif actual_in_all:
                stage = "cut_by_top60"
            else:
                # first is in candidate first-lane set, but exact (1,2,3) is not generated.
                stage = "pair_miss"

            not_in_60_stage_rows.append(
                {
                    "race_id": race_id,
                    "actual_trifecta": actual_trifecta,
                    "stage": stage,
                    "actual_first": actual_first,
                    "actual_second": actual_second,
                    "actual_third": actual_third,
                    "actual_first_win_rank": first_rank,
                    "actual_theoretical_rank_all_candidates": int(all_index[actual_trifecta])
                    if actual_in_all
                    else None,
                    "in_top_first_set": actual_first in top_boats if actual_first is not None else False,
                    "all_candidate_count": int(len(all_candidate_rows)),
                    "top60_candidate_count": int(len(candidate_rows)),
                }
            )
        elif found_rank <= 5:
            rank_buckets["rank_1_5"] += 1
        elif found_rank <= 10:
            rank_buckets["rank_6_10"] += 1
        elif found_rank <= 20:
            rank_buckets["rank_11_20"] += 1
        elif found_rank <= 40:
            rank_buckets["rank_21_40"] += 1
        else:
            rank_buckets["rank_41_60"] += 1

        # Breakdown analysis only for races where actual trifecta exists in candidate set.
        if found_rank is not None and found_approx_prob is not None:
            parts = actual_trifecta.split("-")
            swap_trifecta = None
            swap_rank = None
            swap_approx_prob = None
            if len(parts) == 3:
                swap_trifecta = f"{parts[0]}-{parts[2]}-{parts[1]}"
                swap_meta = candidate_index_map.get(swap_trifecta)
                if swap_meta is not None:
                    swap_rank = int(swap_meta["rank"])
                    swap_approx_prob = float(swap_meta["approx_prob"])

            better_candidate_count = found_rank - 1
            top1_gap = top1_approx_prob - found_approx_prob
            top5_gap = top5_approx_mean - found_approx_prob

            if swap_rank is not None and swap_rank < found_rank:
                cause = "second_third_order_issue"
            elif better_candidate_count >= 5 and (swap_rank is None or swap_rank >= found_rank):
                cause = "scoring_issue"
            else:
                cause = "mixed_or_other"

            ranking_breakdown_rows.append(
                {
                    "race_id": race_id,
                    "actual_trifecta": actual_trifecta,
                    "actual_rank": int(found_rank),
                    "actual_approx_prob": round(found_approx_prob, 8),
                    "top1_approx_prob": round(top1_approx_prob, 8),
                    "top1_minus_actual_prob": round(top1_gap, 8),
                    "top5_mean_approx_prob": round(top5_approx_mean, 8),
                    "top5_mean_minus_actual_prob": round(top5_gap, 8),
                    "better_candidate_count": int(better_candidate_count),
                    "swap_trifecta": swap_trifecta,
                    "swap_exists": swap_rank is not None,
                    "swap_rank": int(swap_rank) if swap_rank is not None else None,
                    "swap_approx_prob": round(swap_approx_prob, 8) if swap_approx_prob is not None else None,
                    "swap_beats_actual": bool(swap_rank is not None and swap_rank < found_rank),
                    "cause": cause,
                }
            )
        total_races += 1

    missed = pd.Series(winner_rank_when_missed, dtype=float)
    ranked_hits = pd.Series(candidate_ranks, dtype=float)
    not_in_60_rate = (rank_buckets["not_in_60"] / total_races) if total_races > 0 else 0.0
    if not_in_60_rate >= 0.5:
        diagnosis = "candidate_set_insufficiency_dominant"
    elif ranked_hits.mean() > 20:
        diagnosis = "in_set_ranking_insufficiency_dominant"
    else:
        diagnosis = "mixed_bottleneck"

    bottleneck = {
        "total_races": int(total_races),
        "candidate_generation": {
            "top_n_win": top_n_win,
            "max_trifecta_combinations": max_trifecta_combinations,
        },
        "trifecta_rank_dist": rank_buckets,
        "trifecta_avg_rank": round(float(ranked_hits.mean()), 2) if len(ranked_hits) > 0 else None,
        "winner_rank_when_trifecta_missed": {
            "count": int(len(missed)),
            "median": round(float(missed.median()), 2) if len(missed) > 0 else None,
            "rank1_rate": round(float((missed == 1).mean()), 4) if len(missed) > 0 else None,
            "rank2_3_rate": round(float((missed <= 3).mean()), 4) if len(missed) > 0 else None,
        },
        "diagnosis": diagnosis,
    }
    OUT_BOTTLENECK.write_text(json.dumps(bottleneck, ensure_ascii=False, indent=2), encoding="utf-8")

    breakdown_df = pd.DataFrame(ranking_breakdown_rows)
    if len(breakdown_df) > 0:
        cause_counts = breakdown_df["cause"].value_counts().to_dict()
        rank_dist = {
            "rank_1_5": int((breakdown_df["actual_rank"] <= 5).sum()),
            "rank_6_10": int(((breakdown_df["actual_rank"] >= 6) & (breakdown_df["actual_rank"] <= 10)).sum()),
            "rank_11_20": int(((breakdown_df["actual_rank"] >= 11) & (breakdown_df["actual_rank"] <= 20)).sum()),
            "rank_21_40": int(((breakdown_df["actual_rank"] >= 21) & (breakdown_df["actual_rank"] <= 40)).sum()),
            "rank_41_60": int(((breakdown_df["actual_rank"] >= 41) & (breakdown_df["actual_rank"] <= 60)).sum()),
        }
        swap_better_count = int(breakdown_df["swap_beats_actual"].sum())
        swap_missing_count = int((~breakdown_df["swap_exists"]).sum())
        same_first_order_only_count = int(
            (
                breakdown_df["swap_beats_actual"]
                & (breakdown_df["better_candidate_count"] == 1)
            ).sum()
        )
        breakdown_report = {
            "target": "races_where_actual_trifecta_is_in_candidate_set",
            "total_rows": int(len(breakdown_df)),
            "rank_distribution_in_set": rank_dist,
            "order_swap_comparison": {
                "swap_beats_actual_count": swap_better_count,
                "same_first_order_only_count": same_first_order_only_count,
                "swap_not_found_count": swap_missing_count,
            },
            "cause_distribution": cause_counts,
            "cause_rate": {
                cause: round(count / len(breakdown_df), 4)
                for cause, count in cause_counts.items()
            },
            "score_gap_summary": {
                "top1_minus_actual_prob_mean": round(
                    float(breakdown_df["top1_minus_actual_prob"].mean()), 8
                ),
                "top5_mean_minus_actual_prob_mean": round(
                    float(breakdown_df["top5_mean_minus_actual_prob"].mean()), 8
                ),
                "better_candidate_count_mean": round(
                    float(breakdown_df["better_candidate_count"].mean()), 4
                ),
            },
            "classification_rules": {
                "second_third_order_issue": "swap_trifecta exists and swap_rank < actual_rank",
                "scoring_issue": "better_candidate_count >= 5 and swap does not outrank actual",
                "mixed_or_other": "all other in-set cases",
            },
        }
    else:
        breakdown_report = {
            "target": "races_where_actual_trifecta_is_in_candidate_set",
            "total_rows": 0,
            "message": "No in-set actual trifecta rows found.",
        }
    OUT_BREAKDOWN.write_text(json.dumps(breakdown_report, ensure_ascii=False, indent=2), encoding="utf-8")

    # Small order adjustment comparison in the same candidate set.
    baseline_stats = init_rank_stats()
    adjusted_stats = init_rank_stats()
    for race_id in race_ids:
        actual = actual_map.get(race_id)
        if actual is None:
            continue
        actual_trifecta = str(actual["actual_trifecta"]).strip()
        race_data = merged_df[merged_df["race_id"].astype(str) == race_id].copy()
        if race_data.empty:
            continue

        baseline_candidates = generate_trifecta_candidates_for_race(
            race_data,
            top_n_win=top_n_win,
            max_trifecta_combinations=max_trifecta_combinations,
        )
        if not baseline_candidates:
            continue
        adjusted_candidates = apply_order_adjustment(
            baseline_candidates,
            alpha=ORDER_ADJUST_ALPHA,
        )

        update_rank_stats(baseline_stats, baseline_candidates, actual_trifecta)
        update_rank_stats(adjusted_stats, adjusted_candidates, actual_trifecta)

    baseline_out = finalize_rank_stats(baseline_stats)
    adjusted_out = finalize_rank_stats(adjusted_stats)
    avg_rank_diff = None
    if baseline_out["trifecta_avg_rank"] is not None and adjusted_out["trifecta_avg_rank"] is not None:
        avg_rank_diff = round(
            float(adjusted_out["trifecta_avg_rank"]) - float(baseline_out["trifecta_avg_rank"]),
            2,
        )
    order_adjustment_report = {
        "order_adjustment": {
            "enabled": True,
            "alpha": ORDER_ADJUST_ALPHA,
            "rule": "adjusted_score = approx_prob + alpha * (second_win_proba - third_win_proba)",
        },
        "baseline": baseline_out,
        "order_adjusted": adjusted_out,
        "diff": {
            "exact_hitrate": round(adjusted_out["exact_hitrate"] - baseline_out["exact_hitrate"], 4),
            "top1_hitrate": round(adjusted_out["top1_hitrate"] - baseline_out["top1_hitrate"], 4),
            "in_set_rate": round(adjusted_out["in_set_rate"] - baseline_out["in_set_rate"], 4),
            "trifecta_avg_rank": avg_rank_diff,
            "not_in_60": int(adjusted_out["rank_dist"]["not_in_60"]) - int(baseline_out["rank_dist"]["not_in_60"]),
        },
    }
    OUT_ORDER_ADJUST.write_text(
        json.dumps(order_adjustment_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Approx rescore comparison on fixed candidate sets.
    rescore_conditions = [
        {"name": "baseline", "mode": "baseline", "gamma": 0.0},
        {"name": "sum23_g0.001", "mode": "sum23", "gamma": 0.001},
        {"name": "sum23_g0.002", "mode": "sum23", "gamma": 0.002},
        {"name": "prod23_g0.01", "mode": "prod23", "gamma": 0.01},
        {"name": "first_and_prod23_g0.001", "mode": "first_and_prod23", "gamma": 0.001},
    ]
    stats_by_condition: dict[str, dict[str, object]] = {
        cond["name"]: init_rank_stats() for cond in rescore_conditions
    }
    for race_id in race_ids:
        actual = actual_map.get(race_id)
        if actual is None:
            continue
        actual_trifecta = str(actual["actual_trifecta"]).strip()
        race_data = merged_df[merged_df["race_id"].astype(str) == race_id].copy()
        if race_data.empty:
            continue
        fixed_candidates = generate_trifecta_candidates_for_race(
            race_data,
            top_n_win=top_n_win,
            max_trifecta_combinations=max_trifecta_combinations,
        )
        if not fixed_candidates:
            continue

        for cond in rescore_conditions:
            ranked = apply_rescore_candidates(
                fixed_candidates,
                mode=str(cond["mode"]),
                gamma=float(cond["gamma"]),
            )
            update_rank_stats(
                stats_by_condition[str(cond["name"])],
                ranked,
                actual_trifecta,
            )

    condition_results = {
        name: finalize_rank_stats(stats)
        for name, stats in stats_by_condition.items()
    }
    baseline_metrics = condition_results["baseline"]
    diffs = {}
    for name, metrics in condition_results.items():
        if name == "baseline":
            continue
        avg_rank_diff = None
        if (
            metrics["trifecta_avg_rank"] is not None
            and baseline_metrics["trifecta_avg_rank"] is not None
        ):
            avg_rank_diff = round(
                float(metrics["trifecta_avg_rank"]) - float(baseline_metrics["trifecta_avg_rank"]),
                2,
            )
        diffs[name] = {
            "exact_hitrate": round(metrics["exact_hitrate"] - baseline_metrics["exact_hitrate"], 4),
            "top1_hitrate": round(metrics["top1_hitrate"] - baseline_metrics["top1_hitrate"], 4),
            "in_set_rate": round(metrics["in_set_rate"] - baseline_metrics["in_set_rate"], 4),
            "trifecta_avg_rank": avg_rank_diff,
            "not_in_60": int(metrics["rank_dist"]["not_in_60"]) - int(baseline_metrics["rank_dist"]["not_in_60"]),
        }
    approx_rescore_report = {
        "target": "fixed_candidate_set_rescore_comparison",
        "candidate_generation": {
            "top_n_win": top_n_win,
            "max_trifecta_combinations": max_trifecta_combinations,
        },
        "conditions": rescore_conditions,
        "metrics_by_condition": condition_results,
        "diff_vs_baseline": diffs,
    }
    OUT_APPROX_RESCORE.write_text(
        json.dumps(approx_rescore_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    not_in_df = pd.DataFrame(not_in_60_stage_rows)
    if len(not_in_df) > 0:
        stage_counts = not_in_df["stage"].value_counts().to_dict()
        report_rows = int(len(not_in_df))
        not_in_60_stage_report = {
            "target": "not_in_60_races_only",
            "total_rows": report_rows,
            "stage_breakdown": stage_counts,
            "stage_rate": {
                stage: round(count / report_rows, 4)
                for stage, count in stage_counts.items()
            },
            "first_win_rank_summary": {
                "median": round(float(not_in_df["actual_first_win_rank"].dropna().median()), 2)
                if len(not_in_df["actual_first_win_rank"].dropna()) > 0
                else None,
                "mean": round(float(not_in_df["actual_first_win_rank"].dropna().mean()), 2)
                if len(not_in_df["actual_first_win_rank"].dropna()) > 0
                else None,
            },
            "cut_by_top60_ratio": round(
                float((not_in_df["stage"] == "cut_by_top60").mean()),
                4,
            ),
            "classification_rules": {
                "first_miss": "actual first lane is not in top_n_win first-lane set",
                "pair_miss": "actual first lane is in top_n_win set but exact trifecta is not in all generated candidates",
                "cut_by_top60": "exact trifecta exists in all generated candidates but not in top60 list",
                "other_or_unknown": "invalid trifecta format or uncategorizable case",
            },
            "sample_rows": not_in_df.head(10).to_dict(orient="records"),
        }
    else:
        not_in_60_stage_report = {
            "target": "not_in_60_races_only",
            "total_rows": 0,
            "message": "No not_in_60 rows were found.",
        }
    OUT_NOT_IN_60_STAGE.write_text(
        json.dumps(not_in_60_stage_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    cut_df = not_in_df[not_in_df["stage"] == "cut_by_top60"].copy() if len(not_in_df) > 0 else pd.DataFrame()
    if len(cut_df) > 0:
        cut_df["actual_theoretical_rank_all_candidates"] = pd.to_numeric(
            cut_df["actual_theoretical_rank_all_candidates"],
            errors="coerce",
        )
        rank_series = cut_df["actual_theoretical_rank_all_candidates"].dropna()
        rank_buckets = {
            "rank_61_80": int(((rank_series >= 61) & (rank_series <= 80)).sum()),
            "rank_81_100": int(((rank_series >= 81) & (rank_series <= 100)).sum()),
            "rank_101_120": int(((rank_series >= 101) & (rank_series <= 120)).sum()),
            "rank_121_plus": int((rank_series >= 121).sum()),
        }
        total_cut = int(len(rank_series))
        cut_rank_report = {
            "target": "cut_by_top60_only",
            "total_rows": total_cut,
            "rank_bucket_distribution": rank_buckets,
            "rank_bucket_rate": {
                key: round(val / total_cut, 4) if total_cut else 0.0
                for key, val in rank_buckets.items()
            },
            "capture_rate": {
                "top_60_capture": round(float((rank_series <= 60).mean()), 4) if total_cut else 0.0,
                "top_80_capture": round(float((rank_series <= 80).mean()), 4) if total_cut else 0.0,
                "top_100_capture": round(float((rank_series <= 100).mean()), 4) if total_cut else 0.0,
                "top_120_capture": round(float((rank_series <= 120).mean()), 4) if total_cut else 0.0,
            },
            "rank_summary": {
                "mean": round(float(rank_series.mean()), 2) if total_cut else None,
                "median": round(float(rank_series.median()), 2) if total_cut else None,
                "p25": round(float(rank_series.quantile(0.25)), 2) if total_cut else None,
                "p75": round(float(rank_series.quantile(0.75)), 2) if total_cut else None,
            },
            "sample_rows": cut_df.head(10).to_dict(orient="records"),
        }
    else:
        cut_rank_report = {
            "target": "cut_by_top60_only",
            "total_rows": 0,
            "message": "No cut_by_top60 rows were found.",
        }
    OUT_CUT_BY_TOP60_RANK.write_text(
        json.dumps(cut_rank_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # max_trifecta_combinations sensitivity on the same candidate-generation logic.
    max_values = [60, 80, 100, 120]
    max_stats: dict[int, dict[str, object]] = {v: init_rank_stats() for v in max_values}
    candidate_include_counter: dict[int, int] = {v: 0 for v in max_values}
    candidate_include_total = 0
    for race_id in race_ids:
        actual = actual_map.get(race_id)
        if actual is None:
            continue
        actual_winner = str(actual["actual_winner"]).strip()
        actual_trifecta = str(actual["actual_trifecta"]).strip()
        race_data = merged_df[merged_df["race_id"].astype(str) == race_id].copy()
        if race_data.empty:
            continue

        race_ranked_lanes = (
            race_data.sort_values("win_proba_norm", ascending=False)["lane"]
            .astype(int)
            .astype(str)
            .tolist()
        )
        top_first_set = set(race_ranked_lanes[:top_n_win])
        candidate_include_total += 1
        for v in max_values:
            if actual_winner in top_first_set:
                candidate_include_counter[v] += 1

        for v in max_values:
            candidates_v = generate_trifecta_candidates_for_race(
                race_data,
                top_n_win=top_n_win,
                max_trifecta_combinations=v,
            )
            if not candidates_v:
                continue
            update_rank_stats(max_stats[v], candidates_v, actual_trifecta)

    max_metrics = {}
    for v in max_values:
        m = finalize_rank_stats(max_stats[v])
        m["candidate_include_rate"] = (
            round(candidate_include_counter[v] / candidate_include_total, 4)
            if candidate_include_total
            else 0.0
        )
        max_metrics[str(v)] = m

    base = max_metrics["60"]
    max_diff_vs_60 = {}
    for v in max_values:
        key = str(v)
        if key == "60":
            continue
        cur = max_metrics[key]
        avg_rank_diff = None
        if cur["trifecta_avg_rank"] is not None and base["trifecta_avg_rank"] is not None:
            avg_rank_diff = round(float(cur["trifecta_avg_rank"]) - float(base["trifecta_avg_rank"]), 2)
        max_diff_vs_60[key] = {
            "exact_hitrate": round(cur["exact_hitrate"] - base["exact_hitrate"], 4),
            "top1_hitrate": round(cur["top1_hitrate"] - base["top1_hitrate"], 4),
            "trifecta_avg_rank": avg_rank_diff,
            "not_in_60": int(cur["rank_dist"]["not_in_60"]) - int(base["rank_dist"]["not_in_60"]),
            "candidate_include_rate": round(cur["candidate_include_rate"] - base["candidate_include_rate"], 4),
        }

    max_trifecta_report = {
        "target": "max_trifecta_combinations_sensitivity",
        "top_n_win": top_n_win,
        "max_values": max_values,
        "metrics_by_max": max_metrics,
        "diff_vs_60": max_diff_vs_60,
    }
    OUT_MAX_TRIFECTA_COMPARISON.write_text(
        json.dumps(max_trifecta_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Fixed top60 selection-method comparison on the same candidate pool.
    selection_methods = {
        "baseline_top60": select_top60_baseline,
        "per_first_balanced_60": select_top60_per_first_balanced,
        "diverse_pair_60": select_top60_diverse_pair,
    }
    selection_stats = {name: init_rank_stats() for name in selection_methods}
    for race_id in race_ids:
        actual = actual_map.get(race_id)
        if actual is None:
            continue
        actual_trifecta = str(actual["actual_trifecta"]).strip()
        race_data = merged_df[merged_df["race_id"].astype(str) == race_id].copy()
        if race_data.empty:
            continue
        full_candidates = generate_trifecta_candidates_for_race(
            race_data,
            top_n_win=top_n_win,
            max_trifecta_combinations=100000,
        )
        if not full_candidates:
            continue
        for name, selector in selection_methods.items():
            picked = selector(full_candidates)
            update_rank_stats(selection_stats[name], picked, actual_trifecta)

    selection_metrics = {
        name: finalize_rank_stats(stats)
        for name, stats in selection_stats.items()
    }
    selection_base = selection_metrics["baseline_top60"]
    selection_diff = {}
    for name, metrics in selection_metrics.items():
        if name == "baseline_top60":
            continue
        avg_rank_diff = None
        if (
            metrics["trifecta_avg_rank"] is not None
            and selection_base["trifecta_avg_rank"] is not None
        ):
            avg_rank_diff = round(
                float(metrics["trifecta_avg_rank"]) - float(selection_base["trifecta_avg_rank"]),
                2,
            )
        selection_diff[name] = {
            "exact_hitrate": round(metrics["exact_hitrate"] - selection_base["exact_hitrate"], 4),
            "top1_hitrate": round(metrics["top1_hitrate"] - selection_base["top1_hitrate"], 4),
            "in_set_rate": round(metrics["in_set_rate"] - selection_base["in_set_rate"], 4),
            "trifecta_avg_rank": avg_rank_diff,
            "not_in_60": int(metrics["rank_dist"]["not_in_60"]) - int(selection_base["rank_dist"]["not_in_60"]),
        }
    top60_selection_report = {
        "target": "fixed_60_selection_method_comparison",
        "top_n_win": top_n_win,
        "selection_methods": list(selection_methods.keys()),
        "metrics_by_method": selection_metrics,
        "diff_vs_baseline_top60": selection_diff,
    }
    OUT_TOP60_SELECTION_COMPARISON.write_text(
        json.dumps(top60_selection_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # per_first_balanced_60 tuning comparison.
    tuning_patterns = [
        {"name": "baseline_top60", "selector": "baseline", "min_per_first": None, "fill_mode": None},
        {"name": "per_first_m8_global", "selector": "pattern", "min_per_first": 8, "fill_mode": "global"},
        {"name": "per_first_m10_global", "selector": "pattern", "min_per_first": 10, "fill_mode": "global"},
        {"name": "per_first_m12_global", "selector": "pattern", "min_per_first": 12, "fill_mode": "global"},
        {"name": "per_first_m10_lane_round_robin", "selector": "pattern", "min_per_first": 10, "fill_mode": "lane_round_robin"},
    ]
    tuning_stats = {p["name"]: init_rank_stats() for p in tuning_patterns}
    for race_id in race_ids:
        actual = actual_map.get(race_id)
        if actual is None:
            continue
        actual_trifecta = str(actual["actual_trifecta"]).strip()
        race_data = merged_df[merged_df["race_id"].astype(str) == race_id].copy()
        if race_data.empty:
            continue
        full_candidates = generate_trifecta_candidates_for_race(
            race_data,
            top_n_win=top_n_win,
            max_trifecta_combinations=100000,
        )
        if not full_candidates:
            continue

        for p in tuning_patterns:
            name = str(p["name"])
            if p["selector"] == "baseline":
                picked = select_top60_baseline(full_candidates)
            else:
                picked = select_top60_per_first_pattern(
                    full_candidates,
                    min_per_first=int(p["min_per_first"]),
                    fill_mode=str(p["fill_mode"]),
                )
            update_rank_stats(tuning_stats[name], picked, actual_trifecta)

    tuning_metrics = {
        name: finalize_rank_stats(stats)
        for name, stats in tuning_stats.items()
    }
    tuning_base = tuning_metrics["baseline_top60"]
    tuning_current = tuning_metrics["per_first_m10_global"]
    tuning_diff_vs_baseline = {}
    tuning_diff_vs_current = {}
    for p in tuning_patterns:
        name = str(p["name"])
        if name == "baseline_top60":
            continue
        cur = tuning_metrics[name]
        avg_rank_diff_base = None
        avg_rank_diff_current = None
        if cur["trifecta_avg_rank"] is not None and tuning_base["trifecta_avg_rank"] is not None:
            avg_rank_diff_base = round(float(cur["trifecta_avg_rank"]) - float(tuning_base["trifecta_avg_rank"]), 2)
        if cur["trifecta_avg_rank"] is not None and tuning_current["trifecta_avg_rank"] is not None:
            avg_rank_diff_current = round(float(cur["trifecta_avg_rank"]) - float(tuning_current["trifecta_avg_rank"]), 2)

        tuning_diff_vs_baseline[name] = {
            "exact_hitrate": round(cur["exact_hitrate"] - tuning_base["exact_hitrate"], 4),
            "top1_hitrate": round(cur["top1_hitrate"] - tuning_base["top1_hitrate"], 4),
            "in_set_rate": round(cur["in_set_rate"] - tuning_base["in_set_rate"], 4),
            "trifecta_avg_rank": avg_rank_diff_base,
            "not_in_60": int(cur["rank_dist"]["not_in_60"]) - int(tuning_base["rank_dist"]["not_in_60"]),
        }
        tuning_diff_vs_current[name] = {
            "exact_hitrate": round(cur["exact_hitrate"] - tuning_current["exact_hitrate"], 4),
            "top1_hitrate": round(cur["top1_hitrate"] - tuning_current["top1_hitrate"], 4),
            "in_set_rate": round(cur["in_set_rate"] - tuning_current["in_set_rate"], 4),
            "trifecta_avg_rank": avg_rank_diff_current,
            "not_in_60": int(cur["rank_dist"]["not_in_60"]) - int(tuning_current["rank_dist"]["not_in_60"]),
        }

    per_first_tuning_report = {
        "target": "per_first_balanced_60_tuning",
        "top_n_win": top_n_win,
        "patterns": tuning_patterns,
        "metrics_by_pattern": tuning_metrics,
        "diff_vs_baseline_top60": tuning_diff_vs_baseline,
        "diff_vs_current_per_first_m10_global": tuning_diff_vs_current,
        "note": "top1_hitrate in this script is currently counted when exact trifecta rank == 1 (same event as exact_hitrate).",
    }
    OUT_PER_FIRST_TUNING.write_text(
        json.dumps(per_first_tuning_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    active_mode = str(candidate_cfg.get("selection_mode", "baseline_top60"))
    mode_alias = {
        "baseline_top60": "baseline_top60",
        "per_first_m12_global": "per_first_m12_global",
    }
    active_key = mode_alias.get(active_mode, "baseline_top60")
    active_metrics = tuning_metrics.get(active_key, tuning_metrics["baseline_top60"])
    baseline_metrics_for_integration = tuning_metrics["baseline_top60"]
    avg_rank_diff_integration = None
    if (
        active_metrics["trifecta_avg_rank"] is not None
        and baseline_metrics_for_integration["trifecta_avg_rank"] is not None
    ):
        avg_rank_diff_integration = round(
            float(active_metrics["trifecta_avg_rank"]) - float(baseline_metrics_for_integration["trifecta_avg_rank"]),
            2,
        )
    integration_report = {
        "target": "per_first_m12_global_integration_check",
        "candidate_generation_config": candidate_cfg,
        "active_mode": active_mode,
        "active_mode_effective_key": active_key,
        "baseline_top60": baseline_metrics_for_integration,
        "active_mode_metrics": active_metrics,
        "diff_active_vs_baseline": {
            "exact_hitrate": round(active_metrics["exact_hitrate"] - baseline_metrics_for_integration["exact_hitrate"], 4),
            "in_set_rate": round(active_metrics["in_set_rate"] - baseline_metrics_for_integration["in_set_rate"], 4),
            "not_in_60": int(active_metrics["rank_dist"]["not_in_60"]) - int(baseline_metrics_for_integration["rank_dist"]["not_in_60"]),
            "trifecta_avg_rank": avg_rank_diff_integration,
        },
    }
    OUT_PER_FIRST_M12_INTEGRATION_CHECK.write_text(
        json.dumps(integration_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ROI / recovery / drawdown evaluation (analysis only; fixed stake=1 per race).
    bt_eval = backtest_df.copy()
    bt_eval["result_available"] = to_bool(bt_eval["result_available"])
    bt_eval = bt_eval[bt_eval["result_available"]].copy()
    bt_eval["race_id"] = bt_eval["race_id"].astype(str)
    bt_eval["date"] = pd.to_datetime(bt_eval.get("date"), errors="coerce")
    bt_eval["official_odds"] = pd.to_numeric(bt_eval.get("official_odds"), errors="coerce")
    bt_eval["settled_odds"] = pd.to_numeric(bt_eval.get("settled_odds"), errors="coerce")
    bt_eval = bt_eval.sort_values(["date", "race_id"]).drop_duplicates(subset=["race_id"], keep="last")

    roi_methods = {
        "baseline_top60": lambda rows: select_top60_baseline(rows),
        "per_first_m12_global": lambda rows: select_top60_per_first_pattern(
            rows, min_per_first=12, fill_mode="global"
        ),
    }
    roi_method_results: dict[str, dict[str, object]] = {}
    for method_name, selector in roi_methods.items():
        race_details: list[dict[str, object]] = []
        profits: list[float] = []
        hit_count = 0
        payout_total = 0.0
        stake_total = 0.0
        missing_odds_rows = 0

        for _, bt_row in bt_eval.iterrows():
            race_id = str(bt_row["race_id"])
            actual = actual_map.get(race_id)
            if actual is None:
                continue
            actual_trifecta = str(actual["actual_trifecta"]).strip()
            race_data = merged_df[merged_df["race_id"].astype(str) == race_id].copy()
            if race_data.empty:
                continue
            full_candidates = generate_trifecta_candidates_for_race(
                race_data,
                top_n_win=top_n_win,
                max_trifecta_combinations=100000,
            )
            if not full_candidates:
                continue
            picked = selector(full_candidates)
            if len(picked) == 0:
                continue

            predicted = str(picked[0]["trifecta"]).strip()
            hit = predicted == actual_trifecta
            stake = 1.0
            odds = bt_row["official_odds"]
            if pd.isna(odds) or float(odds) <= 0:
                odds = bt_row["settled_odds"]
            if pd.isna(odds) or float(odds) <= 0:
                odds = 0.0
                missing_odds_rows += 1
            odds = float(odds)
            payout = odds if hit else 0.0
            profit = payout - stake

            stake_total += stake
            payout_total += payout
            profits.append(profit)
            if hit:
                hit_count += 1
            race_details.append(
                {
                    "race_id": race_id,
                    "date": bt_row["date"].strftime("%Y-%m-%d") if pd.notna(bt_row["date"]) else None,
                    "predicted_trifecta": predicted,
                    "actual_trifecta": actual_trifecta,
                    "hit": bool(hit),
                    "odds_used": odds,
                    "profit": round(float(profit), 4),
                }
            )

        buy_count = len(race_details)
        profit_total = payout_total - stake_total
        roi_method_results[method_name] = {
            "result_available_rows": int(len(bt_eval)),
            "evaluated_rows": int(buy_count),
            "missing_odds_rows": int(missing_odds_rows),
            "stake_total": round(float(stake_total), 4),
            "payout_total": round(float(payout_total), 4),
            "profit_total": round(float(profit_total), 4),
            "roi": round(float(profit_total / stake_total), 4) if stake_total > 0 else None,
            "recovery_rate": round(float(payout_total / stake_total), 4) if stake_total > 0 else None,
            "buy_count": int(buy_count),
            "hit_count": int(hit_count),
            "hit_rate": round(float(hit_count / buy_count), 4) if buy_count > 0 else None,
            "max_drawdown": _max_drawdown(profits),
        }

    baseline_roi = roi_method_results["baseline_top60"]
    m12_roi = roi_method_results["per_first_m12_global"]
    roi_drawdown_report = {
        "target": "roi_recovery_drawdown_comparison",
        "stake_model": "fixed_stake_per_race=1.0",
        "odds_priority": "official_odds_then_settled_odds_else_zero",
        "methods": roi_method_results,
        "diff_m12_vs_baseline": {
            "profit_total": round(float(m12_roi["profit_total"] - baseline_roi["profit_total"]), 4),
            "roi": round(float(m12_roi["roi"] - baseline_roi["roi"]), 4) if baseline_roi["roi"] is not None and m12_roi["roi"] is not None else None,
            "recovery_rate": round(float(m12_roi["recovery_rate"] - baseline_roi["recovery_rate"]), 4) if baseline_roi["recovery_rate"] is not None and m12_roi["recovery_rate"] is not None else None,
            "hit_rate": round(float(m12_roi["hit_rate"] - baseline_roi["hit_rate"]), 4) if baseline_roi["hit_rate"] is not None and m12_roi["hit_rate"] is not None else None,
            "max_drawdown": round(float(m12_roi["max_drawdown"] - baseline_roi["max_drawdown"]), 4),
        },
    }
    OUT_ROI_DRAWDOWN_EVALUATION.write_text(
        json.dumps(roi_drawdown_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    metric_definition_report = {
        "target": "metric_definition_check",
        "definitions": {
            "exact_hitrate": "actual trifecta rank == 1",
            "winner_top1_hitrate": "predicted top-trifecta first lane == actual winner lane",
            "top1_hitrate": "legacy metric (kept for compatibility): actual trifecta rank == 1",
        },
        "compatibility_note": "top1_hitrate is currently equivalent to exact_hitrate by definition.",
        "sample_from_active_mode": {
            "exact_hitrate": active_metrics["exact_hitrate"],
            "winner_top1_hitrate": active_metrics["winner_top1_hitrate"],
            "top1_hitrate_legacy": active_metrics["top1_hitrate"],
        },
        "consistency_checks": {
            "legacy_top1_equals_exact_all_tuning_patterns": all(
                abs(float(m["top1_hitrate"]) - float(m["exact_hitrate"])) < 1e-12
                for m in tuning_metrics.values()
            ),
            "winner_top1_differs_from_exact_any_pattern": any(
                abs(float(m["winner_top1_hitrate"]) - float(m["exact_hitrate"])) > 1e-12
                for m in tuning_metrics.values()
            ),
        },
    }
    OUT_METRIC_DEFINITION_CHECK.write_text(
        json.dumps(metric_definition_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # betting strategy comparison (per_first_m12_global fixed, allocation rules only).
    stake_per_race = 1.0
    betting_methods = ["flat_bet", "prob_proportional", "ev_proportional"]
    betting_results: dict[str, dict[str, object]] = {
        method: {
            "result_available_rows": int(len(bt_eval)),
            "evaluated_rows": 0,
            "missing_odds_rows": 0,
            "total_stake": 0.0,
            "total_return": 0.0,
            "profit": 0.0,
            "hit_count": 0,
            "hit_rate": None,
            "roi": None,
            "recovery_rate": None,
            "max_drawdown": None,
            "longest_losing_streak": 0,
        }
        for method in betting_methods
    }
    betting_profits: dict[str, list[float]] = {method: [] for method in betting_methods}

    for _, bt_row in bt_eval.iterrows():
        race_id = str(bt_row["race_id"])
        actual = actual_map.get(race_id)
        if actual is None:
            continue
        actual_trifecta = str(actual["actual_trifecta"]).strip()
        race_data = merged_df[merged_df["race_id"].astype(str) == race_id].copy()
        if race_data.empty:
            continue

        full_candidates = generate_trifecta_candidates_for_race(
            race_data,
            top_n_win=top_n_win,
            max_trifecta_combinations=100000,
        )
        if not full_candidates:
            continue
        picked = select_top60_per_first_pattern(
            full_candidates,
            min_per_first=12,
            fill_mode="global",
        )
        if len(picked) == 0:
            continue

        odds = pd.to_numeric(bt_row.get("official_odds"), errors="coerce")
        if pd.isna(odds) or float(odds) <= 0:
            odds = pd.to_numeric(bt_row.get("settled_odds"), errors="coerce")
        missing_odds = pd.isna(odds) or float(odds) <= 0
        odds_value = float(odds) if not missing_odds else 0.0

        approx_probs = [float(row.get("approx_prob", 0.0)) for row in picked]
        trifectas = [str(row.get("trifecta", "")).strip() for row in picked]

        for method in betting_methods:
            if method == "flat_bet":
                raw_weights = [1.0] * len(picked)
            elif method == "prob_proportional":
                raw_weights = approx_probs
            else:
                # simple EV proxy: max(approx_prob * odds - 1, 0)
                raw_weights = [max((p * odds_value) - 1.0, 0.0) for p in approx_probs]

            weights = _normalize_positive_weights(raw_weights)
            stake_on_actual = 0.0
            for idx, trifecta in enumerate(trifectas):
                if trifecta == actual_trifecta:
                    stake_on_actual = float(weights[idx]) * stake_per_race
                    break

            payout = stake_on_actual * odds_value if odds_value > 0 else 0.0
            profit = payout - stake_per_race
            hit = stake_on_actual > 0.0

            betting_results[method]["evaluated_rows"] += 1
            if missing_odds:
                betting_results[method]["missing_odds_rows"] += 1
            betting_results[method]["total_stake"] += stake_per_race
            betting_results[method]["total_return"] += payout
            betting_results[method]["profit"] += profit
            if hit:
                betting_results[method]["hit_count"] += 1
            betting_profits[method].append(float(profit))

    for method in betting_methods:
        result = betting_results[method]
        total_stake = float(result["total_stake"])
        total_return = float(result["total_return"])
        profit = float(result["profit"])
        evaluated_rows = int(result["evaluated_rows"])
        hit_count = int(result["hit_count"])
        result["total_stake"] = round(total_stake, 4)
        result["total_return"] = round(total_return, 4)
        result["profit"] = round(profit, 4)
        result["roi"] = round(profit / total_stake, 4) if total_stake > 0 else None
        result["recovery_rate"] = round(total_return / total_stake, 4) if total_stake > 0 else None
        result["hit_rate"] = round(hit_count / evaluated_rows, 4) if evaluated_rows > 0 else None
        result["max_drawdown"] = _max_drawdown(betting_profits[method])
        result["longest_losing_streak"] = _longest_losing_streak(betting_profits[method])

    flat_result = betting_results["flat_bet"]
    diff_vs_flat = {}
    for method in betting_methods:
        if method == "flat_bet":
            continue
        cur = betting_results[method]
        diff_vs_flat[method] = {
            "profit_diff": round(float(cur["profit"]) - float(flat_result["profit"]), 4),
            "roi_diff": round(float(cur["roi"]) - float(flat_result["roi"]), 4)
            if cur["roi"] is not None and flat_result["roi"] is not None
            else None,
            "max_drawdown_diff": round(float(cur["max_drawdown"]) - float(flat_result["max_drawdown"]), 4),
        }

    betting_strategy_report = {
        "target": "betting_strategy_comparison",
        "candidate_mode_fixed": "per_first_m12_global",
        "stake_model": "race_total_stake_fixed_to_1.0",
        "methods": betting_results,
        "diff_vs_flat_bet": diff_vs_flat,
        "ev_proportional_note": "simple_ev_proxy=max(approx_prob*odds-1,0)",
    }
    OUT_BETTING_STRATEGY_COMPARISON.write_text(
        json.dumps(betting_strategy_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # race-level filter comparison (candidate mode fixed, flat stake fixed).
    race_eval_rows: list[dict[str, object]] = []
    for _, bt_row in bt_eval.iterrows():
        race_id = str(bt_row["race_id"])
        actual = actual_map.get(race_id)
        if actual is None:
            continue
        actual_trifecta = str(actual["actual_trifecta"]).strip()
        race_data = merged_df[merged_df["race_id"].astype(str) == race_id].copy()
        if race_data.empty:
            continue

        full_candidates = generate_trifecta_candidates_for_race(
            race_data,
            top_n_win=top_n_win,
            max_trifecta_combinations=100000,
        )
        if not full_candidates:
            continue
        picked = select_top60_per_first_pattern(
            full_candidates,
            min_per_first=12,
            fill_mode="global",
        )
        if len(picked) == 0:
            continue

        odds = pd.to_numeric(bt_row.get("official_odds"), errors="coerce")
        if pd.isna(odds) or float(odds) <= 0:
            odds = pd.to_numeric(bt_row.get("settled_odds"), errors="coerce")
        if pd.isna(odds) or float(odds) <= 0:
            odds = 0.0
        odds_value = float(odds)

        race_sorted = race_data.copy()
        race_sorted["win_proba_norm"] = pd.to_numeric(race_sorted["win_proba_norm"], errors="coerce").fillna(0.0)
        race_sorted = race_sorted.sort_values("win_proba_norm", ascending=False).reset_index(drop=True)
        first_gap = 0.0
        if len(race_sorted) >= 2:
            first_gap = float(race_sorted.iloc[0]["win_proba_norm"] - race_sorted.iloc[1]["win_proba_norm"])

        top_score_gap = 0.0
        if len(picked) >= 2:
            top_score_gap = float(picked[0]["approx_prob"]) - float(picked[1]["approx_prob"])

        picked_probs = [float(r.get("approx_prob", 0.0)) for r in picked]
        concentration_top5 = 0.0
        total_prob = float(sum(picked_probs))
        if total_prob > 0:
            concentration_top5 = float(sum(picked_probs[:5]) / total_prob)

        picked_count = len(picked)
        actual_in_set = any(str(row["trifecta"]).strip() == actual_trifecta for row in picked)
        stake_on_actual = (1.0 / picked_count) if actual_in_set and picked_count > 0 else 0.0
        payout = (stake_on_actual * odds_value) if odds_value > 0 else 0.0
        profit = payout - 1.0

        race_eval_rows.append(
            {
                "race_id": race_id,
                "date": str(bt_row.get("date", "")),
                "first_gap": first_gap,
                "top_score_gap": top_score_gap,
                "concentration_top5": concentration_top5,
                "picked_count": int(picked_count),
                "odds_used": odds_value,
                "hit": bool(actual_in_set),
                "stake_on_actual": float(stake_on_actual),
                "profit_if_bought": float(profit),
                "missing_odds": bool(odds_value <= 0),
            }
        )

    race_eval_df = pd.DataFrame(race_eval_rows)
    q_first_gap = float(race_eval_df["first_gap"].quantile(0.6)) if len(race_eval_df) > 0 else 0.0
    q_top_score_gap = (
        float(race_eval_df["top_score_gap"].quantile(0.6)) if len(race_eval_df) > 0 else 0.0
    )
    q_concentration = (
        float(race_eval_df["concentration_top5"].quantile(0.6)) if len(race_eval_df) > 0 else 0.0
    )

    def build_filter_metrics(
        source_df: pd.DataFrame,
        first_gap_threshold: float,
        top_score_gap_threshold: float,
        concentration_threshold: float,
        *,
        use_and_filter: bool = False,
    ) -> dict[str, dict[str, object]]:
        active_df = source_df.copy()
        if active_df.empty:
            return {
                "no_filter": {
                    "result_available_rows": 0,
                    "bought_races": 0,
                    "skipped_races": 0,
                    "missing_odds_rows": 0,
                    "total_stake": 0.0,
                    "total_return": 0.0,
                    "profit": 0.0,
                    "hit_count": 0,
                    "hit_rate": None,
                    "roi": None,
                    "recovery_rate": None,
                    "max_drawdown": 0.0,
                    "longest_losing_streak": 0,
                }
            }

        race_filters = {
            "no_filter": lambda row: True,
            "first_gap_filter": lambda row: float(row["first_gap"]) >= first_gap_threshold,
            "top_score_gap_filter": lambda row: float(row["top_score_gap"]) >= top_score_gap_threshold,
            "concentration_filter": lambda row: float(row["concentration_top5"]) >= concentration_threshold,
        }
        if use_and_filter:
            race_filters["and_filter"] = lambda row: (
                float(row["first_gap"]) >= first_gap_threshold
                and float(row["concentration_top5"]) >= concentration_threshold
            )

        metrics: dict[str, dict[str, object]] = {}
        for filter_name, filter_fn in race_filters.items():
            bought = active_df[active_df.apply(filter_fn, axis=1)].copy()
            bought_count = int(len(bought))
            skipped_count = int(len(active_df) - bought_count)
            total_stake = float(bought_count)
            total_return = float(bought["profit_if_bought"].sum() + total_stake) if bought_count > 0 else 0.0
            profit = float(total_return - total_stake)
            hit_count = int(bought["hit"].sum()) if bought_count > 0 else 0
            hit_rate = round(float(hit_count / bought_count), 4) if bought_count > 0 else None
            roi = round(float(profit / total_stake), 4) if total_stake > 0 else None
            recovery_rate = round(float(total_return / total_stake), 4) if total_stake > 0 else None
            max_drawdown = _max_drawdown(bought["profit_if_bought"].tolist()) if bought_count > 0 else 0.0
            longest_losing_streak = (
                _longest_losing_streak(bought["profit_if_bought"].tolist()) if bought_count > 0 else 0
            )
            missing_odds_rows = int(bought["missing_odds"].sum()) if bought_count > 0 else 0

            metrics[filter_name] = {
                "result_available_rows": int(len(race_eval_df)),
                "bought_races": bought_count,
                "skipped_races": skipped_count,
                "missing_odds_rows": missing_odds_rows,
                "total_stake": round(total_stake, 4),
                "total_return": round(total_return, 4),
                "profit": round(profit, 4),
                "hit_count": hit_count,
                "hit_rate": hit_rate,
                "roi": roi,
                "recovery_rate": recovery_rate,
                "max_drawdown": max_drawdown,
                "longest_losing_streak": longest_losing_streak,
                "score": round(float(profit) - 0.5 * float(max_drawdown), 4),
            }

        return metrics

    race_filter_metrics = build_filter_metrics(
        race_eval_df,
        q_first_gap,
        q_top_score_gap,
        q_concentration,
        use_and_filter=True,
    )

    threshold_sweep: dict[str, dict[str, object]] = {}
    for level in [0.5, 0.6, 0.7, 0.8]:
        fg = float(race_eval_df["first_gap"].quantile(level)) if len(race_eval_df) > 0 else 0.0
        tg = float(race_eval_df["top_score_gap"].quantile(level)) if len(race_eval_df) > 0 else 0.0
        cg = float(race_eval_df["concentration_top5"].quantile(level)) if len(race_eval_df) > 0 else 0.0
        threshold_sweep[f"q{int(level * 100):02d}"] = {
            "thresholds": {
                "first_gap": round(fg, 8),
                "top_score_gap": round(tg, 8),
                "concentration_top5": round(cg, 8),
            },
            "methods": build_filter_metrics(race_eval_df, fg, tg, cg, use_and_filter=True),
        }

    def pick_best(method_name: str, metric_name: str) -> dict[str, object]:
        best_label = None
        best_metrics = None
        for label, payload in threshold_sweep.items():
            metrics = payload.get("methods", {}).get(method_name)
            if not metrics:
                continue
            value = metrics.get(metric_name)
            if value is None:
                continue
            if best_metrics is None or float(value) > float(best_metrics.get(metric_name, float("-inf"))):
                best_label = label
                best_metrics = metrics
        return {"threshold_label": best_label, "metrics": best_metrics}

    sweep_best_by_roi = {
        method: pick_best(method, "roi")
        for method in ["first_gap_filter", "top_score_gap_filter", "concentration_filter"]
    }
    sweep_best_by_profit = {
        method: pick_best(method, "profit")
        for method in ["first_gap_filter", "top_score_gap_filter", "concentration_filter"]
    }
    sweep_best_by_score = {
        method: pick_best(method, "score")
        for method in ["first_gap_filter", "top_score_gap_filter", "concentration_filter", "and_filter"]
    }

    baseline_filter = race_filter_metrics["no_filter"]
    race_filter_diff_vs_baseline = {}
    for filter_name, cur in race_filter_metrics.items():
        if filter_name == "no_filter":
            continue
        race_filter_diff_vs_baseline[filter_name] = {
            "roi_diff": round(float(cur["roi"]) - float(baseline_filter["roi"]), 4)
            if cur["roi"] is not None and baseline_filter["roi"] is not None
            else None,
            "profit_diff": round(float(cur["profit"]) - float(baseline_filter["profit"]), 4),
            "max_drawdown_diff": round(
                float(cur["max_drawdown"]) - float(baseline_filter["max_drawdown"]),
                4,
            ),
        }

    composite_lambda = 0.5
    for method_name, metrics in race_filter_metrics.items():
        roi = metrics.get("roi")
        max_dd = metrics.get("max_drawdown")
        metrics["score"] = (
            round(float(metrics.get("profit", 0.0)) - composite_lambda * float(max_dd), 4)
            if roi is not None
            else None
        )

    rolling_window_filter_comparison: list[dict[str, object]] = []
    if "date" in race_eval_df.columns:
        date_window_df = race_eval_df[["race_id", "date"]].dropna().drop_duplicates().copy()
        if len(date_window_df) > 0:
            date_window_df["date"] = pd.to_datetime(date_window_df["date"], errors="coerce")
            windows = build_date_windows(date_window_df.rename(columns={"date": "date"}))
            race_eval_lookup = race_eval_df.set_index("race_id")
            for window in windows:
                date_set = set(window["date_set"])
                window_race_ids = [
                    str(rid)
                    for rid, race_date in date_window_df.set_index("race_id")["date"].items()
                    if pd.notna(race_date) and race_date in date_set
                ]
                window_df = race_eval_lookup.loc[
                    [rid for rid in window_race_ids if rid in race_eval_lookup.index]
                ].reset_index()
                if window_df.empty:
                    continue
                win_q_first = float(window_df["first_gap"].quantile(0.6))
                win_q_top = float(window_df["top_score_gap"].quantile(0.6))
                win_q_conc = float(window_df["concentration_top5"].quantile(0.6))
                win_metrics = build_filter_metrics(
                    window_df,
                    win_q_first,
                    win_q_top,
                    win_q_conc,
                    use_and_filter=True,
                )
                rolling_window_filter_comparison.append(
                    {
                        "window": window["name"],
                        "date_count": int(len(date_set)),
                        "date_min": min(date_set).strftime("%Y-%m-%d"),
                        "date_max": max(date_set).strftime("%Y-%m-%d"),
                        "thresholds": {
                            "first_gap_q60": round(win_q_first, 8),
                            "top_score_gap_q60": round(win_q_top, 8),
                            "concentration_top5_q60": round(win_q_conc, 8),
                        },
                        "methods": win_metrics,
                    }
                )

    race_filter_report = {
        "target": "race_filter_comparison",
        "candidate_mode_fixed": "per_first_m12_global",
        "betting_mode_fixed": "flat_bet_race_total_stake_fixed_to_1.0",
        "thresholds": {
            "first_gap_q60": round(q_first_gap, 8),
            "top_score_gap_q60": round(q_top_score_gap, 8),
            "concentration_top5_q60": round(q_concentration, 8),
        },
        "methods": race_filter_metrics,
        "diff_vs_no_filter": race_filter_diff_vs_baseline,
        "threshold_sweep": threshold_sweep,
        "threshold_sweep_best_by_roi": sweep_best_by_roi,
        "threshold_sweep_best_by_profit": sweep_best_by_profit,
        "threshold_sweep_best_by_score": sweep_best_by_score,
        "rolling_window_filter_comparison": rolling_window_filter_comparison,
    }
    race_filter_output_path.write_text(
        json.dumps(race_filter_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # per_first_m12_global reproducibility across date windows.
    date_col = "date"
    if date_col not in merged_df.columns:
        if "date_x" in merged_df.columns:
            date_col = "date_x"
        elif "date_y" in merged_df.columns:
            date_col = "date_y"
    race_date_df = (
        merged_df[["race_id", date_col]]
        .rename(columns={date_col: "date"})
        .drop_duplicates(subset=["race_id"])
        .copy()
    )
    race_date_df["race_id"] = race_date_df["race_id"].astype(str)
    windows = build_date_windows(race_date_df)
    race_date_map = {
        str(row["race_id"]): pd.to_datetime(row["date"], errors="coerce")
        for _, row in race_date_df.iterrows()
    }

    repro_window_results = []
    for window in windows:
        window_name = str(window["name"])
        date_set = set(window["date_set"])
        method_stats = {
            "baseline_top60": init_rank_stats(),
            "per_first_m12_global": init_rank_stats(),
        }

        for race_id in race_ids:
            race_date = race_date_map.get(str(race_id))
            if pd.isna(race_date) or race_date not in date_set:
                continue
            actual = actual_map.get(race_id)
            if actual is None:
                continue
            actual_trifecta = str(actual["actual_trifecta"]).strip()
            race_data = merged_df[merged_df["race_id"].astype(str) == race_id].copy()
            if race_data.empty:
                continue
            full_candidates = generate_trifecta_candidates_for_race(
                race_data,
                top_n_win=top_n_win,
                max_trifecta_combinations=100000,
            )
            if not full_candidates:
                continue

            baseline_selected = select_top60_baseline(full_candidates)
            m12_selected = select_top60_per_first_pattern(
                full_candidates,
                min_per_first=12,
                fill_mode="global",
            )
            update_rank_stats(method_stats["baseline_top60"], baseline_selected, actual_trifecta)
            update_rank_stats(method_stats["per_first_m12_global"], m12_selected, actual_trifecta)

        baseline_metrics = finalize_rank_stats(method_stats["baseline_top60"])
        m12_metrics = finalize_rank_stats(method_stats["per_first_m12_global"])
        avg_rank_diff = None
        if (
            m12_metrics["trifecta_avg_rank"] is not None
            and baseline_metrics["trifecta_avg_rank"] is not None
        ):
            avg_rank_diff = round(
                float(m12_metrics["trifecta_avg_rank"]) - float(baseline_metrics["trifecta_avg_rank"]),
                2,
            )

        diff = {
            "exact_hitrate": round(m12_metrics["exact_hitrate"] - baseline_metrics["exact_hitrate"], 4),
            "in_set_rate": round(m12_metrics["in_set_rate"] - baseline_metrics["in_set_rate"], 4),
            "not_in_60": int(m12_metrics["rank_dist"]["not_in_60"]) - int(baseline_metrics["rank_dist"]["not_in_60"]),
            "trifecta_avg_rank": avg_rank_diff,
        }
        reproduced = (
            diff["exact_hitrate"] >= 0
            and diff["in_set_rate"] >= 0
            and diff["not_in_60"] <= 0
            and (diff["trifecta_avg_rank"] is None or diff["trifecta_avg_rank"] <= 0)
        )
        repro_window_results.append(
            {
                "window": window_name,
                "date_count": int(len(date_set)),
                "date_min": min(date_set).strftime("%Y-%m-%d"),
                "date_max": max(date_set).strftime("%Y-%m-%d"),
                "baseline_top60": baseline_metrics,
                "per_first_m12_global": m12_metrics,
                "diff_m12_vs_baseline": diff,
                "improvement_reproduced": bool(reproduced),
            }
        )

    reproducibility_report = {
        "target": "per_first_m12_global_reproducibility",
        "method_pair": ["baseline_top60", "per_first_m12_global"],
        "windowing": "date_sorted_three_way_split_plus_all",
        "windows": repro_window_results,
        "summary": {
            "window_count": len(repro_window_results),
            "reproduced_windows": int(sum(1 for w in repro_window_results if w["improvement_reproduced"])),
            "non_reproduced_windows": int(sum(1 for w in repro_window_results if not w["improvement_reproduced"])),
        },
    }
    OUT_PER_FIRST_M12_REPRO.write_text(
        json.dumps(reproducibility_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("[PART1] ablation")
    print(json.dumps(ablation_results, ensure_ascii=False, indent=2))
    print("\n[PART2] bottleneck")
    print(json.dumps(bottleneck, ensure_ascii=False, indent=2))
    print("\n[PART3] ranking_breakdown")
    print(json.dumps(breakdown_report, ensure_ascii=False, indent=2))
    print("\n[PART4] order_adjustment_comparison")
    print(json.dumps(order_adjustment_report, ensure_ascii=False, indent=2))
    print("\n[PART5] approx_rescore_comparison")
    print(json.dumps(approx_rescore_report, ensure_ascii=False, indent=2))
    print("\n[PART6] not_in_60_stage_breakdown")
    print(json.dumps(not_in_60_stage_report, ensure_ascii=False, indent=2))
    print("\n[PART7] cut_by_top60_rank_distribution")
    print(json.dumps(cut_rank_report, ensure_ascii=False, indent=2))
    print("\n[PART8] max_trifecta_comparison")
    print(json.dumps(max_trifecta_report, ensure_ascii=False, indent=2))
    print("\n[PART9] top60_selection_comparison")
    print(json.dumps(top60_selection_report, ensure_ascii=False, indent=2))
    print("\n[PART10] per_first_balanced_tuning")
    print(json.dumps(per_first_tuning_report, ensure_ascii=False, indent=2))
    print("\n[PART11] per_first_m12_global_repro")
    print(json.dumps(reproducibility_report, ensure_ascii=False, indent=2))
    print("\n[PART12] per_first_m12_global_integration_check")
    print(json.dumps(integration_report, ensure_ascii=False, indent=2))
    print("\n[PART13] roi_drawdown_evaluation")
    print(json.dumps(roi_drawdown_report, ensure_ascii=False, indent=2))
    print("\n[PART14] metric_definition_check")
    print(json.dumps(metric_definition_report, ensure_ascii=False, indent=2))
    print("\n[PART15] betting_strategy_comparison")
    print(json.dumps(betting_strategy_report, ensure_ascii=False, indent=2))
    print("\n[PART16] race_filter_comparison")
    print(json.dumps(race_filter_report, ensure_ascii=False, indent=2))
    print(f"\n[saved] {OUT_ABLATION}")
    print(f"[saved] {OUT_BOTTLENECK}")
    print(f"[saved] {OUT_BREAKDOWN}")
    print(f"[saved] {OUT_ORDER_ADJUST}")
    print(f"[saved] {OUT_APPROX_RESCORE}")
    print(f"[saved] {OUT_NOT_IN_60_STAGE}")
    print(f"[saved] {OUT_CUT_BY_TOP60_RANK}")
    print(f"[saved] {OUT_MAX_TRIFECTA_COMPARISON}")
    print(f"[saved] {OUT_TOP60_SELECTION_COMPARISON}")
    print(f"[saved] {OUT_PER_FIRST_TUNING}")
    print(f"[saved] {OUT_PER_FIRST_M12_REPRO}")
    print(f"[saved] {OUT_PER_FIRST_M12_INTEGRATION_CHECK}")
    print(f"[saved] {OUT_ROI_DRAWDOWN_EVALUATION}")
    print(f"[saved] {OUT_METRIC_DEFINITION_CHECK}")
    print(f"[saved] {OUT_BETTING_STRATEGY_COMPARISON}")
    print(f"[saved] {race_filter_output_path}")


if __name__ == "__main__":
    main()
