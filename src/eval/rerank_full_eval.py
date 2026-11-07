import argparse
import json
from pathlib import Path

import pandas as pd


def to_bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def load_actual_map(actual_csv: Path, fallback_mismatch_csv: Path) -> dict[str, dict[str, str]]:
    if actual_csv.exists():
        df = pd.read_csv(actual_csv)
        required = {"race_id", "actual_trifecta"}
        if required.issubset(df.columns):
            if "result_available" in df.columns:
                df["result_available"] = to_bool_series(df["result_available"])
                df = df[df["result_available"]].copy()
            df = df[["race_id", "actual_trifecta"]].drop_duplicates("race_id").copy()
            df["actual_winner"] = df["actual_trifecta"].astype(str).str.split("-").str[0].str.strip()
            return {
                str(r["race_id"]): {
                    "actual_winner": str(r["actual_winner"]).strip(),
                    "actual_trifecta": str(r["actual_trifecta"]).strip(),
                }
                for _, r in df.iterrows()
            }

    if fallback_mismatch_csv.exists():
        df = pd.read_csv(fallback_mismatch_csv)
        if {"race_id", "actual_trifecta"}.issubset(df.columns):
            df = df[["race_id", "actual_trifecta"]].drop_duplicates("race_id").copy()
            df["actual_winner"] = df["actual_trifecta"].astype(str).str.split("-").str[0].str.strip()
            return {
                str(r["race_id"]): {
                    "actual_winner": str(r["actual_winner"]).strip(),
                    "actual_trifecta": str(r["actual_trifecta"]).strip(),
                }
                for _, r in df.iterrows()
            }
    return {}


def main():
    parser = argparse.ArgumentParser(description="Full-race rerank eval for winner/trifecta exact hit.")
    parser.add_argument("--proba-csv", default="data/model_outputs/today_win_proba.csv")
    parser.add_argument("--features-csv", default="data/features/today_features.csv")
    parser.add_argument("--actual-csv", default="reports/backtest_race_results.csv")
    parser.add_argument("--fallback-mismatch-csv", default="reports/top1_mismatch_within_top5_max40.csv")
    parser.add_argument("--out-json", default="reports/rerank_full_eval.json")
    parser.add_argument("--beta", type=float, default=0.2)
    args = parser.parse_args()

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

    tiebreak_feats = ["national_win_rate", "local_2ren_rate"]
    for feat in tiebreak_feats:
        merged_df[feat] = pd.to_numeric(merged_df.get(feat), errors="coerce")
        col_min = float(merged_df[feat].min()) if merged_df[feat].notna().any() else 0.0
        col_max = float(merged_df[feat].max()) if merged_df[feat].notna().any() else 1.0
        merged_df[f"{feat}_scaled"] = (merged_df[feat] - col_min) / (col_max - col_min + 1e-9)
        merged_df[f"{feat}_scaled"] = merged_df[f"{feat}_scaled"].fillna(0.0)

    actual_map = load_actual_map(Path(args.actual_csv), Path(args.fallback_mismatch_csv))

    race_ids = [str(x) for x in merged_df["race_id"].astype(str).unique()]
    baseline_hits = 0
    rerank_hits = 0
    trifecta_base = 0
    trifecta_rerank = 0
    total = 0
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

        base_ranked = (
            race_data.sort_values("win_proba_norm", ascending=False)
            .head(3)["lane"]
            .astype(str)
            .str.strip()
            .tolist()
        )

        race_data["rerank_score"] = race_data["win_proba_norm"].copy()
        for feat in tiebreak_feats:
            race_data["rerank_score"] = race_data["rerank_score"] + args.beta * race_data[f"{feat}_scaled"]

        rerank_ranked = (
            race_data.sort_values("rerank_score", ascending=False)
            .head(3)["lane"]
            .astype(str)
            .str.strip()
            .tolist()
        )

        if len(base_ranked) < 3 or len(rerank_ranked) < 3:
            skipped += 1
            continue

        if base_ranked[0] == actual_winner:
            baseline_hits += 1
        if rerank_ranked[0] == actual_winner:
            rerank_hits += 1

        base_tri = "-".join(base_ranked)
        rerank_tri = "-".join(rerank_ranked)
        if base_tri == actual_trifecta:
            trifecta_base += 1
        if rerank_tri == actual_trifecta:
            trifecta_rerank += 1
        total += 1

    result = {
        "beta": args.beta,
        "actual_source": str(args.actual_csv) if Path(args.actual_csv).exists() else str(args.fallback_mismatch_csv),
        "total_races": int(total),
        "skipped_races": int(skipped),
        "winner_top1": {
            "baseline": {"hits": int(baseline_hits), "hitrate": round(float(baseline_hits / total), 4) if total else 0.0},
            f"rerank_beta{args.beta}": {"hits": int(rerank_hits), "hitrate": round(float(rerank_hits / total), 4) if total else 0.0},
            "diff": round(float((rerank_hits - baseline_hits) / total), 4) if total else 0.0,
        },
        "trifecta_exact": {
            "baseline": {"hits": int(trifecta_base), "hitrate": round(float(trifecta_base / total), 4) if total else 0.0},
            f"rerank_beta{args.beta}": {"hits": int(trifecta_rerank), "hitrate": round(float(trifecta_rerank / total), 4) if total else 0.0},
            "diff": round(float((trifecta_rerank - trifecta_base) / total), 4) if total else 0.0,
        },
    }

    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[saved] {out}")


if __name__ == "__main__":
    main()
