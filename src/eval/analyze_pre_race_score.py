from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
PRED_CSV = ROOT / "data" / "strategy_outputs" / "skip_decisions.csv"
HIST_CSV = ROOT / "data" / "processed" / "historical_races.csv"
OUT_DIR = ROOT / "reports" / "pre_race_score"
OUT_JSON = OUT_DIR / "pre_race_score_summary.json"
OUT_CSV = OUT_DIR / "pre_race_score_breakdown.csv"


def _norm_tri(v: object) -> str:
    parts = re.findall(r"\d+", str(v or ""))
    return "-".join(parts[:3]) if len(parts) >= 3 else ""


def _load_actual_map(hist: pd.DataFrame) -> dict[str, str]:
    df = hist.copy()
    if not {"race_id", "lane", "finish_position"}.issubset(df.columns):
        return {}
    df["race_id"] = df["race_id"].astype(str).str.strip()
    df["lane"] = pd.to_numeric(df["lane"], errors="coerce")
    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    top3 = (
        df[df["finish_position"].isin([1, 2, 3])]
        .sort_values(["race_id", "finish_position"])
        .groupby("race_id")["lane"]
        .apply(lambda x: "-".join(x.fillna(-1).astype(int).astype(str)))
        .reset_index()
    )
    out: dict[str, str] = {}
    for _, row in top3.iterrows():
        tri = _norm_tri(row["lane"])
        if tri:
            race_id = str(row["race_id"])
            out[race_id] = tri
            out[re.sub(r"[^0-9]", "", race_id)] = tri
    return out


def main() -> None:
    if not PRED_CSV.exists():
        raise FileNotFoundError(f"missing prediction file: {PRED_CSV}")
    if not HIST_CSV.exists():
        raise FileNotFoundError(f"missing historical file: {HIST_CSV}")

    pred = pd.read_csv(PRED_CSV, low_memory=False)
    hist = pd.read_csv(HIST_CSV, low_memory=False)
    actual_map = _load_actual_map(hist)

    pred["race_id"] = pred["race_id"].astype(str).str.strip()
    pred["pre_race_score"] = pd.to_numeric(pred.get("pre_race_score"), errors="coerce")
    if "pre_race_gate" in pred.columns:
        pred["pre_race_gate"] = pred["pre_race_gate"].astype(str).str.upper()
    else:
        pred["pre_race_gate"] = ""
    pred["odds"] = pd.to_numeric(pred.get("odds"), errors="coerce")

    pred["actual_trifecta"] = pred["race_id"].map(actual_map).fillna(
        pred["race_id"].str.replace(r"[^0-9]", "", regex=True).map(actual_map)
    )
    pred["recommended_trifecta"] = pred["recommended_trifecta"].map(_norm_tri)
    pred["exact_hit"] = (pred["recommended_trifecta"] == pred["actual_trifecta"]).astype(int)
    pred["return"] = pred["odds"].where(pred["exact_hit"] == 1, 0.0).fillna(0.0)
    pred["stake"] = 1.0

    bins = [-999, -1.0, 0.0, 1.0, 2.0, 999]
    labels = ["<=-1", "(-1,0]", "(0,1]", "[1,2)", ">=2"]
    pred["pre_race_score_bin"] = pd.cut(pred["pre_race_score"], bins=bins, labels=labels, include_lowest=True, right=False)

    grouped = (
        pred.groupby("pre_race_score_bin", observed=True)
        .agg(
            sample_count=("race_id", "count"),
            exact_hits=("exact_hit", "sum"),
            total_stake=("stake", "sum"),
            total_return=("return", "sum"),
            avg_odds=("odds", "mean"),
            block_rows=("pre_race_gate", lambda s: int((s == "BLOCK").sum())),
            boost_rows=("pre_race_gate", lambda s: int((s == "BOOST").sum())),
            priority_rows=("pre_race_gate", lambda s: int((s == "PRIORITY").sum())),
        )
        .reset_index()
    )
    grouped["exact_hit_rate"] = grouped["exact_hits"] / grouped["sample_count"]
    grouped["roi"] = grouped["total_return"] / grouped["total_stake"]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    grouped.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    summary = {
        "total_rows": int(len(pred)),
        "exact_hits": int(pred["exact_hit"].sum()),
        "blocked_rows": int((pred["pre_race_gate"] == "BLOCK").sum()),
        "boost_rows": int((pred["pre_race_gate"] == "BOOST").sum()),
        "priority_rows": int((pred["pre_race_gate"] == "PRIORITY").sum()),
        "avg_pre_race_score": round(float(pred["pre_race_score"].mean()), 4) if len(pred) else None,
        "avg_pre_race_score_buy": round(float(pred.loc[pred["decision"] == "BUY", "pre_race_score"].mean()), 4)
        if int((pred["decision"] == "BUY").sum()) > 0
        else None,
        "breakdown_csv": str(OUT_CSV),
    }
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
