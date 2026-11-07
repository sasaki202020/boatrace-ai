import argparse
import json
from pathlib import Path

import pandas as pd


def to_bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def load_actual_map(backtest_csv: Path, mismatch_csv: Path) -> dict[str, dict[str, str]]:
    if backtest_csv.exists():
        bt = pd.read_csv(backtest_csv)
        if {"race_id", "actual_trifecta"}.issubset(bt.columns):
            if "result_available" in bt.columns:
                bt["result_available"] = to_bool_series(bt["result_available"])
                bt = bt[bt["result_available"]].copy()
            bt = bt[["race_id", "actual_trifecta"]].drop_duplicates("race_id").copy()
            bt["actual_winner"] = bt["actual_trifecta"].astype(str).str.split("-").str[0].str.strip()
            return {
                str(r["race_id"]): {
                    "actual_winner": str(r["actual_winner"]).strip(),
                    "actual_trifecta": str(r["actual_trifecta"]).strip(),
                }
                for _, r in bt.iterrows()
            }

    mm = pd.read_csv(mismatch_csv)
    mm["actual_winner"] = mm["actual_trifecta"].astype(str).str.split("-").str[0].str.strip()
    return {
        str(r["race_id"]): {
            "actual_winner": str(r["actual_winner"]).strip(),
            "actual_trifecta": str(r["actual_trifecta"]).strip(),
        }
        for _, r in mm[["race_id", "actual_winner", "actual_trifecta"]].drop_duplicates("race_id").iterrows()
    }


def main():
    parser = argparse.ArgumentParser(description="Finalize rerank beta on full races.")
    parser.add_argument("--proba-csv", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--features-csv", default="data/features/today_features.csv")
    parser.add_argument("--mismatch-csv", default="reports/top1_mismatch_within_top5_max40.csv")
    parser.add_argument("--backtest-csv", default="reports/backtest_race_results.csv")
    parser.add_argument("--out-json", default="reports/rerank_beta_finalize.json")
    args = parser.parse_args()

    tiebreak_feats = ["national_win_rate", "local_2ren_rate"]
    beta_grid = [0.0, 0.1, 0.2, 0.3]

    proba_df = pd.read_csv(args.proba_csv)
    feat_df = pd.read_csv(args.features_csv)
    proba_df["lane"] = pd.to_numeric(proba_df["lane"], errors="coerce")
    feat_df["lane"] = pd.to_numeric(feat_df["lane"], errors="coerce")
    proba_df = proba_df.dropna(subset=["race_id", "lane"]).copy()
    feat_df = feat_df.dropna(subset=["race_id", "lane"]).copy()
    proba_df["lane"] = proba_df["lane"].astype(int)
    feat_df["lane"] = feat_df["lane"].astype(int)

    merged_df = proba_df.merge(feat_df, on=["race_id", "lane"], how="left")
    merged_df["win_proba_norm"] = pd.to_numeric(merged_df["win_proba_norm"], errors="coerce").fillna(0.0)

    for feat in tiebreak_feats:
        merged_df[feat] = pd.to_numeric(merged_df.get(feat), errors="coerce")
        col_min = float(merged_df[feat].min()) if merged_df[feat].notna().any() else 0.0
        col_max = float(merged_df[feat].max()) if merged_df[feat].notna().any() else 1.0
        merged_df[f"{feat}_scaled"] = (merged_df[feat] - col_min) / (col_max - col_min + 1e-9)
        merged_df[f"{feat}_scaled"] = merged_df[f"{feat}_scaled"].fillna(0.0)

    actual_map = load_actual_map(Path(args.backtest_csv), Path(args.mismatch_csv))
    race_ids = [str(x) for x in merged_df["race_id"].astype(str).unique()]

    counters = {b: {"winner_hits": 0, "trifecta_hits": 0, "total": 0} for b in beta_grid}
    skipped = 0

    for race_id in race_ids:
        actual = actual_map.get(race_id)
        if actual is None:
            skipped += 1
            continue
        actual_winner = str(actual["actual_winner"]).strip()
        actual_trifecta = str(actual["actual_trifecta"]).strip()

        race_data = merged_df[merged_df["race_id"].astype(str) == race_id].copy()
        if race_data.empty:
            skipped += 1
            continue

        for beta in beta_grid:
            race_data["rerank_score"] = race_data["win_proba_norm"].copy()
            for feat in tiebreak_feats:
                race_data["rerank_score"] = race_data["rerank_score"] + beta * race_data[f"{feat}_scaled"]

            ranked = (
                race_data.sort_values("rerank_score", ascending=False)
                .head(3)["lane"]
                .astype(str)
                .str.strip()
                .tolist()
            )
            if len(ranked) < 3:
                continue

            c = counters[beta]
            c["total"] += 1
            if ranked[0] == actual_winner:
                c["winner_hits"] += 1
            if "-".join(ranked) == actual_trifecta:
                c["trifecta_hits"] += 1

    result = {"beta_comparison": {}, "recommended_beta": None, "skipped_races": int(skipped)}
    for beta, c in counters.items():
        t = c["total"]
        result["beta_comparison"][str(beta)] = {
            "winner_top1": {
                "hits": int(c["winner_hits"]),
                "hitrate": round(float(c["winner_hits"] / t), 4) if t else 0.0,
            },
            "trifecta_exact": {
                "hits": int(c["trifecta_hits"]),
                "hitrate": round(float(c["trifecta_hits"] / t), 4) if t else 0.0,
            },
            "total": int(t),
        }

    baseline_tri = result["beta_comparison"]["0.0"]["trifecta_exact"]["hitrate"]
    candidates = [
        b
        for b in beta_grid
        if b > 0 and result["beta_comparison"][str(b)]["trifecta_exact"]["hitrate"] >= baseline_tri
    ]
    if not candidates:
        candidates = [b for b in beta_grid if b > 0]

    best_beta = max(
        candidates,
        key=lambda b: (
            result["beta_comparison"][str(b)]["winner_top1"]["hitrate"]
            + result["beta_comparison"][str(b)]["trifecta_exact"]["hitrate"]
        ),
    )
    result["recommended_beta"] = best_beta

    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[saved] {out}")


if __name__ == "__main__":
    main()
