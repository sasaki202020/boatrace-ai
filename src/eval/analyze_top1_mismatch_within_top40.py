import argparse
import itertools
import json
from pathlib import Path

import pandas as pd


def to_bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def build_candidates_for_race(group: pd.DataFrame, top_n_win: int, max_combos: int) -> pd.DataFrame:
    g = group.copy()
    g["win_proba_norm"] = pd.to_numeric(g["win_proba_norm"], errors="coerce").fillna(0.0)
    total = g["win_proba_norm"].sum()
    if total <= 0:
        return pd.DataFrame(columns=["race_id", "trifecta", "approx_prob", "rank"])
    g["win_proba_norm"] = g["win_proba_norm"] / total

    lanes = g["lane"].astype(int).tolist()
    probs = g.set_index("lane")["win_proba_norm"].to_dict()
    top_first = (
        g.sort_values("win_proba_norm", ascending=False)
        .head(top_n_win)["lane"]
        .astype(int)
        .tolist()
    )

    eps = 1e-10
    rows = []
    for c in itertools.permutations(lanes, 3):
        if int(c[0]) not in top_first:
            continue
        p1 = probs[c[0]]
        r1 = sum(probs[l] for l in lanes if l != c[0])
        r2 = sum(probs[l] for l in lanes if l not in (c[0], c[1]))
        if p1 <= 0 or r1 <= eps or r2 <= eps:
            continue
        p2 = probs[c[1]] / r1
        p3 = probs[c[2]] / r2
        approx_prob = min(p1 * p2 * p3, 1.0)
        if approx_prob <= 0:
            continue
        rows.append(
            {
                "race_id": g["race_id"].iloc[0],
                "trifecta": f"{int(c[0])}-{int(c[1])}-{int(c[2])}",
                "approx_prob": approx_prob,
            }
        )

    out = pd.DataFrame(rows).sort_values("approx_prob", ascending=False).head(max_combos).reset_index(drop=True)
    if not out.empty:
        out["rank"] = out.index + 1
    return out


def classify_mismatch(top1: str, actual: str) -> str:
    if top1 == actual:
        return "hit"
    try:
        p1, p2, p3 = [int(x) for x in str(top1).split("-")]
        a1, a2, a3 = [int(x) for x in str(actual).split("-")]
    except Exception:
        return "parse_error"

    if p1 != a1:
        return "first_diff_but_actual_in_top40"
    if p2 != a2 and p3 != a3:
        return "second_third_both_diff"
    if p2 != a2:
        return "second_only_diff"
    if p3 != a3:
        return "third_only_diff"
    return "other"


def main():
    parser = argparse.ArgumentParser(description="Top1 mismatch decomposition within top40-hit races")
    parser.add_argument("--today-win", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--backtest", default="reports/backtest_race_results.csv")
    parser.add_argument("--top-n-win", type=int, default=5)
    parser.add_argument("--max-combos", type=int, default=40)
    parser.add_argument("--out-detail", default="reports/top1_mismatch_within_top5_max40.csv")
    parser.add_argument("--out-summary", default="reports/top1_mismatch_within_top5_max40_summary.json")
    args = parser.parse_args()

    win = pd.read_csv(args.today_win)
    bt = pd.read_csv(args.backtest)
    bt = bt[["race_id", "actual_trifecta", "result_available"]].drop_duplicates("race_id").copy()
    bt["result_available"] = to_bool_series(bt["result_available"])
    bt = bt[bt["result_available"]].copy()

    race_ids = set(bt["race_id"].astype(str))
    win = win[win["race_id"].astype(str).isin(race_ids)].copy()
    win["lane"] = pd.to_numeric(win["lane"], errors="coerce")
    win["win_proba_norm"] = pd.to_numeric(win["win_proba_norm"], errors="coerce")
    win = win.dropna(subset=["race_id", "lane", "win_proba_norm"]).copy()

    all_cands = []
    for _, g in win.groupby("race_id"):
        c = build_candidates_for_race(g, top_n_win=args.top_n_win, max_combos=args.max_combos)
        if not c.empty:
            all_cands.append(c)
    candidates = pd.concat(all_cands, ignore_index=True) if all_cands else pd.DataFrame(columns=["race_id", "trifecta", "approx_prob", "rank"])

    # Races where actual trifecta is inside top40 candidates
    hit_rows = bt.merge(candidates, on="race_id", how="inner")
    hit_rows["is_actual_row"] = hit_rows["trifecta"].astype(str) == hit_rows["actual_trifecta"].astype(str)
    in_set_races = set(hit_rows.loc[hit_rows["is_actual_row"], "race_id"].astype(str))

    c_in = candidates[candidates["race_id"].astype(str).isin(in_set_races)].copy()
    top1 = c_in.sort_values(["race_id", "rank"], ascending=[True, True]).groupby("race_id", as_index=False).first()
    actual = bt[bt["race_id"].astype(str).isin(in_set_races)][["race_id", "actual_trifecta"]].copy()

    comp = top1[["race_id", "trifecta", "rank", "approx_prob"]].rename(
        columns={"trifecta": "top1_trifecta", "rank": "top1_rank", "approx_prob": "top1_approx_prob"}
    ).merge(actual, on="race_id", how="inner")
    comp["mismatch_type"] = [classify_mismatch(t, a) for t, a in zip(comp["top1_trifecta"], comp["actual_trifecta"])]

    total = int(len(comp))
    counts = comp["mismatch_type"].value_counts().to_dict()
    rates = {k: (v / total if total else 0.0) for k, v in counts.items()}

    requested = [
        "second_only_diff",
        "third_only_diff",
        "second_third_both_diff",
        "first_diff_but_actual_in_top40",
        "hit",
    ]
    normalized_counts = {k: int(counts.get(k, 0)) for k in requested}
    normalized_rates = {k: float(rates.get(k, 0.0)) for k in requested}

    out_detail = Path(args.out_detail)
    out_summary = Path(args.out_summary)
    out_detail.parent.mkdir(parents=True, exist_ok=True)
    comp.sort_values("race_id").to_csv(out_detail, index=False)

    summary = {
        "config": {"top_n_win": args.top_n_win, "max_combos": args.max_combos},
        "races_with_actual_in_top40": total,
        "mismatch_breakdown_count": normalized_counts,
        "mismatch_breakdown_rate": normalized_rates,
        "dominant_mismatch": max(normalized_counts, key=normalized_counts.get) if total else None,
    }
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"saved: {out_detail}")
    print(f"saved: {out_summary}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
