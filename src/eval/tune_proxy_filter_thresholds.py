from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SKIP = ROOT / "data" / "strategy_outputs" / "skip_decisions.csv"
PROBA = ROOT / "data" / "model_outputs" / "today_win_proba.csv"
FEAT = ROOT / "data" / "features" / "today_features.csv"
HIST = ROOT / "data" / "processed" / "historical_races.csv"
OUT = ROOT / "reports" / "proxy_filter_tuning.json"

NAT_MAX_GRID = [5.0, 5.5, 6.0, 6.5, 7.0, 8.0, 10.0]
GAP_MAX_GRID = [0.003, 0.004, 0.005, 0.007, 0.01, 0.02]
BUY_RANGE = (100, 200)


def build_proxy_feats() -> pd.DataFrame:
    proba = pd.read_csv(PROBA)
    feat = pd.read_csv(FEAT)
    m = proba.merge(feat, on=["race_id", "lane"], how="left")
    m["win_proba_norm"] = pd.to_numeric(m["win_proba_norm"], errors="coerce")
    m["national_win_rate"] = pd.to_numeric(m.get("national_win_rate"), errors="coerce")

    rows = []
    for race_id, g in m.groupby("race_id"):
        g = g.sort_values("win_proba_norm", ascending=False).reset_index(drop=True)
        if len(g) < 2:
            continue
        rows.append(
            {
                "race_id": str(race_id),
                "score_gap_top1_top2": float(g.loc[0, "win_proba_norm"] - g.loc[1, "win_proba_norm"]),
                "top1_national_win_rate": float(g.loc[0, "national_win_rate"]) if pd.notna(g.loc[0, "national_win_rate"]) else None,
            }
        )
    return pd.DataFrame(rows)


def reconstruct_actual(hist: pd.DataFrame) -> pd.DataFrame:
    top3 = (
        hist[hist["finish_position"].isin([1, 2, 3])]
        .sort_values(["race_id", "finish_position"])
        .groupby("race_id")["lane"]
        .apply(lambda x: "-".join(x.astype(int).astype(str)))
        .reset_index()
        .rename(columns={"lane": "actual_trifecta"})
    )
    return top3


def score(df: pd.DataFrame) -> tuple[int, int, float | None]:
    if len(df) == 0:
        return 0, 0, None
    hit = (df["recommended_trifecta"].astype(str) == df["actual_trifecta"].astype(str))
    buy = int(len(df))
    hits = int(hit.sum())
    roi = float(pd.to_numeric(df.loc[hit, "odds"], errors="coerce").fillna(0).sum() / buy) if buy > 0 else None
    return buy, hits, roi


def main() -> None:
    skip = pd.read_csv(SKIP)
    skip["race_id"] = skip["race_id"].astype(str)
    base = skip[skip["decision"] == "BUY"].copy()

    proxy = build_proxy_feats()
    proxy["race_id"] = proxy["race_id"].astype(str)

    hist = pd.read_csv(HIST, low_memory=False)
    actual = reconstruct_actual(hist)
    actual["race_id"] = actual["race_id"].astype(str)

    merged = base.merge(proxy, on="race_id", how="left").merge(actual, on="race_id", how="left")
    merged["date"] = pd.to_datetime(merged["date"], errors="coerce")
    recent_cut = merged["date"].max() - pd.Timedelta(days=29)

    results = []
    for nat_max in NAT_MAX_GRID:
        for gap_max in GAP_MAX_GRID:
            sub = merged[
                (pd.to_numeric(merged["top1_national_win_rate"], errors="coerce") <= nat_max)
                & (pd.to_numeric(merged["score_gap_top1_top2"], errors="coerce") <= gap_max)
            ].copy()
            buy, hits, roi = score(sub)
            recent = sub[sub["date"] >= recent_cut]
            r_buy, r_hits, r_roi = score(recent)
            results.append(
                {
                    "top1_national_win_rate_max": nat_max,
                    "score_gap_top1_top2_max": gap_max,
                    "buy_count": buy,
                    "hit_count": hits,
                    "hit_rate": (hits / buy) if buy else None,
                    "roi": roi,
                    "recent30_buy": r_buy,
                    "recent30_hit": r_hits,
                    "recent30_hit_rate": (r_hits / r_buy) if r_buy else None,
                    "recent30_roi": r_roi,
                }
            )

    in_range = [r for r in results if BUY_RANGE[0] <= r["buy_count"] <= BUY_RANGE[1]]
    pool = in_range if in_range else results
    best = sorted(
        pool,
        key=lambda r: (
            r["hit_count"],
            (r["roi"] if r["roi"] is not None else -1.0),
            -(abs(r["buy_count"] - ((BUY_RANGE[0] + BUY_RANGE[1]) // 2))),
        ),
        reverse=True,
    )[0]

    payload = {
        "base_buy_count": int(len(base)),
        "buy_target_range": {"min": BUY_RANGE[0], "max": BUY_RANGE[1]},
        "best": best,
        "top10": sorted(
            pool,
            key=lambda r: (r["hit_count"], (r["roi"] if r["roi"] is not None else -1.0)),
            reverse=True,
        )[:10],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(best, ensure_ascii=False, indent=2))
    print(f"[saved] {OUT}")


if __name__ == "__main__":
    main()

