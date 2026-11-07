from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
PRED_CSV = ROOT / "data" / "strategy_outputs" / "skip_decisions.csv"
HIST_CSV = ROOT / "data" / "processed" / "historical_races.csv"
OUT_DIR = ROOT / "reports" / "place_role_scores"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _to_num(v: object) -> float | None:
    n = pd.to_numeric(v, errors="coerce")
    if pd.isna(n):
        return None
    return float(n)


def _make_top3_map(hist: pd.DataFrame) -> pd.DataFrame:
    required = {"race_id", "lane", "finish_position"}
    if not required.issubset(set(hist.columns)):
        raise ValueError(f"historical_races.csv requires columns: {sorted(required)}")
    hist = hist.copy()
    hist["finish_position"] = pd.to_numeric(hist["finish_position"], errors="coerce")
    hist["lane"] = pd.to_numeric(hist["lane"], errors="coerce")
    top3 = (
        hist[hist["finish_position"].isin([1, 2, 3])]
        .sort_values(["race_id", "finish_position"])
        .groupby("race_id")["lane"]
        .apply(lambda x: "-".join(x.dropna().astype(int).astype(str)))
        .reset_index()
        .rename(columns={"lane": "actual_trifecta"})
    )
    top3["actual_first_lane"] = top3["actual_trifecta"].str.split("-").str[0]
    top3["actual_second_lane"] = top3["actual_trifecta"].str.split("-").str[1]
    top3["actual_third_lane"] = top3["actual_trifecta"].str.split("-").str[2]
    top3["race_id"] = top3["race_id"].astype(str)
    return top3


def _bin_score(series: pd.Series, bins: list[float], labels: list[str]) -> pd.Series:
    return pd.cut(series, bins=bins, labels=labels, include_lowest=True, right=True)


def _aggregate(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    rows: list[dict] = []
    for key, g in df.groupby(group_col, dropna=False):
        n = len(g)
        exact_hit_rate = float(g["exact_hit"].mean()) if n else 0.0
        second_hit_rate = float(g["second_hit"].mean()) if n else 0.0
        third_hit_rate = float(g["third_hit"].mean()) if n else 0.0
        pair_hit_rate = float(g["pair_hit"].mean()) if n else 0.0
        total_stake = float(g["stake"].sum()) if "stake" in g.columns else float(n)
        total_return = float(g["realized_return"].sum()) if "realized_return" in g.columns else 0.0
        roi = total_return / total_stake if total_stake > 0 else 0.0
        avg_odds = float(g["odds"].mean()) if "odds" in g.columns and n else 0.0
        rows.append(
            {
                group_col: str(key),
                "sample_count": int(n),
                "exact_hit_rate": round(exact_hit_rate, 4),
                "second_hit_rate": round(second_hit_rate, 4),
                "third_hit_rate": round(third_hit_rate, 4),
                "pair_hit_rate": round(pair_hit_rate, 4),
                "total_stake": round(total_stake, 4),
                "total_return": round(total_return, 4),
                "roi": round(roi, 4),
                "avg_odds": round(avg_odds, 2),
            }
        )
    return pd.DataFrame(rows).sort_values(["sample_count", "roi"], ascending=[False, False]).reset_index(drop=True)


def main() -> None:
    if not PRED_CSV.exists():
        raise FileNotFoundError(PRED_CSV)
    if not HIST_CSV.exists():
        raise FileNotFoundError(HIST_CSV)

    pred = pd.read_csv(PRED_CSV)
    hist = pd.read_csv(HIST_CSV)
    top3 = _make_top3_map(hist)

    empty_series = pd.Series([""] * len(pred), index=pred.index)
    pred["race_id"] = pred["race_id"].astype(str)
    pred["recommended_trifecta"] = pred["recommended_trifecta"].astype(str)
    pred["second_lane"] = pred["second_lane"] if "second_lane" in pred.columns else empty_series
    pred["third_lane"] = pred["third_lane"] if "third_lane" in pred.columns else empty_series
    pred["second_lane"] = pred["second_lane"].astype(str)
    pred["third_lane"] = pred["third_lane"].astype(str)
    pred["odds"] = pd.to_numeric(pred["odds"], errors="coerce") if "odds" in pred.columns else pd.Series([None] * len(pred), index=pred.index)
    pred["stake"] = 1.0
    pred["realized_return"] = pd.to_numeric(pred["realized_return"], errors="coerce").fillna(0.0) if "realized_return" in pred.columns else 0.0
    pred["decision"] = pred["decision"].astype(str).str.upper() if "decision" in pred.columns else ""

    buy = pred[pred["decision"] == "BUY"].copy()
    scope = "BUY" if not buy.empty else "ALL"
    analysis_rows = buy if not buy.empty else pred.copy()

    merged = analysis_rows.merge(top3, on="race_id", how="left")
    merged["exact_hit"] = (
        merged["recommended_trifecta"].astype(str).str.strip() == merged["actual_trifecta"].astype(str).str.strip()
    ).astype(int)
    merged["second_hit"] = (
        merged["second_lane"].astype(str).str.strip() == merged["actual_second_lane"].astype(str).str.strip()
    ).astype(int)
    merged["third_hit"] = (
        merged["third_lane"].astype(str).str.strip() == merged["actual_third_lane"].astype(str).str.strip()
    ).astype(int)
    merged["pair_hit"] = ((merged["second_hit"] == 1) & (merged["third_hit"] == 1)).astype(int)

    score_bins = [-10.0, -1.0, 0.0, 1.0, 2.0, 10.0]
    score_labels = ["<-1", "-1-0", "0-1", "1-2", "2+"]
    second_score_src = pd.to_numeric(merged["second_place_score"], errors="coerce") if "second_place_score" in merged.columns else pd.Series([0.0] * len(merged), index=merged.index)
    third_score_src = pd.to_numeric(merged["third_place_score"], errors="coerce") if "third_place_score" in merged.columns else pd.Series([0.0] * len(merged), index=merged.index)
    merged["second_score_bin"] = _bin_score(second_score_src.fillna(0.0), score_bins, score_labels).astype(str)
    merged["third_score_bin"] = _bin_score(third_score_src.fillna(0.0), score_bins, score_labels).astype(str)
    merged["second_gate"] = merged["second_place_gate"].fillna("MISSING").astype(str) if "second_place_gate" in merged.columns else "MISSING"
    merged["third_gate"] = merged["third_place_gate"].fillna("MISSING").astype(str) if "third_place_gate" in merged.columns else "MISSING"

    by_second_gate = _aggregate(merged, "second_gate")
    by_third_gate = _aggregate(merged, "third_gate")
    by_second_score = _aggregate(merged, "second_score_bin")
    by_third_score = _aggregate(merged, "third_score_bin")

    by_second_gate.to_csv(OUT_DIR / "by_second_gate.csv", index=False, encoding="utf-8-sig")
    by_third_gate.to_csv(OUT_DIR / "by_third_gate.csv", index=False, encoding="utf-8-sig")
    by_second_score.to_csv(OUT_DIR / "by_second_score.csv", index=False, encoding="utf-8-sig")
    by_third_score.to_csv(OUT_DIR / "by_third_score.csv", index=False, encoding="utf-8-sig")
    merged.to_csv(OUT_DIR / "buy_role_rows.csv", index=False, encoding="utf-8-sig")

    summary = {
        "rows": int(len(merged)),
        "buy_rows": int(len(buy)),
        "analysis_scope": scope,
        "note": "BUY rows not found, used all rows" if buy.empty else "",
        "exact_hits": int(merged["exact_hit"].sum()),
        "second_hits": int(merged["second_hit"].sum()),
        "third_hits": int(merged["third_hit"].sum()),
        "pair_hits": int(merged["pair_hit"].sum()),
        "exact_hit_rate": round(float(merged["exact_hit"].mean()), 4),
        "second_hit_rate": round(float(merged["second_hit"].mean()), 4),
        "third_hit_rate": round(float(merged["third_hit"].mean()), 4),
        "pair_hit_rate": round(float(merged["pair_hit"].mean()), 4),
        "total_stake": round(float(merged["stake"].sum()), 4),
        "total_return": round(float(merged["realized_return"].sum()), 4),
        "roi": round(float(merged["realized_return"].sum()) / float(merged["stake"].sum()), 4) if float(merged["stake"].sum()) > 0 else 0.0,
        "avg_odds": round(float(merged["odds"].mean()), 2) if merged["odds"].notna().any() else None,
        "files": {
            "by_second_gate": str((OUT_DIR / "by_second_gate.csv").relative_to(ROOT)),
            "by_third_gate": str((OUT_DIR / "by_third_gate.csv").relative_to(ROOT)),
            "by_second_score": str((OUT_DIR / "by_second_score.csv").relative_to(ROOT)),
            "by_third_score": str((OUT_DIR / "by_third_score.csv").relative_to(ROOT)),
            "rows": str((OUT_DIR / "buy_role_rows.csv").relative_to(ROOT)),
        },
    }
    (OUT_DIR / "place_role_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
