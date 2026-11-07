from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
EV_ANALYSIS = ROOT / "data" / "strategy_outputs" / "ev_analysis.csv"
HIST = ROOT / "data" / "processed" / "historical_races.csv"
OUT_JSON = ROOT / "reports" / "buy_threshold_tuning.json"


FWP_GRID = [0.16, 0.17, 0.18, 0.19, 0.20, 0.22, 0.24]
EV_GRID = [1.1, 20, 50, 100, 150, 200, 300]
BUY_RANGE = (100, 200)


@dataclass
class EvalResult:
    fwp: float
    ev: float
    buy_count: int
    hit_count: int
    hit_rate: float | None
    roi: float | None
    recent30_buy: int
    recent30_hit: int
    recent30_hit_rate: float | None
    recent30_roi: float | None


def _reconstruct_actual_trifecta(hist: pd.DataFrame) -> pd.DataFrame:
    top3 = (
        hist[hist["finish_position"].isin([1, 2, 3])]
        .sort_values(["race_id", "finish_position"])
        .groupby("race_id")["lane"]
        .apply(lambda x: "-".join(x.astype(int).astype(str)))
        .reset_index()
        .rename(columns={"lane": "actual_trifecta"})
    )
    return top3


def _score(df: pd.DataFrame) -> tuple[int, int, float | None, float | None]:
    if len(df) == 0:
        return 0, 0, None, None
    hit = (df["trifecta"].astype(str) == df["actual_trifecta"].astype(str))
    buy_count = int(len(df))
    hit_count = int(hit.sum())
    hit_rate = float(hit.mean()) if buy_count > 0 else None
    # Flat 1-unit stake ROI
    payout = pd.to_numeric(df["odds"], errors="coerce").fillna(0.0)
    roi = float((payout[hit].sum()) / buy_count) if buy_count > 0 else None
    return buy_count, hit_count, hit_rate, roi


def evaluate() -> dict:
    ev_df = pd.read_csv(EV_ANALYSIS)
    hist = pd.read_csv(HIST, low_memory=False)

    actual = _reconstruct_actual_trifecta(hist)
    top = (
        ev_df.sort_values(["race_id", "sort_score"], ascending=[True, False])
        .groupby("race_id")
        .head(1)
        .copy()
    )
    top["date"] = pd.to_datetime(top["date"], errors="coerce")
    merged = top.merge(actual, on="race_id", how="left")

    max_date = merged["date"].max()
    recent_cut = max_date - pd.Timedelta(days=29)

    rows: list[EvalResult] = []
    for fwp in FWP_GRID:
        for ev in EV_GRID:
            sub = merged[
                (pd.to_numeric(merged["first_win_proba"], errors="coerce") >= fwp)
                & (pd.to_numeric(merged["ev"], errors="coerce") >= ev)
            ].copy()
            buy_count, hit_count, hit_rate, roi = _score(sub)

            recent = sub[sub["date"] >= recent_cut].copy()
            r_buy, r_hit, r_hr, r_roi = _score(recent)

            rows.append(
                EvalResult(
                    fwp=fwp,
                    ev=ev,
                    buy_count=buy_count,
                    hit_count=hit_count,
                    hit_rate=hit_rate,
                    roi=roi,
                    recent30_buy=r_buy,
                    recent30_hit=r_hit,
                    recent30_hit_rate=r_hr,
                    recent30_roi=r_roi,
                )
            )

    # Prefer configs in BUY range, then higher hit_count, then higher ROI.
    candidates = [
        r
        for r in rows
        if BUY_RANGE[0] <= r.buy_count <= BUY_RANGE[1]
    ]
    if not candidates:
        candidates = rows
    best = sorted(
        candidates,
        key=lambda r: (
            r.hit_count,
            (r.roi if r.roi is not None else -1.0),
            -(abs(r.buy_count - ((BUY_RANGE[0] + BUY_RANGE[1]) // 2))),
        ),
        reverse=True,
    )[0]

    def to_dict(r: EvalResult) -> dict:
        return {
            "operational_min_win_proba": r.fwp,
            "min_ev_threshold": r.ev,
            "buy_count": r.buy_count,
            "hit_count": r.hit_count,
            "hit_rate": r.hit_rate,
            "roi": r.roi,
            "recent30_buy": r.recent30_buy,
            "recent30_hit": r.recent30_hit,
            "recent30_hit_rate": r.recent30_hit_rate,
            "recent30_roi": r.recent30_roi,
        }

    payload = {
        "buy_target_range": {"min": BUY_RANGE[0], "max": BUY_RANGE[1]},
        "grid": {
            "operational_min_win_proba": FWP_GRID,
            "min_ev_threshold": EV_GRID,
        },
        "best": to_dict(best),
        "top10": [to_dict(r) for r in sorted(candidates, key=lambda r: (r.hit_count, (r.roi or -1.0)), reverse=True)[:10]],
    }
    return payload


def main() -> None:
    payload = evaluate()
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["best"], ensure_ascii=False, indent=2))
    print(f"[saved] {OUT_JSON}")


if __name__ == "__main__":
    main()

