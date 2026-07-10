import argparse
from pathlib import Path

import pandas as pd


def to_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def build_bins(series: pd.Series, edges: list[float], labels: list[str]) -> pd.Series:
    return pd.cut(series, bins=edges, labels=labels, include_lowest=True, right=False)


def hit_rate(df: pd.DataFrame, col: str) -> float:
    n = len(df)
    if n == 0:
        return 0.0
    return float(df[col].sum() / n)


def main():
    parser = argparse.ArgumentParser(description="Compare top1 by approx_prob vs EV")
    parser.add_argument("--ev-analysis", default="data/strategy_outputs/ev_analysis.csv")
    parser.add_argument("--backtest-races", default="reports/backtest_race_results.csv")
    parser.add_argument("--out-csv", default="reports/top1_prob_vs_ev_race_comparison.csv")
    parser.add_argument("--out-md", default="reports/top1_prob_vs_ev_report.md")
    args = parser.parse_args()

    ev_df = pd.read_csv(args.ev_analysis)
    bt_df = pd.read_csv(args.backtest_races)

    truth = bt_df[["race_id", "actual_trifecta", "official_odds", "result_available"]].drop_duplicates("race_id")
    truth["result_available"] = to_bool(truth["result_available"])
    truth["official_odds"] = pd.to_numeric(truth["official_odds"], errors="coerce")
    truth = truth[truth["result_available"]].copy()

    cols = ["race_id", "trifecta", "approx_prob", "first_win_proba", "ev", "odds"]
    work = ev_df[cols].copy()
    for c in ["approx_prob", "first_win_proba", "ev", "odds"]:
        work[c] = pd.to_numeric(work[c], errors="coerce")
    work = work.merge(truth[["race_id", "actual_trifecta", "official_odds"]], on="race_id", how="inner")

    top_prob = (
        work.sort_values(["race_id", "approx_prob"], ascending=[True, False])
        .groupby("race_id", as_index=False)
        .first()
        .rename(
            columns={
                "trifecta": "prob_top_trifecta",
                "approx_prob": "prob_top_approx_prob",
                "first_win_proba": "prob_top_first_win_proba",
                "ev": "prob_top_ev",
                "odds": "prob_top_odds",
            }
        )
    )

    top_ev = (
        work.sort_values(["race_id", "ev"], ascending=[True, False])
        .groupby("race_id", as_index=False)
        .first()
        .rename(
            columns={
                "trifecta": "ev_top_trifecta",
                "approx_prob": "ev_top_approx_prob",
                "first_win_proba": "ev_top_first_win_proba",
                "ev": "ev_top_ev",
                "odds": "ev_top_odds",
            }
        )
    )

    comp = top_prob.merge(
        top_ev[
            [
                "race_id",
                "ev_top_trifecta",
                "ev_top_approx_prob",
                "ev_top_first_win_proba",
                "ev_top_ev",
                "ev_top_odds",
            ]
        ],
        on="race_id",
        how="inner",
    )
    comp["same_pick"] = comp["prob_top_trifecta"] == comp["ev_top_trifecta"]
    comp["prob_hit"] = comp["prob_top_trifecta"] == comp["actual_trifecta"]
    comp["ev_hit"] = comp["ev_top_trifecta"] == comp["actual_trifecta"]
    comp["ev_worse"] = comp["prob_hit"] & (~comp["ev_hit"])
    comp["ev_better"] = (~comp["prob_hit"]) & comp["ev_hit"]
    comp["odds_delta"] = comp["ev_top_odds"] - comp["prob_top_odds"]
    comp["ev_per_prob_delta"] = (comp["ev_top_ev"] / comp["ev_top_approx_prob"]) - (
        comp["prob_top_ev"] / comp["prob_top_approx_prob"]
    )

    # banding on mismatch races only
    mismatch = comp[~comp["same_pick"]].copy()
    mismatch["official_odds_band"] = build_bins(
        mismatch["official_odds"],
        [0, 20, 40, 60, 100, 1000, 100000],
        ["0-20", "20-40", "40-60", "60-100", "100-1000", "1000+"],
    )
    mismatch["approx_prob_band"] = build_bins(
        mismatch["prob_top_approx_prob"],
        [0, 0.03, 0.05, 0.07, 0.10, 1.0],
        ["0-0.03", "0.03-0.05", "0.05-0.07", "0.07-0.10", "0.10+"],
    )
    mismatch["first_win_proba_band"] = build_bins(
        mismatch["prob_top_first_win_proba"],
        [0, 0.20, 0.25, 0.30, 0.35, 1.0],
        ["0-0.20", "0.20-0.25", "0.25-0.30", "0.30-0.35", "0.35+"],
    )
    mismatch["odds_delta_band"] = build_bins(
        mismatch["odds_delta"],
        [-1e9, -100, -50, -20, 20, 50, 100, 1e9],
        ["<-100", "-100~-50", "-50~-20", "-20~20", "20~50", "50~100", "100+"],
    )

    def grouped(df: pd.DataFrame, key: str) -> pd.DataFrame:
        out = (
            df.groupby(key, dropna=False)
            .agg(
                races=("race_id", "count"),
                prob_hit_count=("prob_hit", "sum"),
                ev_hit_count=("ev_hit", "sum"),
                ev_worse_count=("ev_worse", "sum"),
                ev_better_count=("ev_better", "sum"),
                mean_official_odds=("official_odds", "mean"),
                mean_odds_delta=("odds_delta", "mean"),
            )
            .reset_index()
        )
        out["prob_hit_rate"] = out["prob_hit_count"] / out["races"]
        out["ev_hit_rate"] = out["ev_hit_count"] / out["races"]
        return out.sort_values("races", ascending=False)

    odds_band = grouped(mismatch, "official_odds_band")
    approx_band = grouped(mismatch, "approx_prob_band")
    first_band = grouped(mismatch, "first_win_proba_band")
    gap_band = grouped(mismatch, "odds_delta_band")

    # save race-level CSV
    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    comp.to_csv(out_csv, index=False)

    # markdown report
    total = len(comp)
    same = int(comp["same_pick"].sum())
    diff = total - same
    prob_hits = int(comp["prob_hit"].sum())
    ev_hits = int(comp["ev_hit"].sum())
    prob_hr = hit_rate(comp, "prob_hit")
    ev_hr = hit_rate(comp, "ev_hit")
    ev_worse = int(comp["ev_worse"].sum())
    ev_better = int(comp["ev_better"].sum())

    lines = []
    lines.append("# Top1 Comparison: approx_prob vs EV")
    lines.append("")
    lines.append("## Overall")
    lines.append(f"- races: {total}")
    lines.append(f"- same_pick races: {same}")
    lines.append(f"- different_pick races: {diff}")
    lines.append(f"- approx_prob top1: hit_count={prob_hits}, hit_rate={prob_hr:.6f}")
    lines.append(f"- EV top1: hit_count={ev_hits}, hit_rate={ev_hr:.6f}")
    lines.append(f"- EV worse races (prob hit, ev miss): {ev_worse}")
    lines.append(f"- EV better races (prob miss, ev hit): {ev_better}")
    lines.append("")
    lines.append("## Mismatch Bands (different_pick only)")

    def add_table(title: str, df: pd.DataFrame):
        lines.append("")
        lines.append(f"### {title}")
        lines.append("| band | races | prob_hit | ev_hit | prob_hr | ev_hr | ev_worse | ev_better | mean_official_odds | mean_odds_delta |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in df.iterrows():
            lines.append(
                f"| {r.iloc[0]} | {int(r['races'])} | {int(r['prob_hit_count'])} | {int(r['ev_hit_count'])} | "
                f"{r['prob_hit_rate']:.4f} | {r['ev_hit_rate']:.4f} | {int(r['ev_worse_count'])} | {int(r['ev_better_count'])} | "
                f"{(0 if pd.isna(r['mean_official_odds']) else r['mean_official_odds']):.2f} | {(0 if pd.isna(r['mean_odds_delta']) else r['mean_odds_delta']):.2f} |"
            )

    add_table("official_odds_band", odds_band)
    add_table("approx_prob_band", approx_band)
    add_table("first_win_proba_band", first_band)
    add_table("odds_delta_band (ev/approx gap proxy)", gap_band)

    out_md = Path(args.out_md)
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"saved: {out_csv}")
    print(f"saved: {out_md}")
    print(
        f"summary races={total}, same={same}, diff={diff}, prob_hit={prob_hits}, ev_hit={ev_hits}, ev_worse={ev_worse}, ev_better={ev_better}"
    )


if __name__ == "__main__":
    main()
