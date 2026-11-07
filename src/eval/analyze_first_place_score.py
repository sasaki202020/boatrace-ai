from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SKIP_CSV = ROOT / "data" / "strategy_outputs" / "skip_decisions.csv"
HIST_CSV = ROOT / "data" / "processed" / "historical_races.csv"
OUT_DIR = ROOT / "reports" / "first_place_score"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _load_winner_map() -> dict[str, str]:
    if not HIST_CSV.exists():
        return {}
    hist = pd.read_csv(HIST_CSV)
    required = {"race_id", "lane", "finish_position"}
    if not required.issubset(set(hist.columns)):
        return {}
    hist["race_id"] = hist["race_id"].astype(str)
    hist["lane"] = pd.to_numeric(hist["lane"], errors="coerce")
    hist["finish_position"] = pd.to_numeric(hist["finish_position"], errors="coerce")
    winners = (
        hist[hist["finish_position"] == 1]
        .dropna(subset=["lane"])
        .copy()
    )
    out: dict[str, str] = {}
    for _, r in winners.iterrows():
        race_id = str(r["race_id"])
        key = re.sub(r"[^0-9]", "", race_id)
        out[race_id] = str(int(r["lane"]))
        out[key] = str(int(r["lane"]))
    return out


def _roi(stake: float, returns: float) -> float | None:
    if stake <= 0:
        return None
    return round(returns / stake, 4)


def main() -> None:
    if not SKIP_CSV.exists():
        raise FileNotFoundError(f"missing: {SKIP_CSV}")
    preds = pd.read_csv(SKIP_CSV)
    if preds.empty:
        raise RuntimeError("skip_decisions.csv is empty")

    winner_map = _load_winner_map()
    df = preds.copy()
    df["race_id"] = df["race_id"].astype(str)
    df["first_lane"] = df.get("first_lane", pd.Series(index=df.index, dtype=object)).astype(str)
    df["first_place_score"] = pd.to_numeric(df.get("first_place_score"), errors="coerce")
    df["bet_amount"] = pd.to_numeric(df.get("bet_amount"), errors="coerce").fillna(0.0)
    df["odds"] = pd.to_numeric(df.get("odds"), errors="coerce")
    df["is_buy"] = df["decision"].astype(str).str.upper().eq("BUY")

    race_key = df["race_id"].astype(str).str.replace(r"[^0-9]", "", regex=True)
    df["actual_winner_lane"] = df["race_id"].map(winner_map).fillna(race_key.map(winner_map))
    df["first_lane_hit"] = (
        df["first_lane"].astype(str).str.extract(r"(\d+)")[0].fillna("")
        == df["actual_winner_lane"].fillna("")
    )
    df["score_bin"] = pd.cut(
        df["first_place_score"].fillna(-9.9),
        bins=[-10, -2, -1, 0, 1, 2, 3, 10],
        labels=["<-2", "-2--1", "-1-0", "0-1", "1-2", "2-3", "3+"],
        include_lowest=True,
        right=False,
    )

    rows = []
    for score_bin, g in df.groupby("score_bin", dropna=False, observed=True):
        g = g.copy()
        sample_count = int(len(g))
        hit_count = int(g["first_lane_hit"].sum())
        buy_count = int(g["is_buy"].sum())
        buy_hit_count = int((g["is_buy"] & g["first_lane_hit"]).sum())
        stake = float(g.loc[g["is_buy"], "bet_amount"].sum()) if buy_count else float(buy_count * 1000)
        realized_return = float(
            g.loc[g["is_buy"] & g["first_lane_hit"], "odds"].fillna(0.0).sum() * 1.0
        ) if buy_count else 0.0
        rows.append(
            {
                "score_bin": str(score_bin),
                "sample_count": sample_count,
                "hit_count": hit_count,
                "hit_rate": round(hit_count / sample_count, 4) if sample_count else None,
                "buy_count": buy_count,
                "buy_hit_count": buy_hit_count,
                "buy_hit_rate": round(buy_hit_count / buy_count, 4) if buy_count else None,
                "total_stake": round(stake, 2),
                "total_return": round(realized_return, 2),
                "roi": _roi(stake, realized_return),
                "avg_score": round(float(g["first_place_score"].mean()), 4) if g["first_place_score"].notna().any() else None,
                "avg_odds": round(float(g["odds"].mean()), 2) if g["odds"].notna().any() else None,
            }
        )

    out_df = pd.DataFrame(rows).sort_values("score_bin", na_position="last")
    out_csv = OUT_DIR / "first_place_score_breakdown.csv"
    out_json = OUT_DIR / "first_place_score_summary.json"
    out_df.to_csv(out_csv, index=False)

    best_rows = out_df[out_df["roi"].fillna(0.0) > 1.0].copy()
    summary = {
        "rows": int(len(df)),
        "buy_rows": int(df["is_buy"].sum()),
        "hit_rows": int(df["first_lane_hit"].sum()),
        "best_bins": best_rows["score_bin"].tolist(),
        "best_bin_count": int(len(best_rows)),
        "max_roi": None if out_df["roi"].dropna().empty else round(float(out_df["roi"].dropna().max()), 4),
        "max_hit_rate": None if out_df["hit_rate"].dropna().empty else round(float(out_df["hit_rate"].dropna().max()), 4),
        "output_csv": str(out_csv),
        "output_json": str(out_json),
    }
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
