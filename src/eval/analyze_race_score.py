import json
import re
from pathlib import Path

import numpy as np
import pandas as pd


PRED_CSV = Path("data/strategy_outputs/skip_decisions.csv")
HIST_CSV = Path("data/processed/historical_races.csv")
OUT_DIR = Path("reports/race_score")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _safe_bin(series: pd.Series, edges: list[float], labels: list[str]) -> pd.Series:
    if series.empty:
        return pd.Series(dtype="object")
    return pd.cut(series, bins=edges, labels=labels, include_lowest=True, right=False)


def _normalize_key(v: object) -> str:
    return re.sub(r"[^0-9]", "", str(v or ""))


def _actual_trifecta_map() -> dict[str, str]:
    if not HIST_CSV.exists():
        return {}
    hist = pd.read_csv(HIST_CSV)
    required = {"race_id", "lane", "finish_position"}
    if not required.issubset(hist.columns):
        return {}
    hist = hist.copy()
    hist["finish_position"] = pd.to_numeric(hist["finish_position"], errors="coerce")
    hist["lane"] = pd.to_numeric(hist["lane"], errors="coerce")
    top3 = (
        hist[hist["finish_position"].isin([1, 2, 3])]
        .sort_values(["race_id", "finish_position"])
        .groupby("race_id")["lane"]
        .apply(lambda x: "-".join(x.fillna(-1).astype(int).astype(str)))
        .reset_index()
    )
    out: dict[str, str] = {}
    for _, r in top3.iterrows():
        tri = str(r["lane"]) if pd.notna(r["lane"]) else ""
        tri = "-".join(re.findall(r"\d+", tri)[:3])
        if not tri:
            continue
        race_id = str(r["race_id"])
        out[race_id] = tri
        out[_normalize_key(race_id)] = tri
    return out


def main() -> None:
    if not PRED_CSV.exists():
        raise FileNotFoundError(f"Prediction file not found: {PRED_CSV}")

    df = pd.read_csv(PRED_CSV)
    if df.empty:
        raise RuntimeError("skip_decisions.csv is empty")

    df = df.copy()
    df["race_score"] = pd.to_numeric(df.get("race_score"), errors="coerce")
    df["race_gate"] = pd.Series(df.get("race_gate", "MISSING"), index=df.index).astype(str).str.upper()
    df["decision"] = pd.Series(df.get("decision", ""), index=df.index).astype(str).str.upper()
    df["odds"] = pd.to_numeric(df.get("odds"), errors="coerce").fillna(0.0)
    df["approx_prob"] = pd.to_numeric(df.get("approx_prob"), errors="coerce").fillna(0.0)
    df["calibrated_hit_prob"] = pd.to_numeric(df.get("calibrated_hit_prob"), errors="coerce").fillna(0.0)
    df["first_place_score"] = pd.to_numeric(df.get("first_place_score"), errors="coerce").fillna(0.0)
    df["pre_race_score"] = pd.to_numeric(df.get("pre_race_score"), errors="coerce").fillna(0.0)
    df["recommended_trifecta"] = pd.Series(df.get("recommended_trifecta", ""), index=df.index).astype(str)
    actual_map = _actual_trifecta_map()
    df["actual_trifecta"] = df["race_id"].map(actual_map).fillna(df["race_id"].map(_normalize_key).map(actual_map))
    df["is_hit"] = (
        df["recommended_trifecta"].astype(str).str.replace(r"[^0-9]", "", regex=True)
        == df["actual_trifecta"].astype(str).str.replace(r"[^0-9]", "", regex=True)
    ) & df["recommended_trifecta"].ne("") & df["actual_trifecta"].ne("")

    score_edges = [-999, -1.0, 0.0, 1.0, 2.0, 999]
    score_labels = ["< -1", "-1-0", "0-1", "1-2", "2+"]
    df["race_score_bin"] = _safe_bin(df["race_score"].fillna(-999.0), score_edges, score_labels)

    rows = []
    for label, group in df.groupby("race_score_bin", dropna=False):
        if group.empty:
            continue
        buy = group[group["decision"] == "BUY"]
        hit_count = int(buy["is_hit"].sum())
        buy_count = int(len(buy))
        total_stake = float(buy_count)
        total_return = float(buy.loc[buy["is_hit"], "odds"].fillna(0.0).sum())
        hit_rate = float(hit_count / buy_count) if buy_count > 0 else 0.0
        roi = float(total_return / total_stake) if total_stake > 0 else 0.0
        rows.append(
            {
                "race_score_bin": str(label),
                "sample_count": int(len(group)),
                "buy_count": buy_count,
                "watch_count": int((group["decision"] == "WATCH").sum()),
                "skip_count": int((group["decision"] == "SKIP").sum()),
                "buy_rate": round(float((group["decision"] == "BUY").mean()), 4),
                "hit_count": hit_count,
                "hit_rate": round(hit_rate, 4),
                "total_stake": round(total_stake, 2),
                "total_return": round(total_return, 2),
                "roi": round(roi, 4),
                "avg_race_score": round(float(group["race_score"].mean()), 4),
                "avg_first_place_score": round(float(group["first_place_score"].mean()), 4),
                "avg_pre_race_score": round(float(group["pre_race_score"].mean()), 4),
                "avg_odds": round(float(group["odds"].mean()), 2),
                "avg_calibrated_hit_prob": round(float(group["calibrated_hit_prob"].mean()), 4),
                "avg_approx_prob": round(float(group["approx_prob"].mean()), 4),
            }
        )

    summary = {
        "total_rows": int(len(df)),
        "buy_rows": int((df["decision"] == "BUY").sum()),
        "watch_rows": int((df["decision"] == "WATCH").sum()),
        "skip_rows": int((df["decision"] == "SKIP").sum()),
        "hit_rows": int(df["is_hit"].sum()),
        "race_score_avg": round(float(df["race_score"].mean()), 4),
        "race_score_min": round(float(df["race_score"].min()), 4),
        "race_score_max": round(float(df["race_score"].max()), 4),
        "gate_counts": df["race_gate"].value_counts(dropna=False).to_dict(),
    }

    out_df = pd.DataFrame(rows).sort_values("race_score_bin", ascending=False)
    out_df.to_csv(OUT_DIR / "by_race_score_bin.csv", index=False)
    (OUT_DIR / "race_score_summary.json").write_text(
        json.dumps(summary | {"score_bins": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
