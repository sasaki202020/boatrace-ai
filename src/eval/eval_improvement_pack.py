import itertools
import json
from pathlib import Path

import pandas as pd


PROBA_CSV = Path("data/model_outputs/today_win_proba.csv")
FEATURES_CSV = Path("data/features/today_features.csv")
BACKTEST_CSV = Path("reports/backtest_race_results.csv")
CONFIG_JSON = Path("config/strategy_config.json")
OUT_JSON = Path("reports/eval_improvement_pack_result.json")

BEFORE = {
    "top1_hitrate": 0.1119,
    "trifecta_exact_hitrate": 0.0050,
    "candidate_include_rate": None,
}


def to_bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def normalize_within_race(df: pd.DataFrame, col: str, out_col: str) -> pd.DataFrame:
    out = df.copy()
    out[out_col] = out.groupby("race_id")[col].transform(lambda x: x / x.sum() if x.sum() > 0 else 0.0)
    return out


def build_actual_map(backtest_df: pd.DataFrame) -> dict[str, dict[str, str]]:
    bt = backtest_df[["race_id", "actual_trifecta", "result_available"]].drop_duplicates("race_id").copy()
    bt["result_available"] = to_bool_series(bt["result_available"])
    bt = bt[bt["result_available"]].copy()
    bt["actual_winner"] = bt["actual_trifecta"].astype(str).str.split("-").str[0].str.strip()
    return {
        str(r["race_id"]): {
            "actual_winner": str(r["actual_winner"]).strip(),
            "actual_trifecta": str(r["actual_trifecta"]).strip(),
        }
        for _, r in bt.iterrows()
    }


def generate_candidates_for_race(race_df: pd.DataFrame, top_n_win: int, max_combos: int) -> list[dict]:
    g = race_df.copy()
    g["win_proba_norm"] = pd.to_numeric(g["win_proba_norm"], errors="coerce").fillna(0.0)
    total_prob = g["win_proba_norm"].sum()
    if total_prob <= 0:
        return []
    g["win_proba_norm"] = g["win_proba_norm"] / total_prob

    lanes = g["lane"].astype(int).tolist()
    probs = g.set_index("lane")["win_proba_norm"].to_dict()
    top_boats = (
        g.sort_values("win_proba_norm", ascending=False)
        .head(top_n_win)["lane"]
        .astype(int)
        .tolist()
    )

    rows = []
    eps = 1e-10
    for c in itertools.permutations(lanes, 3):
        if int(c[0]) not in top_boats:
            continue
        p1 = probs[c[0]]
        remain1 = sum(probs[x] for x in lanes if x != c[0])
        remain2 = sum(probs[x] for x in lanes if x not in (c[0], c[1]))
        if p1 <= 0 or remain1 <= eps or remain2 <= eps:
            continue
        approx_prob = p1 * (probs[c[1]] / remain1) * (probs[c[2]] / remain2)
        if approx_prob <= 0:
            continue
        rows.append(
            {
                "trifecta": f"{int(c[0])}-{int(c[1])}-{int(c[2])}",
                "approx_prob": approx_prob,
            }
        )
    rows.sort(key=lambda x: x["approx_prob"], reverse=True)
    return rows[:max_combos]


def main():
    proba_df = pd.read_csv(PROBA_CSV)
    features_df = pd.read_csv(FEATURES_CSV)
    backtest_df = pd.read_csv(BACKTEST_CSV)
    config = json.loads(CONFIG_JSON.read_text(encoding="utf-8"))

    proba_df["lane"] = pd.to_numeric(proba_df["lane"], errors="coerce")
    features_df["lane"] = pd.to_numeric(features_df["lane"], errors="coerce")
    proba_df = proba_df.dropna(subset=["race_id", "lane"]).copy()
    features_df = features_df.dropna(subset=["race_id", "lane"]).copy()
    proba_df["lane"] = proba_df["lane"].astype(int)
    features_df["lane"] = features_df["lane"].astype(int)

    merged_df = proba_df.merge(features_df, on=["race_id", "lane"], how="left", suffixes=("", "_feat"))
    actual_map = build_actual_map(backtest_df)

    top_n_win = int(config["candidate_generation"]["top_n_win"])
    max_combos = int(config["candidate_generation"]["max_trifecta_combinations"])

    top1_hits = 0
    top2_hits = 0
    top3_hits = 0
    trifecta_hits = 0
    candidate_include = 0
    trifecta_in_candidate = 0
    rank_sum = 0
    rank_count = 0
    total = 0

    for race_id, race_data in merged_df.groupby("race_id"):
        actual = actual_map.get(str(race_id))
        if actual is None:
            continue

        actual_winner = str(actual["actual_winner"]).strip()
        actual_trifecta = str(actual["actual_trifecta"]).strip()

        race_data = race_data.sort_values("win_proba_norm", ascending=False).reset_index(drop=True)
        ranked_horses = race_data["lane"].astype(int).astype(str).tolist()
        if len(ranked_horses) < 3:
            continue

        if ranked_horses[0] == actual_winner:
            top1_hits += 1
        if actual_winner in ranked_horses[:2]:
            top2_hits += 1
        if actual_winner in ranked_horses[:3]:
            top3_hits += 1
        if actual_winner in ranked_horses[:top_n_win]:
            candidate_include += 1

        top3_tri = "-".join(ranked_horses[:3])
        if top3_tri == actual_trifecta:
            trifecta_hits += 1

        candidates = generate_candidates_for_race(race_data[["race_id", "lane", "win_proba_norm"]], top_n_win, max_combos)
        candidate_tris = [c["trifecta"] for c in candidates]
        if actual_trifecta in candidate_tris:
            trifecta_in_candidate += 1
            rank_sum += candidate_tris.index(actual_trifecta) + 1
            rank_count += 1

        total += 1

    result = {
        "total_races": int(total),
        "config_used": {
            "top_n_win": top_n_win,
            "max_trifecta_combinations": max_combos,
        },
        "after": {
            "top1_hitrate": round(top1_hits / total, 4) if total else 0.0,
            "top2_hitrate": round(top2_hits / total, 4) if total else 0.0,
            "top3_hitrate": round(top3_hits / total, 4) if total else 0.0,
            "trifecta_exact_hitrate": round(trifecta_hits / total, 4) if total else 0.0,
            "trifecta_exact_hits": int(trifecta_hits),
            "candidate_include_rate": round(candidate_include / total, 4) if total else 0.0,
            "trifecta_in_candidate_count": int(trifecta_in_candidate),
            "trifecta_avg_rank": round(rank_sum / rank_count, 2) if rank_count else None,
        },
        "before": BEFORE,
        "diff": {
            "top1_hitrate": round((top1_hits / total) - BEFORE["top1_hitrate"], 4) if total else None,
            "trifecta_exact_hitrate": round((trifecta_hits / total) - BEFORE["trifecta_exact_hitrate"], 4) if total else None,
        },
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[saved] {OUT_JSON}")


if __name__ == "__main__":
    main()
